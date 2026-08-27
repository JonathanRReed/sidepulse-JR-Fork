from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sidepulse import usage_stats
from sidepulse.capacity_refresh import RefreshStatusKind
from sidepulse.capacity_types import SourceKey
from sidepulse.providers import negotiated_provider_sources
from sidepulse.usage_view import build_provider_usage_view
from tests.test_sidepulse import isolate_controller

CODEX_QUOTA = SourceKey(
    "codex",
    "quota",
    "local",
    "remote_quota_windows",
)
CLAUDE_QUOTA = SourceKey(
    "claude",
    "quota",
    "experimental-remote",
    "remote_quota_windows",
)
CODEX_TRANSCRIPTS = SourceKey(
    "codex",
    "transcripts",
    "local",
    "transcript_usage",
)
CLAUDE_TRANSCRIPTS = SourceKey(
    "claude",
    "transcripts",
    "local",
    "transcript_usage",
)


def scan_provider_usage(source, root, cache_path, *, since_epoch):
    """Local oracle over the live scanner (the thin public wrapper was
    deleted 2026-08-26: tests were its only callers)."""
    result, _totals = usage_stats._scan_provider_usage_with_totals(
        source, root, cache_path, since_epoch=since_epoch
    )
    return result



@pytest.fixture
def controller(request):
    class ControllerCase:
        def __init__(self) -> None:
            self._cleanups = []

        def addCleanup(self, callback) -> None:
            self._cleanups.append(callback)

        def skipTest(self, reason: str) -> None:
            pytest.skip(reason)

        def close(self) -> None:
            for callback in reversed(self._cleanups):
                callback()

    case = ControllerCase()
    isolate_controller(case)
    request.addfinalizer(case.close)
    return case.controller, case.status_bar


def test_disabled_remote_capacity_never_owns_a_timer_or_healthy_state(
    controller,
) -> None:
    target, _status_bar = controller
    rows = {
        row.key.source: row
        for row in target._capacity_refresh_coordinator.snapshot_state(100.0).sources
    }

    assert rows[CODEX_QUOTA].enabled is True
    assert rows[CODEX_QUOTA].status is RefreshStatusKind.IDLE
    assert CLAUDE_QUOTA not in rows

    with (
        patch("sidepulse.status_bar.threading.Thread") as thread_type,
        patch.object(target, "update_usage_menu_fields"),
    ):
        assert target.request_usage_refresh((CLAUDE_QUOTA,), reason="menu-open") == ()

    assert target._capacity_refresh_deadline_timers == {}
    thread_type.assert_not_called()


def test_menu_close_releases_visibility_only_capacity_countdown(controller) -> None:
    target, _status_bar = controller
    countdown = MagicMock()
    reset = MagicMock()
    target._capacity_countdown_timer = countdown
    target._capacity_countdown_deadline = 120.0
    target._capacity_reset_timer = reset

    target.menuDidClose_(None)

    countdown.invalidate.assert_called_once_with()
    assert target._capacity_countdown_timer is None
    assert target._capacity_countdown_deadline is None
    reset.invalidate.assert_not_called()


def test_menu_close_preserves_countdown_needed_by_visible_profile_settings(
    controller,
) -> None:
    target, _status_bar = controller
    epoch_now = time.time()
    monotonic_now = time.monotonic()
    target.status_menu_open = True
    target.current_settings_pane = "profile"
    target.settings_window = SimpleNamespace(isVisible=lambda: True)
    target._usage_provider_models = {
        "codex": build_provider_usage_view(
            "codex",
            "Codex",
            (
                {
                    "label": "5-hour",
                    "used_percent": 20,
                    "window_minutes": 300,
                    "resets_at": epoch_now + 120.0,
                },
            ),
            last_success_at=monotonic_now,
            now=monotonic_now,
            reset_now=epoch_now,
        )
    }
    old_countdown = MagicMock()
    target._capacity_countdown_timer = old_countdown
    target._capacity_countdown_deadline = None
    replacement = MagicMock()

    with patch.object(
        target,
        "_schedule_capacity_timer",
        return_value=replacement,
    ):
        target.menuDidClose_(None)

    assert target.status_menu_open is False
    assert target._capacity_countdown_timer is replacement
    assert target._capacity_countdown_deadline is not None


