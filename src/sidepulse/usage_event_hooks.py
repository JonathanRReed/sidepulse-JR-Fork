"""Edge-triggered usage events that run a user-supplied executable.

CodexBar's hooks model, adapted: five events fire on TRANSITIONS, never
on states -- crossing a threshold fires once, sitting under it forever
fires nothing. That edge-triggering is what makes a user script safe to
point at a chime, a webhook, or a shortcut without building rate limits
into it.

Events (argv: executable EVENT PROVIDER_ID LANE_ID DETAIL):
  quota_low            a lane crossed its provider's low-remaining
                       threshold downward (DETAIL: remaining percent)
  quota_reached        a lane ran out (DETAIL: remaining percent, "0")
  quota_reset          a lane's window reset (DETAIL: lane label)
  provider_unavailable a collecting provider stopped answering
                       (DETAIL: state name)
  provider_recovered   it answers again (DETAIL: state name)

The runner is bounded exactly the way CodexBar bounds helpers: argv
array (never a shell), hard timeout, output discarded, one thread per
batch so a slow script cannot back up the app.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass

from .provider_usage_platform import ProviderSourceState

HOOK_TIMEOUT_SECONDS = 15.0

_ANSWERING = frozenset(
    {ProviderSourceState.READY, ProviderSourceState.STALE}
)
_SILENT = frozenset(
    {
        ProviderSourceState.ERROR,
        ProviderSourceState.UNAVAILABLE,
        ProviderSourceState.RATE_LIMITED,
    }
)


@dataclass(frozen=True, slots=True)
class UsageHookEvent:
    name: str
    provider_id: str
    lane_id: str
    detail: str


def _lane_percents(snapshots) -> dict[tuple[str, str], float]:
    return {
        (snapshot.provider_id, lane.lane_id): lane.remaining_percent
        for snapshot in snapshots
        for lane in snapshot.lanes
        if lane.remaining_percent is not None
    }


def detect_usage_hook_events(
    previous_snapshots,
    current_snapshots,
    *,
    thresholds: dict[str, float],
) -> tuple[UsageHookEvent, ...]:
    """Transitions between two usage states, in a stable order.

    Both sides must have a value for a lane to fire quota edges: a lane
    appearing or vanishing is not a crossing. Provider availability
    edges need a definite state on both sides for the same reason.
    """
    events: list[UsageHookEvent] = []
    previous_percents = _lane_percents(previous_snapshots)
    for key, current in sorted(_lane_percents(current_snapshots).items()):
        prior = previous_percents.get(key)
        if prior is None:
            continue
        provider_id, lane_id = key
        threshold = thresholds.get(provider_id)
        if (
            threshold is not None
            and prior > threshold >= current
        ):
            events.append(
                UsageHookEvent("quota_low", provider_id, lane_id, f"{current:.0f}")
            )
        if prior > 0.0 >= current:
            events.append(
                UsageHookEvent("quota_reached", provider_id, lane_id, "0")
            )
        if current - prior >= 50.0:
            # A large upward jump is a window reset seen through the
            # percent lens -- the same signal the reset celebrations use.
            events.append(
                UsageHookEvent("quota_reset", provider_id, lane_id, f"{current:.0f}")
            )
    previous_states = {
        snapshot.provider_id: snapshot.state for snapshot in previous_snapshots
    }
    for snapshot in sorted(current_snapshots, key=lambda item: item.provider_id):
        prior_state = previous_states.get(snapshot.provider_id)
        if prior_state is None:
            continue
        if prior_state in _ANSWERING and snapshot.state in _SILENT:
            events.append(
                UsageHookEvent(
                    "provider_unavailable",
                    snapshot.provider_id,
                    "",
                    snapshot.state.name.lower(),
                )
            )
        elif prior_state in _SILENT and snapshot.state in _ANSWERING:
            events.append(
                UsageHookEvent(
                    "provider_recovered",
                    snapshot.provider_id,
                    "",
                    snapshot.state.name.lower(),
                )
            )
    return tuple(events)


def hook_path_message(hook_path: str) -> str:
    """Save confirmation that never lies about a path that can't run."""
    import os

    if not hook_path:
        return "Usage event hook off."
    if not os.path.exists(hook_path):
        return "Saved, but that path does not exist — the hook will never fire."
    if not os.access(hook_path, os.X_OK):
        return "Saved, but that file is not executable (chmod +x it)."
    return "Usage event hook saved."


def run_usage_hooks(executable: str, events: tuple[UsageHookEvent, ...]) -> None:
    """Fire-and-forget: one background thread runs the batch serially."""
    if not executable or not events:
        return

    def _run() -> None:
        for event in events:
            try:
                subprocess.run(
                    [
                        executable,
                        event.name,
                        event.provider_id,
                        event.lane_id,
                        event.detail,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    timeout=HOOK_TIMEOUT_SECONDS,
                    check=False,
                )
            except Exception as exc:
                # A typo'd path failed forever in total silence; the log
                # is the minimum honesty a fire-and-forget hook owes.
                try:
                    from .status_bar import log_status_bar

                    log_status_bar(f"usage hook failed: {exc}")
                except Exception:
                    pass
                continue

    threading.Thread(
        target=_run, name="SidePulseUsageHooks", daemon=True
    ).start()


__all__ = [
    "HOOK_TIMEOUT_SECONDS",
    "UsageHookEvent",
    "detect_usage_hook_events",
    "hook_path_message",
    "run_usage_hooks",
]
