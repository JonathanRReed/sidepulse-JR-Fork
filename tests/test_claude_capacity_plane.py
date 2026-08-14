"""The Claude consumer capacity plane, from the endpoint to the dropdown.

`test_claude_limits_reachable.py` pins that the four switches are open. This
file pins what now flows through them, and each test fails on a build where
any link is missing: the declaration, the mapping, the conversion, the
authority pass, or the ledger row.

The load-bearing claims:

  * a window this build did not declare is DROPPED, never force-fitted into a
    declared lane -- a "Fable only" ceiling read as the weekly one is worse
    than no reading;
  * utilization is percent USED and is inverted exactly once;
  * a window with no credible reset stays reset-less, so nothing can render a
    countdown that was never observed;
  * the refresh scope and the produced lanes agree, or the coordinator throws
    every reading away as cross-scope;
  * every window reaches the dropdown, including the weekly Opus sub-cap.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sidepulse import claude_quota, usage_card
from sidepulse.capacity_authority import select_binding_lanes
from sidepulse.capacity_refresh import RefreshCause
from sidepulse.capacity_sources import (
    EvidenceMetricKind,
    SupportedCapacityEvidence,
    SupportedLaneEvidence,
    normalize_supported_quota_evidence,
)
from sidepulse.capacity_types import (
    CapacityAccountBinding,
    CapacityEvidenceClass,
    CapacitySnapshot,
    CapacitySourceHealth,
    ExecutionContext,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.provider_capacity import (
    negotiate_provider_capacity_policies,
    select_provider_capacity_policy,
)
from sidepulse.providers import negotiated_provider_sources
from tests.test_sidepulse import isolate_controller

NOW = 1_800_000_000.0


def _descriptor():
    return next(
        row.descriptor
        for row in negotiate_provider_capacity_policies(negotiated_provider_sources())
        if row.descriptor is not None
        and row.descriptor.source == claude_quota.CLAUDE_QUOTA_SOURCE
    )


def _observations(payload, *, observed_at=NOW):
    descriptor = _descriptor()
    windows = claude_quota.windows_from_payload(payload)
    evidence = claude_quota.capacity_evidence_from_windows(
        descriptor,
        windows,
        observed_at=observed_at,
    )
    normalized = normalize_supported_quota_evidence(
        descriptor,
        evidence,
        observed_at=observed_at,
    )
    return normalized.snapshot.lanes


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


# --------------------------------------------------------------------------
# The mapping boundary.
# --------------------------------------------------------------------------


def test_only_declared_windows_become_lanes() -> None:
    """An undeclared window must be dropped, not borrow a declared lane."""
    lanes = _observations(
        {
            "five_hour": {"utilization": 10.0},
            "seven_day": {"utilization": 20.0},
            "seven_day_opus": {"utilization": 88.0},
            "seven_day_sonnet": {"utilization": 40.0},
            # Neither of these is declared. Silently folding either into the
            # weekly lane would report a routine allowance as the ceiling
            # that actually stops work.
            "claude_routines": {"utilization": 99.0},
        }
    )

    assert tuple(lane.semantic_name for lane in lanes) == (
        "5-hour",
        "Weekly",
        "Weekly Opus",
        "Weekly Sonnet",
    )
    assert all(
        lane.key.source == claude_quota.CLAUDE_QUOTA_SOURCE for lane in lanes
    )
    assert tuple((lane.key.model, lane.key.effect) for lane in lanes) == (
        (None, QuotaEffect.ALL_WORKLOADS),
        (None, QuotaEffect.ALL_WORKLOADS),
        ("opus", QuotaEffect.MODEL),
        ("sonnet", QuotaEffect.MODEL),
    )


def test_an_unknown_model_sub_cap_is_dropped_rather_than_renamed() -> None:
    """A tier we never declared cannot arrive wearing a declared tier's name."""
    lanes = _observations(
        {
            "seven_day": {"utilization": 20.0},
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 71.0,
                    "scope": {"model": {"id": "fable"}},
                }
            ],
        }
    )

    assert tuple(lane.semantic_name for lane in lanes) == ("Weekly",)


