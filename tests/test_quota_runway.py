"""The runway LED's producer: tightest JR-plane lane selection."""

from __future__ import annotations

from types import SimpleNamespace

from sidepulse.provider_feature_settings import (
    ProviderInstancePolicyProjection,
    ProviderInstanceRetentionProjection,
    ProviderInstanceSessionActionProjection,
    ProviderInstanceSharingProjection,
    ProviderInstanceVisualPolicy,
    ProviderInstanceVisualProjection,
)
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
    UsageLane,
)
from sidepulse.quota_runway import (
    QuotaRunwayState,
    quota_runway_state_for_controller,
    runway_state_for_lane,
    tightest_runway_lane,
)

NOW = 1_787_000_000.0


def _lane(provider, lane_id, remaining, *, bindable=True, reset_at=NOW + 3_600.0):
    return UsageLane(
        provider_id=provider,
        lane_id=lane_id,
        label=lane_id.replace("_", " ").title(),
        remaining_percent=remaining,
        reset_at=reset_at,
        scope="all",
        model=None,
        feature=None,
        bindable=bindable,
        source_id="official",
    )


def _snapshot(
    provider,
    lanes,
    *,
    state=ProviderSourceState.READY,
    source_instance_id="default",
):
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label=None,
        observed_at=NOW,
        state=state,
        reason_code=None,
        action_label="Retry" if state is not ProviderSourceState.READY else None,
        lanes=tuple(lanes),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
        source_instance_id=source_instance_id,
    )


def test_worst_lane_wins_even_above_every_threshold() -> None:
    # Runway differs from the ember here: no threshold gate. 62% left is
    # the tightest lane, so it drives the fill.
    lane = tightest_runway_lane(
        (
            _snapshot("claude", (_lane("claude", "five_hour", 90.0), _lane("claude", "weekly", 62.0))),
            _snapshot("codex", (_lane("codex", "weekly", 71.0),)),
        )
    )
    assert lane is not None
    assert (lane.provider_id, lane.lane_id) == ("claude", "weekly")


def test_hidden_providers_and_ungated_sources_are_skipped() -> None:
    lane = tightest_runway_lane(
        (
            _snapshot("claude", (_lane("claude", "weekly", 5.0),)),
            _snapshot(
                "cursor",
                (_lane("cursor", "monthly", 1.0),),
                state=ProviderSourceState.ERROR,
            ),
            _snapshot("codex", (_lane("codex", "weekly", 40.0),)),
        ),
        hidden_providers=frozenset({"claude"}),
    )
    assert lane is not None
    assert lane.provider_id == "codex"


def test_detail_only_lanes_never_bind_and_stale_last_known_good_does() -> None:
    lane = tightest_runway_lane(
        (
            _snapshot("claude", (_lane("claude", "opus_weekly", 3.0, bindable=False),)),
            _snapshot(
                "codex",
                (_lane("codex", "weekly", 55.0),),
                state=ProviderSourceState.STALE,
            ),
        )
    )
    assert lane is not None
    assert lane.provider_id == "codex"


def test_no_percent_anywhere_returns_none() -> None:
    assert tightest_runway_lane(()) is None
    assert (
        tightest_runway_lane(
            (_snapshot("claude", (_lane("claude", "weekly", None),)),)
        )
        is None
    )


def test_runway_state_matches_the_renderer_and_claim_shape() -> None:
    state = runway_state_for_lane(_lane("claude", "weekly", 30.0), color="#D97757")
    assert type(state) is QuotaRunwayState
    # The LED display claim and program factory consume indexes 0 and 1.
    assert state[0] == 0.3
    assert state[1] == "#D97757"
    assert state.provider_label == "Claude"
    assert state.lane_label == "Weekly"
    assert state.remaining_percent == 30.0
    assert state.reset_at == NOW + 3_600.0
    # And the renderer accepts the pair directly.
    from sidepulse.led_status import quota_runway_program

    program = quota_runway_program(state[0], led_count=8, brightness=255, color=state[1])
    assert "repeat" in program
    assert ":off" not in program


def test_controller_seam_reads_the_jr_plane_and_identity_color() -> None:
    class Colors:
        @staticmethod
        def agent_color(provider_id):
            assert provider_id == "claude"
            return "#D97757"

    class Menu:
        @staticmethod
        def hidden_menu_providers():
            return frozenset({"codex"})

    controller = SimpleNamespace(
        provider_usage_state=SimpleNamespace(
            snapshots=(
                _snapshot("claude", (_lane("claude", "weekly", 25.0),)),
                _snapshot("codex", (_lane("codex", "weekly", 1.0),)),
            )
        ),
        settings=SimpleNamespace(colors=Colors()),
        _usage_menu_settings=lambda: Menu(),
    )
    state = quota_runway_state_for_controller(controller)
    assert state is not None
    assert state.provider_id == "claude"
    assert state[0] == 0.25
    assert state[1] == "#D97757"


def test_controller_seam_returns_none_without_lanes() -> None:
    controller = SimpleNamespace(
        provider_usage_state=SimpleNamespace(snapshots=()),
        settings=SimpleNamespace(colors=None),
        _usage_menu_settings=lambda: None,
    )
    assert quota_runway_state_for_controller(controller) is None


def _instance_policies(*visual_policies) -> ProviderInstancePolicyProjection:
    return ProviderInstancePolicyProjection(
        visual=ProviderInstanceVisualProjection(tuple(visual_policies)),
        retention=ProviderInstanceRetentionProjection(()),
        sharing=ProviderInstanceSharingProjection(()),
        session_action=ProviderInstanceSessionActionProjection(()),
    )


def test_controller_seam_uses_exact_instance_profile_identity() -> None:
    class Colors:
        @staticmethod
        def agent_color(_provider_id):
            return "#D97757"

    controller = SimpleNamespace(
        provider_usage_state=SimpleNamespace(
            snapshots=(
                _snapshot(
                    "claude",
                    (_lane("claude", "weekly", 25.0),),
                    source_instance_id="work",
                ),
            )
        ),
        settings=SimpleNamespace(colors=Colors()),
        _usage_menu_settings=lambda: None,
        _sidepulse_provider_instance_policies=_instance_policies(
            ProviderInstanceVisualPolicy(
                provider_id="claude",
                source_instance_id="work",
                label="Client Claude",
                color_override="#112233",
            )
        ),
    )

    state = quota_runway_state_for_controller(controller)

    assert state is not None
    assert state.source_instance_id == "work"
    assert state.provider_label == "Client Claude"
    assert state.color == "#112233"


def test_controller_seam_keeps_provider_color_without_exact_override() -> None:
    class Colors:
        @staticmethod
        def agent_color(provider_id):
            assert provider_id == "claude"
            return "#D97757"

    controller = SimpleNamespace(
        provider_usage_state=SimpleNamespace(
            snapshots=(
                _snapshot(
                    "claude",
                    (_lane("claude", "weekly", 25.0),),
                    source_instance_id="personal",
                ),
            )
        ),
        settings=SimpleNamespace(colors=Colors()),
        _usage_menu_settings=lambda: None,
        _sidepulse_provider_instance_policies=_instance_policies(
            ProviderInstanceVisualPolicy(
                provider_id="claude",
                source_instance_id="work",
                label="Client Claude",
                color_override="#112233",
            )
        ),
    )

    state = quota_runway_state_for_controller(controller)

    assert state is not None
    assert state.source_instance_id == "personal"
    assert state.provider_label == "Claude"
    assert state.color == "#D97757"
