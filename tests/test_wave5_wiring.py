"""The wiring, not the units.

Four modules arrived fully tested and completely unreachable: peer
transport, cloud ingest, the animation model and its library. Every test
in this file crosses a SEAM -- it drives a real StatusBarController
method or a real menu builder and asserts what a person would see -- so
that "wired up" cannot mean "imported once and never called".

The rules these hold, in the owner's words:

* every new capability is OFF until it is turned on;
* remote and cloud agents appear in the ledger, marked by where they are;
* they do not take a light here until a machine is unmuted by name;
* sub-agents are invisible everywhere, including in a peer's rows;
* nothing reaches a surface without the discipline the local path uses.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_sidepulse import isolate_controller

from sidepulse.models import AgentMode, AgentStatus
from sidepulse.remote_peers import (
    PeerHealth,
    PeerRefreshResult,
    RemotePeerSettings,
    build_remote_ledger_document,
    parse_remote_ledger_document,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def status(
    agent_id: str,
    *,
    provider: str = "codex",
    mode: AgentMode = AgentMode.WAITING_FOR_INPUT,
    display_name: str = "review",
    event_name: str = "Notification",
    updated_at: datetime | None = None,
    message: str | None = None,
) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=agent_id,
        display_name=display_name,
        mode=mode,
        updated_at=updated_at or NOW,
        event_name=event_name,
        message=message,
    )


class FakePopup:
    """Just enough NSPopUpButton for the library popup's real refresh path.

    The controller repopulates this popup after every library mutation,
    so a stub that only answers ``selectedItem`` would make the rename
    and delete tests pass while the real refresh raised on a live window.
    """

    def __init__(self, selected: str = "") -> None:
        self.titles: list[str] = []
        self.values: list[str] = []
        self._selected = selected

    def removeAllItems(self) -> None:
        self.titles.clear()
        self.values.clear()

    def addItemWithTitle_(self, title) -> None:
        self.titles.append(str(title))
        self.values.append("")

    def lastItem(self):
        popup = self

        class _Item:
            def setRepresentedObject_(self, value) -> None:
                popup.values[-1] = str(value or "")

            def representedObject(self):
                return popup.values[-1]

        return _Item()

    def selectedItem(self):
        return SimpleNamespace(representedObject=lambda: self._selected)


def peer_ledger(*statuses: AgentStatus, machine: str = "mac-b", generated_at=None):
    """A peer's document, built and parsed exactly as the transport would.

    Deliberately round-tripped through the real serializer and the real
    strict parser rather than hand-constructed: a test that fabricates
    RemoteLedger objects would keep passing after the wire format stopped
    agreeing with itself.
    """
    document = build_remote_ledger_document(
        machine=machine,
        statuses=statuses,
        generated_at=generated_at or NOW,
        include_messages=True,
    )
    return parse_remote_ledger_document(document, machine=machine)


class RemotePeerWiringTests(unittest.TestCase):
    """`refresh_` is the seam: peers in, ledger and lights out."""

    def setUp(self) -> None:
        isolate_controller(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "remote-ledger.json"
        for target in (
            "sidepulse.remote_peers.default_remote_ledger_path",
            "sidepulse.status_bar.default_remote_ledger_path",
        ):
            patcher = patch(target, return_value=self.ledger_path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller.status_bar_devices = lambda *a, **k: []
        # This gate runs on a desk with real SidePulse hardware mounted.
        self.controller.status_keepalive_targets = lambda: ()

    def _refresh_with(self, *, local=(), remote=(), health=(), machine="mac-b"):
        """Drive a real refresh_ with a real snapshot and a peer result.

        A real ``MonitorSnapshot``, not a namespace: ``refresh_`` replaces
        the statuses on a FROZEN dataclass, and a stand-in that happens to
        have the right attributes would hide that.
        """
        snapshot = self.status_bar.MonitorSnapshot(
            aggregate=self.status_bar.aggregate_status(tuple(local)),
            statuses=tuple(local),
            stale_statuses=(),
            sources=(),
            collected_at=NOW,
        )
        self.controller.monitor = SimpleNamespace(
            snapshot=lambda: snapshot,
            write_latest_state=lambda: None,
        )
        self.controller.ingest_transcript_fallback = lambda: None
        if remote:
            self.controller._remote_refresh = PeerRefreshResult(
                ledgers=(peer_ledger(*remote, machine=machine),),
                health=tuple(health)
                or (PeerHealth(machine=machine, host=machine, reachable=True),),
            )
        elif health:
            self.controller._remote_refresh = PeerRefreshResult(health=tuple(health))
        self.led_statuses: list = []
        self.controller.sync_leds = (
            lambda *args, **kwargs: self.led_statuses.append(args[3])
        )
        self.controller.refresh_(None)
        return self.controller.current_merged_ledger

    def test_a_peer_row_reaches_the_ledger_and_names_its_machine(self) -> None:
        merged = self._refresh_with(
            local=(status("codex:session:here", display_name="here"),),
            remote=(status("codex:session:there", display_name="there"),),
        )
        remote_rows = merged.remote_rows
        self.assertEqual(len(remote_rows), 1)
        row = remote_rows[0]
        self.assertTrue(row.is_remote)
        self.assertEqual(row.machine, "mac-b")
        # Marked by origin: the SAME channel a cloud agent's row uses, so
        # every renderer that already prints an origin prints the machine.
        self.assertEqual(row.status.origin, "mac-b")
        self.assertEqual(row.ledger_label, "there (mac-b)")
        self.assertTrue(row.status.agent_id.startswith("remote:mac-b:"))

    def test_a_peer_row_is_muted_in_the_interrupt_budget_by_default(self) -> None:
        """It is in the ledger AND it is not in the lights. Both halves."""
        merged = self._refresh_with(
            local=(status("codex:session:here"),),
            remote=(status("codex:session:there"),),
        )
        self.assertEqual(len(merged.remote_rows), 1)
        delivered = self.led_statuses[-1]
        self.assertEqual(
            [item.agent_id for item in delivered], ["codex:session:here"]
        )

    def test_unmuting_one_machine_by_name_admits_only_that_machine(self) -> None:
        self.controller.settings = self.controller.settings.with_remote_machine_muted(
            "mac-b", False
        )
        self._refresh_with(
            local=(status("codex:session:here"),),
            remote=(status("codex:session:there"),),
        )
        delivered = [item.agent_id for item in self.led_statuses[-1]]
        self.assertIn("remote:mac-b:codex:session:there", delivered)

        # A different machine is still muted -- unmuting is per machine,
        # not a global "remote is fine now".
        self.controller._remote_refresh = PeerRefreshResult()
        self._refresh_with(
            local=(status("codex:session:here"),),
            remote=(status("codex:session:elsewhere"),),
            machine="mac-c",
        )
        delivered = [item.agent_id for item in self.led_statuses[-1]]
        self.assertEqual(delivered, ["codex:session:here"])

    def test_a_stale_peer_document_never_reaches_the_interrupt_budget(self) -> None:
        """An unmuted machine whose news is old is still not proof."""
        self.controller.settings = self.controller.settings.with_remote_machine_muted(
            "mac-b", False
        )
        self.controller._remote_refresh = PeerRefreshResult(
            ledgers=(
                peer_ledger(
                    status("codex:session:there"),
                    generated_at=NOW - timedelta(minutes=30),
                ),
            ),
            health=(PeerHealth(machine="mac-b", host="mac-b", reachable=True),),
        )
        merged = self._refresh_with(local=(status("codex:session:here"),))
        self.assertTrue(merged.remote_rows[0].status.stale)
        delivered = [item.agent_id for item in self.led_statuses[-1]]
        self.assertEqual(delivered, ["codex:session:here"])

    def test_a_peer_sub_agent_never_survives_the_wire(self) -> None:
        """Gates one and two: the publisher drops them, the parser drops them."""
        merged = self._refresh_with(
            local=(),
            remote=(
                status("codex:session:parent", display_name="parent"),
                status("codex:agent:worker", display_name="worker"),
            ),
        )
        names = [row.status.display_name for row in merged.remote_rows]
        self.assertEqual(names, ["parent"])

    def test_a_sub_agent_row_is_dropped_even_if_it_gets_past_the_wire(self) -> None:
        """Gate three, which the test above CANNOT reach.

        The publisher and the parser both refuse sub-agents, so a
        round-tripped document can never carry one -- which means the
        wiring's own filter is invisible to any test that goes through the
        wire. This one hands the merge a sub-agent row directly, the way a
        peer on a build we have not written yet would.
        """
        from sidepulse.remote_peers import LedgerRow, MergedLedger
        from sidepulse.status_bar import mark_remote_ledger_origins

        merged = MergedLedger(
            local_machine="mac-a",
            rows=(
                LedgerRow(
                    status=status("codex:session:parent", display_name="parent"),
                    machine="mac-b",
                    is_remote=True,
                ),
                LedgerRow(
                    status=status("codex:agent:worker", display_name="worker"),
                    machine="mac-b",
                    is_remote=True,
                ),
            ),
        )
        kept = mark_remote_ledger_origins(merged)
        self.assertEqual(
            [row.status.display_name for row in kept.rows], ["parent"]
        )

    def test_with_peers_off_the_merge_is_the_identity(self) -> None:
        """The safety property: nothing about a normal Mac changes.

        Not "an equal tuple" -- the SAME tuple. Anything that copies here
        is a new object per refresh in the hottest path in the app.
        """
        local = (status("codex:session:here"),)
        self._refresh_with(local=local)
        self.assertIs(self.led_statuses[-1], local)

    def test_the_dropdown_lists_muted_peer_rows_under_other_macs(self) -> None:
        """Muting decides what may take a light, never what may be said."""
        self._refresh_with(
            local=(status("codex:session:here", display_name="here"),),
            remote=(status("codex:session:there", display_name="there"),),
        )
        menu = self.status_bar.build_menu(
            self.controller.last_snapshot,
            self.status_bar.STATE_IDLE,
            self.controller,
        )
        titles = [
            str(menu.itemAtIndex_(index).title())
            for index in range(menu.numberOfItems())
        ]
        self.assertIn("Other Macs", titles)
        self.assertTrue(
            any("there (mac-b)" in title for title in titles),
            titles,
        )

    def test_an_unreachable_peer_says_so_instead_of_going_quiet(self) -> None:
        self._refresh_with(
            local=(),
            health=(
                PeerHealth(
                    machine="mac-b",
                    host="mac-b",
                    reachable=False,
                    failure="unreachable",
                ),
            ),
        )
        menu = self.status_bar.build_menu(
            self.controller.last_snapshot,
            self.status_bar.STATE_IDLE,
            self.controller,
        )
        titles = [
            str(menu.itemAtIndex_(index).title())
            for index in range(menu.numberOfItems())
        ]
        self.assertTrue(
            any("mac-b" in title and "unreachable" in title for title in titles),
            titles,
        )

    def test_the_menu_signature_notices_a_peer_row_appearing(self) -> None:
        """Without this the section would never repaint on its own.

        Through `menu_content_signature`, the function the rebuild gate
        actually calls -- an earlier version of this test asked the helper
        directly, which stayed green with the helper unwired.
        """
        self._refresh_with(local=(status("codex:session:here"),))
        before = self.status_bar.menu_content_signature(
            self.controller.last_snapshot, self.status_bar.STATE_IDLE, self.controller
        )
        self._refresh_with(
            local=(status("codex:session:here"),),
            remote=(status("codex:session:there"),),
        )
        after = self.status_bar.menu_content_signature(
            self.controller.last_snapshot, self.status_bar.STATE_IDLE, self.controller
        )
        self.assertNotEqual(before, after)

    def test_clicking_a_remote_row_never_opens_a_local_session(self) -> None:
        merged = self._refresh_with(
            local=(),
            remote=(status("codex:session:there", display_name="there"),),
        )
        remote_status = merged.remote_rows[0].status
        opened: list = []
        messages: list[str] = []
        self.controller.set_settings_message = messages.append
        with (
            patch.object(self.status_bar, "open_url", lambda *a: opened.append(a)),
            patch.object(
                self.status_bar,
                "open_terminal_command",
                lambda *a: opened.append(a),
            ),
        ):
            self.controller.open_session(remote_status, None, remember=True)
        self.assertEqual(opened, [])
        self.assertIn("mac-b", messages[-1])

    def test_a_local_row_still_opens(self) -> None:
        """The gate above must refuse remote rows, not every row."""
        opened: list = []
        with (
            patch.object(self.status_bar, "open_url", lambda *a: opened.append(a)),
            patch.object(
                self.status_bar,
                "open_terminal_command",
                lambda *a: opened.append(a),
            ),
        ):
            self.controller.open_session(
                dataclass_replace(
                    status("codex:session:here", display_name="here"),
                    session_id="here",
                    cwd=str(Path(self.tmp.name)),
                ),
                "terminal",
                remember=False,
            )
        self.assertEqual(len(opened), 1)


class _SynchronousLedgerPublisher:
    """Deterministic stand-in for the off-main production publisher.

    The production path hands publication to a worker thread and applies the
    result on the main run loop; under test there is no run loop to pump, so
    the publish and its result delivery both happen inline. The wiring being
    proven -- the off-by-default gate, the change/heartbeat debounce, the
    signature bookkeeping -- all still runs in the controller.
    """

    def __init__(self, controller) -> None:
        self._controller = controller
        self._generation = 0
        self.publishes = 0

    def request(self, *, statuses, generated_at, settings, signature, callback):
        from sidepulse.ledger_runtime import (
            LedgerPublishRequest,
            LedgerPublishResult,
        )
        from sidepulse.remote_peers import publish_local_ledger

        self._generation += 1
        request = LedgerPublishRequest(
            self._generation,
            tuple(statuses),
            generated_at,
            settings,
            signature,
        )
        try:
            path = publish_local_ledger(
                statuses,
                generated_at=generated_at,
                settings=settings,
            )
            result = LedgerPublishResult(request, path)
        except Exception:
            result = LedgerPublishResult(
                request,
                None,
                "remote_ledger_publish_failed",
            )
        self.publishes += 1
        self._controller.applyLedgerPublishResult_(result)
        return request.generation

    def close(self) -> None:
        return None


class RemotePublishWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger_path = Path(self.tmp.name) / "remote-ledger.json"
        for target in (
            "sidepulse.remote_peers.default_remote_ledger_path",
            "sidepulse.status_bar.default_remote_ledger_path",
        ):
            patcher = patch(target, return_value=self.ledger_path)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.publisher = _SynchronousLedgerPublisher(self.controller)
        self.controller._production_ledger_publisher = self.publisher

    def _enable_publishing(self, **changes):
        from dataclasses import replace

        self.controller.settings = self.controller.settings.with_remote_peers(
            replace(
                self.controller.settings.remote_peers,
                publish_enabled=True,
                **changes,
            )
        )

    def test_publishing_is_off_until_the_owner_turns_it_on(self) -> None:
        """Off means no file -- and it means the debounce forgets, too.

        `publish_local_ledger` refuses on its own, so "no file" alone
        cannot tell the wiring's gate from the module's. The second half
        can: while publishing is off the wiring clears the change
        signature, so switching back on republishes IMMEDIATELY instead of
        being debounced against whatever was true before the gap. Without
        that, a peer reads a file frozen at the moment you turned it off.
        """
        rows = (status("codex:session:here"),)
        self.assertIsNone(self.controller.publish_local_ledger_now(rows))
        self.assertFalse(self.ledger_path.exists())

        self._enable_publishing()
        self.assertIsNotNone(self.controller.publish_local_ledger_now(rows))
        self.assertTrue(self.ledger_path.exists())
        self.ledger_path.unlink()

        from dataclasses import replace

        self.controller.settings = self.controller.settings.with_remote_peers(
            replace(self.controller.settings.remote_peers, publish_enabled=False)
        )
        self.assertIsNone(self.controller.publish_local_ledger_now(rows))
        self._enable_publishing()
        self.assertIsNotNone(self.controller.publish_local_ledger_now(rows))

    def test_publishing_writes_this_desk_and_drops_sub_agents(self) -> None:
        self._enable_publishing()
        written = self.controller.publish_local_ledger_now(
            (
                status("codex:session:parent", display_name="parent"),
                status("codex:agent:worker", display_name="worker"),
            ),
            generated_at=NOW,
        )
        self.assertEqual(written, self.ledger_path)
        payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [row["display_name"] for row in payload["rows"]], ["parent"]
        )

    def test_no_capacity_number_ever_crosses_the_wire(self) -> None:
        """A remote reading has no binding lane on the receiving Mac.

        `capacity_authority.select_binding_lanes` is the only door a
        capacity number may come through, and it does not run over there.
        So the published row schema must have nowhere to put one.
        """
        self._enable_publishing()
        self.controller.publish_local_ledger_now(
            (status("codex:session:here"),), generated_at=NOW
        )
        payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        blob = json.dumps(payload).lower()
        for forbidden in ("capacity", "percent", "quota", "limit", "runway"):
            self.assertNotIn(forbidden, blob)

    def test_the_question_text_stays_home_unless_asked_to_travel(self) -> None:
        self._enable_publishing()
        self.controller.publish_local_ledger_now(
            (status("codex:session:here", message="May I delete prod?"),),
            generated_at=NOW,
        )
        first = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertIsNone(first["rows"][0]["message"])

        self._enable_publishing(include_messages=True)
        self.controller._published_ledger_signature = None
        self.controller.publish_local_ledger_now(
            (status("codex:session:here", message="May I delete prod?"),),
            generated_at=NOW,
        )
        second = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(second["rows"][0]["message"], "May I delete prod?")

    def test_an_unchanged_desk_republishes_only_on_the_heartbeat(self) -> None:
        """Bounded both ways: no write per tick, no silence past staleness.

        The production path returns the latest published path even when a
        call is debounced, so "did this call write" is asserted against the
        publisher itself rather than the return value.
        """
        self._enable_publishing()
        rows = (status("codex:session:here"),)
        self.controller.publish_local_ledger_now(rows)
        self.assertEqual(self.publisher.publishes, 1)
        self.controller.publish_local_ledger_now(rows)
        self.assertEqual(self.publisher.publishes, 1)
        # A change always publishes...
        self.controller.publish_local_ledger_now(
            (status("codex:session:here", mode=AgentMode.WORKING),)
        )
        self.assertEqual(self.publisher.publishes, 2)
        # ...and so does the heartbeat, so a quiet desk never looks dead.
        self.controller._published_ledger_at -= (
            self.status_bar.REMOTE_PUBLISH_HEARTBEAT_SECONDS + 1.0
        )
        self.controller.publish_local_ledger_now(
            (status("codex:session:here", mode=AgentMode.WORKING),)
        )
        self.assertEqual(self.publisher.publishes, 3)

    def test_turning_publishing_off_removes_the_file(self) -> None:
        """A frozen ledger left on disk would be a lie a peer keeps reading."""
        self._enable_publishing()
        self.controller.publish_local_ledger_now((status("codex:session:here"),))
        self.assertTrue(self.ledger_path.exists())
        self.controller.toggleRemotePublish_(SimpleNamespace(state=lambda: 0))
        self.assertFalse(self.ledger_path.exists())


class CloudIngestWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        token = patch(
            "sidepulse.cloud_ingest.default_token_path",
            return_value=Path(self.tmp.name) / "cloud-ingest.token",
        )
        token.start()
        self.addCleanup(token.stop)
        self.addCleanup(self.controller.stop_cloud_ingest_server)

    def test_the_loopback_door_is_shut_until_the_owner_opens_it(self) -> None:
        self.controller.start_cloud_ingest_server()
        self.assertIsNone(self.controller.cloud_ingest)

    def _start(self):
        from dataclasses import replace

        self.controller.settings = replace(
            self.controller.settings, cloud_ingest_enabled=True
        )
        self.controller.start_cloud_ingest_server()
        self.assertIsNotNone(self.controller.cloud_ingest)
        server = self.controller.cloud_ingest
        self._row_ready = threading.Event()
        sink = server.sink

        def observed_sink(record) -> None:
            sink(record)
            self._row_ready.set()

        server.sink = observed_sink
        return server

    def _post(self, server, body: dict) -> int:
        import http.client

        from sidepulse.cloud_ingest import default_token_path, read_ingest_token

        host, port = server.address
        payload = json.dumps(body).encode("utf-8")
        connection = http.client.HTTPConnection(host, port, timeout=5.0)
        try:
            connection.request(
                "POST",
                "/v1/agent-event",
                body=payload,
                headers={
                    "Host": f"{host}:{port}",
                    "Authorization": (
                        f"Bearer {read_ingest_token(default_token_path())}"
                    ),
                    "Content-Length": str(len(payload)),
                },
            )
            return connection.getresponse().status
        finally:
            connection.close()

    def test_it_binds_loopback_only(self) -> None:
        server = self._start()
        self.assertEqual(server.address[0], "127.0.0.1")

    def test_a_real_cloud_event_becomes_a_row_through_the_local_sink(self) -> None:
        """End to end over an actual socket, into the actual monitor.

        A cloud row is not a second kind of row: it goes through
        `monitor.ingest_record`, the same door a local hook uses, so the
        collector, the mailbox and the interrupt budget all treat it as
        what it is -- an agent session that happens to be elsewhere.
        """
        server = self._start()
        self.assertEqual(
            self._post(
                server,
                {
                    "version": 1,
                    "provider": "claude",
                    "session_id": "cloud-review-1",
                    "event": "Notification",
                    "display_name": "PR 42 review",
                },
            ),
            202,
        )
        rows = self._wait_for_rows()
        self.assertTrue(rows, "no cloud row reached the monitor")
        self.assertTrue(
            any("Claude Cloud" == (row.origin or "") for row in rows),
            [(row.agent_id, row.origin) for row in rows],
        )

    def test_a_cloud_sub_agent_stays_invisible(self) -> None:
        server = self._start()
        self._post(
            server,
            {
                "version": 1,
                "provider": "claude",
                "session_id": "cloud-review-1",
                "agent_id": "worker-7",
                "event": "Notification",
            },
        )
        rows = self._wait_for_rows()
        self.assertTrue(all(row.is_subagent for row in rows), rows)
        # And the ledger the dropdown is built from shows none of them.
        merged = self.controller.merged_ledger_for(
            SimpleNamespace(statuses=tuple(rows), collected_at=NOW)
        )
        self.assertEqual(
            [row.status.agent_id for row in merged.rows if row.status.is_subagent],
            [row.agent_id for row in rows],
            "sub-agents belong in the model but never on a surface",
        )
        from sidepulse.status_bar import mailbox_attention_statuses

        visible = mailbox_attention_statuses(
            SimpleNamespace(statuses=tuple(rows), stale_statuses=(), collected_at=NOW)
        )
        self.assertTrue(all(row.is_subagent for row in visible))

    def _wait_for_rows(self):
        self.assertTrue(self._row_ready.wait(2.5), "cloud row did not publish")
        return self.controller.monitor.snapshot().statuses

    def test_the_toggle_starts_and_stops_the_server(self) -> None:
        self.controller.toggleCloudIngest_(SimpleNamespace(state=lambda: 1))
        self.assertIsNotNone(self.controller.cloud_ingest)
        self.assertTrue(self.controller.settings.cloud_ingest_enabled)
        self.controller.toggleCloudIngest_(SimpleNamespace(state=lambda: 0))
        self.assertIsNone(self.controller.cloud_ingest)
        self.assertFalse(self.controller.settings.cloud_ingest_enabled)


class StudioAnimationWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library_path = Path(self.tmp.name) / "animation-library.json"
        patcher = patch(
            "sidepulse.status_bar.default_animation_library_path",
            return_value=self.library_path,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.controller.status_bar_devices = lambda *a, **k: []

    def test_the_editor_says_what_is_wrong_and_where(self) -> None:
        """"the wasm said no" was the whole feedback this editor gave."""
        self.controller.studio_editor = SimpleNamespace(string=lambda: "0:off")
        severity, text = self.controller.refresh_studio_problem_label()
        self.assertEqual(severity, "error")
        self.assertIn("step 1", text)
        self.assertIn("#000000", text)

    def test_a_warning_is_a_warning_and_not_a_refusal(self) -> None:
        """The firmware accepts an out-of-range index and ignores it.

        That is not an error -- it writes fine and then does nothing --
        so it must be sayable without blocking a save.
        """
        self.controller.studio_editor = SimpleNamespace(
            string=lambda: "9:#FF0000 1s"
        )
        severity, text = self.controller.refresh_studio_problem_label()
        self.assertEqual(severity, "warning")
        self.assertIn("9", text)
        self.assertIsNone(self.controller.validate_studio_program("9:#FF0000 1s"))

    def test_typing_repaints_the_line(self) -> None:
        """The delegate seam: without it the label is right exactly once."""
        editor = SimpleNamespace(string=lambda: "0:off")
        self.controller.studio_editor = editor
        self.controller.studio_problem_label = None
        self.assertEqual(
            self.controller.textDidChange_(SimpleNamespace(object=lambda: editor)),
            None,
        )
        severity, _text = self.controller.studio_problem_summary(editor.string())
        self.assertEqual(severity, "error")

    def test_a_saved_look_lands_in_the_bounded_library_not_settings(self) -> None:
        self.controller.studio_editor = SimpleNamespace(
            string=lambda: "#00E5FF 800ms pulse\noff 300ms cosine\nrepeat"
        )
        self.controller.studio_save_name_field = SimpleNamespace(
            stringValue=lambda: "Cyan Breath",
            setStringValue_=lambda _value: None,
        )
        self.controller.saveStudioLook_(None)
        self.assertTrue(self.library_path.exists())
        self.assertEqual(
            self.controller.ensure_animation_library().names, ("Cyan Breath",)
        )
        self.assertEqual(self.controller.settings.studio_library, ())

    def test_the_library_refuses_a_look_the_device_would_reject(self) -> None:
        self.controller.studio_editor = SimpleNamespace(string=lambda: "0:off")
        self.controller.studio_save_name_field = SimpleNamespace(
            stringValue=lambda: "Broken",
            setStringValue_=lambda _value: None,
        )
        self.controller.saveStudioLook_(None)
        self.assertEqual(self.controller.ensure_animation_library().names, ())

    def test_old_looks_migrate_out_of_settings_json_once(self) -> None:
        from dataclasses import replace

        self.controller.settings = replace(
            self.controller.settings,
            studio_library=(("Old Look", "#FF0000 1s pulse\nrepeat"),),
        )
        library = self.controller.ensure_animation_library()
        self.assertEqual(library.names, ("Old Look",))
        self.assertEqual(self.controller.settings.studio_library, ())

    def test_an_unmigratable_look_is_kept_not_quietly_dropped(self) -> None:
        from dataclasses import replace

        self.controller.settings = replace(
            self.controller.settings,
            studio_library=(
                ("Good", "#FF0000 1s pulse\nrepeat"),
                ("Bad", "0:off"),
            ),
        )
        self.controller.ensure_animation_library()
        self.assertEqual(self.controller.ensure_animation_library().names, ("Good",))
        self.assertEqual(
            [name for name, _program in self.controller.settings.studio_library],
            ["Bad"],
        )

    def test_rename_keeps_the_list_in_the_shape_the_owner_learned(self) -> None:
        self.controller.studio_editor = SimpleNamespace(
            string=lambda: "#FF0000 1s pulse\nrepeat"
        )
        names: list[str] = []
        for name in ("First", "Second", "Third"):
            self.controller.studio_save_name_field = SimpleNamespace(
                stringValue=lambda name=name: name,
                setStringValue_=lambda _value: None,
            )
            self.controller.saveStudioLook_(None)
            names.append(name)
        popup = FakePopup("Second")
        self.controller.studio_library_popup = popup
        self.controller.studio_save_name_field = SimpleNamespace(
            stringValue=lambda: "Middle",
            setStringValue_=lambda _value: None,
        )
        self.controller.renameStudioLook_(None)
        self.assertEqual(
            self.controller.ensure_animation_library().names,
            ("First", "Middle", "Third"),
        )
        # And the popup the owner actually reads was repopulated in place.
        self.assertEqual(popup.titles[1:], ["First", "Middle", "Third"])
        self.assertEqual(names, ["First", "Second", "Third"])

    def test_a_dot_is_validated_as_a_dot_not_as_a_pro(self) -> None:
        """An 8-LED program on a 2-LED Dot parses and then paints nothing.

        The burn used to validate every device at a hardcoded eight, so
        the one device that would silently swallow half the animation was
        the one device the check could not see.
        """
        dot = self.status_bar.StatusBarDevice(
            device_id="SidePulseDot",
            name="SidePulseDot",
            root=Path("/Volumes/SidePulseDot"),
            target=Path("/Volumes/SidePulseDot/LEDS.LED"),
            connected=True,
            display=self.status_bar.LED_DISPLAY_AGENT,
        )
        self.controller.status_bar_devices = lambda *a, **k: [dot]
        self.assertEqual(self.controller.studio_led_count(), 2)
        severity, text = self.controller.studio_problem_summary("5:#FF0000 1s")
        self.assertEqual(severity, "warning")
        self.assertIn("5", text)

    def test_the_burn_writes_init_led_through_the_animation_gates(self) -> None:
        device = self.status_bar.StatusBarDevice(
            device_id="SidePulsePro",
            name="SidePulsePro",
            root=Path("/Volumes/SidePulsePro"),
            target=Path("/Volumes/SidePulsePro/LEDS.LED"),
            connected=True,
            display=self.status_bar.LED_DISPLAY_AGENT,
        )
        self.controller.status_bar_devices = lambda *a, **k: [device]
        self.controller.studio_editor = SimpleNamespace(
            string=lambda: "#FF0000 1s pulse\noff 500ms cosine"
        )
        writes: list = []
        with patch(
            "sidepulse.device_writer.write_led_program",
            side_effect=lambda *a, **k: (
                writes.append((a, k)) or Path("/Volumes/SidePulsePro/INIT.LED")
            ),
        ):
            self.controller.applyStudioAsPowerUp_(None)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1]["file_name"], "INIT.LED")

    def test_burning_a_saved_look_goes_through_the_library(self) -> None:
        self.controller.studio_editor = SimpleNamespace(
            string=lambda: "#FF0000 1s pulse\nrepeat"
        )
        self.controller.studio_save_name_field = SimpleNamespace(
            stringValue=lambda: "Pulse",
            setStringValue_=lambda _value: None,
        )
        self.controller.saveStudioLook_(None)
        plan = self.controller.burn_saved_look_as_power_up("Pulse", dry_run=True)
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.written)
        self.assertIn(b"#FF0000", plan.payload)


class SettingsAreaWiringTests(unittest.TestCase):
    """The three areas the owner asked for, built from the real builders."""

    def setUp(self) -> None:
        isolate_controller(self)
        self.controller.status_bar_devices = lambda *a, **k: []
        self.controller.status_keepalive_targets = lambda: ()
        self.controller.show_settings_window()
        self.addCleanup(self.controller.settings_window.close)

    def test_the_sidebar_offers_agents_messages_and_extras(self) -> None:
        labels = dict(self.status_bar.SETTINGS_SIDEBAR_ITEMS)
        self.assertEqual(labels["agents"], "Agents")
        self.assertEqual(labels["notifications"], "Messages")
        self.assertEqual(labels["extras"], "Extras")

    def test_every_sidebar_key_builds(self) -> None:
        """A sidebar row that raises on click is worse than no row."""
        self.controller.ensure_all_settings_panes()
        for key, _label in self.status_bar.SETTINGS_SIDEBAR_ITEMS:
            if not key.startswith("header:"):
                self.assertIn(key, self.controller.settings_panes, key)

    def test_agents_offers_a_motion_per_provider_and_it_saves(self) -> None:
        from sidepulse.colors import PROVIDER_ANIMATION_LABELS
        from sidepulse.providers import PROVIDER_SPECS

        self.controller.ensure_settings_pane("agents")
        for spec in PROVIDER_SPECS:
            self.assertIn(
                f"{spec.provider}_agent_animation", self.controller.settings_fields
            )
        popup = self.controller.settings_fields["claude_agent_animation"]
        chosen = next(
            popup.itemAtIndex_(index)
            for index in range(popup.numberOfItems())
            if popup.itemAtIndex_(index).title()
            == PROVIDER_ANIMATION_LABELS["blink"]
        )
        popup.selectItem_(chosen)
        self.controller.setAgentAnimation_(popup)
        self.assertEqual(
            self.controller.settings.colors.provider_animation.get("claude"), "blink"
        )

    def test_the_remote_and_cloud_switches_start_off_and_persist(self) -> None:
        self.controller.ensure_settings_pane("agents")
        for key in (
            "remote_peers_enabled",
            "remote_publish_enabled",
            "remote_messages_enabled",
            "cloud_ingest_enabled",
        ):
            self.assertIn(key, self.controller.settings_buttons, key)
            self.assertEqual(
                self.controller.settings_buttons[key].state(), 0, key
            )
        self.controller.toggleRemotePeers_(SimpleNamespace(state=lambda: 1))
        self.assertTrue(self.controller.settings.remote_peers.enabled)
        from sidepulse.settings import load_settings

        self.assertTrue(load_settings(self._settings_path).remote_peers.enabled)

    def test_messages_holds_the_interrupt_mute_for_a_known_machine(self) -> None:
        self.controller.current_merged_ledger = self.controller.merged_ledger_for(
            SimpleNamespace(statuses=(), collected_at=NOW)
        )
        self.controller.settings = self.controller.settings.with_remote_machine_muted(
            "mac-b", True
        )
        self.controller.ensure_settings_pane("notifications")
        box = self.controller.settings_buttons["remote_machine:mac-b"]
        self.assertEqual(box.state(), 0)
        box.setState_(1)
        self.controller.toggleRemoteMachineInterrupt_(box)
        self.assertTrue(
            self.controller.settings.remote_peers.interrupt_policy().allows_machine(
                "mac-b"
            )
        )

    def test_extras_carries_the_weather_and_calendar_controls(self) -> None:
        self.controller.ensure_settings_pane("extras")
        for key in (
            "calendar_alerts_enabled",
            "reminder_alerts_enabled",
            "weather_alerts_enabled",
        ):
            self.assertIn(key, self.controller.settings_buttons, key)
        for key in (
            "calendar_lead_field",
            "weather_latitude_field",
            "weather_longitude_field",
        ):
            self.assertIn(key, self.controller.settings_fields, key)

    def test_refreshing_the_window_re_reads_the_new_controls(self) -> None:
        """Every one of these can change from somewhere else."""
        from dataclasses import replace

        self.controller.ensure_settings_pane("agents")
        self.controller.settings = self.controller.settings.with_remote_peers(
            replace(self.controller.settings.remote_peers, enabled=True)
        )
        self.controller.settings = replace(
            self.controller.settings, cloud_ingest_enabled=True
        )
        self.controller.refresh_settings_window()
        self.assertEqual(
            self.controller.settings_buttons["remote_peers_enabled"].state(), 1
        )
        self.assertEqual(
            self.controller.settings_buttons["cloud_ingest_enabled"].state(), 1
        )


class RemotePeerRefreshCadenceTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_controller(self)
        self.controller.status_bar_devices = lambda *a, **k: []
        self.controller.status_keepalive_targets = lambda: ()

    def test_the_fetch_never_runs_on_the_refresh_path(self) -> None:
        """Eight seconds of subprocess I/O is not a UI tick.

        `refresh_` is called on every hook event. If the peer fetch ever
        moves into it, the menu bar freezes for as long as the slowest
        Mac takes to answer.
        """
        from dataclasses import replace

        # Enabled, or a fetch smuggled into refresh_ would return early on
        # its own and this guard would pass while being unable to fail.
        self.controller.settings = self.controller.settings.with_remote_peers(
            replace(self.controller.settings.remote_peers, enabled=True)
        )
        calls: list = []
        with patch(
            "sidepulse.status_bar.collect_remote_ledgers",
            side_effect=lambda **kwargs: calls.append(kwargs) or PeerRefreshResult(),
        ):
            self.controller.monitor = SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    statuses=(),
                    stale_statuses=(),
                    sources=(),
                    collected_at=NOW,
                    operator_events=(),
                    operator_state=None,
                    aggregate=SimpleNamespace(mode=AgentMode.IDLE_READY),
                ),
                write_latest_state=lambda: None,
            )
            self.controller.ingest_transcript_fallback = lambda: None
            self.controller.refresh_(None)
        self.assertEqual(calls, [])

    def test_the_fetch_is_inert_while_the_feature_is_off(self) -> None:
        calls: list = []
        with patch(
            "sidepulse.status_bar.collect_remote_ledgers",
            side_effect=lambda **kwargs: calls.append(kwargs) or PeerRefreshResult(),
        ):
            self.controller.refresh_remote_peers()
        self.assertEqual(calls, [])

    def test_the_breakers_ride_forward_between_fetches(self) -> None:
        """Without this a dead Mac is retried every single minute, forever."""
        from dataclasses import replace

        from sidepulse.remote_peers import PeerBreaker

        self.controller.settings = self.controller.settings.with_remote_peers(
            replace(self.controller.settings.remote_peers, enabled=True)
        )
        self.controller._remote_refresh = PeerRefreshResult(
            breakers=(PeerBreaker(host="mac-b", consecutive_failures=3),)
        )
        seen: list = []
        with patch(
            "sidepulse.status_bar.collect_remote_ledgers",
            side_effect=lambda **kwargs: (
                seen.append(tuple(kwargs["breakers"])) or PeerRefreshResult()
            ),
        ):
            self.controller.refresh_remote_peers()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0].host, "mac-b")

    def test_an_unchanged_peer_result_does_not_rebuild_the_menu(self) -> None:
        refreshes: list = []
        self.controller.refresh_ = lambda _sender: refreshes.append(1)
        result = PeerRefreshResult(
            health=(PeerHealth(machine="mac-b", host="mac-b", reachable=True),)
        )
        self.controller.applyRemotePeerRefresh_(result)
        self.assertEqual(len(refreshes), 1)
        self.controller.applyRemotePeerRefresh_(
            PeerRefreshResult(
                health=(PeerHealth(machine="mac-b", host="mac-b", reachable=True),)
            )
        )
        self.assertEqual(len(refreshes), 1)


class RemotePeerSettingsPersistenceTests(unittest.TestCase):
    def test_the_record_round_trips_and_defaults_to_silence(self) -> None:
        from dataclasses import replace

        from sidepulse.settings import AgentMonitorSettings, load_settings, save_settings

        settings = AgentMonitorSettings()
        self.assertEqual(settings.remote_peers, RemotePeerSettings())
        self.assertFalse(settings.remote_peers.enabled)
        self.assertTrue(settings.remote_peers.remote_interrupts_muted)
        self.assertFalse(settings.cloud_ingest_enabled)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            saved = replace(
                settings,
                remote_peers=replace(
                    settings.remote_peers, enabled=True, unmuted_machines=("mac-b",)
                ),
                cloud_ingest_enabled=True,
            )
            save_settings(saved, path)
            restored = load_settings(path)
        self.assertTrue(restored.remote_peers.enabled)
        self.assertEqual(restored.remote_peers.unmuted_machines, ("mac-b",))
        self.assertTrue(restored.cloud_ingest_enabled)

    def test_a_corrupt_peer_block_degrades_to_off_rather_than_crashing(self) -> None:
        from sidepulse.settings import load_settings

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps({"remote_peers": {"enabled": True, "max_peers": "lots"}}),
                encoding="utf-8",
            )
            restored = load_settings(path)
        self.assertEqual(restored.remote_peers, RemotePeerSettings())


if __name__ == "__main__":
    unittest.main()
