"""Incident feeds are advisory context: silence on anything unexpected."""

from __future__ import annotations

from sidepulse.status_feeds import (
    STATUS_FEED_THREAD_NAME,
    FeedState,
    StatusFeedPoller,
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
    assert (
        parse_statuspage_indicator(
            {"status": {"indicator": "surprising", "description": "unknown"}}
        )
        is None
    )


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


def test_poller_exposes_only_fresh_confirmed_incidents() -> None:
    now = {"value": 100.0}
    documents = iter(
        (
            {"status": {"indicator": "major", "description": "API errors"}},
            OSError("offline"),
        )
    )
    calls: list[tuple[str, dict[str, str], float, int]] = []

    def fetch(url, *, headers, timeout, max_bytes):
        calls.append((url, headers, timeout, max_bytes))
        result = next(documents)
        if isinstance(result, Exception):
            raise result
        return result

    poller = StatusFeedPoller(
        feeds={"codex": ("OpenAI", "https://status.example/api", "https://status.example")},
        fetch_json=fetch,
        clock=lambda: now["value"],
        freshness_seconds=60.0,
    )

    poller.poll_once()
    incident = poller.incident_for("codex", now=100.0)
    assert incident is not None
    assert incident.description == "API errors"
    assert incident.source_url == "https://status.example/api"
    assert incident.observed_at == 100.0
    assert poller.feed_state("codex", now=100.0) is FeedState.CONFIRMED_INCIDENT
    assert calls == [
        (
            "https://status.example/api",
            {"Accept": "application/json", "User-Agent": "JR-Bar/status-feed"},
            10.0,
            65_536,
        )
    ]

    now["value"] = 101.0
    poller.poll_once()
    assert poller.incident_for("codex", now=101.0) is None
    assert poller.feed_state("codex", now=101.0) is FeedState.UNAVAILABLE


def test_poller_distinguishes_healthy_and_stale_feeds() -> None:
    now = {"value": 200.0}
    poller = StatusFeedPoller(
        feeds={"claude": ("Anthropic", "https://status.example/api", "https://status.example")},
        fetch_json=lambda *_args, **_kwargs: {
            "status": {"indicator": "none", "description": "All systems operational"}
        },
        clock=lambda: now["value"],
        freshness_seconds=30.0,
    )

    poller.poll_once()
    assert poller.feed_state("claude", now=200.0) is FeedState.NO_INCIDENT
    assert poller.incident_for("claude", now=200.0) is None

    now["value"] = 231.0
    assert poller.feed_state("claude", now=231.0) is FeedState.STALE


def test_status_feed_worker_uses_current_product_identity_and_scoped_start(
    monkeypatch,
) -> None:
    started: list[tuple[str, bool]] = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            del target
            started.append((name, daemon))

        def start(self):
            return None

    monkeypatch.setattr("sidepulse.status_feeds.threading.Thread", Thread)

    poller = StatusFeedPoller()
    poller.start(provider_ids=("codex",))
    poller.start(provider_ids=("codex",))

    assert STATUS_FEED_THREAD_NAME == "JR-BarStatusFeeds"
    assert started == [("JR-BarStatusFeeds-codex", True)]


def test_scoped_poll_fetches_only_requested_provider() -> None:
    fetched: list[str] = []
    poller = StatusFeedPoller(
        feeds={
            "codex": ("OpenAI", "https://status.example/codex", "https://status.example"),
            "claude": (
                "Anthropic",
                "https://status.example/claude",
                "https://status.example",
            ),
        },
        fetch_json=lambda url, **_kwargs: (
            fetched.append(url)
            or {"status": {"indicator": "none", "description": "Healthy"}}
        ),
    )

    poller.poll_once(provider_ids=("codex",))

    assert fetched == ["https://status.example/codex"]
    assert poller.feed_state("claude") is FeedState.UNAVAILABLE