def test_the_contract_stamps_the_semantics_not_the_payload() -> None:
    """Provider labels must not become display semantics or horizons."""
    lanes = _observations(
        {"five_hour": {"utilization": 10.0}, "seven_day_opus": {"utilization": 88.0}}
    )
    five_hour, opus = lanes

    assert (five_hour.semantic_name, five_hour.horizon) == ("5-hour", QuotaHorizon.SHORT)
    assert (opus.semantic_name, opus.horizon) == ("Weekly Opus", QuotaHorizon.LONG)
    # `build_observation` refuses a key it did not declare, which is what
    # makes "the descriptor owns identity" true rather than aspirational.
    assert all(
        any(declared.key == lane.key for declared in _descriptor().lanes)
        for lane in lanes
    )


def test_declaration_order_survives_a_reshuffled_payload() -> None:
    """Reading order is a product decision, not the endpoint's key order."""
    lanes = _observations(
        {
            "seven_day_opus": {"utilization": 88.0},
            "seven_day": {"utilization": 20.0},
            "five_hour": {"utilization": 10.0},
        }
    )

    assert tuple(lane.semantic_name for lane in lanes) == (
        "5-hour",
        "Weekly",
        "Weekly Opus",
    )


# --------------------------------------------------------------------------
# The two numbers that are easy to get quietly wrong.
# --------------------------------------------------------------------------


def test_utilization_is_percent_used_and_is_inverted_exactly_once() -> None:
    """88% used is 12% LEFT. Inverting twice, or not at all, reads as calm."""
    lanes = _observations(
        {
            "seven_day_opus": {"utilization": 88.0},
            "seven_day_sonnet": {"utilization": 100.0},
            "five_hour": {"utilization": 0.0},
        }
    )
    five_hour, opus, sonnet = lanes

    assert opus.value.remaining == 12.0
    assert five_hour.value.remaining == 100.0
    # Fully consumed is a distinct state, not a missing value.
    assert sonnet.value.remaining == 0.0
    assert sonnet.value.state is ObservationState.OBSERVED_ZERO
    assert opus.value.state is ObservationState.OBSERVED


def test_one_out_of_range_window_does_not_take_the_batch_down() -> None:
    """A bad weekly reading must not cost the owner the Opus sub-cap too."""
    descriptor = _descriptor()
    evidence = claude_quota.capacity_evidence_from_windows(
        descriptor,
        [
            {"label": "weekly", "utilization": 140.0},
            {"label": "Opus only", "utilization": 88.0},
        ],
        observed_at=NOW,
    )
    lanes = normalize_supported_quota_evidence(
        descriptor,
        evidence,
        observed_at=NOW,
    ).snapshot.lanes

    assert tuple(lane.semantic_name for lane in lanes) == ("Weekly", "Weekly Opus")
    assert lanes[0].value.remaining == 0.0
    assert lanes[1].value.remaining == 12.0


def test_the_evidence_stays_used_first_until_the_single_conversion() -> None:
    """One place converts, so there is one place to get it wrong."""
    descriptor = _descriptor()
    evidence = claude_quota.capacity_evidence_from_windows(
        descriptor,
        claude_quota.windows_from_payload({"seven_day_opus": {"utilization": 88.0}}),
        observed_at=NOW,
    )

    assert evidence.lanes[0].metric_kind is EvidenceMetricKind.PERCENT_USED
    assert evidence.lanes[0].percent == 88.0


def test_a_window_without_a_credible_reset_stays_reset_less() -> None:
    """No reset must mean no epoch -- a faked one becomes a fake countdown."""
    lanes = _observations(
        {
            "five_hour": {"utilization": 10.0},
            "seven_day": {"utilization": 20.0, "resets_at": NOW - 60.0},
            "seven_day_opus": {"utilization": 88.0, "resets_at": "not a date"},
        }
    )

    for lane in lanes:
        assert lane.reset.state is ResetState.UNKNOWN
        assert lane.reset.reset_epoch is None


def test_iso_and_epoch_resets_both_become_one_future_fact() -> None:
    """The endpoint has used both spellings; neither may be the odd one out."""
    lanes = _observations(
        {
            "five_hour": {"utilization": 10.0, "resets_at": "2027-01-15T09:00:00Z"},
            "seven_day": {"utilization": 20.0, "resets_at": NOW + 7_200.0},
            # Milliseconds, which the shared normalizer also accepts.
            "seven_day_opus": {"utilization": 88.0, "resets_at": (NOW + 10_800.0) * 1000},
        }
    )
    five_hour, weekly, opus = lanes

    assert all(lane.reset.state is ResetState.FUTURE for lane in lanes)
    assert five_hour.reset.reset_epoch == NOW + 3_600.0
    assert weekly.reset.reset_epoch == NOW + 7_200.0
    assert opus.reset.reset_epoch == NOW + 10_800.0
    assert five_hour.reset.window_minutes == 300.0
    assert weekly.reset.window_minutes == 10_080.0


