"""Whether SidePulse can hear its agents at all -- and when it last did.

"Idle" is the most dangerous word this app prints. A healthy Mac with
nothing to do, a Mac nobody ever connected, and a Mac whose hooks all die
in a TypeError produce the identical menu bar. They were identical for an
hour the first time it happened, which is how this project started.

This module tells those cases apart from evidence, and refuses to raise an
alarm without any:

  * no provider has a hook installed        -- nothing will EVER arrive
  * a hook is writing but nothing arrives   -- the wire is broken
  * a hook wrote once and nothing since     -- probably broken
  * a hook is installed and never wrote     -- NOT an alarm. Setup may
                                               have finished ten seconds
                                               ago, and crying wolf at the
                                               person who just connected
                                               you is unforgivable.

Two clocks, one unit (provider event epochs, the same field on both sides):

  wire_written_at    newest ``occurred_at_epoch`` in the provider's own
                     hook log -- proof the HOOK RAN.
  event_accepted_at  newest provider watermark in canonical operator
                     state -- proof SIDEPULSE UNDERSTOOD it.

A healthy pair is equal: the hook writes the record and the same epoch
arrives as a watermark. A wire ahead of an accepted event is the exact
shape of the failure that opened this project, and is the only silence
this module is willing to call broken outright.

The log's last record is read for its timestamp, never its mtime: hook
logs are compacted in place (``audit.compact_jsonl_file``), so mtime says
"a janitor rewrote this file", not "an agent said something".

Findings carry doctor.py's codes. This module invents none.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from .doctor import DiagnosticCheck, DiagnosticCode, DiagnosticFinding
from .freshness import bounded_age_seconds
from .operator_state import CanonicalOperatorState
from .providers import PROVIDER_SPECS, ProviderConfig, default_log_path

# How long a provider may stay silent before its last word stops counting
# as evidence that the wire works. Deliberately generous: a weekend away
# must not be reported as a fault.
DEFAULT_SILENCE_SECONDS: Final = 24 * 60 * 60.0
# How far the hook log may run ahead of canonical state before that gap is
# the wire failing rather than the next refresh not having happened yet.
DEFAULT_INGEST_LAG_SECONDS: Final = 300.0
# One read per provider, from the end. Enough for many records at the
# ~300 bytes a normalized record occupies.
MAX_LOG_TAIL_BYTES: Final = 16 * 1024
MAX_TAIL_LINES_SCANNED: Final = 64

NOT_SET_UP_LABEL: Final = "Not set up"
NOT_HEARING_LABEL: Final = "Not hearing agents"

# Delivery codes that mean "this provider is fine, or at least has not
# been shown to be broken".
_DELIVERING: Final = frozenset({DiagnosticCode.HEALTHY, DiagnosticCode.CONFIGURED})


@dataclass(frozen=True, slots=True)
class ProviderProbe:
    """What the filesystem says about one provider, before any judgement."""

    provider: str
    label: str
    probed: bool
    installed: bool
    wire_written_at: float | None

    def __post_init__(self) -> None:
        if not (
            type(self.provider) is str
            and self.provider
            and type(self.label) is str
            and self.label
            and type(self.probed) is bool
            and type(self.installed) is bool
            and (
                self.wire_written_at is None
                or (
                    type(self.wire_written_at) is float
                    and self.wire_written_at >= 0.0
                )
            )
        ):
            raise ValueError("invalid provider probe")


@dataclass(frozen=True, slots=True)
class ProviderIntake:
    """One provider's answer to "can you still hear me, and when last?"."""

    provider: str
    label: str
    code: DiagnosticCode
    installed: bool
    wire_written_at: float | None
    event_accepted_at: float | None
    heard_age_seconds: float | None

    def __post_init__(self) -> None:
        if type(self.code) is not DiagnosticCode:
            raise ValueError("invalid provider intake code")

    @property
    def delivering(self) -> bool:
        return self.code in _DELIVERING

    @property
    def stuck(self) -> bool:
        """The hook is running and nothing it says is arriving."""
        return self.code is DiagnosticCode.PARTIAL


