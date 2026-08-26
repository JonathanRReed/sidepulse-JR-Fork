"""Why the ledger stopped keeping up with what was actually running.

Three independent mechanisms, all found on the owner's machine on 2026-08-14,
each of which alone is enough to freeze the dropdown:

  * a Python exception inside a PyObjC `drawRect:` is fatal -- AppKit escalates
    it to SIGTRAP -- and with no launchd job behind the status bar the process
    stayed dead, so the socket was orphaned and hooks piled up against nobody;
  * the timing quarantine had no upper bound and its recovery counter was
    reset by routine partial batches, so all three hook sources sat
    `timing_uncertain` for four days on a provably continuous clock, and every
    lifecycle update was dropped;
  * the hook send breaker exempted the events that END a turn but not the one
    that STARTS one, so a tripped breaker taught the app that agents stopped
    and stopped teaching it that they started.
"""

from __future__ import annotations

import json

import pytest

from sidepulse import draw_guard, ipc
from sidepulse.capacity_types import SourceKey
from sidepulse.operator_state import (
    TIMING_RECOVERY_CONFIRMATIONS,
    TIMING_UNCERTAINTY_LEASE_SECONDS,
    BootIdentifier,
    ClockContinuityStatus,
    ClockSample,
    empty_operator_state,
    reduce_operator_state,
)
from sidepulse.provider_facts import (
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderWatermark,
    ProviderWorkFact,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
)

# --------------------------------------------------------------------------
# A drawing bug must not be a fatal one.
# --------------------------------------------------------------------------


def test_a_raising_draw_callback_never_reaches_appkit() -> None:
    """`PyObjCErr_ToObjCWithGILState` -> `_crashOnException:` -> SIGTRAP."""
    draw_guard.reset_draw_failures()

    class Boom:
        @draw_guard.guard_draw
        def drawRect_(self, _rect):
            raise ValueError("a bad number in a chart")

    assert Boom().drawRect_(None) is None
    assert draw_guard.draw_failures() == (("Boom", 1),)


def test_the_guard_keeps_the_selector_shape_pyobjc_needs() -> None:
    class View:
        @draw_guard.guard_draw
        def drawRect_(self, rect):
            return rect

    assert View.drawRect_.__name__ == "drawRect_"
    assert View.drawRect_.__code__.co_argcount == 2
    assert View().drawRect_("rect") == "rect"


def test_repeated_failures_stay_bounded() -> None:
    draw_guard.reset_draw_failures()
    for index in range(draw_guard.MAX_TRACKED_DRAW_FAILURES + 5):
        draw_guard.record_draw_failure(f"View{index}", RuntimeError("x"))

    failures = dict(draw_guard.draw_failures())
    assert len(failures) == draw_guard.MAX_TRACKED_DRAW_FAILURES + 1
    assert failures["other"] == 5
    draw_guard.reset_draw_failures()


def test_the_usage_graph_survives_a_model_it_cannot_plot() -> None:
    """365 days of history reach this view; one bad value must not be fatal."""
    status_bar = pytest.importorskip("sidepulse.status_bar")
    draw_guard.reset_draw_failures()

    view = status_bar.UsageGraphView.alloc().initWithFrame_(((0, 0), (400, 200)))
    view.setModel_(
        {
            "metric": "cost",
            "labels": tuple(str(day) for day in range(365)),
            "series": (
                {"provider_id": "claude", "values": (None, "not a number", 1)},
            ),
            "scale_max": float("nan"),
        }
    )
    rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)

    assert draw_guard.draw_failures() == ()


def test_a_model_that_crossed_the_main_thread_boundary_is_still_read() -> None:
    """An NSDictionary proxy is not a `dict`; rejecting it drew an empty year."""
    status_bar = pytest.importorskip("sidepulse.status_bar")
    Foundation = pytest.importorskip("Foundation")

    view = status_bar.UsageGraphView.alloc().initWithFrame_(((0, 0), (400, 200)))
    bridged = Foundation.NSDictionary.dictionaryWithDictionary_(
        {"metric": "tokens", "labels": ("a",), "scale_max": 10}
    )
    view.setModel_(bridged)

    assert not isinstance(bridged, dict)
    assert view.model.get("metric") == "tokens"


# --------------------------------------------------------------------------
# The breaker must not bias the ledger toward "stopped".
# --------------------------------------------------------------------------


