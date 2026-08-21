"""Incident feeds are advisory context: silence on anything unexpected."""

from __future__ import annotations

from sidepulse.status_feeds import (
    incident_row_title,
    incidents_from_documents,
    parse_statuspage_indicator,
)


def test_parse_accepts_statuspage_shape_only() -> None:
    good = {"status": {"indicator": "major", "description": "Elevated errors"}}
    assert parse_statuspage_indicator(good) == ("major", "Elevated errors")
    assert parse_statuspage_indicator({"status": {}}) is None
    assert parse_statuspage_indicator({"weird": True}) is None
    assert parse_statuspage_indicator("not a dict") is None


def test_healthy_feeds_produce_no_incident() -> None:
    documents = {
        "claude": {"status": {"indicator": "none", "description": "All good"}},
        "codex": {"status": {"indicator": "minor", "description": "API errors"}},
        "unknown-provider": {"status": {"indicator": "major", "description": "x"}},
        "cursor": {"broken": True},
    }
    incidents = incidents_from_documents(documents)
    assert set(incidents) == {"codex"}
    assert incidents["codex"].vendor == "OpenAI"
    assert "OpenAI" in incident_row_title(incidents["codex"])
    assert incidents["codex"].page_url.startswith("https://")