def test_canonical_menu_open_still_requests_usage_sources(controller) -> None:
    target, _status_bar = controller
    target.last_snapshot = SimpleNamespace(
        operator_state=SimpleNamespace(),
        statuses=(),
    )

    with (
        patch("sidepulse.status_bar._canonical_agent_browser_projection"),
        patch.object(target, "maybe_refresh_usage_summary") as refresh,
    ):
        target.menuWillOpen_(None)

    refresh.assert_called_once_with(reason="menu-open")


def test_no_capacity_timer_exists_without_due_or_visible_reason(controller) -> None:
    target, _status_bar = controller
    target._usage_provider_states = {
        provider_id: state.__class__(
            source_key=state.source_key,
            enabled=False,
            visible=False,
        )
        for provider_id, state in target._usage_provider_states.items()
    }
    target._usage_provider_models = {}
    target.status_menu_open = False
    target.settings_window = None

    with patch.object(target, "_schedule_capacity_timer") as schedule:
        target.schedule_capacity_timers(epoch_now=1_000.0)

    schedule.assert_not_called()
    assert target._capacity_reset_timer is None
    assert target._capacity_countdown_timer is None


def test_duplicate_exact_sources_create_one_generation_and_one_batch_worker(
    controller,
) -> None:
    target, status_bar = controller
    timer_api = MagicMock()
    timer_api.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_.return_value = (
        MagicMock()
    )

    with (
        patch.object(status_bar, "NSTimer", timer_api),
        patch("sidepulse.status_bar.threading.Thread") as thread_type,
    ):
        started = target.request_usage_refresh(
            (
                CODEX_TRANSCRIPTS,
                CODEX_TRANSCRIPTS,
                CODEX_QUOTA,
                CODEX_QUOTA,
            ),
            reason="menu-open",
        )

    assert started == (CODEX_TRANSCRIPTS, CODEX_QUOTA)
    thread_type.assert_called_once()
    assert thread_type.call_args.kwargs["args"][0] == {
        CODEX_TRANSCRIPTS: 1,
        CODEX_QUOTA: 1,
    }
    schedule = (
        timer_api.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_
    )
    assert schedule.call_count == 1


def test_warm_unchanged_exact_usage_source_performs_zero_disk_writes(
    tmp_path,
) -> None:
    root = tmp_path / "claude"
    root.mkdir()
    row = {
        "type": "assistant",
        "timestamp": "2026-08-12T12:00:00Z",
        "message": {
            "id": "message-1",
            "model": "claude-sonnet-5",
            "usage": {
                "input_tokens": 17,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }
    (root / "usage.jsonl").write_text(json.dumps(row) + "\n")
    cache = tmp_path / "state" / "usage.json"
    source = next(
        candidate
        for candidate in negotiated_provider_sources()
        if candidate.source_key == CLAUDE_TRANSCRIPTS
    )

    cold = scan_provider_usage(source, root, cache, since_epoch=0.0)
    with patch("sidepulse.usage_stats.atomic_private_write") as write:
        warm = scan_provider_usage(source, root, cache, since_epoch=0.0)

    assert warm.input_tokens == cold.input_tokens == 17
    assert warm.coverage.cache_hits == 1
    write.assert_not_called()


def test_countdown_tick_performs_no_disk_or_source_work(controller) -> None:
    target, _status_bar = controller
    epoch_now = time.time()
    monotonic_now = time.monotonic()
    target.status_menu_open = True
    target._usage_provider_models = {
        "codex": build_provider_usage_view(
            "codex",
            "Codex",
            (
                {
                    "label": "5-hour",
                    "used_percent": 20,
                    "window_minutes": 300,
                    "resets_at": epoch_now + 120.0,
                },
            ),
            last_success_at=monotonic_now,
            now=monotonic_now,
            reset_now=epoch_now,
        )
    }
    timer = MagicMock()
    target._capacity_countdown_timer = timer

    with (
        patch("sidepulse.status_bar.usage_stats.scan_usage") as scan,
        patch("sidepulse.status_bar.usage_stats.codex_rate_limits") as codex,
        patch("sidepulse.status_bar.claude_quota.fetch_windows") as claude,
        patch("sidepulse.usage_stats.atomic_private_write") as write,
        patch.object(target, "_schedule_capacity_timer", return_value=MagicMock()),
    ):
        target.capacityCountdown_(timer)

    scan.assert_not_called()
    codex.assert_not_called()
    claude.assert_not_called()
    write.assert_not_called()