@dataclass(frozen=True, slots=True)
class IntakeReport:
    """Every provider, plus the two doctor findings they add up to."""

    providers: tuple[ProviderIntake, ...]
    hook_state: DiagnosticFinding
    source_health: DiagnosticFinding
    silence_seconds: float

    def __post_init__(self) -> None:
        if not (
            type(self.providers) is tuple
            and all(type(item) is ProviderIntake for item in self.providers)
            and type(self.hook_state) is DiagnosticFinding
            and self.hook_state.check is DiagnosticCheck.HOOK_DETECTOR_STATE
            and type(self.source_health) is DiagnosticFinding
            and self.source_health.check is DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH
        ):
            raise ValueError("invalid intake report")

    @property
    def any_installed(self) -> bool:
        return self.hook_state.code is not DiagnosticCode.NOT_CONFIGURED

    @property
    def known(self) -> tuple[ProviderIntake, ...]:
        """Providers this user actually has: installed, or heard from once.

        A provider that was never installed and never spoke is not part of
        this Mac's world and does not deserve a row saying so.
        """
        return tuple(
            item
            for item in self.providers
            if item.installed or item.event_accepted_at is not None
        )

    @property
    def stuck_providers(self) -> tuple[ProviderIntake, ...]:
        return tuple(item for item in self.providers if item.stuck)

    def newest_heard_age_seconds(self) -> float | None:
        ages = [
            item.heard_age_seconds
            for item in self.providers
            if item.heard_age_seconds is not None
        ]
        return min(ages) if ages else None


