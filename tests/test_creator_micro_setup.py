"""Device acknowledgement alone must never count as successful keymap setup."""

import importlib
import importlib.util
import json
import stat

import pytest

from sidepulse.creator_micro_adapter import Receipt


def keymap():
    return json.dumps({"activeProfileId": 0, "profiles": [{"layers": [{"layout": {
        "keymap": [["KC_A", "KC_B"], ["KC_C"] * 4, ["KC_D"] * 4, ["KC_E"] * 3],
    }}]}], "macro": "private user macro"})


class Device:
    connected = True

    def __init__(self, raw=None, *, ignores_writes=False, empty_write_ack=False):
        self.raw = raw or keymap()
        self.status = {"layer_index": 1}
        self.ignores_writes = ignores_writes
        self.empty_write_ack = empty_write_ack
        self.writes = []
        self.before_write = lambda: None

    def _call(self, method, params):
        if method == "device.status":
            result = self.status.copy()
        elif method == "fs.read":
            assert params == {"file": "keymap.json"}
            result = {"data": self.raw}
        elif method == "fs.write":
            self.before_write()
            assert params["file"] == "keymap.json"
            self.writes.append(params["data"])
            if not self.ignores_writes:
                self.raw = params["data"]
            result = {"ok": 1}
        else:
            raise AssertionError(method)
        if method == "fs.write" and self.empty_write_ack:
            return Receipt("applied"), {"id": 1, "method": method, "result": None}
        return Receipt("applied"), {"id": 1, "method": method, "params": result}


def service(tmp_path, device=None, serial="test-device", **kwargs):
    assert importlib.util.find_spec("sidepulse.creator_micro_setup"), "keymap setup is not implemented"
    module = importlib.import_module("sidepulse.creator_micro_setup")
    return module.CreatorMicroSetup(device or Device(), serial, tmp_path / "backup.json", **kwargs)


