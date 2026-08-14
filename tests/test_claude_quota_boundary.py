from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse import claude_quota
from sidepulse.capacity_types import SourceHealthKind


def _unexpected_boundary(*_args, **_kwargs):
    raise AssertionError("trusted Claude quota normalization crossed an I/O boundary")


def test_remote_claude_quota_fails_closed_without_credentials_or_network() -> None:
    with (
        patch.object(Path, "read_text", side_effect=_unexpected_boundary),
        patch.object(subprocess, "run", side_effect=_unexpected_boundary),
        patch.object(urllib.request, "urlopen", side_effect=_unexpected_boundary),
    ):
        with pytest.raises(
            claude_quota.ClaudeQuotaUnavailableError,
            match="claude_remote_quota_unsupported",
        ):
            claude_quota.fetch_windows()


def test_remote_claude_quota_has_no_hidden_access_token_route() -> None:
    with (
        patch.object(Path, "read_text", side_effect=_unexpected_boundary),
        patch.object(subprocess, "run", side_effect=_unexpected_boundary),
        patch.object(urllib.request, "urlopen", side_effect=_unexpected_boundary),
    ):
        with pytest.raises(TypeError):
            claude_quota.fetch_windows(token="must-not-be-accepted")


def test_explicit_claude_evidence_normalization_is_pure_and_bounded() -> None:
    payload = {
        "five_hour": {"utilization": 25, "resets_at": "2026-08-12T18:00:00Z"},
        "seven_day": {"utilization": 50},
        "limits": [
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 75,
                "scope": {"model": {"id": "claude-opus", "display_name": "Opus"}},
            }
        ],
    }
    with (
        patch.object(Path, "read_text", side_effect=_unexpected_boundary),
        patch.object(subprocess, "run", side_effect=_unexpected_boundary),
        patch.object(urllib.request, "urlopen", side_effect=_unexpected_boundary),
    ):
        windows = claude_quota.windows_from_payload(payload)
        health = claude_quota.unsupported_source_health(observed_at=100.0)

    assert [window["label"] for window in windows] == [
        "5-hour",
        "weekly",
        "Opus only",
    ]
    assert health.kind is SourceHealthKind.UNSUPPORTED
    assert health.reason_code == "claude_remote_quota_unsupported"


def test_untrusted_claude_labels_and_unbounded_lanes_are_not_forwarded() -> None:
    raw_label = "private-account@example.com/" + "x" * 200
    payload = {
        "limits": [
            {
                "utilization": index,
                "name": raw_label,
                "scope": {"model": {"display_name": raw_label}},
            }
            for index in range(100)
        ]
    }

    windows = claude_quota.windows_from_payload(payload)

    assert len(windows) <= 32
    assert all(raw_label not in window["label"] for window in windows)
