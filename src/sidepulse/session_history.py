"""Per-day session counts from the agent-monitor event ledgers.

"Why does the graph only have Claude and Codex?" -- because tokens and
cost genuinely exist only in those two CLIs' local transcripts. But
SESSIONS exist for every provider SidePulse watches: the hook ledgers
under agent-monitor/ record a `session_start` event with a timestamp
and a work id for grok, devin, and any other hook-emitting provider.
This module turns those ledgers into the same day-bucketed counts the
transcript scanner produces, so the sessions metric can chart the whole
fleet.

Bounded and defensive: ledgers are append-only JSONL that other code
trims; a torn last line or a foreign record is skipped, never fatal.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

#: Providers whose sessions already come from their own transcripts --
#: the ledger must not double-count them.
TRANSCRIPT_SESSION_PROVIDERS = frozenset({"claude", "codex"})

_MAX_LEDGER_BYTES = 8 * 1024 * 1024


def ledger_session_days(
    state_dir: Path,
    *,
    since_epoch: float,
    provider_ids: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    """{provider_id: {ISO day: distinct session_start count}}.

    Distinctness is by provider_work_id per day, so a replayed or
    duplicated event never inflates the chart.
    """
    root = Path(state_dir)
    results: dict[str, dict[str, int]] = {}
    for provider_id in provider_ids:
        if provider_id in TRANSCRIPT_SESSION_PROVIDERS:
            continue
        path = root / f"{provider_id}.jsonl"
        try:
            if not path.is_file() or path.stat().st_size > _MAX_LEDGER_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen: dict[str, set[str]] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                not isinstance(event, dict)
                or event.get("event_name") != "session_start"
                or event.get("provider_id") != provider_id
            ):
                continue
            occurred = event.get("occurred_at_epoch")
            if (
                isinstance(occurred, bool)
                or not isinstance(occurred, (int, float))
                or occurred < since_epoch
            ):
                continue
            day = datetime.fromtimestamp(float(occurred)).strftime("%Y-%m-%d")
            work_id = str(
                event.get("provider_work_id")
                or event.get("event_token")
                or f"line:{len(seen.get(day, ()))}"
            )
            seen.setdefault(day, set()).add(work_id)
        counts = {day: len(ids) for day, ids in seen.items() if ids}
        if counts:
            results[provider_id] = counts
    return results


__all__ = ["TRANSCRIPT_SESSION_PROVIDERS", "ledger_session_days"]