# --------------------------------------------------------------------------
# The scope invariant, and the authority pass.
# --------------------------------------------------------------------------


def test_the_refresh_scope_matches_the_lanes_the_producer_emits(controller) -> None:
    """A scope mismatch throws every reading away, and says nothing about it.

    `CapacityRefreshCoordinator._validate_snapshot` rejects a snapshot whose
    lanes fall outside its key's (source, pool, account, auth_mode) scope. The
    refresh key used to hardcode pool="plan", which matches no declared pool
    at all -- invisible only while every producer emitted zero lanes.
    """
    target, _status_bar = controller
    refresh_key = target._capacity_refresh_keys_by_provider["claude"]
    lanes = _observations({"five_hour": {"utilization": 10.0}})

    assert refresh_key.pool == "claude-consumer-plan"
    for lane in lanes:
        assert lane.key.source == refresh_key.source
        assert lane.key.pool == refresh_key.pool
        assert lane.account_discriminator == refresh_key.account_discriminator
        assert lane.auth_mode == refresh_key.auth_mode


def test_a_lane_this_context_cannot_speak_for_never_reaches_the_ledger(
    controller,
) -> None:
    """Display is downstream of `select_binding_lanes`, not of the wire."""
    _target, status_bar = controller
    declared = _observations(
        {
            "five_hour": {"utilization": 10.0},
            "seven_day_opus": {"utilization": 88.0},
            "seven_day_sonnet": {"utilization": 40.0},
        }
    )
    foreign_source = SourceKey("codex", "quota", "elsewhere", "remote_quota_windows")
    foreign = declared[0].__class__(
        key=declared[0].key.__class__(
            foreign_source,
            "all",
            "codex-chatgpt-plan",
            None,
            "five-hour",
            QuotaEffect.ALL_WORKLOADS,
        ),
        semantic_name="Foreign window",
        horizon=QuotaHorizon.SHORT,
        value=declared[0].value,
        reset=declared[0].reset,
        observed_at=NOW,
        source_health=CapacitySourceHealth(
            foreign_source,
            SourceHealthKind.HEALTHY,
            NOW,
            NOW,
            None,
            None,
            False,
        ),
        account_discriminator=None,
    )
    snapshot = CapacitySnapshot(
        observed_at=NOW,
        lanes=(*declared, foreign),
        source_health=(declared[0].source_health, foreign.source_health),
    )

    kept = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    # An unregistered source instance is out of context and is dropped.
    assert foreign not in kept
    assert tuple(lane.semantic_name for lane in kept) == (
        "5-hour",
        "Weekly Opus",
        "Weekly Sonnet",
    )

    # And when the running model IS known, the sub-cap for the other tier is
    # inapplicable -- an Opus ceiling must not speak for a Sonnet session.
    context = status_bar.ExecutionContext(
        provider_ids=("claude", "codex"),
        source_instances=("local", "oauth"),
        selected_model="sonnet",
        selected_feature=None,
        source_scopes=(("claude", "oauth"), ("codex", "local")),
    )
    with patch.object(status_bar, "capacity_execution_context", return_value=context):
        scoped = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    assert tuple(lane.semantic_name for lane in scoped) == ("5-hour", "Weekly Sonnet")


# --------------------------------------------------------------------------
# End to end: a refresh reaches the dropdown, with every window.
# --------------------------------------------------------------------------


def _run_claude_refresh(target, status_bar, payload_windows):
    """Drive one real claude capacity refresh through the worker and publish."""
    target.settings = target.settings.with_claude_plan_limits_enabled(True)
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["claude"]
    source_key = refresh_key.source
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    generation = decision.generation
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        generation,
        monotonic_now + 30.0,
    )
    published = []
    with (
        patch.object(target, "claude_access_token", return_value="tok"),
        patch.object(
            status_bar.claude_quota,
            "fetch_windows",
            return_value=payload_windows,
        ),
        patch.object(
            target,
            "performSelectorOnMainThread_withObject_waitUntilDone_",
            side_effect=lambda _sel, payload, _wait: published.append(payload),
        ),
    ):
        target._usage_refresh_source_worker(source_key, generation, None, {}, None)

    assert published, "the worker published nothing"
    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(published[0])
    return published[0]