def _age_seconds(now_epoch: float, epoch: float | None) -> float | None:
    if epoch is None:
        return None
    try:
        now = datetime.fromtimestamp(float(now_epoch), timezone.utc)
        observed = datetime.fromtimestamp(float(epoch), timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    age = bounded_age_seconds(now, observed)
    return None if age == float("inf") else age


def _newest_record_epoch(path: Path, *, tail_bytes: int) -> float | None:
    """Newest ``occurred_at_epoch`` in a hook log, read from its end.

    Content-free by construction: one numeric field is taken and every
    other key in the record is ignored.
    """
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
            return None
        seeked = info.st_size > tail_bytes
        if seeked:
            os.lseek(descriptor, info.st_size - tail_bytes, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = tail_bytes
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    lines = b"".join(chunks).split(b"\n")
    if seeked and lines:
        # The first line of a mid-file read is a fragment of a record.
        lines = lines[1:]
    newest: float | None = None
    for line in reversed(lines[-MAX_TAIL_LINES_SCANNED:]):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if type(record) is not dict:
            continue
        epoch = record.get("occurred_at_epoch")
        if type(epoch) not in {int, float} or type(epoch) is bool:
            continue
        value = float(epoch)
        if value < 0.0 or value != value:
            continue
        if newest is None or value > newest:
            newest = value
    return newest


def _log_paths(provider: str, config: ProviderConfig, home: Path | None) -> tuple[Path, ...]:
    paths = [default_log_path(provider, home)]
    for path in config.log_paths:
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def probe_providers(
    *,
    home: Path | None = None,
    tail_bytes: int = MAX_LOG_TAIL_BYTES,
) -> tuple[ProviderProbe, ...]:
    """Read installation state and the newest hook-log record per provider.

    Never raises: a provider whose detector explodes is reported as
    unprobed, which reads as ``UNAVAILABLE`` rather than as "not
    installed" -- an unreadable config is not the same claim as an absent
    one.
    """
    probes: list[ProviderProbe] = []
    for spec in PROVIDER_SPECS:
        try:
            config = spec.detector(home)
            installed = bool(config.exists and config.hook_events)
            written: float | None = None
            for path in _log_paths(spec.provider, config, home):
                epoch = _newest_record_epoch(path.expanduser(), tail_bytes=tail_bytes)
                if epoch is not None and (written is None or epoch > written):
                    written = epoch
            probes.append(
                ProviderProbe(
                    provider=spec.provider,
                    label=spec.label,
                    probed=True,
                    installed=installed,
                    wire_written_at=written,
                )
            )
        except Exception:
            probes.append(
                ProviderProbe(
                    provider=spec.provider,
                    label=spec.label,
                    probed=False,
                    installed=False,
                    wire_written_at=None,
                )
            )
    return tuple(probes)


def accepted_epochs_by_provider(
    state: CanonicalOperatorState | None,
) -> dict[str, float]:
    """Newest canonical watermark per provider -- what SidePulse understood."""
    if type(state) is not CanonicalOperatorState:
        return {}
    newest: dict[str, float] = {}
    for source, watermark in state.source_watermarks:
        epoch = float(watermark.occurred_at_epoch)
        current = newest.get(source.provider_id)
        if current is None or epoch > current:
            newest[source.provider_id] = epoch
    return newest


def _provider_code(
    probe: ProviderProbe,
    *,
    accepted: float | None,
    now_epoch: float,
    silence_seconds: float,
    lag_seconds: float,
) -> DiagnosticCode:
    if not probe.probed:
        return DiagnosticCode.UNAVAILABLE
    if not probe.installed:
        return DiagnosticCode.NOT_CONFIGURED
    written = probe.wire_written_at
    written_age = _age_seconds(now_epoch, written)
    # A wire timestamp from the future is a broken clock, not a broken
    # hook, and it must not be allowed to convict the provider.
    wire_ahead = (
        written is not None
        and written_age is not None
        and (accepted is None or written - accepted > lag_seconds)
    )
    if wire_ahead:
        # The hook ran and said something SidePulse never turned into
        # state. This is the only silence that is broken on its face.
        # Present tense is only earned while the write is recent; an
        # unlanded write from last week is silence, not activity.
        return (
            DiagnosticCode.PARTIAL
            if written_age <= silence_seconds
            else DiagnosticCode.UNAVAILABLE
        )
    if accepted is None:
        # Installed, and has never spoken through any path. Nothing has
        # been lost yet, because nothing has been said yet.
        return DiagnosticCode.CONFIGURED
    age = _age_seconds(now_epoch, accepted)
    if age is None or age > silence_seconds:
        return DiagnosticCode.UNAVAILABLE
    return DiagnosticCode.HEALTHY


def _hook_state_finding(providers: tuple[ProviderIntake, ...]) -> DiagnosticFinding:
    limit = len(providers)
    installed = sum(1 for item in providers if item.installed)
    unprobed = sum(1 for item in providers if not item.installed and item.code is DiagnosticCode.UNAVAILABLE)
    if installed == 0 and unprobed == limit and limit:
        code = DiagnosticCode.UNAVAILABLE
    elif installed == 0:
        code = DiagnosticCode.NOT_CONFIGURED
    elif installed == limit:
        code = DiagnosticCode.CONFIGURED
    else:
        code = DiagnosticCode.PARTIAL
    return DiagnosticFinding(
        check=DiagnosticCheck.HOOK_DETECTOR_STATE,
        code=code,
        count=installed,
        limit=limit,
    )


def _source_health_finding(providers: tuple[ProviderIntake, ...]) -> DiagnosticFinding:
    installed = [item for item in providers if item.installed]
    limit = len(installed)
    delivering = sum(1 for item in installed if item.delivering)
    if limit == 0 or delivering == 0:
        code = DiagnosticCode.UNAVAILABLE
    elif delivering == limit:
        code = DiagnosticCode.HEALTHY
    else:
        code = DiagnosticCode.PARTIAL
    return DiagnosticFinding(
        check=DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH,
        code=code,
        count=delivering,
        limit=limit,
    )


def build_intake_report(
    probes: tuple[ProviderProbe, ...],
    *,
    accepted_by_provider: Mapping[str, float],
    now_epoch: float,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
    lag_seconds: float = DEFAULT_INGEST_LAG_SECONDS,
) -> IntakeReport:
    """Judge every provider once, from facts the caller already gathered."""
    if type(probes) is not tuple or any(type(item) is not ProviderProbe for item in probes):
        raise ValueError("invalid provider probes")
    providers: list[ProviderIntake] = []
    for probe in probes:
        raw = accepted_by_provider.get(probe.provider)
        accepted = float(raw) if type(raw) in {int, float} and type(raw) is not bool else None
        code = _provider_code(
            probe,
            accepted=accepted,
            now_epoch=now_epoch,
            silence_seconds=silence_seconds,
            lag_seconds=lag_seconds,
        )
        providers.append(
            ProviderIntake(
                provider=probe.provider,
                label=probe.label,
                code=code,
                installed=probe.installed,
                wire_written_at=probe.wire_written_at,
                event_accepted_at=accepted,
                heard_age_seconds=_age_seconds(now_epoch, accepted),
            )
        )
    frozen = tuple(providers)
    return IntakeReport(
        providers=frozen,
        hook_state=_hook_state_finding(frozen),
        source_health=_source_health_finding(frozen),
        silence_seconds=float(silence_seconds),
    )


def format_age_ago(seconds: float | None) -> str:
    """"never" / "just now" / "4m ago" -- the same ladder the cards use."""
    if seconds is None:
        return "never"
    age = max(0.0, float(seconds))
    if age <= 30.0:
        return "just now"
    if age < 3_600.0:
        return f"{max(1, int(age // 60.0))}m ago"
    if age < 86_400.0:
        return f"{max(1, int(age // 3_600.0))}h ago"
    return f"{max(1, int(age // 86_400.0))}d ago"


def format_duration(seconds: float) -> str:
    age = max(0.0, float(seconds))
    if age < 3_600.0:
        return f"{max(1, int(age // 60.0))}m"
    if age < 86_400.0:
        return f"{max(1, int(age // 3_600.0))}h"
    return f"{max(1, int(age // 86_400.0))}d"


def idle_disclosure(report: IntakeReport | None) -> str | None:
    """The honest replacement for "Idle", or None when Idle is the truth.

    Only whole-surface failures reach the menu bar. One stuck provider
    beside one live provider is a dropdown row naming the provider, never
    a title claiming SidePulse hears nothing -- it hears the other one.
    """
    if type(report) is not IntakeReport:
        return None
    if report.hook_state.code is DiagnosticCode.NOT_CONFIGURED:
        return NOT_SET_UP_LABEL
    if report.source_health.code is DiagnosticCode.UNAVAILABLE and report.any_installed:
        return NOT_HEARING_LABEL
    return None


def intake_alert_title(report: IntakeReport | None) -> str | None:
    """The one dropdown row that names the fault and earns the Setup click."""
    if type(report) is not IntakeReport:
        return None
    if report.hook_state.code is DiagnosticCode.NOT_CONFIGURED:
        return "⚠ Not set up — connect your agents in Setup…"
    stuck = report.stuck_providers
    if report.source_health.code is DiagnosticCode.UNAVAILABLE and report.any_installed:
        if stuck:
            return (
                f"⚠ {_provider_names(stuck)}: writing to the log, "
                "nothing arriving — reinstall in Setup…"
            )
        age = report.newest_heard_age_seconds()
        if age is None:
            return (
                "⚠ No agent event has ever arrived — "
                "reinstall in Setup…"
            )
        return (
            f"⚠ No agent events for {format_duration(age)} — "
            "hooks may be broken. Reinstall in Setup…"
        )
    if stuck:
        return (
            f"⚠ {_provider_names(stuck)}: writing to the log, "
            "nothing arriving — reinstall in Setup…"
        )
    return None


def _provider_names(providers: tuple[ProviderIntake, ...]) -> str:
    labels = [item.label for item in providers]
    if len(labels) <= 2:
        return " and ".join(labels)
    return f"{labels[0]}, {labels[1]} and {len(labels) - 2} more"


def last_heard_summary(report: IntakeReport | None) -> str | None:
    """Parent row text: the freshest thing SidePulse has heard from anyone."""
    if type(report) is not IntakeReport or not report.known:
        return None
    return f"Last heard from · {format_age_ago(report.newest_heard_age_seconds())}"


def intake_content_signature(report: IntakeReport | None) -> tuple:
    """Everything the dropdown renders from intake, ages bucketed to the
    minute -- the rows say "4m ago", so a second is not a change.

    Without this the alarm row would sit on screen for up to the menu
    signature's 30s safety valve after the user finished Setup, which is
    the worst possible half-minute to still be calling them unconnected.
    """
    if type(report) is not IntakeReport:
        return ()
    return (
        report.hook_state.code.value,
        report.hook_state.count,
        report.source_health.code.value,
        report.source_health.count,
        tuple(
            (
                item.provider,
                item.code.value,
                (
                    -1
                    if item.heard_age_seconds is None
                    else int(item.heard_age_seconds // 60.0)
                ),
            )
            for item in report.known
        ),
    )


def last_heard_rows(report: IntakeReport | None) -> tuple[str, ...]:
    """One row per provider this Mac actually has, newest first."""
    if type(report) is not IntakeReport:
        return ()
    rows: list[tuple[float, str]] = []
    for item in report.known:
        rows.append(
            (
                float("inf") if item.heard_age_seconds is None else item.heard_age_seconds,
                _last_heard_row(item),
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    return tuple(text for _age, text in rows)


def _last_heard_row(item: ProviderIntake) -> str:
    if item.code is DiagnosticCode.PARTIAL:
        return f"⚠ {item.label} · writing to the log, nothing arriving"
    if item.code is DiagnosticCode.UNAVAILABLE and item.installed:
        return f"⚠ {item.label} · {format_age_ago(item.heard_age_seconds)}"
    if item.code is DiagnosticCode.CONFIGURED:
        return f"{item.label} · connected, nothing yet"
    if item.code is DiagnosticCode.NOT_CONFIGURED:
        return f"{item.label} · not connected · last heard {format_age_ago(item.heard_age_seconds)}"
    return f"{item.label} · {format_age_ago(item.heard_age_seconds)}"
