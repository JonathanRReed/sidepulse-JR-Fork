from __future__ import annotations

import io
import json
from pathlib import Path

from sidepulse import provider_usage_cli
from sidepulse.provider_usage_platform import (
    ProviderSourceState,
    ProviderUsageSnapshot,
)
from sidepulse.provider_usage_runtime import ProviderUsageState


class Credentials:
    def __init__(self):
        self.values = {}

    def set(self, provider, account, secret):
        self.values[(provider, account)] = secret

    def get(self, provider, account):
        value = self.values.get((provider, account))
        return type(
            "Read",
            (),
            {
                "available": value is not None,
                "secret": value,
                "reason": None if value is not None else "credential_not_found",
            },
        )()

    def delete(self, provider, account):
        return self.values.pop((provider, account), None) is not None


def empty_snapshot(provider, state, action=None):
    return ProviderUsageSnapshot(
        provider_id=provider,
        account_label=None,
        observed_at=1000,
        state=state,
        reason_code=None if state in {ProviderSourceState.READY, ProviderSourceState.DISABLED} else "fixture_reason",
        action_label=action,
        lanes=(),
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        model_count=0,
        estimated_cost_usd=None,
        cache_savings_usd=None,
        credits_remaining=None,
        incident=None,
    )


def test_status_json_explains_actionable_provider_state(tmp_path: Path):
    state = ProviderUsageState(
        (
            empty_snapshot(
                "claude",
                ProviderSourceState.NEEDS_CONSENT,
                "Connect Claude usage",
            ),
        ),
        1000,
        1100,
        False,
    )
    output = io.StringIO()
    code = provider_usage_cli.main(
        ["status", "--json"],
        stdout=output,
        home=tmp_path,
        state_loader=lambda: state,
    )
    document = json.loads(output.getvalue())
    assert code == 0
    assert document["providers"][0]["state"] == "needs_consent"
    assert document["providers"][0]["action"] == "Connect Claude usage"
    assert "no reading" not in output.getvalue().lower()


def test_enable_disable_and_configuration_round_trip(tmp_path: Path):
    settings_path = tmp_path / "provider-usage.json"
    output = io.StringIO()
    assert provider_usage_cli.main(
        ["disable", "grok"],
        stdout=output,
        home=tmp_path,
        settings_path=settings_path,
    ) == 0
    assert provider_usage_cli.main(
        [
            "configure",
            "devin",
            "--browser-sources",
            "on",
            "--threshold-remaining",
            "15",
            "--option",
            "organization=org_fixture",
        ],
        stdout=output,
        home=tmp_path,
        settings_path=settings_path,
    ) == 0
    loaded = provider_usage_cli.load_provider_usage_settings(settings_path).settings
    assert loaded.preference("grok").enabled is False
    assert loaded.preference("devin").browser_sources is True
    assert loaded.preference("devin").threshold_remaining == 15
    assert loaded.preference("devin").option("organization") == "org_fixture"


def test_credential_set_reads_stdin_and_never_echoes_secret(tmp_path: Path):
    credentials = Credentials()
    output = io.StringIO()
    secret = "fixture-provider-session-long"
    code = provider_usage_cli.main(
        ["credential", "set", "openai-api", "admin-key", "--stdin"],
        stdin=io.StringIO(secret),
        stdout=output,
        home=tmp_path,
        credentials=credentials,
    )
    assert code == 0
    assert credentials.values[("openai-api", "admin-key")] == secret
    assert secret not in output.getvalue()


def test_browser_consent_grant_is_exact_and_provider_scoped(tmp_path: Path):
    consent_path = tmp_path / "browser-consent.json"
    output = io.StringIO()
    code = provider_usage_cli.main(
        [
            "browser-consent",
            "grant",
            "devin",
            "--browser",
            "chrome",
            "--profile",
            "Default",
        ],
        stdout=output,
        home=tmp_path,
        consent_path=consent_path,
        clock=lambda: 1000,
    )
    assert code == 0
    loaded = provider_usage_cli.load_browser_consents(consent_path).store
    assert loaded.allows(
        provider_id="devin",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )
    assert not loaded.allows(
        provider_id="cursor",
        browser="chrome",
        profile="Default",
        domain="app.devin.ai",
        field="auth1_session",
    )


def test_refresh_uses_native_service_and_persists_result(tmp_path: Path):
    state = ProviderUsageState(
        (empty_snapshot("codex", ProviderSourceState.READY),),
        1000,
        1100,
        False,
    )

    class Service:
        def refresh_now(self, **_kwargs):
            return state

        def close(self):
            pass

    saved = []
    output = io.StringIO()
    code = provider_usage_cli.main(
        ["refresh", "codex", "--json"],
        stdout=output,
        home=tmp_path,
        service_factory=lambda **_kwargs: Service(),
        state_saver=saved.append,
    )
    assert code == 0
    assert saved == [state]
    assert json.loads(output.getvalue())["providers"][0]["provider_id"] == "codex"