def _one_line_measure(text, style, width):
    """Deterministic stand-in for AppKit: every string fits on one line.

    macOS line heights for the card's two sizes, so the derived geometry is
    the real one -- the point is to pin the coordinates, not to re-measure the
    system font.
    """
    del text, width
    line_height = 14.0 if style.font_size >= 11.0 else 13.0
    return usage_card.TextMetrics(
        natural_width=10.0,
        wrapped_height=line_height,
        line_height=line_height,
    )


def _live_windows():
    reset = time.time() + 3_600.0
    return [
        {
            "label": "5-hour",
            "utilization": 10.0,
            "window_minutes": 300,
            "resets_at": reset,
        },
        {
            "label": "weekly",
            "utilization": 20.0,
            "window_minutes": 7 * 24 * 60,
            "resets_at": reset + 86_400.0,
        },
        {
            "label": "Opus only",
            "utilization": 88.0,
            "window_minutes": 7 * 24 * 60,
            "resets_at": reset + 86_400.0,
        },
        {
            "label": "Sonnet only",
            "utilization": 40.0,
            "window_minutes": 7 * 24 * 60,
            "resets_at": reset + 86_400.0,
        },
    ]


def test_a_refresh_publishes_contract_stamped_observations(controller) -> None:
    """The worker's result carries typed observations, not just raw windows."""
    target, status_bar = controller

    payload = _run_claude_refresh(target, status_bar, _live_windows())

    result = payload["results"][target._capacity_refresh_keys_by_provider["claude"].source]
    observations = result["capacity_observations"]
    assert type(observations) is tuple
    assert tuple(lane.semantic_name for lane in observations) == (
        "5-hour",
        "Weekly",
        "Weekly Opus",
        "Weekly Sonnet",
    )
    # Without a stable source generation every reset stays unconfirmed and no
    # countdown is ever shown, so its absence is a real defect.
    assert type(result["source_generation"]) is int

    state = next(
        row
        for row in target._capacity_refresh_coordinator.snapshot_state(
            time.monotonic()
        ).sources
        if row.key == target._capacity_refresh_keys_by_provider["claude"]
    )
    assert state.has_last_known_good
    assert len(state.last_known_good.lanes) == 4


def test_every_window_reaches_the_dropdown_with_usage_and_reset(controller) -> None:
    """The ledger showed windows[0] and dropped the rest -- including Opus."""
    target, status_bar = controller

    _run_claude_refresh(target, status_bar, _live_windows())

    model = target._usage_provider_models["claude"]
    assert len(model.windows) == 4

    status_bar.build_usage_menu_item(target)
    primary = target._usage_menu_labels["claude"].stringValue()
    rows = tuple(
        field.stringValue()
        for field in target._usage_menu_window_labels["claude"]
    )

    assert primary == "Claude · 5h 90% left"
    assert "resets in" in target._usage_menu_secondary_labels["claude"].stringValue()
    # The sub-cap that actually stops the owner's work is visible, named, and
    # carries both numbers. Three "7d" rows would have hidden it.
    assert rows == (
        "Weekly 80% left · resets in 1d 1h",
        "Weekly Opus 12% left · resets in 1d 1h",
        "Weekly Sonnet 60% left · resets in 1d 1h",
    )


def test_the_card_grows_for_extra_windows_without_moving_a_single_row() -> None:
    """Zero extra windows must lay out exactly as the literal geometry did.

    The geometry is measured from the drawn text now rather than assembled
    from per-row literals, so this pins the two things that has to preserve:
    content that fits on one line lands on exactly the coordinates the literal
    card used, and each extra window still costs exactly one row.
    """
    rows = usage_card.capacity_card_rows(
        (
            ("codex", "Codex · 5h 62% left", "resets in 2h", ()),
            ("claude", "Claude · 5h 90% left", "resets in 1d 1h", ()),
        )
    )
    layout = usage_card.usage_card_layout(rows, measure=_one_line_measure)

    assert (layout.height, layout.row("header").rect.y) == (110, 87)
    assert layout.row("codex:primary").rect.y == 64
    assert layout.row("codex:secondary").rect.y == 47
    assert layout.row("claude:primary").rect.y == 25
    assert layout.row("claude:secondary").rect.y == 8

    taller_rows = usage_card.capacity_card_rows(
        (
            ("codex", "Codex · 5h 62% left", "resets in 2h", ()),
            (
                "claude",
                "Claude · 5h 90% left",
                "resets in 1d 1h",
                ("Weekly 80% left", "Weekly Opus 12% left", "Weekly Sonnet 60% left"),
            ),
        )
    )
    taller = usage_card.usage_card_layout(taller_rows, measure=_one_line_measure)

    assert taller.height > layout.height
    assert taller.row("header").rect.y > layout.row("header").rect.y
    # Rows read downward, so the first extra window sits highest.
    assert tuple(
        taller.row(f"claude:window:{index}").rect.y for index in range(3)
    ) == (42, 25, 8)


