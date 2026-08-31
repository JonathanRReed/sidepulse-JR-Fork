from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .device_writer import KNOWN_LED_FILE_NAMES
from .models import AgentMode
from .power_policy import configure_caffeinate_display_assertion

AWAKE_GRACE_SECONDS = 300.0
SD_STATUS_READ_SECONDS = 60.0
KEEPALIVE_FILE_NAME = "keepalive"
STATUS_FILE_NAME = KEEPALIVE_FILE_NAME
# -i idle sleep, -m disk sleep, -s system sleep (AC): the machine stays
# up for the AGENTS. No -d and no -u -- the display may sleep and the
# hold must never count as user activity; -dimsu kept the SCREEN awake
# all night for a background monitor.
CAFFEINATE_COMMAND = ("/usr/bin/caffeinate", "-ims")

#: The modes that hold the machine awake in their own right.
WORK_MODES = frozenset(
    {AgentMode.WORKING, AgentMode.TOOL_RUNNING, AgentMode.LONG_TASK_PROGRESS}
)


def battery_yields_hold(snapshot, settings) -> bool:
    """True when the battery is low enough that the hold must yield --
    judged DIRECTLY from the snapshot and threshold, never through
    low_power_active, which is gated on the charge-reminder DISPLAY
    toggle (regression review: disabling that cosmetic reminder used to
    disable the safety yield with it)."""
    if snapshot is None or not getattr(snapshot, "battery_present", False):
        return False
    if getattr(snapshot, "is_plugged", True):
        return False
    threshold = float(getattr(settings, "low_battery_threshold_percent", 5.0))
    return float(getattr(snapshot, "percent", 100.0)) <= threshold


