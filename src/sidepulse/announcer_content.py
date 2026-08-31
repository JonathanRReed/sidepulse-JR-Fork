"""Pure content projection for the Screen Bar announcer pill."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

ANNOUNCER_NAME_CAP = 40
ANNOUNCER_QUESTION_CAP = 80
ANNOUNCER_TEXT_CAP = 140
_VISIBLE_ADDITIONAL_COUNT_CAP = 99


class _StatusWithMessage(Protocol):
    message: str | None


class ActionableAttentionRow(Protocol):
    agent_id: str
    provider: str
    display_name: str
    source_status: _StatusWithMessage


@dataclass(frozen=True, slots=True)
class AnnouncerContent:
    """The immutable words and routing identity for one announcer pill."""

    text: str
    total_actionable_count: int
    primary_agent_id: str


def _single_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _additional_asks_suffix(additional_count: int) -> str:
    if additional_count <= 0:
        return ""
    if additional_count > _VISIBLE_ADDITIONAL_COUNT_CAP:
        visible_count = f"{_VISIBLE_ADDITIONAL_COUNT_CAP}+"
    else:
        visible_count = str(additional_count)
    noun = "ask" if additional_count == 1 else "asks"
    return f" · {visible_count} more {noun}"


def project_announcer_content(
    actionable_rows: Iterable[ActionableAttentionRow],
) -> AnnouncerContent | None:
    """Project one bounded line from already-actionable attention rows."""

    rows = tuple(actionable_rows)
    if not rows:
        return None

    primary = rows[0]
    name = _single_line(primary.display_name)[:ANNOUNCER_NAME_CAP]
    if not name:
        name = (_single_line(primary.provider).title() or "Agent")[
            :ANNOUNCER_NAME_CAP
        ]
    question = _single_line(primary.source_status.message)[:ANNOUNCER_QUESTION_CAP]
    primary_text = f"{name} — {question}" if question else f"{name} needs you"
    text = f"{primary_text}{_additional_asks_suffix(len(rows) - 1)}"

    return AnnouncerContent(
        text=text[:ANNOUNCER_TEXT_CAP],
        total_actionable_count=len(rows),
        primary_agent_id=primary.agent_id,
    )


__all__ = [
    "ANNOUNCER_NAME_CAP",
    "ANNOUNCER_QUESTION_CAP",
    "ANNOUNCER_TEXT_CAP",
    "ActionableAttentionRow",
    "AnnouncerContent",
    "project_announcer_content",
]