def test_in_place_updates_never_silently_drop_a_window(controller) -> None:
    """A card built for one window must not eat a second one when it appears."""
    target, status_bar = controller

    _run_claude_refresh(target, status_bar, _live_windows()[:1])
    status_bar.build_usage_menu_item(target)
    assert target._usage_menu_window_labels["claude"] == ()

    _run_claude_refresh(target, status_bar, _live_windows())
    target._menu_signature = "unchanged"
    target.update_usage_menu_fields()

    # Nothing to fold into, so the card asks to be rebuilt rather than
    # pretending the extra ceilings do not exist.
    assert target._menu_signature is None

    # And once the card has been rebuilt at the right size it stops asking,
    # rather than invalidating the menu on every refresh forever.
    status_bar.build_usage_menu_item(target)
    target._menu_signature = "unchanged"
    target.update_usage_menu_fields()
    assert target._menu_signature == "unchanged"
    assert len(target._usage_menu_window_labels["claude"]) == 3


def test_the_publish_path_still_refuses_untyped_observations(controller) -> None:
    """The producer is the only way in; a hand-rolled tuple stays refused."""
    target, _status_bar = controller
    target.settings = target.settings.with_claude_plan_limits_enabled(True)
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["claude"]
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    assert decision.generation is not None
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        decision.generation,
        monotonic_now + 30.0,
    )

    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(
            {
                "requests": {refresh_key.source: decision.generation},
                "results": {
                    refresh_key.source: {
                        "provider_id": "claude",
                        "title": "Claude",
                        "windows": [],
                        "capacity_observations": ({"label": "weekly"},),
                    }
                },
                "failures": {},
            }
        )

    assert target._usage_provider_states["claude"].consecutive_failures == 1


# --------------------------------------------------------------------------
# The authority layer's veto must never be the trigger for a bypass.
# --------------------------------------------------------------------------


def _drifted_windows():
    """Every label outside `CLAUDE_LANE_IDENTITIES` -- total schema drift."""
    reset = time.time() + 3_600.0
    return [
        {
            "label": "Fable",
            "utilization": 91.0,
            "window_minutes": 300,
            "resets_at": reset,
        },
        {
            "label": "Daily Routines",
            "utilization": 97.0,
            "window_minutes": 7 * 24 * 60,
            "resets_at": reset + 86_400.0,
        },
    ]


def _rendered_capacity_text(target, status_bar) -> str:
    """Every string the owner can actually read, menu and Settings."""
    status_bar.build_usage_menu_item(target)
    model = target._usage_provider_models["claude"]
    return " | ".join(
        (
            target._usage_menu_labels["claude"].stringValue(),
            target._usage_menu_secondary_labels["claude"].stringValue(),
            *(
                field.stringValue()
                for field in target._usage_menu_window_labels["claude"]
            ),
            model.menu_line,
            model.settings_text,
        )
    )


def test_a_totally_drifted_payload_renders_nothing_rather_than_raw_percentages(
    controller,
) -> None:
    """Zero authorised lanes must not promote the raw window list to display.

    `authorised` is a tuple-like, so an empty one read as "no authority pass
    ran" and the view fell back to `result["windows"]` -- the never-authorised
    wire values. A "Fable" allowance then rendered as `Claude · 5h 9% left`,
    indistinguishable from the ceiling that actually stops work, with a
    countdown off an unverified `resets_at` and lane keys minted outside the
    contract (pool="unspecified", opaque_scope="legacy:N").
    """
    target, status_bar = controller

    payload = _run_claude_refresh(target, status_bar, _drifted_windows())

    result = payload["results"][target._capacity_refresh_keys_by_provider["claude"].source]
    # The raw list never even reaches the main thread now.
    assert "windows" not in result
    assert result["capacity_observations"] == ()

    model = target._usage_provider_models["claude"]
    assert model.windows == ()
    assert model.missing is True
    assert model.error_text == status_bar.CAPACITY_UNAUTHORISED_COPY

    rendered = _rendered_capacity_text(target, status_bar)
    assert target._usage_menu_labels["claude"].stringValue() == (
        "Claude · Capacity reading unavailable"
    )
    assert target._usage_menu_window_labels["claude"] == ()
    assert model.settings_text == status_bar.CAPACITY_UNAUTHORISED_COPY
    # No number, no label the contract never declared, no invented countdown.
    assert "%" not in rendered
    assert "Fable" not in rendered
    assert "Daily Routines" not in rendered
    assert "resets in" not in rendered.lower()


