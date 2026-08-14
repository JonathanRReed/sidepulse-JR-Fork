from __future__ import annotations

import json
import math
import os
import stat
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.operator_export import (
    DEBUG_EXPORT_DOCUMENT,
    HISTORY_EXPORT_DOCUMENT,
    DebugExportV1,
    ExportValidationError,
    HistoryExportV1,
    encode_debug_export,
    encode_history_export,
)
from sidepulse.operator_history import (
    HistoryCoverage,
    HistoryValidationError,
    OperatorHistoryDay,
)
from sidepulse.private_export import (
    PUBLIC_EXPORT_ERROR_MESSAGE,
    PrivateExportError,
    write_private_export,
)
from sidepulse.provider_contracts import ProviderIdentifier


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _day(
    day_key: str = "2026-08-12",
    *,
    provider: str = "codex",
    completed: int = 1,
) -> OperatorHistoryDay:
    return OperatorHistoryDay(
        day_key=day_key,
        timezone_offset_minutes=-300,
        provider_id=ProviderIdentifier(provider),
        started=1,
        needs_user=1,
        completed=completed,
        failed=0,
        acknowledged=1,
        active_duration_bands=(1, 0, 0, 0),
        attention_wait_bands=(0, 1, 0, 0),
        primary_count=1,
        worker_count=0,
        source_recoveries=0,
        device_recoveries=0,
        coverage=HistoryCoverage.COMPLETE,
        sample_count=4,
    )


def _history_export(*days: OperatorHistoryDay) -> HistoryExportV1:
    return HistoryExportV1(
        generated_at=1_754_976_000.25,
        retention_days=30,
        days=days or (_day(),),
    )


def _debug_export(**changes: object) -> DebugExportV1:
    values: dict[str, object] = {
        "generated_at": 1_754_976_000.25,
        "app_version": "0.1.0",
        "build_trust": "source_checkout",
        "provider_health_counts": (("healthy", 2), ("partial", 1)),
        "delivery_disposition_counts": (("delivered", 4), ("failed", 1)),
        "device_health_counts": (("healthy", 1), ("not_updating", 1)),
        "history_health": "healthy",
    }
    values.update(changes)
    return DebugExportV1(**values)


def test_history_export_has_exact_deterministic_versioned_schema() -> None:
    first = _day("2026-08-11", provider="claude", completed=0)
    second = _day("2026-08-12")

    encoded = encode_history_export(_history_export(second, first))
    reordered = encode_history_export(_history_export(first, second))
    document = json.loads(encoded)

    assert encoded == reordered
    assert encoded.endswith(b"\n")
    assert set(document) == {
        "days",
        "document",
        "generated_at",
        "retention_days",
        "version",
    }
    assert document["document"] == HISTORY_EXPORT_DOCUMENT
    assert document["version"] == 1
    assert document["generated_at"] == 1_754_976_000.25
    assert document["retention_days"] == 30
    assert [row["day_key"] for row in document["days"]] == [
        "2026-08-11",
        "2026-08-12",
    ]
    assert set(document["days"][0]) == {
        "acknowledged",
        "active_duration_bands",
        "attention_wait_bands",
        "completed",
        "coverage",
        "day_key",
        "device_recoveries",
        "failed",
        "needs_user",
        "primary_count",
        "provider_id",
        "sample_count",
        "source_recoveries",
        "started",
        "timezone_offset_minutes",
        "worker_count",
    }


def test_debug_export_has_exact_deterministic_versioned_schema() -> None:
    encoded = encode_debug_export(_debug_export())
    reordered = encode_debug_export(
        _debug_export(
            provider_health_counts=(("partial", 1), ("healthy", 2)),
            delivery_disposition_counts=(("failed", 1), ("delivered", 4)),
            device_health_counts=(("not_updating", 1), ("healthy", 1)),
        )
    )
    document = json.loads(encoded)

    assert encoded == reordered
    assert encoded.endswith(b"\n")
    assert set(document) == {
        "app_version",
        "build_trust",
        "delivery_disposition_counts",
        "device_health_counts",
        "document",
        "generated_at",
        "history_health",
        "provider_health_counts",
        "version",
    }
    assert document["document"] == DEBUG_EXPORT_DOCUMENT
    assert document["version"] == 1
    assert document["provider_health_counts"] == [
        ["healthy", 2],
        ["partial", 1],
    ]


