"""Nothing expensive, blocking or re-entrant runs on a menu-bar timer.

Reported live: "it's super laggy at times." Measured with `sample` on the
running app -- 100.7% CPU sustained, and 65.7% of the main thread inside
``__CFRunLoopDoTimers``. Two callers owned most of it:

  422 -[NSMenuItem accessibilityLabel]
  422   NSAccessibilityGetObjectForAttributeUsingLegacyAPI
  377     -[NSMenu(Accessibility) _openForInspection:]
  377       -[NSMenu _simulateOpening:]
  377         -[NSMenu _sendAndRecordMenuOpeningNotification]   <- menuWillOpen_
   45       -[NSMenu _sendMenuClosedNotification:]              <- menuDidClose_

  636 __NSFireTimer  (is_alcove_running)
   13   -[NSRunningApplication bundleIdentifier]
   10     _LSCopyApplicationInformation
   10       xpc_connection_send_message_with_reply_sync

Reading an accessibility label made AppKit fake-open the whole menu and
run this app's own delegates -- 15 fsync calls to USB mass storage on the
main thread in an 8 second window, with menuDidClose_ able to re-enter
update_status_menu. The Alcove probe made one blocking LaunchServices XPC
round trip per running app, on a 2s timer, behind a 3s TTL that did not
cover its own cadence.
"""

from __future__ import annotations

import threading
import time

from sidepulse import status_bar, virtual_device


class _RecordingMenuItem:
    """An NSMenuItem stand-in that screams if anyone reads accessibility."""

    def __init__(self, title: str) -> None:
        self._title = title
        self.accessibility_reads: list[str] = []

    def title(self):
        return self._title

    def keyEquivalent(self):
        return ""

    def isEnabled(self):
        return True

    def state(self):
        return 0

    def submenu(self):
        return None

    def accessibilityLabel(self):
        self.accessibility_reads.append("accessibilityLabel")
        return "read back from AppKit"

    def accessibilityValue(self):
        self.accessibility_reads.append("accessibilityValue")
        return "read back from AppKit"

    def accessibilityHelp(self):
        self.accessibility_reads.append("accessibilityHelp")
        return "read back from AppKit"


def test_building_the_menu_snapshot_never_reads_accessibility_off_an_item() -> None:
    """Any read here fake-opens the menu and runs menuWillOpen_."""
    item = _RecordingMenuItem("Agent Mailbox · 3 active · 0 need you")

    state = status_bar._native_item_state(
        item,
        item_key="agent-mailbox:summary",
        parent_key=None,
        order=0,
        submenu_key=None,
        action_kind=None,
    )

    assert item.accessibility_reads == []
    assert state.accessibility_label == "Agent Mailbox summary"


def test_every_root_item_kind_still_gets_a_screen_reader_label() -> None:
    """Not reading back is not the same as not labelling."""
    labels = {
        key: status_bar._native_item_state(
            _RecordingMenuItem("row"),
            item_key=key,
            parent_key=None,
            order=0,
            submenu_key=None,
            action_kind=None,
        ).accessibility_label
        for key in (
            "agent-mailbox:summary",
            "agent-mailbox:urgent:claude:local:local.01:sessions:w",
            "agent-mailbox:overflow:g1",
            "agent-mailbox:browser:g1",
            "agent-mailbox:urgent:x:action:open",
        )
    }

    assert all(labels.values())
    assert len(set(labels.values())) == len(labels)


# --- the LaunchServices probe ----------------------------------------------


class _BlockingProbe:
    """Stands in for one blocking XPC round trip per running app."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.release = threading.Event()
        self.started = threading.Event()

    def __call__(self) -> bool:
        self.calls.append(threading.current_thread().name)
        self.started.set()
        self.release.wait(timeout=5.0)
        return True


def test_the_alcove_probe_is_sampled_once_then_never_blocks_again() -> None:
    probe = _BlockingProbe()
    probe.release.set()
    presence = virtual_device.AlcovePresenceProbe(probe=probe, ttl_seconds=3.0)

    assert presence.running(now=100.0) is True
    for tick in range(1, 40):
        assert presence.running(now=100.0 + tick * 0.05) is True

    assert len(probe.calls) == 1


def test_a_stale_alcove_answer_is_refreshed_off_the_main_thread() -> None:
    probe = _BlockingProbe()
    probe.release.set()
    presence = virtual_device.AlcovePresenceProbe(probe=probe, ttl_seconds=3.0)
    presence.running(now=100.0)
    main_thread = threading.current_thread().name
    probe.started.clear()

    started = time.monotonic()
    assert presence.running(now=200.0) is True
    elapsed = time.monotonic() - started

    assert probe.started.wait(2.0)

    assert elapsed < 0.05
    assert len(probe.calls) == 2
    assert probe.calls[0] == main_thread
    assert probe.calls[1] != main_thread


def test_a_slow_refresh_never_stalls_the_caller_and_is_not_stampeded() -> None:
    """A 2s timer must not queue a thread per tick behind a slow probe."""
    probe = _BlockingProbe()
    probe.release.set()
    presence = virtual_device.AlcovePresenceProbe(probe=probe, ttl_seconds=3.0)
    presence.running(now=100.0)
    probe.release.clear()

    started = time.monotonic()
    for tick in range(20):
        assert presence.running(now=200.0 + tick) is True
    elapsed = time.monotonic() - started
    probe.release.set()

    assert elapsed < 0.5
    assert len(probe.calls) <= 2


def test_the_probe_answers_not_running_rather_than_raising() -> None:
    def explode() -> bool:
        raise OSError("LaunchServices is having a day")

    presence = virtual_device.AlcovePresenceProbe(probe=explode, ttl_seconds=3.0)

    assert presence.running(now=100.0) is False
