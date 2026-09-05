"""Setup transfers stay bounded without inheriting the tiny lighting budget."""

import json
from collections import deque

from sidepulse.creator_micro_adapter import CreatorMicro2Adapter


class Transport:
    def __init__(self, response):
        raw = json.dumps(response, ensure_ascii=False).encode()
        self.reads = deque(
            bytes((6, 2, len(raw[i:i + 61]))) + raw[i:i + 61].ljust(61, b"\0")
            for i in range(0, len(raw), 61)
        )
        self.writes = []
        self.closed = False

    def open(self, *, nonexclusive):
        pass

    def close(self):
        self.closed = True

    def write(self, report):
        self.writes.append(report)

    def read(self, *, timeout_ms):
        return self.reads.popleft() if self.reads else None


def adapter(response, **kwargs):
    transport = Transport(response)
    result = CreatorMicro2Adapter(transport, {
        "vendor_id": 0x303A, "product_id": 0x8297, "usage_page": 0xFF00, "usage": 1,
    }, **kwargs)
    assert result.connect().code == "connected"
    return result, transport


def test_setup_can_read_a_fragmented_keymap_larger_than_lighting_messages():
    raw = json.dumps({"profiles": [], "label": "é" * 12_000}, ensure_ascii=False)
    device, _ = adapter({"id": 1, "method": "fs.read", "params": {"data": raw}},
                        rpc_max_bytes=132_096)
    receipt, response = device._call("fs.read", {"file": "keymap.json"})
    assert receipt.code == "applied"
    assert response["params"]["data"] == raw


def test_setup_write_is_framed_completely_and_defaults_still_reject_large_messages():
    data = json.dumps({"label": "x" * 12_000})
    device, transport = adapter({"id": 1, "result": {"ok": 1}}, rpc_max_bytes=132_096)
    receipt, _ = device._call("fs.write", {"file": "keymap.json", "data": data})
    assert receipt.code == "applied"
    encoded = b"".join(report[3:3 + report[2]] for report in transport.writes)
    assert json.loads(encoded)["params"]["data"] == data
    assert all(len(report) == 64 for report in transport.writes)
    small, transport = adapter({"id": 1, "result": {"ok": 1}})
    receipt, _ = small._call("fs.write", {"file": "keymap.json", "data": data})
    assert receipt.code == "request_too_large"
    assert not transport.writes
    assert not small.conflict.issued_ids


def test_wrong_method_with_matching_response_id_cannot_verify_a_write():
    device, transport = adapter({"id": 1, "method": "device.status", "params": {"ok": 1}})
    receipt, _ = device._call("fs.write", {"file": "keymap.json", "data": "{}"})
    assert receipt.code == "malformed_report"
    assert transport.closed


def test_file_budget_cannot_disable_global_bound():
    import pytest

    for invalid in (True, 0, -1, 132_097, float("inf")):
        with pytest.raises(ValueError):
            adapter({"id": 1, "result": {}}, rpc_max_bytes=invalid)


def test_oversized_response_disconnects_and_does_not_leave_a_partial_stream():
    device, transport = adapter({"id": 1, "result": {"data": "x" * 4000}})
    receipt, _ = device._call("fs.read", {"file": "keymap.json"})
    assert receipt.code == "malformed_report"
    assert transport.closed
    assert not device.connected