def _trip_breaker(directory) -> None:
    (directory / "hook-send-breaker.json").write_text(
        json.dumps({"failures": ipc.HOOK_BREAKER_TRIP_AFTER, "since": 2_000_000_000.0}),
        encoding="utf-8",
    )


def test_a_tripped_breaker_still_lets_a_prompt_submission_through(tmp_path) -> None:
    """The event that makes a row ACTIVE was suppressible; the stops were not."""
    _trip_breaker(tmp_path)
    breaker = ipc._HookSendBreaker()
    socket_path = tmp_path / "events.sock"

    assert breaker.should_attempt("UserPromptSubmit", 0.0, socket_path) is True
    assert breaker.should_attempt("Stop", 0.0, socket_path) is True
    # Heartbeats are what the breaker is for, and they stay suppressible.
    assert breaker.should_attempt("PreToolUse", 0.0, socket_path) is False
    assert breaker.should_attempt("PostToolUse", 0.0, socket_path) is False


def test_the_exemption_is_symmetric_across_every_gateways_spelling() -> None:
    """A start and its matching stop must be exempt together, or neither."""
    pairs = (
        ("UserPromptSubmit", "Stop"),
        ("beforeSubmitPrompt", "stop"),
        ("PreInvocation", "Stop"),
        ("SessionStart", "SessionEnd"),
        ("sessionStart", "sessionEnd"),
    )
    for start, end in pairs:
        assert start in ipc.LIFECYCLE_HOOK_EVENTS, start
        assert end in ipc.LIFECYCLE_HOOK_EVENTS, end


def test_the_breaker_for_one_socket_does_not_read_another_ones_sentinel(
    tmp_path,
) -> None:
    """Its claim is about ONE socket, so its sentinel belongs beside it."""
    other = tmp_path / "other"
    other.mkdir()
    _trip_breaker(other)
    breaker = ipc._HookSendBreaker()

    assert breaker.should_attempt("PreToolUse", 0.0, other / "events.sock") is False
    assert breaker.should_attempt("PreToolUse", 0.0, tmp_path / "events.sock") is True


# --------------------------------------------------------------------------
# The timing quarantine must be escapable.
# --------------------------------------------------------------------------


_SOURCE = SourceKey("claude", "hooks", "global", "live_agent_events")


def _clock(monotonic: float, *, wall: float = 1_800_000_000.0) -> ClockSample:
    return ClockSample(wall + monotonic, monotonic, BootIdentifier("boot:01"))


def _hook_batch(
    token: str,
    *,
    epoch: float,
    freshness: SourceFreshness = SourceFreshness.FRESH,
    health: SourceHealth = SourceHealth.HEALTHY,
) -> ProviderFactBatch:
    watermark = ProviderWatermark(
        source_key=_SOURCE,
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        occurred_at_epoch=epoch,
        event_token=EventToken(token),
        sequence=None,
        tie_break_rank=10,
    )
    return ProviderFactBatch(
        source_key=_SOURCE,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=health,
        source_freshness=freshness,
        observed_at_epoch=epoch,
        watermark=watermark,
        work_facts=(
            ProviderWorkFact(
                key=WorkKey(_SOURCE, WorkIdentifier("session:01")),
                lifecycle=WorkLifecycle.ACTIVE,
                watermark=watermark,
                safe_label="Claude session:01",
                parent_key=None,
                next_actor=NextActor.PROVIDER,
            ),
        ),
        request_facts=(),
        diagnostics=(),
    )


def _quarantined():
    """One clock jump, which is how every source enters the quarantine."""
    state = reduce_operator_state(
        empty_operator_state(),
        _hook_batch("event:001", epoch=1_800_000_000.0),
        clock=_clock(100.0),
    ).state
    jumped = reduce_operator_state(
        state,
        _hook_batch("event:002", epoch=1_800_000_001.0),
        # Monotonic leaps an hour while the wall advances one second: the
        # wall clock stepped BACKWARDS relative to the machine's own
        # timeline. (A forward wall gap is ordinary sleep now -- macOS
        # monotonic time pauses while the lid is closed -- and must stay
        # continuous, so it can no longer enter the quarantine here.)
        clock=ClockSample(1_800_000_101.0, 3_701.0, BootIdentifier("boot:01")),
    )
    assert jumped.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN
    return jumped.state