def test_export_dto_field_manifests_are_exact_and_distinct() -> None:
    assert tuple(field.name for field in fields(HistoryExportV1)) == (
        "generated_at",
        "retention_days",
        "days",
    )
    assert tuple(field.name for field in fields(DebugExportV1)) == (
        "generated_at",
        "app_version",
        "build_trust",
        "provider_health_counts",
        "delivery_disposition_counts",
        "device_health_counts",
        "history_health",
    )
    assert HISTORY_EXPORT_DOCUMENT != DEBUG_EXPORT_DOCUMENT


@pytest.mark.parametrize("encoder", (encode_history_export, encode_debug_export))
def test_export_encoders_reject_unknown_objects(encoder) -> None:
    with pytest.raises(ExportValidationError, match="export object"):
        encoder(object())


@pytest.mark.parametrize("generated_at", (math.nan, math.inf, -math.inf, -1.0, True))
def test_export_dtos_reject_invalid_generated_time(generated_at: object) -> None:
    with pytest.raises(ExportValidationError, match="generated_at"):
        HistoryExportV1(generated_at, 30, (_day(),))
    with pytest.raises(ExportValidationError, match="generated_at"):
        _debug_export(generated_at=generated_at)


@pytest.mark.parametrize("retention_days", (-1, 1, 14, 91, 30.0, True))
def test_history_export_rejects_unapproved_retention(retention_days: object) -> None:
    with pytest.raises(ExportValidationError, match="retention"):
        HistoryExportV1(1.0, retention_days, (_day(),))


def test_history_export_rejects_duplicate_daily_row_identity() -> None:
    with pytest.raises(ExportValidationError, match="duplicate"):
        _history_export(_day(), _day(completed=0))


@pytest.mark.parametrize(
    "provider",
    (
        "secret-cache",
        "token-store",
        "prompt-data",
        "message-body",
        "command-output",
        "transcript-path",
        "raw-error",
        "email-address",
        "session-id",
        "account-id",
        "navigation-target",
        "abcdef0123456789abcdef0123456789",
    ),
)
def test_history_export_rejects_private_shaped_provider_values(provider: str) -> None:
    with pytest.raises(
        (ExportValidationError, HistoryValidationError),
        match="private",
    ):
        _history_export(_day(provider=provider))


@pytest.mark.parametrize(
    "app_version",
    (
        "/Users/example/project",
        "person@example.com",
        "https://example.com/private",
        "rm -rf project",
        "Bearer secret-token",
        "session_01HZX5BX8J8DG6YF7JMV2J0E2G",
        "Traceback: PermissionError",
        "1.0-secret",
        "123e4567-e89b-12d3-a456-426614174000",
    ),
)
def test_debug_export_rejects_private_shaped_app_versions(app_version: str) -> None:
    with pytest.raises(ExportValidationError, match="app_version"):
        _debug_export(app_version=app_version)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("build_trust", "credential_dump"),
        ("history_health", "raw_error"),
        ("provider_health_counts", (("session_id", 1),)),
        ("delivery_disposition_counts", (("prompt_body", 1),)),
        ("device_health_counts", (("private_path", 1),)),
    ),
)
def test_debug_export_rejects_non_allowlisted_labels(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ExportValidationError, match="allowlisted"):
        _debug_export(**{field_name: value})


@pytest.mark.parametrize("count", (-1, True, 1.5, 1_000_001))
def test_debug_export_rejects_invalid_counts(count: object) -> None:
    with pytest.raises(ExportValidationError, match="count"):
        _debug_export(provider_health_counts=(("healthy", count),))


