from __future__ import annotations

from collections import deque

import pytest

from sidepulse.creator_micro_adapter import (
    CreatorMicro2Adapter,
    CreatorMicro2Framer,
    DeviceCapability,
    DeviceConflict,
    RpcStreamDecoder,
    SemanticState,
)
from sidepulse.creator_micro_hidapi import HidApiTransport, NoDeviceError

INFO = {"vendor_id": 0x303A, "product_id": 0x8297, "usage_page": 0xFF00, "usage": 1}


def rpc_result(ident: int, result=None) -> bytes:
    return b"".join(
        CreatorMicro2Framer.encode_message({"jsonrpc": "2.0", "id": ident, "result": {} if result is None else result})
    )


class FakeTransport:
    def __init__(self, reads=(), open_error=None):
        self.reads, self.open_error, self.writes = deque(reads), open_error, []
        self.opened = self.closed = False

    def open(self, *, nonexclusive=True):
        assert nonexclusive
        if self.open_error:
            raise self.open_error
        self.opened = True

    def write(self, report):
        self.writes.append(report)

    def read(self, *, timeout_ms):
        assert 0 <= timeout_ms <= 8_000
        return self.reads.popleft() if self.reads else None

    def close(self):
        self.closed = True


def test_frames_every_fragment_and_reassembles_split_and_concatenated_messages():
    message = {"jsonrpc": "2.0", "method": "x" * 150, "params": {"text": 'a } \\" { b'}, "id": 7}
    reports = CreatorMicro2Framer.encode_request(message)
    assert len(reports) >= 3
    assert all(len(report) == 64 and report[0:2] == b"\x06\x02" for report in reports)
    decoder, out = RpcStreamDecoder(), []
    for report in reports:
        out.extend(decoder.feed(report))
    out.extend(decoder.feed(rpc_result(8)[1:]))
    assert out == [message, {"jsonrpc": "2.0", "id": 8, "result": {}}]


@pytest.mark.parametrize(
    "message",
    [
        {"method": "x", "id": 1},
        {"jsonrpc": "1.0", "method": "x", "id": 1},
        {"jsonrpc": "2.0", "method": "x"},
        {"jsonrpc": "2.0", "method": "x", "id": True},
        {"jsonrpc": "2.0", "method": "x", "id": 1000},
    ],
)
def test_request_envelope_is_strict(message):
    with pytest.raises(ValueError):
        CreatorMicro2Framer.encode_request(message)


def test_decoder_rejects_malformed_reports_and_envelopes_without_retaining_fragments():
    decoder = RpcStreamDecoder()
    with pytest.raises(ValueError, match="length"):
        decoder.feed(bytes((6, 2, 62)) + bytes(61))
    with pytest.raises(ValueError, match="JSON-RPC"):
        payload = b'{"id":1,"unknown":{}}'
        decoder.feed(bytes((6, 2, len(payload))) + payload.ljust(61, b"\0"))
    assert decoder.pending_bytes == 0


def test_incoming_envelopes_reject_ambiguous_or_extra_fields():
    ambiguous = {"jsonrpc": "2.0", "id": 1, "result": {}, "method": "also-a-response", "params": {}}
    extra = {"jsonrpc": "2.0", "m": "v.oai.hid", "p": {}, "extra": True}
    wrong_version = {"jsonrpc": "1.0", "m": "v.oai.hid", "p": {}}
    for message in (ambiguous, extra, wrong_version):
        with pytest.raises(ValueError):
            CreatorMicro2Framer.validate_incoming(message)


