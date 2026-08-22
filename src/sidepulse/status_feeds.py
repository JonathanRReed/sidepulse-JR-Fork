"""Provider incident feeds: "the provider is down" vs "your fetch broke".

The single biggest false-alarm source for a usage monitor is a provider
incident being read as a local failure. CodexBar's answer, adapted:
poll the public Statuspage summary for each provider that has one, on a
slow cadence, entirely off-main -- and let the usage menu say
"provider incident" next to the provider instead of implying the
user's own setup broke. A feed that cannot be fetched is silence, never
an alarm: the feed is advisory context, not a health source.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass

POLL_SECONDS = 600.0
FETCH_TIMEOUT_SECONDS = 10.0

#: provider_id -> (display name, summary endpoint, human page).
STATUS_FEEDS: dict[str, tuple[str, str, str]] = {
    "claude": (
        "Anthropic",
        "https://status.anthropic.com/api/v2/status.json",
        "https://status.anthropic.com",
    ),
    "codex": (
        "OpenAI",
        "https://status.openai.com/api/v2/status.json",
        "https://status.openai.com",
    ),
    "cursor": (
        "Cursor",
        "https://status.cursor.com/api/v2/status.json",
        "https://status.cursor.com",
    ),
}


@dataclass(frozen=True, slots=True)
class ProviderIncident:
    provider_id: str
    vendor: str
    indicator: str
    description: str
    page_url: str


def parse_statuspage_indicator(payload: object) -> tuple[str, str] | None:
    """(indicator, description) from a Statuspage v2 status document.

    Returns None for anything unexpected -- an unparseable feed is
    silence. "none" is a valid, healthy indicator and IS returned;
    callers decide that healthy means no badge.
    """
    if type(payload) is not dict:
        return None
    status = payload.get("status")
    if type(status) is not dict:
        return None
    indicator = status.get("indicator")
    description = status.get("description")
    if type(indicator) is not str or not indicator:
        return None
    return indicator, str(description or "")


def incidents_from_documents(
    documents: dict[str, object],
) -> dict[str, ProviderIncident]:
    """Active incidents only: healthy feeds produce no entry."""
    incidents: dict[str, ProviderIncident] = {}
    for provider_id, payload in documents.items():
        feed = STATUS_FEEDS.get(provider_id)
        if feed is None:
            continue
        parsed = parse_statuspage_indicator(payload)
        if parsed is None:
            continue
        indicator, description = parsed
        if indicator == "none":
            continue
        incidents[provider_id] = ProviderIncident(
            provider_id=provider_id,
            vendor=feed[0],
            indicator=indicator,
            description=description or indicator.replace("_", " "),
            page_url=feed[2],
        )
    return incidents


def incident_row_title(incident: ProviderIncident) -> str:
    return f"⚠ {incident.vendor}: {incident.description} — status page…"


class StatusFeedPoller:
    """Slow background poll of every feed; readers only touch a dict."""

    def __init__(self) -> None:
        self._incidents: dict[str, ProviderIncident] = {}
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(
            target=self._loop, name="SidePulseStatusFeeds", daemon=True
        ).start()

    def current(self) -> dict[str, ProviderIncident]:
        with self._lock:
            return dict(self._incidents)

    def _loop(self) -> None:
        while True:
            documents: dict[str, object] = {}
            for provider_id, (_vendor, endpoint, _page) in STATUS_FEEDS.items():
                try:
                    request = urllib.request.Request(
                        endpoint, headers={"User-Agent": "SidePulse"}
                    )
                    with urllib.request.urlopen(
                        request, timeout=FETCH_TIMEOUT_SECONDS
                    ) as response:
                        documents[provider_id] = json.loads(
                            response.read(65_536).decode("utf-8", "replace")
                        )
                except Exception:
                    continue
            fresh = incidents_from_documents(documents)
            with self._lock:
                # Merge per provider: a fetch hiccup (absent from
                # documents) KEEPS the prior incident -- a network blip
                # must never read as "incident resolved". Only a
                # successful healthy fetch clears a provider's entry.
                merged = dict(self._incidents)
                for provider_id, payload in documents.items():
                    parsed = parse_statuspage_indicator(payload)
                    if parsed is None:
                        continue
                    if provider_id in fresh:
                        merged[provider_id] = fresh[provider_id]
                    else:
                        merged.pop(provider_id, None)
                self._incidents = merged
            time.sleep(POLL_SECONDS)


_shared_poller: StatusFeedPoller | None = None
_shared_lock = threading.Lock()


def shared_status_feed_poller() -> StatusFeedPoller:
    global _shared_poller
    with _shared_lock:
        if _shared_poller is None:
            _shared_poller = StatusFeedPoller()
        return _shared_poller


__all__ = [
    "STATUS_FEEDS",
    "ProviderIncident",
    "StatusFeedPoller",
    "incident_row_title",
    "incidents_from_documents",
    "parse_statuspage_indicator",
    "shared_status_feed_poller",
]