def test_debug_export_rejects_duplicate_count_labels() -> None:
    with pytest.raises(ExportValidationError, match="duplicate"):
        _debug_export(provider_health_counts=(("healthy", 1), ("healthy", 2)))


def test_encoded_exports_exclude_private_sentinel_corpus() -> None:
    encoded = encode_history_export(_history_export()) + encode_debug_export(_debug_export())
    for sentinel in (
        b"/Users/example",
        b"person@example.com",
        b"https://",
        b"secret-token",
        b"private prompt",
        b"raw error",
        b"session_01HZX5BX8J8DG6YF7JMV2J0E2G",
        b"rm -rf",
    ):
        assert sentinel not in encoded


def test_history_encoder_enforces_two_mibibyte_cap() -> None:
    rows = []
    start = date(2000, 1, 1)
    for index in range(7_000):
        rows.append(_day((start + timedelta(days=index)).isoformat()))
    export = _history_export(*rows)

    with pytest.raises(ExportValidationError, match="maximum size"):
        encode_history_export(export)


def test_debug_encoder_enforces_512_kibibyte_cap() -> None:
    export = _debug_export()
    with (
        patch("sidepulse.operator_export.MAX_DEBUG_EXPORT_BYTES", 1),
        pytest.raises(ExportValidationError, match="maximum size"),
    ):
        encode_debug_export(export)