def _lane_evidence(descriptor, key, *, state, percent=40.0):
    return SupportedLaneEvidence(
        key=key,
        metric_kind=EvidenceMetricKind.PERCENT_USED,
        percent=percent,
        state=state,
        reset_state=ResetState.UNKNOWN,
        reset_epoch=None,
        window_minutes=300.0,
    )


def _observations_with(*, health_kind, states):
    """Contract-stamped observations carrying a chosen health and value state."""
    descriptor = _descriptor()
    evidence = SupportedCapacityEvidence(
        source=claude_quota.CLAUDE_QUOTA_SOURCE,
        health_kind=health_kind,
        lanes=tuple(
            _lane_evidence(
                descriptor,
                lane.key,
                state=state,
                percent=None if state is ObservationState.NULL else 40.0,
            )
            for lane, state in zip(descriptor.lanes, states, strict=True)
        ),
        account_discriminator=claude_quota.CLAUDE_ACCOUNT_SCOPE,
        has_last_known_good=False,
        auth_mode=claude_quota.CLAUDE_AUTH_MODE,
    )
    return normalize_supported_quota_evidence(
        descriptor,
        evidence,
        observed_at=NOW,
    ).snapshot.lanes


@pytest.mark.parametrize(
    ("health_kind", "state", "refusal"),
    (
        (SourceHealthKind.HEALTHY, ObservationState.PARTIAL, "usage_partial"),
        (SourceHealthKind.HEALTHY, ObservationState.NULL, "usage_missing"),
        (
            SourceHealthKind.ACCESS_DENIED,
            ObservationState.OBSERVED,
            "source_access_denied",
        ),
    ),
)
def test_a_lane_refused_for_any_reason_never_reaches_the_ledger(
    controller,
    health_kind,
    state,
    refusal,
) -> None:
    """The display boundary enforces the whole refusal, not just applicability.

    It filtered on `applicability is not INAPPLICABLE` and threw
    `refusal_code` away, so every value-level refusal the authority layer had
    just computed -- a partial batch, a null reading, a source that answered
    ACCESS_DENIED -- published as an ordinary percentage.
    """
    _target, status_bar = controller
    lanes = _observations_with(
        health_kind=health_kind,
        states=(state,) * len(_descriptor().lanes),
    )
    snapshot = CapacitySnapshot(
        observed_at=NOW,
        lanes=lanes,
        source_health=(lanes[0].source_health,),
    )

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    assert authorised.lanes == ()
    assert {code for _name, code in authorised.withheld} == {refusal}
    # And the reason is carried, not dropped: it is what the card says.
    assert len(authorised.withheld) == len(lanes)


def test_one_refused_lane_is_named_beside_the_ones_that_survived(controller) -> None:
    """A row that vanishes silently is worse than a row that says why."""
    _target, status_bar = controller
    healthy = _observations({"five_hour": {"utilization": 10.0}})
    partial = _observations_with(
        health_kind=SourceHealthKind.HEALTHY,
        states=(
            ObservationState.OBSERVED,
            ObservationState.PARTIAL,
            ObservationState.PARTIAL,
            ObservationState.PARTIAL,
        ),
    )[1:]
    lanes = (*healthy, *partial)
    snapshot = CapacitySnapshot(
        observed_at=NOW,
        lanes=lanes,
        source_health=(lanes[0].source_health,),
    )

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    assert tuple(lane.semantic_name for lane in authorised.lanes) == ("5-hour",)
    assert authorised.withheld == (
        ("Weekly", "usage_partial"),
        ("Weekly Opus", "usage_partial"),
        ("Weekly Sonnet", "usage_partial"),
    )