def test_vendor_envelopes_without_a_jsonrpc_field_deliver_responses_and_key_presses():
    def report(payload):
        return bytes((6, 2, len(payload))) + payload.ljust(61, b"\0")

    transport = FakeTransport([
        report(b'{"m":"v.oai.hid","p":{"k":"AG03","act":1}}'),
        report(b'{"id":1,"method":"v.oai.thstatus","params":{"ok":1}}'),
    ])
    adapter = CreatorMicro2Adapter(transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"]))
    adapter.connect()
    assert adapter.apply(SemanticState.ACTIVE).code == "applied"
    assert adapter.poll_inputs() == [{"method": "v.oai.hid", "params": {"k": "AG03", "act": 1}}]


def test_device_status_result_with_echoed_method_is_accepted():
    # The connected Creator Micro returns result plus an echoed method.
    # Build raw reports so the encoder cannot hide a receive-side rejection.
    payload = (
        b'{"id":1,"method":"device.status","result":'
        b'{"version":"v0.6.1","layer_index":1,"profile_index":0}}'
    )
    parts = [payload[index:index + 61] for index in range(0, len(payload), 61)]
    transport = FakeTransport([
        bytes((6, 2, len(part))) + part.ljust(61, b"\0") for part in parts
    ])
    adapter = CreatorMicro2Adapter(transport, INFO)
    assert adapter.connect().code == "connected"

    receipt, response = adapter._call("device.status", None)

    assert receipt.code == "applied"
    assert response["result"] == {
        "version": "v0.6.1", "layer_index": 1, "profile_index": 0,
    }


def test_echoed_result_for_another_method_is_refused_and_disconnects():
    payload = b'{"id":1,"method":"fs.read","result":{}}'
    transport = FakeTransport([
        bytes((6, 2, len(payload))) + payload.ljust(61, b"\0")
    ])
    adapter = CreatorMicro2Adapter(transport, INFO)
    adapter.connect()

    receipt, response = adapter._call("device.status", None)

    assert receipt.code == "malformed_report"
    assert response is None
    assert transport.closed and not adapter.connected


@pytest.mark.parametrize("extra", [{"params": {}}, {"error": {}}, {"extra": True}])
def test_echoed_result_does_not_accept_ambiguous_or_extra_fields(extra):
    with pytest.raises(ValueError, match="response"):
        CreatorMicro2Framer.validate_incoming({
            "id": 1, "method": "device.status", "result": {}, **extra,
        })


def test_key_notification_after_a_response_in_the_same_report_is_not_lost():
    payload = b'{"id":1,"result":"' + b"x" * 50 + b'"}'
    payload += b'{"m":"v.oai.hid","p":{"k":"AG03","act":1}}'
    fragments = [payload[index:index + 61] for index in range(0, len(payload), 61)]
    transport = FakeTransport([bytes((6, 2, len(part))) + part.ljust(61, b"\0") for part in fragments])
    adapter = CreatorMicro2Adapter(transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"]))
    adapter.connect()
    assert adapter.apply(SemanticState.ACTIVE).code == "applied"
    assert adapter.poll_inputs() == [{"method": "v.oai.hid", "params": {"k": "AG03", "act": 1}}]


def test_discovery_is_exact_and_has_no_transport_side_effects():
    transport = FakeTransport()
    adapter = CreatorMicro2Adapter(transport, INFO)
    assert adapter.discover() and not CreatorMicro2Framer.discover({**INFO, "usage": 6})
    assert not transport.opened and transport.writes == []


def test_connect_returns_explicit_no_device_permission_and_unavailable_receipts():
    assert CreatorMicro2Adapter(FakeTransport(), {**INFO, "product_id": 1}).connect().code == "no_device"
    assert CreatorMicro2Adapter(FakeTransport(open_error=NoDeviceError()), INFO).connect().code == "no_device"
    assert CreatorMicro2Adapter(FakeTransport(open_error=PermissionError()), INFO).connect().code == "permission_denied"
    assert (
        CreatorMicro2Adapter(FakeTransport(open_error=OSError("backend gone")), INFO).connect().code
        == "transport_unavailable"
    )


def test_apply_writes_all_fragments_and_correlates_response():
    transport = FakeTransport([rpc_result(1)])
    adapter = CreatorMicro2Adapter(transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"]))
    assert adapter.connect().code == "connected"
    params = [{"id": i, "c": 0x123456, "b": 1, "e": 1, "s": 0.5} for i in range(13)]
    assert adapter.apply(SemanticState.INPUT_REQUIRED, params).code == "applied"
    assert len(transport.writes) > 1
    decoder = RpcStreamDecoder()
    decoded = (message for report in transport.writes for message in decoder.feed(report))
    assert next(decoded)["params"] == params


@pytest.mark.parametrize("state", list(SemanticState))
def test_default_state_output_sets_explicit_lighting_instead_of_only_an_id(state):
    transport = FakeTransport([rpc_result(1)])
    adapter = CreatorMicro2Adapter(
        transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"])
    )
    adapter.connect()
    assert adapter.apply(state).code == "applied"
    decoder = RpcStreamDecoder()
    request = next(message for report in transport.writes for message in decoder.feed(report))
    assert request["method"] == "v.oai.thstatus"
    assert len(request["params"]) == 20
    assert [item["id"] for item in request["params"]] == list(range(20))
    assert all({"c", "b", "e", "s", "sk", "sa"} <= set(item) for item in request["params"])
    if state is SemanticState.IDLE:
        assert all(item["b"] == 0 and item["e"] == 0 for item in request["params"])
    else:
        assert all(item["b"] > 0 and item["e"] > 0 and item["c"] > 0 for item in request["params"])


def test_notifications_survive_fragmentation_while_waiting_for_response():
    note = {"jsonrpc": "2.0", "m": "v.oai.hid", "p": {"k": "AG01", "act": 1}}
    transport = FakeTransport([*CreatorMicro2Framer.encode_message(note), rpc_result(1)])
    adapter = CreatorMicro2Adapter(transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"]))
    adapter.connect()
    assert adapter.apply(SemanticState.ACTIVE, [{"id": 1}]).code == "applied"
    assert adapter.poll_inputs() == [{"method": "v.oai.hid", "params": {"k": "AG01", "act": 1}}]


def test_input_poll_has_a_report_budget_and_preserves_remaining_input():
    note = {"jsonrpc": "2.0", "m": "v.oai.hid", "p": {"k": "AG01", "act": 1}}
    reports = CreatorMicro2Framer.encode_message(note)
    transport = FakeTransport(reports * 200)
    adapter = CreatorMicro2Adapter(transport, INFO)
    adapter.connect()
    first = adapter.poll_inputs()
    assert 0 < len(first) < 200
    assert transport.reads
    received = len(first)
    while transport.reads:
        received += len(adapter.poll_inputs())
    assert received == 200


def test_input_conflict_discards_notifications_from_the_contested_batch():
    note = {"jsonrpc": "2.0", "m": "v.oai.hid", "p": {"k": "AG01", "act": 1}}
    transport = FakeTransport([*CreatorMicro2Framer.encode_message(note), rpc_result(999)])
    adapter = CreatorMicro2Adapter(transport, INFO)
    adapter.connect()
    assert adapter.poll_inputs() == []
    assert adapter.conflict.active


def test_disconnect_discards_queued_input_instead_of_replaying_it_on_reconnect():
    note = {"jsonrpc": "2.0", "m": "v.oai.hid", "p": {"k": "AG01", "act": 1}}
    transport = FakeTransport(CreatorMicro2Framer.encode_message(note))
    adapter = CreatorMicro2Adapter(
        transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"])
    )
    adapter.connect()
    assert adapter.apply(SemanticState.ACTIVE).code == "timeout"
    assert adapter.poll_inputs() == []


def test_foreign_response_activates_single_writer_stop_and_closes_safely():
    transport = FakeTransport([rpc_result(999), rpc_result(1)])
    adapter = CreatorMicro2Adapter(transport, INFO, capabilities=DeviceCapability.from_methods(["v.oai.thstatus"]))
    adapter.connect()
    assert adapter.apply(SemanticState.ACTIVE, [{"id": 1}]).code == "device_conflict"
    before = len(transport.writes)
    assert adapter.apply(SemanticState.IDLE).code == "device_conflict"
    assert len(transport.writes) == before
    adapter.close()
    adapter.close()
    assert transport.closed and not adapter.connected


def test_timeout_disconnects_and_next_connect_honours_backoff():
    now = [10.0]
    transport = FakeTransport()
    adapter = CreatorMicro2Adapter(
        transport,
        INFO,
        capabilities=DeviceCapability.from_methods(["v.oai.thstatus"]),
        clock=lambda: now[0],
        rpc_timeout_ms=1,
        reconnect_backoff_s=5,
    )
    adapter.connect()
    assert adapter.apply(SemanticState.ACTIVE, [{"id": 1}]).code == "timeout"
    assert adapter.connect().code == "backoff"
    now[0] += 5
    assert adapter.connect().code == "connected"


def test_capability_probe_is_opt_in_and_falls_back_honestly_on_method_not_found():
    error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    transport = FakeTransport([*CreatorMicro2Framer.encode_message(error), rpc_result(2)])
    adapter = CreatorMicro2Adapter(transport, INFO)
    adapter.connect()
    assert transport.writes == []
    assert adapter.negotiate_capabilities().code == "capabilities_negotiated"
    assert adapter.capabilities().methods == frozenset({"lights.preview"})


def test_semantic_priority_is_explicit_and_user_priority_is_exact():
    assert SemanticState.INPUT_REQUIRED.priority == 800
    assert SemanticState.FAILURE.priority == 700
    assert SemanticState.IDLE.priority == 100


def test_hidapi_transport_is_injectable_read_only_until_opt_in_and_uses_timeout_contract(monkeypatch):
    monkeypatch.setattr("sidepulse.creator_micro_hidapi._enable_macos_nonexclusive", lambda _: None)
    class Device:
        def open_path(self, path):
            self.path = path

        def set_nonblocking(self, value):
            assert value is False

        def write(self, report):
            self.report = report
            return len(report)

        def read(self, size, timeout):
            self.read_args = (size, timeout)
            return [6, 2, 0] + [0] * 61

        def close(self):
            self.closed = True

    device = Device()

    class Hid:
        def enumerate(self, vendor):
            return [{**INFO, "path": b"x", "serial_number": "CM2-123"}]

        def device(self):
            return device

    transport = HidApiTransport(Hid(), approved_serial="CM2-123")
    assert transport.enumerate() and not hasattr(device, "report")
    transport.open()
    with pytest.raises(PermissionError):
        transport.write(b"x")
    transport.enable_writes()
    transport.write(b"x")
    transport.read(timeout_ms=321)
    assert device.read_args == (64, 321)
    transport.close()


def test_hidapi_output_requires_one_approved_stable_device_identity(monkeypatch):
    monkeypatch.setattr("sidepulse.creator_micro_hidapi._enable_macos_nonexclusive", lambda _: None)
    opened = []

    class Device:
        def open_path(self, path):
            opened.append(path)

        def set_nonblocking(self, value):
            assert value is False

        def close(self):
            pass

    class Hid:
        def enumerate(self, vendor):
            assert vendor == INFO["vendor_id"]
            return [
                {**INFO, "path": b"spoof", "serial_number": "other-device"},
                {**INFO, "path": b"approved", "serial_number": "CM2-123"},
            ]

        def device(self):
            return Device()

    with pytest.raises(PermissionError, match="approved device identity"):
        HidApiTransport(Hid()).open()

    transport = HidApiTransport(Hid(), approved_serial="CM2-123")
    assert [row["path"] for row in transport.enumerate()] == [b"approved"]
    transport.open()
    assert opened == [b"approved"]


def test_hidapi_output_rejects_ambiguous_or_identityless_matches():
    class Hid:
        def __init__(self, rows):
            self.rows = rows

        def enumerate(self, _vendor):
            return self.rows

        def device(self):
            raise AssertionError("ambiguous or identityless device was opened")

    with pytest.raises(PermissionError, match="stable serial"):
        HidApiTransport(
            Hid([{**INFO, "path": b"identityless"}]),
            approved_serial="CM2-123",
        ).open()

    with pytest.raises(PermissionError, match="ambiguous"):
        HidApiTransport(
            Hid(
                [
                    {**INFO, "path": b"one", "serial_number": "CM2-123"},
                    {**INFO, "path": b"two", "serial_number": "CM2-123"},
                ]
            ),
            approved_serial="CM2-123",
        ).open()


def test_conflict_only_accepts_integer_ids_issued_by_this_adapter():
    conflict = DeviceConflict({42})
    assert conflict.observe(42) is None
    assert conflict.observe("42") == "foreign_response_id"
