from __future__ import annotations

from sidepulse.device_identity import (
    DeviceHardwareFacts,
    DeviceKind,
    RememberedDeviceRow,
    derive_device_identity,
    migrate_remembered_devices,
    normalize_device_label,
)


def facts(**changes) -> DeviceHardwareFacts:
    values = dict(
        mount_path="/Volumes/SidePulse",
        product_name="SidePulse Dot",
        volume_uuid="A1B2-C3D4",
        disk_identifier="disk4s1",
        serial_number=None,
        connected=True,
    )
    values.update(changes)
    return DeviceHardwareFacts(**values)


def test_serial_number_is_the_strongest_stable_identity() -> None:
    identity = derive_device_identity(facts(serial_number="SERIAL-123"))
    assert identity.kind is DeviceKind.DOT
    assert identity.key.startswith("sidepulse:dot:serial:")
    assert "SERIAL-123" not in identity.key


def test_volume_uuid_survives_mount_path_change() -> None:
    first = derive_device_identity(facts(mount_path="/Volumes/SidePulse"))
    second = derive_device_identity(
        facts(mount_path="/Volumes/SidePulse 1", disk_identifier="disk9s1")
    )
    assert first.key == second.key


def test_disk_identifier_is_used_when_uuid_is_unavailable() -> None:
    identity = derive_device_identity(facts(volume_uuid=None))
    assert identity.key.startswith("sidepulse:dot:disk:")


def test_virtual_screen_bar_has_one_fixed_identity() -> None:
    identity = derive_device_identity(
        facts(
            mount_path="screen-bar",
            product_name="Screen Bar",
            volume_uuid=None,
            disk_identifier=None,
            serial_number=None,
        )
    )
    assert identity.kind is DeviceKind.SCREEN_BAR
    assert identity.key == "sidepulse:screen-bar"


def test_ephemeral_and_non_sidepulse_mounts_are_rejected() -> None:
    assert derive_device_identity(
        facts(mount_path="/var/folders/x/SidePulseDot")
    ) is None
    assert derive_device_identity(
        facts(mount_path="/Volumes/Backup", product_name="Backup")
    ) is None


def test_device_labels_never_repeat_the_product_suffix() -> None:
    assert normalize_device_label("SidePulse Dot Dot", DeviceKind.DOT) == "SidePulse Dot"
    assert normalize_device_label("SidePulse Pro Pro", DeviceKind.PRO) == "SidePulse Pro"
    assert normalize_device_label("SidePulse", DeviceKind.DOT) == "SidePulse Dot"


def test_migration_merges_remounts_and_preserves_preferences() -> None:
    rows = (
        RememberedDeviceRow(
            device_id="/Volumes/SidePulse",
            name="SidePulse Dot",
            path="/Volumes/SidePulse",
            preferences={"brightness": 90, "provider_pin": "grok"},
            updated_at=10.0,
        ),
        RememberedDeviceRow(
            device_id="/Volumes/SidePulse 1",
            name="SidePulse Dot Dot",
            path="/Volumes/SidePulse 1",
            preferences={"brightness": 120, "resting_glow": 0.1},
            updated_at=20.0,
        ),
        RememberedDeviceRow(
            device_id="/var/folders/x/SidePulseDot",
            name="SidePulse Dot",
            path="/var/folders/x/SidePulseDot",
            preferences={"brightness": 255},
            updated_at=30.0,
        ),
    )
    live = (
        derive_device_identity(facts(mount_path="/Volumes/SidePulse 1")),
    )
    migrated = migrate_remembered_devices(rows, tuple(row for row in live if row))

    assert len(migrated) == 1
    row = migrated[0]
    assert row.device_id.startswith("sidepulse:dot:volume:")
    assert row.name == "SidePulse Dot"
    assert row.path == "/Volumes/SidePulse 1"
    assert row.preferences == {
        "brightness": 120,
        "provider_pin": "grok",
        "resting_glow": 0.1,
    }


def test_disconnected_legacy_rows_are_deduplicated_by_kind() -> None:
    rows = (
        RememberedDeviceRow(
            "/Volumes/Old", "SidePulse Dot", "/Volumes/Old", {"brightness": 50}, 1.0
        ),
        RememberedDeviceRow(
            "/Volumes/Older", "SidePulse Dot Dot", "/Volumes/Older", {"brightness": 80}, 2.0
        ),
    )
    migrated = migrate_remembered_devices(rows, ())
    assert len(migrated) == 1
    assert migrated[0].name == "SidePulse Dot"
    assert migrated[0].preferences["brightness"] == 80
