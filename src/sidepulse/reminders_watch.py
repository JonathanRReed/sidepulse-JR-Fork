"""EventKit Reminders access for the "glow when a reminder comes due"
signal -- calendar_watch's sibling, with one structural difference:
EventKit fetches reminders ASYNCHRONOUSLY (fetchRemindersMatchingPredicate_
completion_), so ``fetch_due`` takes a completion callback invoked on an
arbitrary EventKit queue; callers dispatch back to the main thread.

Same failure contract as calendar_watch: anything that can't work right
now raises (or quietly skips, on the async path) -- never an app error.
The bundle already ships NSRemindersFullAccessUsageDescription, so the
permission prompt is presentable without another re-sign.
"""

from __future__ import annotations

AUTH_AUTHORIZED = "authorized"
AUTH_DENIED = "denied"
AUTH_NOT_DETERMINED = "not_determined"


class RemindersUnavailableError(RuntimeError):
    """EventKit reminders can't be used right now; treat as no signal."""


_store = None


def _eventkit():
    try:
        import EventKit

        return EventKit
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RemindersUnavailableError(str(exc)) from exc


def _shared_store():
    global _store
    eventkit = _eventkit()
    if _store is None:
        _store = eventkit.EKEventStore.alloc().init()
    return _store


def authorization_status() -> str:
    eventkit = _eventkit()
    try:
        status = int(
            eventkit.EKEventStore.authorizationStatusForEntityType_(
                eventkit.EKEntityTypeReminder
            )
        )
    except Exception as exc:
        raise RemindersUnavailableError(str(exc)) from exc
    if status == 3:
        return AUTH_AUTHORIZED
    if status == 0:
        return AUTH_NOT_DETERMINED
    return AUTH_DENIED


def request_access(completion) -> None:
    """Presents the system Reminders prompt; ``completion(granted)`` is
    called on an arbitrary queue."""
    eventkit = _eventkit()
    store = _shared_store()

    def _handler(granted, _error):
        try:
            completion(bool(granted))
        except Exception:
            pass

    if hasattr(store, "requestFullAccessToRemindersWithCompletion_"):
        store.requestFullAccessToRemindersWithCompletion_(_handler)
    else:  # pragma: no cover - pre-macOS 14 fallback
        store.requestAccessToEntityType_completion_(
            eventkit.EKEntityTypeReminder, _handler
        )


def fetch_due(lookback_seconds: float, completion) -> None:
    """Async: ``completion(items)`` where items is a list of
    (calendar_item_identifier, title) for INCOMPLETE reminders whose due
    date fell inside [now - lookback, now]. Called on an EventKit queue;
    on any setup failure this raises synchronously instead."""
    from Foundation import NSDate

    if authorization_status() != AUTH_AUTHORIZED:
        raise RemindersUnavailableError("reminders access not granted")
    store = _shared_store()
    start = NSDate.dateWithTimeIntervalSinceNow_(-max(0.0, float(lookback_seconds)))
    end = NSDate.date()

    def _handler(reminders):
        items: list[tuple[str, str]] = []
        for reminder in reminders or []:
            try:
                identifier = str(reminder.calendarItemIdentifier() or "")
                title = str(reminder.title() or "Reminder")
            except Exception:
                continue
            if identifier:
                items.append((identifier, title))
        try:
            completion(items)
        except Exception:
            pass

    try:
        predicate = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
            start, end, None
        )
        store.fetchRemindersMatchingPredicate_completion_(predicate, _handler)
    except Exception as exc:
        raise RemindersUnavailableError(str(exc)) from exc
