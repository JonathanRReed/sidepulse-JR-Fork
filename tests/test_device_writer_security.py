from __future__ import annotations

import errno
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse import device_writer

PROGRAM_A = "1:#00FF00 1s"
PROGRAM_B = "1:#FF0000 1s"


def _capture_write_error(
    device: Path,
    program: str = PROGRAM_B,
    *,
    replace_error: OSError | None = None,
) -> BaseException | None:
    try:
        if replace_error is None:
            device_writer.write_led_program(program, device_path=device)
        else:
            with patch(
                "sidepulse.device_writer.os.replace",
                side_effect=replace_error,
            ):
                device_writer.write_led_program(program, device_path=device)
    except BaseException as error:
        return error
    return None


@pytest.mark.parametrize(
    "file_name",
    (
        "",
        ".",
        "..",
        "../LEDS.LED",
        "nested/LEDS.LED",
        "/tmp/LEDS.LED",
        "OTHER.LED",
        "W123.TMP",
    ),
)
def test_production_file_names_are_fixed_and_traversal_safe(
    tmp_path: Path,
    file_name: str,
) -> None:
    with pytest.raises(device_writer.DeviceWriteError):
        device_writer.write_led_program(
            PROGRAM_A,
            device_path=tmp_path,
            file_name=file_name,
            dry_run=True,
        )


@pytest.mark.parametrize("file_name", ("LEDS.LED", "INIT.LED"))
def test_known_device_file_names_remain_supported(
    tmp_path: Path,
    file_name: str,
) -> None:
    target = device_writer.write_led_program(
        PROGRAM_A,
        device_path=tmp_path,
        file_name=file_name,
    )

    assert target == tmp_path / file_name
    assert target.read_text(encoding="utf-8") == PROGRAM_A


def test_live_firmware_write_preserves_the_existing_led_file_identity(
    tmp_path: Path,
) -> None:
    """The device firmware watches the existing FAT directory entry.

    A host-side replace can pass exact readback while leaving the physical
    LEDs at their firmware default.  The live path therefore keeps the
    verified leaf identity and writes through the already-held parent.
    """
    target = tmp_path / device_writer.DEFAULT_FILE_NAME
    target.write_text(PROGRAM_A, encoding="utf-8")
    before = target.stat()

    written = device_writer.write_led_program(
        PROGRAM_B,
        device_path=tmp_path,
        preserve_existing_inode=True,
    )

    after = target.stat()
    assert written == target
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert target.read_text(encoding="utf-8") == PROGRAM_B
    assert not tuple(tmp_path.glob("*.TMP"))


def test_live_firmware_write_safely_creates_the_first_led_file(tmp_path: Path) -> None:
    target = device_writer.write_led_program(
        PROGRAM_A,
        device_path=tmp_path,
        preserve_existing_inode=True,
    )

    assert target == tmp_path / device_writer.DEFAULT_FILE_NAME
    assert target.read_text(encoding="utf-8") == PROGRAM_A


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
@pytest.mark.parametrize("fallback", (False, True))
def test_normal_and_enospc_paths_refuse_linked_targets(
    tmp_path: Path,
    link_kind: str,
    fallback: bool,
) -> None:
    device = tmp_path / "device"
    device.mkdir()
    outside = tmp_path / "external-sentinel.LED"
    outside.write_text("external sentinel", encoding="utf-8")
    target = device / device_writer.DEFAULT_FILE_NAME
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    replace_error = (
        OSError(errno.ENOSPC, "No space left on device") if fallback else None
    )
    error = _capture_write_error(device, replace_error=replace_error)

    assert outside.read_text(encoding="utf-8") == "external sentinel"
    assert isinstance(error, OSError)