def test_the_production_context_scopes_each_provider_to_its_own_instance(
    controller,
) -> None:
    """Registering claude/quota/oauth must not teach codex to accept oauth.

    The context carried two flat lists and the authority layer tested them
    independently, so the second registration silently produced a cross
    product: codex/quota/oauth and claude/transcripts/local both matched, and
    neither is a capacity source this build negotiated.
    """
    _target, status_bar = controller
    context = status_bar.capacity_execution_context()

    assert context.source_scopes == (("claude", "oauth"), ("codex", "local"))

    declared = _observations({"five_hour": {"utilization": 10.0}})[0]
    for provider_id, adapter_id, instance in (
        ("codex", "quota", "oauth"),
        ("claude", "transcripts", "local"),
    ):
        foreign_source = SourceKey(
            provider_id, adapter_id, instance, "remote_quota_windows"
        )
        foreign = declared.__class__(
            key=declared.key.__class__(
                foreign_source,
                "all",
                "claude-consumer-plan",
                None,
                "five-hour",
                QuotaEffect.ALL_WORKLOADS,
            ),
            semantic_name="Crossed window",
            horizon=QuotaHorizon.SHORT,
            value=declared.value,
            reset=declared.reset,
            observed_at=NOW,
            source_health=CapacitySourceHealth(
                foreign_source,
                SourceHealthKind.HEALTHY,
                NOW,
                NOW,
                None,
                None,
                False,
            ),
            account_discriminator=None,
        )
        snapshot = CapacitySnapshot(
            observed_at=NOW,
            lanes=(foreign,),
            source_health=(foreign.source_health,),
        )

        authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

        assert authorised.lanes == ()
        assert authorised.withheld == (("Crossed window", "source_out_of_context"),)


def test_the_contract_carries_the_auth_mode_the_policy_declares() -> None:
    """Binding needs an exact auth mode, so the observation has to carry one.

    `build_observation` had no `auth_mode` parameter while
    `CapacityAccountBinding.auth_mode` must be non-None, so EVERY
    contract-built observation was refused `auth_mode_binding_mismatch` --
    binding was structurally impossible and `limit=0` was standing in for a
    contract that could not express the claim.
    """
    policy = select_provider_capacity_policy("claude", claude_quota.CLAUDE_AUTH_MODE)

    assert policy is not None
    assert policy.capacity_profile_id == "anthropic-consumer"
    # One declared mode, and the producer stamps exactly it.
    assert policy.auth_modes == (claude_quota.CLAUDE_AUTH_MODE,)

    lanes = _observations({"five_hour": {"utilization": 10.0}})
    assert all(lane.auth_mode == claude_quota.CLAUDE_AUTH_MODE for lane in lanes)

    # And the refusal that made binding impossible is gone: with a binding
    # that agrees, the lane binds.
    binding = CapacityAccountBinding(
        source=claude_quota.CLAUDE_QUOTA_SOURCE,
        provider_id="claude",
        auth_mode=claude_quota.CLAUDE_AUTH_MODE,
        opaque_account_id=claude_quota.CLAUDE_ACCOUNT_SCOPE,
        pool_id="claude-consumer-plan",
        evidence_class=CapacityEvidenceClass.OFFICIAL_API,
        observed_at=NOW,
    )
    reset_epoch = NOW + 3_600.0
    lanes = _observations(
        {"five_hour": {"utilization": 10.0, "resets_at": reset_epoch}},
        observed_at=NOW,
    )
    projection = select_binding_lanes(
        CapacitySnapshot(NOW, lanes, (lanes[0].source_health,)),
        ExecutionContext(("claude",), ("oauth",), None, None),
        NOW,
        bindings=(binding,),
    )

    assert [row.lane.semantic_name for row in projection.binding_lanes] == ["5-hour"]
    assert projection.detail_lanes[0].refusal_code is None


def test_no_capacity_reading_gains_a_hardware_or_alert_effect(controller) -> None:
    """Declaring lanes authorises a LEDGER, and deliberately nothing else."""
    target, _status_bar = controller
    target.post_webhook = MagicMock()
    target.post_completion_notification = MagicMock()

    _run_claude_refresh(target, _status_bar, _live_windows())

    assert target.presentation_capacity_glance() is None
    assert target.quota_runway_state() is None
    assert target.quota_blink_until == 0.0
    assert target.post_webhook.call_count == 0
    assert target.post_completion_notification.call_count == 0
    assert target.peek_program(255, led_count=8) == "off"
    assert Path("/private/tmp").exists()  # no device path was touched
