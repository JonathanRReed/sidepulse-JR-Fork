from __future__ import annotations

import json
from pathlib import Path

from sidepulse.provider_usage_pairing import (
    PAIRING_DOCUMENT_SCHEMA_VERSION,
    export_pairing_document,
    import_pairing_document,
)


class Credentials:
    def __init__(self):
        self.values = {}

    def set(self, provider, account, secret):
        self.values[(provider, account)] = secret


def test_export_is_owner_private_and_contains_bounded_pairing_material(tmp_path: Path):
    target = tmp_path / "pairing.json"
    export_pairing_document(
        local_device_id="mac-mini",
        peer_id="macbook",
        secret_account="pairing-macbook",
        target=target,
        random_bytes=lambda size: b"a" * size,
    )
    document = json.loads(target.read_text())
    assert document["schema_version"] == PAIRING_DOCUMENT_SCHEMA_VERSION
    assert document["local_device_id"] == "mac-mini"
    assert document["peer_id"] == "macbook"
    assert document["secret_account"] == "pairing-macbook"
    assert len(document["shared_secret_b64"]) == 44
    assert target.stat().st_mode & 0o777 == 0o600


def test_import_stores_secret_in_keychain_and_returns_public_metadata(tmp_path: Path):
    target = tmp_path / "pairing.json"
    export_pairing_document(
        local_device_id="mac-mini",
        peer_id="macbook",
        secret_account="pairing-macbook",
        target=target,
        random_bytes=lambda size: b"b" * size,
    )
    credentials = Credentials()
    result = import_pairing_document(target, credentials=credentials)
    assert result.local_device_id == "mac-mini"
    assert result.peer_id == "macbook"
    assert result.secret_account == "pairing-macbook"
    assert credentials.values[("sidepulse-sync", "pairing-macbook")]
    assert "YmJi" not in repr(result)


def test_import_rejects_world_readable_pairing_file(tmp_path: Path):
    target = tmp_path / "pairing.json"
    export_pairing_document(
        local_device_id="mac-mini",
        peer_id="macbook",
        secret_account="pairing-macbook",
        target=target,
        random_bytes=lambda size: b"c" * size,
    )
    target.chmod(0o644)
    try:
        import_pairing_document(target, credentials=Credentials())
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("world-readable pairing document accepted")


def test_import_rejects_unknown_or_malformed_document(tmp_path: Path):
    target = tmp_path / "pairing.json"
    target.write_text(json.dumps({"schema_version": 99}))
    target.chmod(0o600)
    try:
        import_pairing_document(target, credentials=Credentials())
    except ValueError:
        pass
    else:
        raise AssertionError("future pairing document accepted")