@pytest.mark.parametrize("parent_mode", (0o700, 0o755))
def test_private_export_preserves_parent_mode_and_writes_exact_0600_leaf(
    tmp_path: Path,
    parent_mode: int,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir(mode=parent_mode)
    parent.chmod(parent_mode)
    target = parent / "SidePulse-History.json"
    payload = encode_history_export(_history_export())

    assert write_private_export(target, payload, max_bytes=2 * 1024 * 1024) == target

    assert target.read_bytes() == payload
    assert _mode(parent) == parent_mode
    assert _mode(target) == 0o600
    assert list(parent.iterdir()) == [target]


@pytest.mark.parametrize("parent_mode", (0o702, 0o720, 0o722, 0o777))
def test_private_export_refuses_group_or_other_writable_parent(
    tmp_path: Path,
    parent_mode: int,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir(mode=0o700)
    parent.chmod(parent_mode)
    target = parent / "debug.json"

    with pytest.raises(PrivateExportError, match="destination"):
        write_private_export(target, b"{}\n", max_bytes=100)

    assert not target.exists()
    assert _mode(parent) == parent_mode


def test_private_export_refuses_oversize_before_touching_destination(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    target = parent / "debug.json"

    with pytest.raises(PrivateExportError, match="maximum size"):
        write_private_export(target, b"12345", max_bytes=4)

    assert not target.exists()
    assert list(parent.iterdir()) == []
    assert _mode(parent) == 0o755


@pytest.mark.parametrize("max_bytes", (-1, True, 1.5))
def test_private_export_rejects_invalid_byte_limits(
    tmp_path: Path,
    max_bytes: object,
) -> None:
    with pytest.raises(PrivateExportError, match="max_bytes"):
        write_private_export(tmp_path / "debug.json", b"{}\n", max_bytes=max_bytes)


def test_private_export_refuses_relative_or_missing_parent_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(PrivateExportError, match="absolute"):
        write_private_export(Path("debug.json"), b"{}\n", max_bytes=100)
    with pytest.raises(PrivateExportError, match="destination"):
        write_private_export(
            tmp_path / "missing" / "debug.json",
            b"{}\n",
            max_bytes=100,
        )


@pytest.mark.parametrize("kind", ("symlink", "hardlink"))
def test_private_export_refuses_linked_leaf_and_preserves_outside_sentinel(
    tmp_path: Path,
    kind: str,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside stays exact")
    outside.chmod(0o644)
    target = parent / "debug.json"
    if kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    with pytest.raises(PrivateExportError, match="destination"):
        write_private_export(target, b"private export", max_bytes=100)

    assert outside.read_bytes() == b"outside stays exact"
    assert _mode(outside) == 0o644


def test_private_export_refuses_symlinked_selected_parent(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "selected"
    parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PrivateExportError, match="destination"):
        write_private_export(parent / "debug.json", b"{}\n", max_bytes=100)

    assert list(outside.iterdir()) == []


def test_private_export_parent_swap_fails_closed_and_cleans_owned_scratch(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir(mode=0o755)
    outside = tmp_path / "outside"
    outside.mkdir()
    held = tmp_path / "selected-held"
    target = parent / "debug.json"
    real_open = os.open
    swapped = False

    def swap_on_scratch_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and dir_fd is not None and flags & os.O_EXCL:
            parent.rename(held)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with (
        patch("sidepulse.private_export.os.open", side_effect=swap_on_scratch_open),
        pytest.raises(PrivateExportError, match="destination changed"),
    ):
        write_private_export(target, b"{}\n", max_bytes=100)

    assert list(held.iterdir()) == []
    assert list(outside.iterdir()) == []


def test_private_export_parent_swap_during_directory_fsync_fails_closed(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir(mode=0o755)
    outside = tmp_path / "outside"
    outside.mkdir()
    held = tmp_path / "selected-held"
    target = parent / "debug.json"
    real_fsync = os.fsync
    swapped = False

    def swap_on_parent_fsync(descriptor: int) -> None:
        nonlocal swapped
        opened = os.fstat(descriptor)
        if not swapped and stat.S_ISDIR(opened.st_mode):
            parent.rename(held)
            parent.mkdir()
            swapped = True
        real_fsync(descriptor)

    with (
        patch("sidepulse.private_export.os.fsync", side_effect=swap_on_parent_fsync),
        pytest.raises(PrivateExportError, match="destination changed"),
    ):
        write_private_export(target, b"{}\n", max_bytes=100)

    assert (held / target.name).read_bytes() == b"{}\n"
    assert list(parent.iterdir()) == []
    assert list(outside.iterdir()) == []


def test_private_export_target_swap_fails_closed(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    target.write_bytes(b"old target")
    replacement = parent / "replacement.json"
    replacement.write_bytes(b"attacker replacement")
    moved = parent / "moved-original.json"
    real_replace = os.replace
    swapped = False

    def swap_target_then_replace(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        nonlocal swapped
        if not swapped:
            target.rename(moved)
            replacement.rename(target)
            swapped = True
        return real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    with (
        patch("sidepulse.private_export.os.replace", side_effect=swap_target_then_replace),
        pytest.raises(PrivateExportError, match="destination changed"),
    ):
        write_private_export(target, b"new export", max_bytes=100)

    assert moved.read_bytes() == b"old target"


def test_private_export_target_swap_during_directory_fsync_fails_closed(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    replacement = parent / "replacement.json"
    replacement.write_bytes(b"attacker replacement")
    moved = parent / "moved-export.json"
    real_fsync = os.fsync
    swapped = False

    def swap_on_parent_fsync(descriptor: int) -> None:
        nonlocal swapped
        opened = os.fstat(descriptor)
        if not swapped and stat.S_ISDIR(opened.st_mode):
            target.rename(moved)
            replacement.rename(target)
            swapped = True
        real_fsync(descriptor)

    with (
        patch("sidepulse.private_export.os.fsync", side_effect=swap_on_parent_fsync),
        pytest.raises(PrivateExportError, match="changed"),
    ):
        write_private_export(target, b"new export", max_bytes=100)

    assert moved.read_bytes() == b"new export"
    assert target.read_bytes() == b"attacker replacement"


def test_private_export_scratch_collision_preserves_preexisting_leaf(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    collision = parent / "preexisting.tmp"
    collision.write_bytes(b"outside sentinel")
    real_open = os.open

    def collide_on_exclusive_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and flags & os.O_EXCL:
            raise FileExistsError("collision")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with (
        patch("sidepulse.private_export.os.open", side_effect=collide_on_exclusive_open),
        pytest.raises(PrivateExportError, match="scratch"),
    ):
        write_private_export(target, b"new export", max_bytes=100)

    assert collision.read_bytes() == b"outside sentinel"
    assert not target.exists()


def test_private_export_replace_failure_preserves_target_and_cleans_scratch(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    target.write_bytes(b"old target")

    with (
        patch("sidepulse.private_export.os.replace", side_effect=OSError("replace failed")),
        pytest.raises(PrivateExportError, match="publish"),
    ):
        write_private_export(target, b"new export", max_bytes=100)

    assert target.read_bytes() == b"old target"
    assert list(parent.iterdir()) == [target]


def test_private_export_scratch_swap_never_unlinks_outside_sentinel(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside stays exact")
    stolen = parent / "stolen-owned-scratch"
    real_replace = os.replace

    def swap_scratch_then_replace(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        os.rename(
            source,
            stolen.name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=src_dir_fd,
        )
        os.symlink(outside, parent / source)
        return real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    with (
        patch("sidepulse.private_export.os.replace", side_effect=swap_scratch_then_replace),
        pytest.raises(PrivateExportError, match="scratch changed"),
    ):
        write_private_export(target, b"new export", max_bytes=100)

    assert outside.read_bytes() == b"outside stays exact"
    assert stolen.read_bytes() == b"new export"
    assert target.is_symlink()


def test_private_export_replaces_only_selected_leaf(tmp_path: Path) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    neighbor = parent / "keep.json"
    target.write_bytes(b"old target")
    neighbor.write_bytes(b"neighbor stays exact")

    write_private_export(target, b"new export", max_bytes=100)

    assert target.read_bytes() == b"new export"
    assert neighbor.read_bytes() == b"neighbor stays exact"
    assert _mode(target) == 0o600


def test_private_export_detects_post_publish_hardlink_and_never_claims_success(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "selected"
    parent.mkdir()
    target = parent / "debug.json"
    outside_link = tmp_path / "outside-link.json"
    real_fsync = os.fsync
    linked = False

    def link_on_parent_fsync(descriptor: int) -> None:
        nonlocal linked
        opened = os.fstat(descriptor)
        if not linked and stat.S_ISDIR(opened.st_mode):
            os.link(target, outside_link)
            linked = True
        real_fsync(descriptor)

    with (
        patch("sidepulse.private_export.os.fsync", side_effect=link_on_parent_fsync),
        pytest.raises(PrivateExportError, match="changed"),
    ):
        write_private_export(target, b"new export", max_bytes=100)

    assert target.read_bytes() == b"new export"
    assert outside_link.read_bytes() == b"new export"


def test_private_export_exposes_one_path_free_public_error_message() -> None:
    assert PUBLIC_EXPORT_ERROR_MESSAGE == "Export could not be saved."
    assert PrivateExportError("internal category").public_message == (PUBLIC_EXPORT_ERROR_MESSAGE)
    assert "/" not in PUBLIC_EXPORT_ERROR_MESSAGE


def test_private_export_rejects_non_bytes_payload(tmp_path: Path) -> None:
    with pytest.raises(PrivateExportError, match="payload"):
        write_private_export(
            tmp_path / "debug.json",
            bytearray(b"{}\n"),
            max_bytes=100,
        )


def test_export_modules_have_no_runtime_data_or_external_route_imports() -> None:
    from sidepulse import operator_export, private_export

    assert (
        set(operator_export.__dict__)
        & {
            "audit",
            "clipboard",
            "hook",
            "notification",
            "capacity_refresh",
            "requests",
            "socket",
            "subprocess",
            "urllib",
            "webbrowser",
        }
        == set()
    )
    assert (
        set(private_export.__dict__)
        & {
            "audit",
            "clipboard",
            "hook",
            "notification",
            "capacity_refresh",
            "requests",
            "socket",
            "subprocess",
            "urllib",
            "webbrowser",
        }
        == set()
    )
