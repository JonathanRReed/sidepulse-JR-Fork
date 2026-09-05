from __future__ import annotations

import plistlib
import subprocess
import threading
from pathlib import Path

from sidepulse.device_identity import DeviceKind
from sidepulse.device_inventory import (
    DeviceIdentityCache,
    diskutil_facts,
    inventory_mounts,
)


def completed(payload: dict, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["diskutil"],
        returncode=returncode,
        stdout=plistlib.dumps(payload),
        stderr=b"",
    )


def test_diskutil_facts_extracts_stable_identifiers(tmp_path: Path) -> None:
    mount = tmp_path / "SidePulseDot"
    mount.mkdir()

    def runner(arguments, **kwargs):
        assert arguments[-1] == str(mount)
        assert kwargs["timeout"] <= 2.0
        return completed(
            {
                "MountPoint": str(mount),
                "VolumeName": "SidePulseDot",
                "VolumeUUID": "A1B2-C3D4",
                "DeviceIdentifier": "disk4s1",
                "DiskUUID": "SERIAL-ISH",
            }
        )

    facts = diskutil_facts(mount, runner=runner)
    assert facts is not None
    assert facts.volume_uuid == "A1B2-C3D4"
    assert facts.disk_identifier == "disk4s1"
    assert facts.serial_number == "SERIAL-ISH"


def test_inventory_ignores_non_sidepulse_and_failed_probes(tmp_path: Path) -> None:
    for name in ("SidePulseDot", "Backup", "SidePulsePro"):
        (tmp_path / name).mkdir()

    def runner(arguments, **kwargs):
        path = Path(arguments[-1])
        if path.name == "SidePulsePro":
            return completed({}, returncode=1)
        return completed(
            {
                "MountPoint": str(path),
                "VolumeName": path.name,
                "VolumeUUID": path.name + "-uuid",
                "DeviceIdentifier": "disk4s1",
            }
        )

    identities = inventory_mounts(tmp_path, runner=runner)
    assert len(identities) == 1
    assert identities[0].kind is DeviceKind.DOT


def test_inventory_rejects_mount_replaced_during_identity_probe(tmp_path: Path) -> None:
    mount = tmp_path / "SidePulseDot"
    mount.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    def runner(arguments, **kwargs):
        mount.rename(tmp_path / "detached")
        replacement.rename(mount)
        return completed(
            {
                "MountPoint": str(mount),
                "VolumeName": "SidePulseDot",
                "VolumeUUID": "stale-volume-uuid",
            }
        )

    assert inventory_mounts(tmp_path, runner=runner) == ()


def test_identity_cache_returns_last_snapshot_without_blocking(tmp_path: Path) -> None:
    root = tmp_path / "Volumes"
    mount = root / "SidePulseDot"
    mount.mkdir(parents=True)

    def inventory():
        return inventory_mounts(
            root,
            runner=lambda arguments, **kwargs: completed(
                {
                    "MountPoint": str(mount),
                    "VolumeName": "SidePulseDot",
                    "VolumeUUID": "stable-uuid",
                    "DeviceIdentifier": "disk4s1",
                }
            ),
        )

    cache = DeviceIdentityCache(inventory=inventory)
    assert cache.snapshot() == ()
    assert cache.request_refresh() is True
    assert cache.wait(2.0) is True
    assert len(cache.snapshot()) == 1
    assert cache.identity_for_mount(mount).key.startswith("sidepulse:dot:")
    cache.close()


def test_refresh_requests_are_latest_wins(tmp_path: Path) -> None:
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def inventory():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(2.0)
        return ()

    cache = DeviceIdentityCache(inventory=inventory)
    assert cache.request_refresh() is True
    assert started.wait(1.0)
    assert cache.request_refresh() is False
    assert cache.request_refresh() is False
    release.set()
    assert cache.wait(2.0) is True
    assert calls == 2
    cache.close()