def test_a_routine_partial_batch_no_longer_erases_earned_confirmations() -> None:
    """Hook records without a request identity arrive PARTIAL. Constantly.

    Escape used to require `TIMING_RECOVERY_CONFIRMATIONS` batches in a row,
    and every partial one in between reset the counter to zero. Interleaved --
    which is the normal traffic shape -- the counter never climbed, and the
    owner's three hook sources sat quarantined for four days.
    """
    state = _quarantined()
    clean_one = reduce_operator_state(
        state,
        _hook_batch("event:003", epoch=1_800_000_002.0),
        clock=_clock(3_702.0, wall=1_799_996_400.0),
    ).state
    assert clean_one.clock_continuity.status is ClockContinuityStatus.UNCERTAIN

    partial = reduce_operator_state(
        clean_one,
        _hook_batch(
            "event:004",
            epoch=1_800_000_003.0,
            freshness=SourceFreshness.PARTIAL,
            health=SourceHealth.PARTIAL,
        ),
        clock=_clock(3_703.0, wall=1_799_996_400.0),
    ).state

    clean_two = reduce_operator_state(
        partial,
        _hook_batch("event:005", epoch=1_800_000_004.0),
        clock=_clock(3_704.0, wall=1_799_996_400.0),
    ).state

    assert clean_two.clock_continuity.status is ClockContinuityStatus.STABLE
    assert clean_two.works[0].source_freshness is SourceFreshness.FRESH
    assert clean_two.works[0].timing_uncertain is False


def test_lifecycle_updates_are_applied_again_once_the_source_is_out() -> None:
    """The quarantine drops the SEMANTIC half of every batch. That is the freeze."""
    state = _quarantined()
    # One clean batch, then a routine partial one, then the batch that closes
    # the work. That is the ordinary traffic shape, and under the old rule the
    # partial in the middle put the counter back to zero, so the closing batch
    # was only ever the FIRST confirmation and its semantics were dropped.
    state = reduce_operator_state(
        state,
        _hook_batch("event:006", epoch=1_800_000_006.0),
        clock=_clock(3_702.0, wall=1_799_996_400.0),
    ).state
    state = reduce_operator_state(
        state,
        _hook_batch(
            "event:007",
            epoch=1_800_000_007.0,
            freshness=SourceFreshness.PARTIAL,
            health=SourceHealth.PARTIAL,
        ),
        clock=_clock(3_703.0, wall=1_799_996_400.0),
    ).state

    closing = _hook_batch("event:100", epoch=1_800_000_050.0)
    closed = reduce_operator_state(
        state,
        ProviderFactBatch(
            source_key=closing.source_key,
            observation_authority=closing.observation_authority,
            source_health=closing.source_health,
            source_freshness=closing.source_freshness,
            observed_at_epoch=closing.observed_at_epoch,
            watermark=closing.watermark,
            work_facts=(
                ProviderWorkFact(
                    key=WorkKey(_SOURCE, WorkIdentifier("session:01")),
                    lifecycle=WorkLifecycle.COMPLETED,
                    watermark=closing.watermark,
                    safe_label="Claude session:01",
                    parent_key=None,
                    next_actor=NextActor.NONE,
                ),
            ),
            request_facts=(),
            diagnostics=(),
        ),
        clock=_clock(3_704.0, wall=1_799_996_400.0),
    ).state

    assert closed.works[0].lifecycle is WorkLifecycle.COMPLETED


def test_the_quarantine_expires_on_a_clock_that_stayed_continuous() -> None:
    """A source that never assembles a clean run must not be held forever.

    `uncertain_since_monotonic` is re-stamped every time the clock jumps again,
    so an entry carrying the same stamp for a whole lease is one whose cause
    has demonstrably not recurred -- and past that, one healthy fresh direct
    observation is enough where two were required.
    """
    state = _quarantined()
    recovered = reduce_operator_state(
        state,
        _hook_batch("event:200", epoch=1_800_000_010.0),
        clock=_clock(
            3_701.0 + TIMING_UNCERTAINTY_LEASE_SECONDS,
            wall=1_799_996_400.0,
        ),
    )

    assert recovered.state.clock_continuity.status is ClockContinuityStatus.STABLE
    assert (
        recovered.state.clock_continuity.recovery_confirmations
        == TIMING_RECOVERY_CONFIRMATIONS
    )
    assert "timing_quarantine_lease_expired" in {
        diagnostic.identifier.value for diagnostic in recovered.diagnostics
    }