def test_inspect_only_reads_and_does_not_create_backup_or_program_device(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    plan = setup.inspect()
    assert plan.changes
    assert plan.original_json == keymap()
    assert device.writes == []
    assert not (tmp_path / "backup.json").exists()


@pytest.mark.parametrize("empty_write_ack", [False, True])
def test_apply_persists_private_verified_backup_before_write_and_restore_round_trips(tmp_path, empty_write_ack):
    device = Device(empty_write_ack=empty_write_ack)
    setup = service(tmp_path, device)
    original = device.raw
    plan = setup.inspect()

    def backup_exists_before_write():
        backup = tmp_path / "backup.json"
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert json.loads(backup.read_text())["original_json"] == original

    device.before_write = backup_exists_before_write
    assert setup.apply(plan).code == "keymap_verified"
    assert json.loads(device.raw)["profiles"][0]["layers"][0]["layout"]["keymap"][0] == [
        "KV_OAI_AG00", "KV_OAI_AG01",
    ]
    assert setup.restore().code == "keymap_restored"
    assert device.raw == original
    assert len(device.writes) == 2


@pytest.mark.parametrize("empty_write_ack", [False, True])
def test_acknowledged_but_ignored_write_fails_verification_and_keeps_backup(tmp_path, empty_write_ack):
    device = Device(ignores_writes=True, empty_write_ack=empty_write_ack)
    setup = service(tmp_path, device)
    assert setup.apply(setup.inspect()).code == "readback_mismatch"
    assert (tmp_path / "backup.json").exists()


def test_apply_refuses_keymap_or_layer_changed_since_preview(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    plan = setup.inspect()
    device.raw = keymap().replace("KC_A", "KC_Z")
    assert setup.apply(plan).code == "keymap_changed"
    assert not device.writes
    device.raw = keymap()
    device.status = {"layer_index": 2}
    assert setup.apply(plan).code == "keymap_changed"
    assert not device.writes


def test_restore_refuses_external_edits_or_different_device(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    assert setup.apply(setup.inspect()).code == "keymap_verified"
    device.raw = device.raw.replace("KV_OAI_AG00", "KC_Z")
    assert setup.restore().code == "keymap_changed"
    other = service(tmp_path, device, serial="other-device")
    assert other.restore().code == "backup_invalid"
    assert len(device.writes) == 1


def test_backup_write_failure_or_existing_unrelated_backup_never_writes_device(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    (tmp_path / "backup.json").symlink_to(tmp_path / "missing")
    assert setup.apply(setup.inspect()).code == "backup_failed"
    assert not device.writes


def test_restore_does_not_trust_tampered_backup(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    assert setup.apply(setup.inspect()).code == "keymap_verified"
    backup = tmp_path / "backup.json"
    document = json.loads(backup.read_text())
    document["original_json"] = "{}"
    backup.write_text(json.dumps(document))
    assert setup.restore().code == "backup_invalid"
    assert len(device.writes) == 1


def test_noop_setup_preserves_first_backup(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    assert setup.apply(setup.inspect()).code == "keymap_verified"
    before = (tmp_path / "backup.json").read_bytes()
    assert setup.apply(setup.inspect()).code == "already_configured"
    assert (tmp_path / "backup.json").read_bytes() == before
    assert len(device.writes) == 1


def test_inspect_refuses_missing_status_or_non_file_response(tmp_path):
    device = Device()
    device.status = {"ok": 1}
    with pytest.raises(ValueError):
        service(tmp_path, device).inspect()
    assert not device.writes


def test_empty_file_read_result_is_not_treated_as_a_write_acknowledgement(tmp_path):
    class EmptyReadDevice(Device):
        def _call(self, method, params):
            if method == "fs.read":
                return Receipt("applied"), {"id": 1, "method": method, "result": None}
            return super()._call(method, params)

    device = EmptyReadDevice()
    with pytest.raises(ValueError, match="malformed_report"):
        service(tmp_path, device).inspect()
    assert device.writes == []


def test_cancelled_generation_cannot_write_a_preview_or_restore(tmp_path):
    device = Device()
    current = [True]
    setup = service(tmp_path, device, is_current=lambda: current[0])
    plan = setup.inspect()
    current[0] = False
    assert setup.apply(plan).code == "cancelled"
    assert not device.writes
    current[0] = True
    assert setup.apply(plan).code == "keymap_verified"
    current[0] = False
    assert setup.restore().code == "cancelled"
    assert len(device.writes) == 1


def test_cancellation_after_backup_before_flash_retains_backup_without_programming(tmp_path):
    device = Device()
    setup = service(tmp_path, device, is_current=lambda: not (tmp_path / "backup.json").exists())
    assert setup.apply(setup.inspect()).code == "cancelled"
    assert (tmp_path / "backup.json").exists()
    assert not device.writes


def test_backup_conflict_never_replaces_the_only_recoverable_original(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    assert setup.apply(setup.inspect()).code == "keymap_verified"
    original_backup = (tmp_path / "backup.json").read_bytes()
    device.raw = keymap().replace("KC_A", "KC_Z")
    assert setup.apply(setup.inspect()).code == "backup_failed"
    assert (tmp_path / "backup.json").read_bytes() == original_backup
    assert len(device.writes) == 1


def test_device_disconnect_during_readback_keeps_original_for_recovery(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    device.before_write = lambda: setattr(device, "connected", False)
    assert setup.apply(setup.inspect()).code == "not_connected"
    assert (tmp_path / "backup.json").exists()


def test_a_backup_created_by_another_process_before_publish_is_never_overwritten(tmp_path, monkeypatch):
    module = importlib.import_module("sidepulse.creator_micro_setup")
    real_write = module.atomic_private_write
    backup = tmp_path / "backup.json"

    def race(path, data, **kwargs):
        real_write(path, '{"other":"existing recovery data"}')
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_private_write", race)
    device = Device()
    setup = service(tmp_path, device)
    assert setup.apply(setup.inspect()).code == "backup_failed"
    assert backup.read_text() == '{"other":"existing recovery data"}'
    assert not device.writes


def test_a_disconnected_device_is_not_misreported_as_an_edited_keymap(tmp_path):
    device = Device()
    setup = service(tmp_path, device)
    plan = setup.inspect()
    device.connected = False
    assert setup.apply(plan).code == "not_connected"
    assert not device.writes


def test_large_keymap_round_trip_through_real_adapter_and_fragment_decoder(tmp_path):
    from collections import deque

    from sidepulse.creator_micro_adapter import CreatorMicro2Adapter, CreatorMicro2Framer, RpcStreamDecoder

    document = json.loads(keymap())
    document["macro"] = "quoted \\\" braces {} é " * 1000
    firmware = Device(json.dumps(document, ensure_ascii=False))
    original = firmware.raw

    class Wire:
        def __init__(self):
            self.decoder = RpcStreamDecoder(max_bytes=132_096)
            self.responses = deque()

        def open(self, *, nonexclusive):
            pass

        def close(self):
            pass

        def write(self, report):
            for request in self.decoder.feed(report):
                _, response = firmware._call(request["method"], request["params"])
                response["id"] = request["id"]
                self.responses.extend(CreatorMicro2Framer._encode(response, max_bytes=132_096))

        def read(self, *, timeout_ms):
            return self.responses.popleft() if self.responses else None

    wire = Wire()
    adapter = CreatorMicro2Adapter(wire, {
        "vendor_id": 0x303A, "product_id": 0x8297, "usage_page": 0xFF00, "usage": 1,
    }, rpc_max_bytes=132_096)
    assert adapter.connect().code == "connected"
    setup = service(tmp_path, adapter)
    assert setup.apply(setup.inspect()).code == "keymap_verified"
    assert json.loads(firmware.raw)["macro"] == document["macro"]
    assert setup.restore().code == "keymap_restored"
    assert firmware.raw == original