class KeepAwakeController:
    def __init__(
        self,
        *,
        enabled: bool = True,
        grace_seconds: float = AWAKE_GRACE_SECONDS,
        status_read_seconds: float = SD_STATUS_READ_SECONDS,
        command: Sequence[str] = CAFFEINATE_COMMAND,
        process_factory: Callable[..., object] | None = None,
        status_reader: Callable[[Path], None] | None = None,
        status_read_async: bool = True,
        watch_current_process: bool = True,
        keep_display_awake: bool = False,
    ) -> None:
        self.enabled = enabled
        self.grace_seconds = grace_seconds
        self.status_read_seconds = status_read_seconds
        self.command = tuple(command)
        self.process_factory = process_factory or subprocess.Popen
        self.status_reader = status_reader or touch_keepalive_file
        self.status_read_async = status_read_async
        self.watch_current_process = watch_current_process
        self.keep_display_awake = bool(keep_display_awake)
        self.process = None
        self.last_mode: AgentMode | None = None
        self.holding_requested = False
        self.grace_until_monotonic: float | None = None
        self.last_error: str | None = None
        self.last_status_read_monotonic_by_path: dict[Path, float] = {}
        self.last_status_error: str | None = None
        self.status_read_in_flight_by_path: set[Path] = set()

    def set_enabled(self, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.enabled = enabled
        if not enabled:
            self.release()
            self.holding_requested = False
            self.grace_until_monotonic = None
            self.last_status_error = None
            self.last_status_read_monotonic_by_path.clear()
            self.status_read_in_flight_by_path.clear()

    def set_grace_seconds(self, seconds: float) -> None:
        """Live-adjustable -- called every poll with the current setting
        value (see StatusBarController.sync_keep_awake) rather than fixed
        once at construction, so changing it in Settings takes effect on
        the very next tick instead of needing a restart."""
        self.grace_seconds = max(0.0, float(seconds))

    def set_keep_display_awake(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.keep_display_awake == enabled:
            return
        self.keep_display_awake = enabled
        was_running = self.process_running()
        if was_running:
            self._terminate_process()
            if self.enabled and self.holding_requested:
                self.ensure_awake()

    def update(
        self,
        mode: AgentMode,
        *,
        now: float | None = None,
        on_battery: bool | None = None,
        hold_on_battery: bool = True,
    ) -> bool:
        current = time.monotonic() if now is None else now
        should_hold = self.should_hold_for_mode(mode, current)
        self.holding_requested = should_hold
        self.last_mode = mode

        # Only a POSITIVE battery reading may suppress the hold: an
        # unknown power state must never silently release keep-awake and
        # let a lid-closed agent sleep mid-task.
        battery_blocked = on_battery is True and not hold_on_battery

        if not self.enabled or not should_hold or battery_blocked:
            self.release()
            return False

        self.ensure_awake()
        return self.process_running()

    def should_hold_for_mode(self, mode: AgentMode, now: float) -> bool:
        if mode in WORK_MODES:
            self.grace_until_monotonic = None
            return True

        # One grace window per stretch of work, started the moment work
        # STOPS -- so a momentary idle blip between tool calls (or a
        # bare IDLE_READY fallback where an explicit Completed never
        # arrives) still gets the full window. The window is NOT
        # refreshed by transitions among rest modes: overnight the
        # display flapped idle-completed-idle as sessions aged out, each
        # flap re-armed a five-minute hold, and the machine never slept
        # again. Rest-to-rest changes now ride out the original window.
        if self.grace_until_monotonic is None:
            if self.last_mode is None or self.last_mode in WORK_MODES:
                self.grace_until_monotonic = now + self.grace_seconds
            else:
                return False
        return now < self.grace_until_monotonic

    def ensure_awake(self) -> None:
        if self.process_running():
            return

        try:
            self.process = self.process_factory(
                self.caffeinate_command(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.last_error = None
        except Exception as exc:
            self.process = None
            self.last_error = str(exc)

    def release(self) -> None:
        self._terminate_process()

    def _terminate_process(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return

        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=1)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def process_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def poke_status_file(self, target: Path | None, *, now: float | None = None) -> Path | None:
        if not self.enabled or target is None:
            return None

        current = time.monotonic() if now is None else now
        status_path = keepalive_file_for_target(target)
        last_read = self.last_status_read_monotonic_by_path.get(status_path)
        if last_read is not None and current - last_read < self.status_read_seconds:
            return None

        self.last_status_read_monotonic_by_path[status_path] = current
        if self.status_read_async:
            if status_path in self.status_read_in_flight_by_path:
                return None
            self.status_read_in_flight_by_path.add(status_path)
            thread = threading.Thread(
                target=self._run_status_reader,
                args=(status_path,),
                daemon=True,
            )
            thread.start()
            return status_path

        return self._run_status_reader(status_path)

    def caffeinate_command(self) -> list[str]:
        command = list(
            configure_caffeinate_display_assertion(
                self.command,
                keep_display_awake=self.keep_display_awake,
            )
        )
        if self.watch_current_process:
            command.extend(["-w", str(os.getpid())])
        return command

    def _run_status_reader(self, status_path: Path) -> Path | None:
        try:
            self.status_reader(status_path)
            self.last_status_error = None
            return status_path
        except Exception as exc:
            self.last_status_error = str(exc)
            return None
        finally:
            self.status_read_in_flight_by_path.discard(status_path)

    def detail(self, *, now: float | None = None) -> str:
        if not self.enabled:
            return "Keep awake disabled"
        if self.last_error:
            return f"Keep awake error: {self.last_error}"
        current = time.monotonic() if now is None else now
        if self.grace_until_monotonic is not None and current < self.grace_until_monotonic:
            remaining = int(self.grace_until_monotonic - current)
            return f"Keep awake grace: {format_duration(remaining)}"
        if self.process_running():
            return (
                "Keep awake active, display held awake"
                if self.keep_display_awake
                else "Keep awake active, display may sleep"
            )
        return "Keep awake standby"


def keepalive_file_for_target(target: Path) -> Path:
    known_file_names = KNOWN_LED_FILE_NAMES | {KEEPALIVE_FILE_NAME.upper(), "STATUS.TXT"}
    if target.name.upper() in known_file_names:
        return target.parent / KEEPALIVE_FILE_NAME
    return target / KEEPALIVE_FILE_NAME


def touch_keepalive_file(path: Path) -> None:
    subprocess.run(
        ["/usr/bin/touch", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=2,
        check=True,
    )


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, rest = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m{rest:02d}s"
    return f"{rest}s"