def test_a_source_still_losing_is_not_released_by_the_lease() -> None:
    """The lease relaxes corroboration; it never invents recovery."""
    state = _quarantined()
    still_lost = reduce_operator_state(
        state,
        _hook_batch(
            "event:300",
            epoch=1_800_000_010.0,
            freshness=SourceFreshness.UNAVAILABLE,
            health=SourceHealth.UNAVAILABLE,
        ),
        clock=_clock(
            3_701.0 + 2 * TIMING_UNCERTAINTY_LEASE_SECONDS,
            wall=1_799_996_400.0,
        ),
    )

    assert still_lost.state.clock_continuity.status is ClockContinuityStatus.UNCERTAIN


def test_quiescent_only_quarantine_survives_the_v2_round_trip() -> None:
    """The live-source election can leave the GLOBAL clock STABLE while
    quiescent sources still hold timing entries. v2 used to reconstruct
    per-source stamps from the global uncertain_since -- impossible in
    that shape -- so the app's own latest.json failed its own validator
    and every restart lost its warm start."""
    from sidepulse._collector_legacy import (
        _state_to_document,
        _v2_state_from_document,
    )

    state = reduce_operator_state(
        empty_operator_state(),
        _hook_batch("event:001", epoch=1_800_000_000.0),
        clock=_clock(100.0),
    ).state
    jumped = reduce_operator_state(
        state,
        _hook_batch("event:002", epoch=1_800_000_001.0),
        clock=ClockSample(1_800_000_101.0, 3_701.0, BootIdentifier("boot:01")),
    ).state
    assert jumped.timing_uncertain_sources

    document = _state_to_document(jumped)
    restored = _v2_state_from_document(document)
    assert restored.timing_uncertain_sources == jumped.timing_uncertain_sources
    assert restored._source_timing == jumped._source_timing

    # And a document from the broken window (no source_timing, STABLE
    # clock, uncertain sources listed) HEALS instead of failing restore.
    legacy = dict(document)
    legacy.pop("source_timing")
    legacy["clock_continuity"] = {
        "status": "stable",
        "uncertain_since_monotonic": None,
        "recovery_confirmations": 0,
    }
    healed = _v2_state_from_document(legacy)
    assert healed.timing_uncertain_sources == ()


def test_a_rebooted_strip_voids_the_write_dedupe(tmp_path):
    """2026-08-20 live incident: the strip rebooted on lid-open and
    looped the lid flourish for two hours while every dedupe-skipped
    tick assumed the steady program was still showing. A firmware
    uptime that goes BACKWARDS voids the dedupe so the next tick
    repaints unconditionally."""
    from sidepulse._led_status_legacy import AgentLedController

    status = tmp_path / "STATUS.TXT"
    status.write_text("serial SPP-000067\nuptime_ms 5000000\nstate idle\n")
    # The FILE path, exactly as production constructs its controllers
    # (device.target is <volume>/LEDS.LED). The old directory-path
    # fixture passed while the shipped app resolved
    # <volume>/LEDS.LED/STATUS.TXT and never detected a reboot at all.
    writer = AgentLedController(device_path=tmp_path / "LEDS.LED")
    writer.UPTIME_CHECK_SECONDS = 0.0  # check on every call in this test

    import time as time_module

    now = time_module.monotonic()
    # Prime: first read learns the uptime; no reboot signal.
    assert writer._device_rebooted_since_last_write(now) is False
    # Same boot, more uptime: still no signal.
    status.write_text("serial SPP-000067\nuptime_ms 6000000\nstate idle\n")
    assert writer._device_rebooted_since_last_write(now + 1) is False
    # Uptime went BACKWARDS: the device rebooted -- and the repaint
    # flag arms for the WRITE path to consume (the check itself runs on
    # the background keepalive thread; the write path reads only memory,
    # after the inline SD read blocked the main thread for seconds).
    status.write_text("serial SPP-000067\nuptime_ms 120000\nstate idle\n")
    assert writer._device_rebooted_since_last_write(now + 2) is True
    assert writer.pending_reboot_repaint is True
    # Unreadable STATUS.TXT is not evidence of anything.
    status.unlink()
    assert writer._device_rebooted_since_last_write(now + 3) is False
