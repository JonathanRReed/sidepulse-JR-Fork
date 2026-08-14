"""Claude plan limits: the four switches, and the order they opened in.

Four independent switches each made Claude limits unreachable:

  1. `fetch_windows()` unconditionally raised.
  2. `with_claude_plan_limits_enabled()` deleted its argument and wrote False.
  3. Claude declared no `remote_quota_windows` source.
  4. The refresh coordinator registered Claude with `enabled=False`.

All four are open now, and the order matters: (3) came first. The root
declaration -- `provider_capacity.ProviderCapacityPolicy("anthropic-consumer",
...)` -- now names four real lanes (5-hour, weekly, and the weekly Opus and
Sonnet sub-caps) against an OFFICIAL_API source, so
`capacity_authority.select_binding_lanes` finally has something to authorise.
Only then could (2) honour its argument and (4) follow the setting.

What did NOT open: the switch is still off by default and the policy is
opt_in_required, because reading it means presenting the user's own
subscription credential. And no raw percentage gained a consumer --
`test_capacity_consumer_authority.py` still pins that half.

This file pins the whole chain: the adapter works, the lanes are declared,
the source negotiates, the toggle is real, and the coordinator asks the
setting rather than a provider name.
"""

from __future__ import annotations

import io
import json

import pytest

from sidepulse import claude_quota
from sidepulse.capacity_types import CapacityEvidenceClass, QuotaEffect
from sidepulse.provider_capacity import CapacityPolicyState
from sidepulse.settings import AgentMonitorSettings


# --------------------------------------------------------------------------
# What now works: the adapter itself.
# --------------------------------------------------------------------------


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_fetch_is_no_longer_a_stub() -> None:
    """Switch 1: it used to raise no matter what it was given."""
    payload = {
        "five_hour": {"utilization": 10.0},
        "seven_day": {"utilization": 20.0},
        "seven_day_opus": {"utilization": 88.0},
    }
    windows = claude_quota.fetch_windows(
        access_token="tok",
        opener=lambda request, timeout: _Response(json.dumps(payload).encode()),
    )
    assert [window["label"] for window in windows] == ["5-hour", "weekly", "Opus only"]


def test_the_credential_path_is_hardened() -> None:
    """Reading a credential must never happen on a background timer."""
    from sidepulse.credentials import CLAUDE_CODE_KEYCHAIN, read_keychain_secret

    result = read_keychain_secret(
        CLAUDE_CODE_KEYCHAIN,
        allow_prompt=False,
        runner=lambda item: pytest.fail("background read reached the Keychain"),
    )
    assert result.secret is None


# --------------------------------------------------------------------------
# The four switches, in the order they had to open.
# --------------------------------------------------------------------------


def test_consumer_claude_declares_every_window_it_can_observe() -> None:
    """The root switch. Everything downstream follows from this declaration.

    A policy with zero lanes gives the authority layer nothing to reason
    about, so opening any other switch first would have wired the UI to raw
    provider percentages. Every window the endpoint reports is named here,
    including both per-model weekly sub-caps -- an Opus ceiling the owner
    cannot see is the one that stops their work without warning.
    """
    from sidepulse.provider_capacity import provider_capacity_policies

    consumer = next(
        policy
        for policy in provider_capacity_policies()
        if policy.capacity_profile_id == "anthropic-consumer"
    )
    assert consumer.state is CapacityPolicyState.OBSERVABLE
    assert consumer.evidence_class is CapacityEvidenceClass.OFFICIAL_API
    assert tuple(
        (lane.semantic_name, lane.window, lane.model, lane.effect, lane.bindable)
        for lane in consumer.lanes
    ) == (
        ("5-hour", "five-hour", None, QuotaEffect.ALL_WORKLOADS, True),
        ("Weekly", "weekly", None, QuotaEffect.ALL_WORKLOADS, True),
        ("Weekly Opus", "weekly", "opus", QuotaEffect.MODEL, True),
        ("Weekly Sonnet", "weekly", "sonnet", QuotaEffect.MODEL, True),
    )


def test_the_toggle_is_real_but_still_starts_off() -> None:
    """Switch 2. The gate is no longer a lie, and no longer on by surprise."""
    assert AgentMonitorSettings().claude_plan_limits_enabled is False
    enabled = AgentMonitorSettings().with_claude_plan_limits_enabled(True)
    assert enabled.claude_plan_limits_enabled is True
    assert enabled.with_claude_plan_limits_enabled(False).claude_plan_limits_enabled is False
    # An opt-in that does not survive a save is not an opt-in.
    assert enabled.to_dict()["claude_plan_limits_enabled"] is True


def test_claude_declares_exactly_one_negotiated_capacity_source() -> None:
    """Switch 3. Two eligible sources for one provider is a scheduling bug."""
    from sidepulse.providers import negotiated_provider_sources

    capacity_rows = tuple(
        row
        for row in negotiated_provider_sources()
        if row.source_key.capability_id == "remote_quota_windows"
        and row.observation_invocation_allowed
    )
    assert tuple(row.source_key.provider_id for row in capacity_rows) == (
        "codex",
        "claude",
    )
    claude_row = capacity_rows[1]
    # `oauth`, not `local`: sharing an instance id with claude/transcripts
    # would make an OAuth 401 back off local transcript reads too.
    assert claude_row.source_key.adapter_id == "quota"
    assert claude_row.source_key.source_instance_id == "oauth"


def test_the_coordinator_follows_the_setting_rather_than_a_hardcoded_name() -> None:
    """Switch 4 was `enabled=provider_id != "claude"`, ignoring the user.

    It now reads the setting. The setting still fails closed, so the
    behaviour is unchanged today -- but the moment the policy above declares
    lanes, this stops being a special case that has to be remembered.
    """
    from sidepulse.status_bar import StatusBarController

    class _Probe:
        settings = AgentMonitorSettings()

    decide = StatusBarController._capacity_source_enabled
    assert decide(_Probe(), "claude") is False
    assert decide(_Probe(), "codex") is True


def test_the_scheduler_follows_the_setting_too() -> None:
    """Switch 4's other half: the per-poll `enabled` recompute.

    `_capacity_source_enabled` was already honest, but the scheduler
    recomputed visibility from `provider_id != "claude"` on every menu open
    and every refresh plan, which quietly overrode it. Both sides have to ask
    the setting or the opt-in still does nothing.
    """
    from sidepulse.status_bar import StatusBarController

    class _Probe:
        def __init__(self, settings) -> None:
            self.settings = settings

        _capacity_source_enabled = StatusBarController._capacity_source_enabled

    row_enabled = StatusBarController._capacity_row_enabled
    off = _Probe(AgentMonitorSettings())
    on = _Probe(AgentMonitorSettings().with_claude_plan_limits_enabled(True))

    assert row_enabled(off, "claude") is False
    assert row_enabled(on, "claude") is True
    # Codex keeps its own display preference, which is a separate knob with a
    # separate default: a local read that is on unless the user hides it.
    assert row_enabled(on, "codex") is True
    assert (
        row_enabled(
            _Probe(AgentMonitorSettings().with_codex_percent_enabled(False)),
            "codex",
        )
        is False
    )