@pytest.mark.parametrize("fallback", (False, True))
def test_normal_and_enospc_paths_refuse_symlinked_parent(
    tmp_path: Path,
    fallback: bool,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / device_writer.DEFAULT_FILE_NAME
    sentinel.write_text("external sentinel", encoding="utf-8")
    device = tmp_path / "device"
    device.symlink_to(outside, target_is_directory=True)

    replace_error = (
        OSError(errno.ENOSPC, "No space left on device") if fallback else None
    )
    error = _capture_write_error(device, replace_error=replace_error)

    assert sentinel.read_text(encoding="utf-8") == "external sentinel"
    assert isinstance(error, OSError)


def test_explicit_device_path_refuses_any_symlink_ancestor(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    device = outside / "device"
    device.mkdir(parents=True)
    sentinel = device / device_writer.DEFAULT_FILE_NAME
    sentinel.write_text("external sentinel", encoding="utf-8")
    linked_ancestor = tmp_path / "linked-ancestor"
    linked_ancestor.symlink_to(outside, target_is_directory=True)

    error = _capture_write_error(linked_ancestor / "device")

    assert sentinel.read_text(encoding="utf-8") == "external sentinel"
    assert isinstance(error, OSError)


def test_device_discovery_refuses_symlinked_mount_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-mount-root"
    device = outside / "SidePulseDot"
    device.mkdir(parents=True)
    (device / device_writer.DEFAULT_FILE_NAME).write_text(
        PROGRAM_A,
        encoding="utf-8",
    )
    linked_mount_root = tmp_path / "mounts"
    linked_mount_root.symlink_to(outside, target_is_directory=True)

    assert device_writer.discover_devices(mount_root=linked_mount_root) == []


def test_device_discovery_refuses_symlinked_child_volume(
    tmp_path: Path,
) -> None:
    mount_root = tmp_path / "mounts"
    mount_root.mkdir()
    outside = tmp_path / "outside-device"
    outside.mkdir()
    (outside / device_writer.DEFAULT_FILE_NAME).write_text(
        PROGRAM_A,
        encoding="utf-8",
    )
    (mount_root / "SidePulseDot").symlink_to(
        outside,
        target_is_directory=True,
    )

    assert device_writer.discover_devices(mount_root=mount_root) == []


def test_predictable_preplanted_scratch_cannot_modify_external_sentinel(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "external-sentinel.txt"
    sentinel.write_text("external sentinel", encoding="utf-8")
    predictable_scratch = tmp_path / f"W{os.getpid() % 10000000}.TMP"
    predictable_scratch.symlink_to(sentinel)

    target = device_writer.write_led_program(PROGRAM_A, device_path=tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "external sentinel"
    assert target.read_text(encoding="utf-8") == PROGRAM_A


def test_leaf_replacement_before_normal_publish_is_refused(
    tmp_path: Path,
) -> None:
    target = device_writer.write_led_program(PROGRAM_A, device_path=tmp_path)
    outside = tmp_path / "outside-sentinel.txt"
    outside.write_text("external sentinel", encoding="utf-8")
    real_publish = device_writer._legacy._publish_scratch
    replaced = False

    def replacing_publish(*args, **kwargs):
        nonlocal replaced
        target.unlink()
        target.symlink_to(outside)
        replaced = True
        return real_publish(*args, **kwargs)

    with (
        patch.object(
            device_writer._legacy,
            "_publish_scratch",
            side_effect=replacing_publish,
        ),
        pytest.raises(OSError),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=tmp_path)

    assert replaced
    assert outside.read_text(encoding="utf-8") == "external sentinel"


def test_parent_replacement_before_normal_publish_is_refused(
    tmp_path: Path,
) -> None:
    device = tmp_path / "device"
    device.mkdir()
    target = device_writer.write_led_program(PROGRAM_A, device_path=device)
    held_device = tmp_path / "held-device"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / target.name
    outside_target.write_text("external sentinel", encoding="utf-8")
    real_publish = device_writer._legacy._publish_scratch
    replaced = False

    def replacing_publish(*args, **kwargs):
        nonlocal replaced
        device.rename(held_device)
        device.symlink_to(outside, target_is_directory=True)
        replaced = True
        return real_publish(*args, **kwargs)

    with (
        patch.object(
            device_writer._legacy,
            "_publish_scratch",
            side_effect=replacing_publish,
        ),
        pytest.raises(OSError),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=device)

    assert replaced
    assert (held_device / target.name).read_text(encoding="utf-8") == PROGRAM_A
    assert outside_target.read_text(encoding="utf-8") == "external sentinel"


def test_leaf_replacement_that_triggers_enospc_fallback_is_refused(
    tmp_path: Path,
) -> None:
    device = tmp_path / "device"
    device.mkdir()
    target = device_writer.write_led_program(PROGRAM_A, device_path=device)
    outside = tmp_path / "outside-sentinel.txt"
    outside.write_text("external sentinel", encoding="utf-8")

    def replace_with_link_then_report_full(*_args, **_kwargs):
        target.unlink()
        target.symlink_to(outside)
        raise OSError(errno.ENOSPC, "No space left on device")

    with (
        patch(
            "sidepulse.device_writer.os.replace",
            side_effect=replace_with_link_then_report_full,
        ),
        pytest.raises(OSError),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=device)

    assert outside.read_text(encoding="utf-8") == "external sentinel"


def test_parent_replacement_that_triggers_enospc_fallback_is_refused(
    tmp_path: Path,
) -> None:
    device = tmp_path / "device"
    device.mkdir()
    target = device_writer.write_led_program(PROGRAM_A, device_path=device)
    held_device = tmp_path / "held-device"
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_target = outside / target.name
    outside_target.write_text("external sentinel", encoding="utf-8")

    def replace_parent_then_report_full(*_args, **_kwargs):
        device.rename(held_device)
        device.symlink_to(outside, target_is_directory=True)
        raise OSError(errno.ENOSPC, "No space left on device")

    with (
        patch(
            "sidepulse.device_writer.os.replace",
            side_effect=replace_parent_then_report_full,
        ),
        pytest.raises(OSError),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=device)

    assert (held_device / target.name).read_text(encoding="utf-8") == PROGRAM_A
    assert outside_target.read_text(encoding="utf-8") == "external sentinel"


def test_concurrent_same_target_writes_use_independent_scratch_files(
    tmp_path: Path,
) -> None:
    device_writer.write_led_program("off", device_path=tmp_path)
    barrier = threading.Barrier(2, timeout=5)
    real_replace = os.replace
    scratch_names: list[str] = []

    def synchronized_replace(source, destination, *args, **kwargs):
        scratch_names.append(str(source))
        barrier.wait()
        return real_replace(source, destination, *args, **kwargs)

    def write(program: str) -> BaseException | None:
        try:
            device_writer.write_led_program(program, device_path=tmp_path)
        except BaseException as error:
            return error
        return None

    with patch("sidepulse.device_writer.os.replace", side_effect=synchronized_replace):
        with ThreadPoolExecutor(max_workers=2) as executor:
            errors = list(executor.map(write, (PROGRAM_A, PROGRAM_B)))

    assert errors.count(None) >= 1
    assert all(error is None or isinstance(error, OSError) for error in errors)
    assert not any(isinstance(error, FileNotFoundError) for error in errors)
    assert len(set(scratch_names)) == 2
    assert (tmp_path / device_writer.DEFAULT_FILE_NAME).read_text(
        encoding="utf-8"
    ) in {PROGRAM_A, PROGRAM_B}
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        device_writer.DEFAULT_FILE_NAME
    ]


@pytest.mark.parametrize("fallback", (False, True))
def test_success_requires_bounded_exact_readback(
    tmp_path: Path,
    fallback: bool,
) -> None:
    device_writer.write_led_program(PROGRAM_A, device_path=tmp_path)
    requested_sizes: list[int] = []
    real_read = os.read

    def observing_read(descriptor: int, size: int) -> bytes:
        requested_sizes.append(size)
        return real_read(descriptor, size)

    replace = (
        patch(
            "sidepulse.device_writer.os.replace",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        )
        if fallback
        else patch("sidepulse.device_writer.os.replace", wraps=os.replace)
    )
    with replace, patch("sidepulse.device_writer.os.read", side_effect=observing_read):
        target = device_writer.write_led_program(PROGRAM_B, device_path=tmp_path)

    assert target.read_text(encoding="utf-8") == PROGRAM_B
    assert requested_sizes
    assert max(requested_sizes) <= len(PROGRAM_B.encode("utf-8")) + 1


@pytest.mark.parametrize("fallback", (False, True))
def test_readback_mismatch_never_reports_success(
    tmp_path: Path,
    fallback: bool,
) -> None:
    device_writer.write_led_program(PROGRAM_A, device_path=tmp_path)
    replace = (
        patch(
            "sidepulse.device_writer.os.replace",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        )
        if fallback
        else patch("sidepulse.device_writer.os.replace", wraps=os.replace)
    )

    with (
        replace,
        patch("sidepulse.device_writer.os.read", return_value=b"corrupt"),
        pytest.raises(device_writer.DeviceWriteError, match="readback"),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=tmp_path)


def test_failed_scratch_write_preserves_prior_complete_target(
    tmp_path: Path,
) -> None:
    target = device_writer.write_led_program(PROGRAM_A, device_path=tmp_path)

    with (
        patch(
            "sidepulse.device_writer.os.write",
            side_effect=OSError(errno.EIO, "device disconnected"),
        ),
        pytest.raises(OSError, match="device disconnected"),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=tmp_path)

    assert target.read_text(encoding="utf-8") == PROGRAM_A
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]


def test_rejected_opened_scratch_is_removed_without_touching_target(
    tmp_path: Path,
) -> None:
    target = device_writer.write_led_program(PROGRAM_A, device_path=tmp_path)
    real_require_regular_leaf = device_writer._require_regular_leaf

    def reject_scratch(path: Path, *args, **kwargs) -> None:
        if path.suffix == ".TMP":
            raise OSError("scratch identity rejected")
        real_require_regular_leaf(path, *args, **kwargs)

    with (
        patch.object(
            device_writer._legacy,
            "_require_regular_leaf",
            side_effect=reject_scratch,
        ),
        pytest.raises(OSError, match="scratch identity rejected"),
    ):
        device_writer.write_led_program(PROGRAM_B, device_path=tmp_path)

    assert target.read_text(encoding="utf-8") == PROGRAM_A
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]
