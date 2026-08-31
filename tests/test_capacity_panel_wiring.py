"""The capacity plane's two newly-wired consumers, at their seams.

`capacity_view` was 1,139 lines of tested presentation code that nothing
imported, and `capacity_history_store` was 501 lines of tested persistence
with no producer. Both are reachable now through one surface: the
"Why Is It Doing That?" panel, which already explains the light and now
also explains the capacity card sitting three rows above it.

The load-bearing claims:

  * the panel's capacity section is built from `build_capacity_detail`, off
    the authority projection the refresh already computed -- not off raw
    provider numbers, which is the bug this project has now fixed twice;
  * a window the authority layer refused is named in the panel, in a
    sentence, where the card could only say how many went missing;
  * a lane that IS showing a number never prints a refusal that reads as
    "no number" -- the binding refusal and the presentation refusal are two
    different questions and were one field;
  * capacity history records nothing at all until the owner consents, is
    fed from the AUTHORISED projection, and its file is deleted the moment
    consent is withdrawn;
  * shortening retention prunes immediately rather than at some later
    flush that a quiet desk may never reach;
  * the panel keeps the pledge printed at the bottom of it: no payload, no
    prompt, no path, no session id.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from sidepulse.capacity_authority import CapacityProjection
from sidepulse.capacity_history import HistoryInterval
from sidepulse.capacity_types import (
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.capacity_view import CapacityDetailModel
from sidepulse.decision_trace import CAPACITY_SECTION_TITLE, capacity_detail_text
from sidepulse.persistence_writer import SerialPersistenceWriter
from tests.test_activity_ledger import _limits, _run_codex_refresh
from tests.test_sidepulse import isolate_controller

NOW = 1_800_000_000.0


@pytest.fixture
def controller(request, tmp_path):
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
    history_path = tmp_path / "capacity-history.json"
    patcher = patch(
        "sidepulse.capacity_history_runtime.default_capacity_history_path",
        return_value=history_path,
    )
    patcher.start()
    case.addCleanup(patcher.stop)
    request.addfinalizer(case.close)
    return case.controller, case.status_bar, history_path


def _enable_history(target, *, days: int = 7) -> None:
    target.settings = target.settings.with_capacity_history_enabled(True)
    target.settings = target.settings.with_capacity_history_retention_days(days)


def _capacity_section(body: str) -> str:
    assert CAPACITY_SECTION_TITLE in body, body
    return body.split(CAPACITY_SECTION_TITLE, 1)[1]


def _terminate(target) -> None:
    target.monitor = None
    with (
        patch.object(target.virtual_status_device, "terminate"),
        patch.object(target, "stop_event_server"),
        patch.object(target.closed_lid_awake, "release"),
        patch.object(target.keep_awake, "release"),
    ):
        target.applicationWillTerminate_(None)


# --------------------------------------------------------------------------
# capacity_view reaches the panel.
# --------------------------------------------------------------------------


def test_the_why_panel_carries_a_capacity_section_after_a_real_refresh(
    controller,
) -> None:
    """The seam: one real codex refresh, and the panel gains the section.

    Driven through `_run_codex_refresh` -- worker to publish to
    `applyUsageSummary_` -- so nothing here hands the controller a
    hand-built projection it would never see in the app.
    """
    target, _status_bar, _history = controller

    # Before any refresh the section exists and says it has nothing, which
    # is a different statement from the section being absent.
    assert "No capacity reading has been authorised yet." in target.why_panel_body()

    _run_codex_refresh(target, _status_bar, _limits(85.0))

    section = _capacity_section(target.why_panel_body())
    assert "Codex" in section
    assert "5-hour" in section
    assert "15% left" in section


def test_the_open_panel_refresh_renders_the_same_body_as_opening_it(
    controller,
) -> None:
    """An open panel that shrinks on its next tick is the same defect as a
    section that never shipped."""
    target, status_bar, _history = controller
    _run_codex_refresh(target, status_bar, _limits(85.0))

    rendered: list[str] = []
    window = type(
        "_Window",
        (),
        {"isVisible": lambda self: True},
    )()
    with patch.object(
        status_bar.why_panel_module,
        "set_text_preserving_position",
        side_effect=lambda _view, text: rendered.append(text),
    ):
        target.why_panel_window = window
        target.why_panel_text_view = object()
        assert target.refresh_why_panel() is True

    assert rendered and CAPACITY_SECTION_TITLE in rendered[0]


def _drifted_spark_snapshot(status_bar):
    """The payload that broke this project: a model allowance beside a plan
    ceiling.

    A `GPT-5.3-Codex-Spark` window once rendered as the owner's 5-hour
    ceiling. Its general form is a window whose effect this build cannot
    classify: the authority layer calls it INAPPLICABLE and withholds it,
    while it still carries a perfectly well-formed percentage. That
    combination is the point -- the value column can render the number, so
    only the refusal can say why the card will not.
    """
    source = SourceKey("codex", "quota", "local", "remote_quota_windows")
    health = CapacitySourceHealth(
        source, SourceHealthKind.HEALTHY, NOW, NOW, None, None, False
    )

    def lane(window, name, remaining, *, model=None, effect=QuotaEffect.ALL_WORKLOADS):
        return QuotaLaneObservation(
            key=QuotaLaneKey(
                source,
                "all",
                "codex-chatgpt-plan",
                model,
                window,
                effect,
            ),
            semantic_name=name,
            horizon=QuotaHorizon.SHORT,
            value=CapacityValue(
                CapacityUnit.PERCENT_REMAINING, remaining, ObservationState.OBSERVED
            ),
            reset=ResetFact(ResetState.FUTURE, NOW + 3_600.0, 300.0, NOW),
            observed_at=NOW,
            source_health=health,
            account_discriminator="codex-chatgpt",
        )

    return status_bar.CapacitySnapshot(
        observed_at=NOW,
        lanes=(
            lane("five-hour", "5-hour", 60.0),
            lane("spark", "Spark", 3.0, effect=QuotaEffect.UNKNOWN),
        ),
        source_health=(health,),
    )


def test_a_refused_window_is_named_in_the_panel_not_merely_counted(
    controller,
) -> None:
    """The card can only say "1 window unavailable". This says which, and why.

    Driven through the REAL `authorised_capacity_lanes`, so the refusal is
    the authority layer's own and not a string this test invented. The
    withheld lane carries a renderable percentage, so the value column
    cannot be what proves this: only the refusal line can say that the
    number is real and still does not belong on the card.
    """
    target, status_bar, _history = controller
    snapshot = _drifted_spark_snapshot(status_bar)

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)
    assert [name for name, _reason in authorised.withheld] == ["Spark"]
    target._capacity_detail_inputs["codex"] = (snapshot, authorised.projection)

    section = _capacity_section(target.why_panel_body())
    detail_lines = [line.strip() for line in section.splitlines()]
    assert "5-hour .............................. 60% left" in section
    # The withheld lane's own number renders fine -- that is exactly why the
    # refusal has to be printed as well.
    assert "Spark ............................... 3% left" in section
    assert "Not applicable · Healthy · Scope is not supported" in detail_lines
    assert "unknown_effect" not in section


def test_the_effect_refusal_is_stated_once_for_the_card_not_once_per_row(
    controller,
) -> None:
    """`refusal_text` answers "may this fire an alert", which in a build with
    no `CapacityAccountBinding` is "no" for every lane alike. Printed per
    row it appeared under every percentage on screen; a panel that repeats
    one fact on every line stops being read.

    Counted, not merely searched for: asserting the sentence is ABSENT
    would pass for the wrong reason the moment its copy changes, and
    asserting it is PRESENT passes in both the fixed and the broken build.
    """
    target, status_bar, _history = controller
    _run_codex_refresh(target, status_bar, _limits(85.0))

    section = _capacity_section(target.why_panel_body())
    lane_rows = [line for line in section.splitlines() if "% left" in line]
    assert len(lane_rows) >= 2, section
    assert "15% left" in section

    reason = "not tied to a known account yet"
    assert section.lower().count(reason) == 1, section
    summary = [line for line in section.splitlines() if "Drives alerts" in line]
    assert summary and reason in summary[0].lower()
    # No lane row carries it: the per-row line is the presentation refusal.
    assert not [line for line in lane_rows if reason in line.lower()]


def test_the_capacity_section_prints_no_payload_prompt_or_path(controller) -> None:
    """The pledge at the bottom of this panel covers the whole panel."""
    target, status_bar, _history = controller
    _run_codex_refresh(target, status_bar, _limits(85.0))

    section = _capacity_section(target.why_panel_body())
    for forbidden in ("/Users/", "http://", "https://", "Bearer ", "session_id"):
        assert forbidden not in section


def test_the_panel_is_built_from_the_authority_projection(controller) -> None:
    """Not from `authorised.lanes`, and not from the raw window list.

    `AuthorisedCapacity` flattens each refusal to a bare code; the
    projection is the only thing that still holds the per-lane decision, so
    the section cannot exist without it being retained.
    """
    target, status_bar, _history = controller
    _run_codex_refresh(target, status_bar, _limits(85.0))

    snapshot, projection = target._capacity_detail_inputs["codex"]
    assert type(projection) is CapacityProjection
    assert {authority.lane.key for authority in projection.detail_lanes} == {
        lane.key for lane in snapshot.lanes
    }
    models = target.capacity_detail_models(now=time.time())
    assert models and all(type(model) is CapacityDetailModel for model in models)


# --------------------------------------------------------------------------
# capacity_history_store reaches disk, and only with consent.
# --------------------------------------------------------------------------


def test_capacity_history_records_nothing_until_the_owner_consents(
    controller,
) -> None:
    target, status_bar, history_path = controller

    _run_codex_refresh(target, status_bar, _limits(85.0))

    assert target.capacity_history_store() is None
    assert not history_path.exists()
    assert "Capacity history ...." in target.why_panel_body().replace(
        "Capacity history ", "Capacity history "
    ) or "Capacity history" in target.why_panel_body()


def test_a_consented_refresh_writes_one_bounded_sample_per_lane(
    controller,
) -> None:
    """The producer this store never had. Every field it needs -- the
    disposition, the refusal code, the reset and the remaining value --
    was already computed by `evaluate_reset_continuity` on the live refresh
    path and then dropped."""
    target, status_bar, history_path = controller
    _enable_history(target)

    _run_codex_refresh(target, status_bar, _limits(85.0))
    assert target._persistence_writer.wait_idle(timeout_seconds=1.0)

    store = target.capacity_history_store()
    assert store is not None
    assert store.state.capacity_samples, "no sample was admitted"
    assert history_path.exists(), "nothing reached disk"
    document = json.loads(history_path.read_text(encoding="utf-8"))
    text = json.dumps(document)
    # Metadata only: no session, no path, no prompt.
    for forbidden in ("/Users/", "prompt", "message"):
        assert forbidden not in text


def test_history_summaries_reach_the_panel_once_there_are_samples(
    controller,
) -> None:
    target, status_bar, _history = controller
    _enable_history(target)

    _run_codex_refresh(target, status_bar, _limits(85.0))

    presentation = target.capacity_history_presentation(now=time.time())
    assert presentation.enabled is True
    assert {item.interval for item in presentation.summaries} == set(HistoryInterval)
    section = _capacity_section(target.why_panel_body())
    assert "Last Day" in section
    assert "observation" in section


def test_turning_history_off_deletes_what_was_already_kept(controller) -> None:
    """"Off" that leaves the file on disk is not off."""
    target, status_bar, history_path = controller
    _enable_history(target)
    _run_codex_refresh(target, status_bar, _limits(85.0))
    assert target._persistence_writer.wait_idle(timeout_seconds=1.0)
    assert history_path.exists()

    target.settings = target.settings.with_capacity_history_enabled(False)
    assert target.capacity_history_store() is None

    assert not history_path.exists()


def test_turning_history_off_invalidates_a_pending_flush(controller) -> None:
    """A queued pre-consent-revocation write must not recreate the file."""
    target, status_bar, history_path = controller
    _enable_history(target)
    _run_codex_refresh(target, status_bar, _limits(85.0))
    assert target._persistence_writer.wait_idle(timeout_seconds=1.0)
    assert history_path.exists()

    running = threading.Event()
    release = threading.Event()

    def block_writer() -> None:
        running.set()
        release.wait(2.0)

    target._persistence_writer.submit("test-block", block_writer)
    assert running.wait(1.0)
    _run_codex_refresh(target, status_bar, _limits(86.0))

    target.settings = target.settings.with_capacity_history_enabled(False)
    assert target.capacity_history_store() is None
    release.set()
    assert target._persistence_writer.wait_idle(timeout_seconds=1.0)

    assert not history_path.exists()


def test_termination_drains_a_dirty_capacity_history_tail(controller) -> None:
    target, status_bar, history_path = controller
    _enable_history(target)
    running = threading.Event()
    release = threading.Event()

    def block_writer() -> None:
        running.set()
        release.wait(2.0)

    target._persistence_writer.submit("test-block", block_writer)
    assert running.wait(1.0)
    _run_codex_refresh(target, status_bar, _limits(85.0))
    assert not history_path.exists()

    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        _terminate(target)
    finally:
        release.set()
        timer.join(1.0)

    assert history_path.exists()


def test_termination_reserves_one_tail_slot_when_normal_queue_is_full(
    controller,
) -> None:
    target, status_bar, history_path = controller
    _enable_history(target)
    assert target._persistence_writer.close(timeout_seconds=0.0)
    target._persistence_writer = SerialPersistenceWriter(
        max_pending=1,
        receipt_handler=target._record_persistence_receipt,
    )
    running = threading.Event()
    release = threading.Event()

    def block_writer() -> None:
        running.set()
        release.wait(2.0)

    target._persistence_writer.submit("test-block", block_writer)
    assert running.wait(1.0)
    target._persistence_writer.submit("test-filler", lambda: None)
    _run_codex_refresh(target, status_bar, _limits(85.0))
    assert target._persistence_writer.snapshot().refused_full >= 1
    assert not history_path.exists()

    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        _terminate(target)
    finally:
        release.set()
        timer.join(1.0)

    snapshot = target._persistence_writer.snapshot()
    assert snapshot.reserved_drain_tail == 1
    assert history_path.exists()


def test_shortening_retention_prunes_now_not_at_some_later_flush(
    controller,
) -> None:
    """`flush` short-circuits on a clean store, so a quiet desk could keep
    90 days of samples for weeks after the owner asked for 7."""
    target, status_bar, history_path = controller
    _enable_history(target, days=90)
    _run_codex_refresh(target, status_bar, _limits(85.0))
    store = target.capacity_history_store()
    assert store is not None and store.state.capacity_samples

    # A sample well outside a 7-day window, written the way the store writes.
    old = store.state.capacity_samples[0]
    aged = type(old)(
        schema_version=old.schema_version,
        lane_key=old.lane_key,
        account_discriminator=old.account_discriminator,
        observed_at=time.time() - 30 * 86_400.0,
        remaining=old.remaining,
        reset_epoch=old.reset_epoch,
        window_minutes=old.window_minutes,
        source_health=old.source_health,
        disposition=old.disposition,
        refusal_code=old.refusal_code,
    )
    store.state = type(store.state)((aged,), store.state.activity_samples)
    store.apply_retention(90, now=time.time())
    assert store.state.capacity_samples

    store.apply_retention(7, now=time.time())

    assert store.state.capacity_samples == ()
    assert json.loads(history_path.read_text(encoding="utf-8"))


def test_a_history_file_that_cannot_be_read_is_no_history_not_a_crash(
    controller,
) -> None:
    target, status_bar, history_path = controller
    _enable_history(target)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text("{not json", encoding="utf-8")

    _run_codex_refresh(target, status_bar, _limits(85.0))

    assert CAPACITY_SECTION_TITLE in target.why_panel_body()


# --------------------------------------------------------------------------
# The pure renderer's own bounds.
# --------------------------------------------------------------------------


def test_the_renderer_refuses_anything_that_is_not_a_detail_model() -> None:
    for bad in ((object(),), [], "models"):
        with pytest.raises(ValueError):
            capacity_detail_text(bad)


def test_no_capacity_reading_says_so_rather_than_printing_an_empty_heading() -> None:
    text = capacity_detail_text(())
    assert CAPACITY_SECTION_TITLE in text
    assert "No capacity reading has been authorised yet." in text
