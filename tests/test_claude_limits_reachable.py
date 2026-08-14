"""Claude plan limits: what is built, and what is deliberately still closed.

Four independent switches each made Claude limits unreachable:

  1. `fetch_windows()` unconditionally raised.
  2. `with_claude_plan_limits_enabled()` deleted its argument and wrote False.
  3. Claude declared no `remote_quota_windows` source.
  4. The refresh coordinator registered Claude with `enabled=False`.

Only (1) is fixed. The other three are held closed by an architecture, not
by an oversight: `test_capacity_consumer_authority.py` asserts that a
capacity reading may only reach a consumer through
`capacity_authority.select_binding_lanes`, which refuses stale,
model-inapplicable and unknown-source evidence. And the root declaration --
`provider_capacity.ProviderCapacityPolicy("anthropic-consumer", ...)` -- says
consumer Claude subscriptions have UNSUPPORTED evidence and zero lanes, so
there is nothing for that layer to authorise.

Opening the switches without declaring those lanes would wire user-visible
effects to raw provider percentages, which is exactly how a false 95% ends
up blinking the hardware.

This file pins both halves: the adapter genuinely works, and the gate
genuinely holds.
"""

from __future__ import annotations

import io
import json

import pytest

from sidepulse import claude_quota
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
# What is deliberately still closed, and why.
# --------------------------------------------------------------------------


def test_consumer_claude_declares_no_capacity_lanes_yet() -> None:
    """The root switch. Everything downstream follows from this declaration.

    When this policy gains real lanes and an OBSERVABLE state, the toggle,
    the source registration and the coordinator can all open honestly -- and
    this test is the one that should be updated first.
    """
    from sidepulse.provider_capacity import provider_capacity_policies

    consumer = next(
        policy
        for policy in provider_capacity_policies()
        if policy.capacity_profile_id == "anthropic-consumer"
    )
    assert consumer.state is CapacityPolicyState.UNSUPPORTED
    assert consumer.lanes == ()


def test_the_toggle_still_fails_closed() -> None:
    """Switch 2. Deliberate: there are no authorised lanes to show."""
    settings = AgentMonitorSettings().with_claude_plan_limits_enabled(True)
    assert settings.claude_plan_limits_enabled is False


def test_claude_declares_no_capacity_source_yet() -> None:
    """Switch 3. A registration without declared lanes cannot be authorised."""
    from sidepulse.providers import negotiated_provider_sources

    sources = {
        (row.source_key.provider_id, row.source_key.capability_id)
        for row in negotiated_provider_sources()
    }
    assert ("codex", "remote_quota_windows") in sources
    assert ("claude", "remote_quota_windows") not in sources


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
