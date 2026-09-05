"""Explicit keymap setup with a private backup and verified device readback.

Call from the sole device owner, never AppKit's main thread. Inspection performs
RPC reads only. Apply and restore are separate user-confirmed operations.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from .creator_micro_adapter import Receipt
from .creator_micro_keymap import KeymapPlan, keymap_digest, plan_keymap
from .private_io import atomic_private_write, read_private_text


class SetupError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def device_backup_key(serial: str) -> str:
    if type(serial) is not str or not serial.strip() or len(serial) > 256 or not serial.isprintable():
        raise ValueError("invalid approved device identity")
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


class CreatorMicroSetup:
    def __init__(self, adapter, approved_serial: str, backup_path: Path, *, is_current: Callable[[], bool] = lambda: True):
        self.adapter = adapter
        self.device_key = device_backup_key(approved_serial)
        self.backup_path = Path(backup_path)
        self.is_current = is_current

    def _rpc(self, method: str, params=None) -> dict:
        if not self.adapter.connected:
            raise SetupError("not_connected")
        receipt, response = self.adapter._call(method, params)
        if receipt.code != "applied":
            raise SetupError(receipt.code)
        if type(response) is not dict:
            raise SetupError("malformed_report")
        value = response.get("result", response.get("params"))
        if method == "fs.write" and value is None:
            # Firmware acknowledges writes with null. Only the subsequent
            # file readback can establish that the intended data was stored.
            return {}
        if type(value) is not dict:
            raise SetupError("malformed_report")
        return value

    def _read_keymap(self) -> str:
        raw = self._rpc("fs.read", {"file": "keymap.json"}).get("data")
        keymap_digest(raw)
        return raw

    def inspect(self) -> KeymapPlan:
        before = self._rpc("device.status")
        raw = self._read_keymap()
        after = self._rpc("device.status")
        if before.get("layer_index") != after.get("layer_index"):
            raise SetupError("keymap_changed")
        return plan_keymap(raw, after)

    def _backup_document(self, plan: KeymapPlan) -> dict:
        return {
            "version": 1, "device_key": self.device_key,
            "original_json": plan.original_json, "proposed_json": plan.proposed_json,
            "original_digest": plan.original_digest, "proposed_digest": plan.proposed_digest,
        }

    def _load_backup(self) -> dict:
        raw = read_private_text(self.backup_path, max_bytes=266_240)
        document = json.loads(raw)
        if (
            type(document) is not dict
            or set(document) != {"version", "device_key", "original_json", "proposed_json",
                                 "original_digest", "proposed_digest"}
            or type(document["version"]) is not int or document["version"] != 1
            or document["device_key"] != self.device_key
            or keymap_digest(document["original_json"]) != document["original_digest"]
            or keymap_digest(document["proposed_json"]) != document["proposed_digest"]
        ):
            raise SetupError("backup_invalid")
        return document

    def _save_backup(self, plan: KeymapPlan) -> None:
        expected = self._backup_document(plan)
        try:
            existing = self._load_backup()
        except FileNotFoundError:
            atomic_private_write(self.backup_path, json.dumps(expected, ensure_ascii=False) + "\n", overwrite=False)
        else:
            # Never replace the first recoverable keymap with a later state.
            if existing != expected:
                raise SetupError("backup_conflict")
        if self._load_backup() != expected:
            raise SetupError("backup_failed")

    def _write_verified(self, raw: str, digest: str, success_code: str) -> Receipt:
        if not self.is_current():
            return Receipt("cancelled", "No keymap was written.")
        try:
            self._rpc("fs.write", {"file": "keymap.json", "data": raw})
            actual = self._read_keymap()
            if keymap_digest(actual) != digest:
                return Receipt("readback_mismatch", "Backup retained; device state was not verified.")
        except SetupError as exc:
            return Receipt(exc.code, "Backup retained; device state was not verified.")
        except (ValueError, OSError):
            return Receipt("readback_failed", "Backup retained; device state was not verified.")
        return Receipt(success_code)

    def apply(self, plan: KeymapPlan) -> Receipt:
        if not self.is_current():
            return Receipt("cancelled", "No keymap was written.")
        if type(plan) is not KeymapPlan:
            return Receipt("invalid_plan")
        try:
            # Recompute the reviewed transformation so a forged/stale plan
            # cannot write arbitrary JSON through the setup confirmation.
            expected = plan_keymap(plan.original_json, {"layer_index": plan.layer_index + 1})
            if expected != plan:
                return Receipt("invalid_plan")
            current = self.inspect()
            if (current.original_digest, current.profile_index, current.layer_index) != (
                plan.original_digest, plan.profile_index, plan.layer_index,
            ):
                return Receipt("keymap_changed")
        except SetupError as exc:
            return Receipt(exc.code)
        except (ValueError, OSError):
            return Receipt("keymap_changed")
        if not plan.changes:
            return Receipt("already_configured")
        try:
            self._save_backup(plan)
        except (ValueError, OSError):
            return Receipt("backup_failed", "No keymap was written.")
        return self._write_verified(plan.proposed_json, plan.proposed_digest, "keymap_verified")

    def restore(self) -> Receipt:
        if not self.is_current():
            return Receipt("cancelled", "No keymap was written.")
        try:
            backup = self._load_backup()
        except (ValueError, OSError):
            return Receipt("backup_invalid", "No keymap was written.")
        try:
            current = keymap_digest(self._read_keymap())
        except (ValueError, OSError):
            return Receipt("readback_failed", "No keymap was written.")
        if current == backup["original_digest"]:
            return Receipt("already_restored")
        if current != backup["proposed_digest"]:
            return Receipt("keymap_changed", "Refusing to overwrite later device edits.")
        return self._write_verified(backup["original_json"], backup["original_digest"], "keymap_restored")
