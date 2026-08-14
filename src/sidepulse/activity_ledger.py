"""Pure, bounded 'what did I miss' activity ledger.

The dropdown answers "what is happening now". After ten minutes away the
owner's actual question is "what CHANGED", and a completion that fired while
he was gone left no trace anywhere: the mailbox shows the session's CURRENT
state, the LEDs already stopped, and the Screen Bar announcement is long
gone. This is the record those three surfaces do not keep.

Three rules the model enforces rather than documents:

* **Sub-agents never appear.** This module never sees them: every producer
  feeds it main-session rows only (``canonical_current_statuses`` already
  drops sub-agents, and ``detect_completion_batch`` additionally holds a
  parent open while its workers run). One main agent fans out to 100+
  workers; a ledger that listed them would be 100 lines of noise per turn.

* **No words, only facts.** The entry carries the session's display name and
  what happened -- never the question text or the assistant's message. The
  announcer surface (Screen Bar / notch) carries WORDS; the ledger is the
  ledger. That is also what keeps this file, which is persisted at rest,
  free of prompt content.

* **Bounded by construction.** ``MAX_ACTIVITY_ENTRIES`` and
  ``MAX_ACTIVITY_LEDGER_BYTES`` are both enforced, the same both-caps
  discipline ``audit._bounded_tail`` uses -- because a line count alone
  cannot bound a file whose rows vary in size. This app has been bitten
  twice by unbounded state; a "recent activity" list is exactly the shape
  that grows forever.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

MAX_ACTIVITY_ENTRIES: Final = 50
# Both caps genuinely bind, which is the whole point of having two. A row is
# ~150 bytes for a short session name and ~450 for a long one with a long
# opaque agent id, so 50 rows is 7 KB or 23 KB depending on nothing the code
# controls. At 16 KiB the count binds for ordinary rows and the byte budget
# binds for fat ones -- exactly the situation audit.py describes, where a
# 4,000-line cap left files anywhere between 1 MB and 22 MB.
MAX_ACTIVITY_LEDGER_BYTES: Final = 16 * 1024
MAX_ACTIVITY_LABEL_LENGTH: Final = 96
MAX_ACTIVITY_SUBJECT_LENGTH: Final = 256
MAX_ACTIVITY_DETAIL_LENGTH: Final = 32
MAX_ACTIVITY_PROVIDER_LENGTH: Final = 32

# Reserved for the document envelope the store wraps these entries in
# ({"version": 1, "last_seen_epoch": ..., "entries": [...]}) plus the JSON
# punctuation between them. The budget below is what the entries themselves
# may occupy, so a ledger that fits here always fits the store's own cap.
_ENVELOPE_RESERVE_BYTES: Final = 512


class ActivityValidationError(ValueError):
    """Activity state failed closed at its pure typed boundary."""


class ActivityKind(str, Enum):
    COMPLETED = "completed"
    ASKED = "asked"
    BLOCKED = "blocked"
    THRESHOLD_CROSSED = "threshold_crossed"


class ActivityRestoreHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


# One phrase per kind, in the owner's words rather than the state machine's.
# "became_idle" is a transition name; "finished" is what he wants to read at
# a glance three hours later.
ACTIVITY_PHRASES: Final[dict[ActivityKind, str]] = {
    ActivityKind.COMPLETED: "finished",
    ActivityKind.ASKED: "asked you",
    ActivityKind.BLOCKED: "hit an error",
    ActivityKind.THRESHOLD_CROSSED: "passed",
}


def _finite_nonnegative(value: object) -> bool:
    return (
        type(value) in {int, float}
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0.0
    )


def _bounded_text(value: object, limit: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= limit
        and value == value.strip()
        and value.isprintable()
    )


def safe_activity_text(value: object, limit: int) -> str:
    """Collapse arbitrary provider text into one bounded printable line.

    Display names arrive from provider hooks and have already been observed
    to carry newlines and control characters. Sanitising here (rather than at
    the menu) means the *persisted* row is safe too, so nothing downstream --
    an export, a log line, a future surface -- has to re-sanitise it.
    """
    if type(value) is not str:
        return ""
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    text = " ".join(printable.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    """One thing that happened, with the name the owner would recognise."""

    kind: ActivityKind
    occurred_at_epoch: float
    label: str
    provider: str
    subject_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if not (
            type(self.kind) is ActivityKind
            and _finite_nonnegative(self.occurred_at_epoch)
            and _bounded_text(self.label, MAX_ACTIVITY_LABEL_LENGTH)
            and _bounded_text(self.provider, MAX_ACTIVITY_PROVIDER_LENGTH)
            and (
                self.subject_id is None
                or _bounded_text(self.subject_id, MAX_ACTIVITY_SUBJECT_LENGTH)
            )
            and (
                self.detail is None
                or _bounded_text(self.detail, MAX_ACTIVITY_DETAIL_LENGTH)
            )
        ):
            raise ActivityValidationError("invalid activity entry")
        object.__setattr__(self, "occurred_at_epoch", float(self.occurred_at_epoch))

    @property
    def identity(self) -> tuple[object, ...]:
        """What makes two rows the same event rather than two events."""
        return (
            self.kind.value,
            self.occurred_at_epoch,
            self.subject_id,
            self.detail,
        )


@dataclass(frozen=True, slots=True)
class ActivityLedger:
    """Newest-first entries plus the watermark that defines "left"."""

    entries: tuple[ActivityEntry, ...] = ()
    last_seen_epoch: float = 0.0

    def __post_init__(self) -> None:
        if not (
            type(self.entries) is tuple
            and all(type(entry) is ActivityEntry for entry in self.entries)
            and _finite_nonnegative(self.last_seen_epoch)
        ):
            raise ActivityValidationError("invalid activity ledger")
        if len(self.entries) > MAX_ACTIVITY_ENTRIES:
            raise ActivityValidationError("activity ledger exceeds entry cap")
        if len({entry.identity for entry in self.entries}) != len(self.entries):
            raise ActivityValidationError("duplicate activity entry")
        ordered = tuple(sorted(self.entries, key=_entry_sort_key))
        if ordered != self.entries:
            object.__setattr__(self, "entries", ordered)
        object.__setattr__(self, "last_seen_epoch", float(self.last_seen_epoch))

    @property
    def unseen(self) -> tuple[ActivityEntry, ...]:
        """Entries recorded strictly after the last time the menu was opened."""
        return tuple(
            entry
            for entry in self.entries
            if entry.occurred_at_epoch > self.last_seen_epoch
        )


def _entry_sort_key(entry: ActivityEntry) -> tuple[object, ...]:
    # Newest first, then a total order so two events sharing a timestamp
    # cannot make the rendered list flicker between refreshes.
    return (
        -entry.occurred_at_epoch,
        entry.kind.value,
        entry.subject_id or "",
        entry.detail or "",
        entry.label,
    )


def activity_entry_to_payload(entry: ActivityEntry) -> dict[str, object]:
    if type(entry) is not ActivityEntry:
        raise ActivityValidationError("invalid activity entry")
    return {
        "kind": entry.kind.value,
        "occurred_at_epoch": float(entry.occurred_at_epoch),
        "label": entry.label,
        "provider": entry.provider,
        "subject_id": entry.subject_id,
        "detail": entry.detail,
    }


def entry_encoded_bytes(entry: ActivityEntry) -> int:
    """Exact serialized cost of one entry, in the store's own encoding."""
    return len(
        json.dumps(
            activity_entry_to_payload(entry),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) + 1  # the comma that joins it to the next entry


def bounded_activity_entries(
    entries: tuple[ActivityEntry, ...],
) -> tuple[ActivityEntry, ...]:
    """Newest entries within BOTH the count cap and the byte budget.

    A count cap alone cannot bound this file: a row is ~140 bytes for a short
    session name and ~330 for a long one with a project prefix, so 50 rows is
    anywhere from 7 KB to 17 KB, and a future field would move that again.
    Same reasoning, same shape, as ``audit._bounded_tail``.
    """
    kept = tuple(sorted(entries, key=_entry_sort_key))[:MAX_ACTIVITY_ENTRIES]
    budget = MAX_ACTIVITY_LEDGER_BYTES - _ENVELOPE_RESERVE_BYTES
    total = 0
    for index, entry in enumerate(kept):
        total += entry_encoded_bytes(entry)
        if total > budget:
            # Always keep at least the newest row: an empty section reads as
            # "nothing happened", which is a different and wrong claim.
            return kept[: max(1, index)]
    return kept


def record_activity(ledger: ActivityLedger, entry: ActivityEntry) -> ActivityLedger:
    """Append one fact, newest first, within both bounds. Never duplicates."""
    if type(ledger) is not ActivityLedger or type(entry) is not ActivityEntry:
        raise ActivityValidationError("invalid activity record input")
    if any(existing.identity == entry.identity for existing in ledger.entries):
        return ledger
    return ActivityLedger(
        bounded_activity_entries((entry, *ledger.entries)),
        ledger.last_seen_epoch,
    )


def record_activities(
    ledger: ActivityLedger,
    entries: tuple[ActivityEntry, ...],
) -> ActivityLedger:
    """Fold a batch through ``record_activity`` in one pass."""
    if type(entries) is not tuple:
        raise ActivityValidationError("invalid activity record batch")
    result = ledger
    for entry in entries:
        result = record_activity(result, entry)
    return result


def mark_activity_seen(ledger: ActivityLedger, epoch: float) -> ActivityLedger:
    """Advance the "left" watermark. Monotonic: a clock that steps BACKWARD
    must not resurrect rows the owner has already read."""
    if type(ledger) is not ActivityLedger or not _finite_nonnegative(epoch):
        raise ActivityValidationError("invalid activity seen mark")
    advanced = max(float(epoch), ledger.last_seen_epoch)
    if advanced == ledger.last_seen_epoch:
        return ledger
    return ActivityLedger(ledger.entries, advanced)


def relative_age_label(seconds: float) -> str:
    """"just now" / "4m ago" / "2h ago" / "3d ago".

    Same buckets as the capacity row's ``updated ...`` text two lines further
    down the same menu -- one dropdown should not measure time two ways.
    """
    if not _finite_nonnegative(seconds):
        return "just now"
    age = float(seconds)
    if age < 60.0:
        return "just now"
    if age < 3_600.0:
        return f"{max(1, int(age // 60.0))}m ago"
    if age < 24 * 3_600.0:
        return f"{max(1, int(age // 3_600.0))}h ago"
    return f"{max(1, int(age // (24 * 3_600.0)))}d ago"


def activity_row_text(entry: ActivityEntry, now_epoch: float) -> str:
    """One menu row: what it was, what happened, how long ago."""
    if type(entry) is not ActivityEntry:
        raise ActivityValidationError("invalid activity entry")
    phrase = ACTIVITY_PHRASES[entry.kind]
    if entry.detail:
        phrase = f"{phrase} {entry.detail}"
    age = relative_age_label(max(0.0, float(now_epoch) - entry.occurred_at_epoch))
    return f"{entry.label} · {phrase} · {age}"


__all__ = [
    "ACTIVITY_PHRASES",
    "MAX_ACTIVITY_DETAIL_LENGTH",
    "MAX_ACTIVITY_ENTRIES",
    "MAX_ACTIVITY_LABEL_LENGTH",
    "MAX_ACTIVITY_LEDGER_BYTES",
    "ActivityEntry",
    "ActivityKind",
    "ActivityLedger",
    "ActivityRestoreHealth",
    "ActivityValidationError",
    "activity_entry_to_payload",
    "activity_row_text",
    "bounded_activity_entries",
    "entry_encoded_bytes",
    "mark_activity_seen",
    "record_activities",
    "record_activity",
    "relative_age_label",
    "safe_activity_text",
]
