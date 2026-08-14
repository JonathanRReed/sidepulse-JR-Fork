from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.delivery_ledger import (
    DeliveryChannel,
    DeliveryDiagnostic,
    DeliveryDisposition,
    DeliveryKey,
    DeliveryLedger,
    DeliveryReceipt,
    DeliveryRestoreHealth,
    delivery_disposition,
    pending_quiet_summary_keys,
    record_delivery,
)
from sidepulse.delivery_ledger_store import load_delivery_ledger, save_delivery_ledger
from sidepulse.operator_state import SemanticEventKey, TransitionKind
from sidepulse.provider_facts import (
    EventToken,
    ProviderWatermark,
    WatermarkBasis,
    WorkIdentifier,
    WorkKey,
)

NOW = 1_786_536_000.0


def _event(
    suffix: str = "001",
    *,
    transition: TransitionKind = TransitionKind.COMPLETED,
) -> SemanticEventKey:
    source = SourceKey("codex", "hooks", "local:01", "live_agent_events")
    work = WorkKey(source, WorkIdentifier(f"work:{suffix}"))
    return SemanticEventKey(
        work,
        transition,
        ProviderWatermark(
            source,
            WatermarkBasis.PROVIDER_SEQUENCE,
            NOW + int(suffix),
            EventToken(f"event:{suffix}"),
            int(suffix),
            0,
        ),
    )


def _receipt(
    suffix: str,
    channel: DeliveryChannel,
    disposition: DeliveryDisposition,
    *,
    stage: int = 0,
    generation: int = 0,
) -> DeliveryReceipt:
    return DeliveryReceipt(
        DeliveryKey(_event(suffix), channel, stage),
        disposition,
        NOW + int(suffix),
        generation,
        (
            DeliveryDiagnostic.DELIVERY_FAILED
            if disposition is DeliveryDisposition.FAILED
            else None
        ),
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _sample_ledger() -> DeliveryLedger:
    return DeliveryLedger(
        (
            _receipt(
                "001",
                DeliveryChannel.MAILBOX_CUE,
                DeliveryDisposition.DELIVERED,
            ),
            _receipt(
                "002",
                DeliveryChannel.SOUND,
                DeliveryDisposition.SUPPRESSED_QUIET,
                stage=2,
            ),
            _receipt(
                "003",
                DeliveryChannel.SYSTEM_NOTIFICATION,
                DeliveryDisposition.FAILED,
                generation=4,
            ),
        )
    )


def test_round_trip_uses_exact_metadata_only_v1_schema_and_private_modes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state" / "delivery-ledger.json"
    ledger = _sample_ledger()

    save_delivery_ledger(target, ledger)
    restored = load_delivery_ledger(target)

    assert restored.ledger == ledger
    assert restored.health is DeliveryRestoreHealth.HEALTHY
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = json.loads(target.read_text())
    assert set(document) == {"receipts", "version"}
    assert document["version"] == 1
    assert all(
        set(receipt)
        == {
            "attempt_generation",
            "diagnostic",
            "disposition",
            "key",
            "recorded_at_epoch",
        }
        for receipt in document["receipts"]
    )
    assert all(
        set(receipt["key"]) == {"channel", "stage", "subject", "subject_kind"}
        for receipt in document["receipts"]
    )


def test_save_tightens_existing_private_root_and_file(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    parent.mkdir(mode=0o755)
    target = parent / "delivery-ledger.json"
    target.write_text("old")
    target.chmod(0o644)

    save_delivery_ledger(target, _sample_ledger())

    assert _mode(parent) == 0o700
    assert _mode(target) == 0o600


@pytest.mark.parametrize("operation", ("load", "save"))
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_store_refuses_linked_leaf_without_touching_outside_file(
    tmp_path: Path,
    operation: str,
    link_kind: str,
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    target = tmp_path / "state" / "delivery-ledger.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        result = load_delivery_ledger(target)
        assert result.ledger == DeliveryLedger(())
        assert result.health is DeliveryRestoreHealth.UNAVAILABLE
    else:
        with pytest.raises(OSError):
            save_delivery_ledger(target, _sample_ledger())

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_load_uses_held_parent_during_parent_path_swap(tmp_path: Path) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "delivery-ledger.json"
    inside_ledger = DeliveryLedger(
        (_receipt("001", DeliveryChannel.MAILBOX_CUE, DeliveryDisposition.DELIVERED),)
    )
    outside_ledger = DeliveryLedger(
        (_receipt("002", DeliveryChannel.MAILBOX_CUE, DeliveryDisposition.DELIVERED),)
    )
    save_delivery_ledger(target, inside_ledger)
    save_delivery_ledger(outside / target.name, outside_ledger)
    held_parent = parent.with_name("state-held")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and Path(path).name == target.name:
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        restored = load_delivery_ledger(target)

    assert restored.ledger == inside_ledger
    assert restored.health is DeliveryRestoreHealth.HEALTHY
    assert load_delivery_ledger(held_parent / target.name).ledger == inside_ledger
    assert load_delivery_ledger(outside / target.name).ledger == outside_ledger


@pytest.mark.parametrize("operation", ("load", "save"))
def test_store_refuses_parent_swap_while_opening(
    tmp_path: Path,
    operation: str,
) -> None:
    parent = tmp_path / "state"
    outside = tmp_path / "outside"
    target = parent / "delivery-ledger.json"
    ledger = _sample_ledger()
    save_delivery_ledger(target, ledger)
    save_delivery_ledger(outside / target.name, DeliveryLedger(()))
    held_parent = parent.with_name("state-held")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and Path(path) == parent:
            parent.rename(held_parent)
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    with patch("sidepulse.private_io.os.open", side_effect=swapping_open):
        if operation == "load":
            restored = load_delivery_ledger(target)
            assert restored.ledger == DeliveryLedger(())
            assert restored.health is DeliveryRestoreHealth.UNAVAILABLE
        else:
            with pytest.raises(OSError):
                save_delivery_ledger(target, ledger)

    assert load_delivery_ledger(held_parent / target.name).ledger == ledger
    assert load_delivery_ledger(outside / target.name).ledger == DeliveryLedger(())


def test_missing_corrupt_unknown_and_oversize_stores_never_claim_healthy(
    tmp_path: Path,
) -> None:
    missing = load_delivery_ledger(tmp_path / "missing" / "delivery-ledger.json")
    assert missing.ledger == DeliveryLedger(())
    assert missing.health is DeliveryRestoreHealth.MISSING

    target = tmp_path / "state" / "delivery-ledger.json"
    target.parent.mkdir()
    target.write_text("not-json")
    corrupt = load_delivery_ledger(target)
    assert corrupt.ledger == DeliveryLedger(())
    assert corrupt.health is DeliveryRestoreHealth.CORRUPT

    target.write_text('{"receipts":[],"version":2}')
    unsupported = load_delivery_ledger(target)
    assert unsupported.ledger == DeliveryLedger(())
    assert unsupported.health is DeliveryRestoreHealth.UNSUPPORTED

    target.write_bytes(b"{" + b" " * 262_144 + b"}")
    oversize = load_delivery_ledger(target)
    assert oversize.ledger == DeliveryLedger(())
    assert oversize.health is DeliveryRestoreHealth.UNAVAILABLE


def test_save_refuses_a_valid_ledger_larger_than_262144_bytes(
    tmp_path: Path,
) -> None:
    source = SourceKey("p" * 64, "a" * 64, "s" * 64, "c" * 64)
    receipts: list[DeliveryReceipt] = []
    for index in range(512):
        suffix = f"{index:03x}"
        work = WorkKey(source, WorkIdentifier(f"w{suffix}" + "x" * 59))
        event = SemanticEventKey(
            work,
            TransitionKind.COMPLETED,
            ProviderWatermark(
                source,
                WatermarkBasis.PROVIDER_EVENT_ID,
                NOW + index,
                EventToken(f"e{suffix}" + "x" * 59),
                None,
                0,
            ),
        )
        receipts.append(
            DeliveryReceipt(
                DeliveryKey(event, DeliveryChannel.HISTORY_FACT, 0),
                DeliveryDisposition.DELIVERED,
                NOW + index,
                0,
                None,
            )
        )
    target = tmp_path / "delivery-ledger.json"

    with pytest.raises(ValueError, match="maximum size"):
        save_delivery_ledger(target, DeliveryLedger(tuple(receipts)))

    assert not target.exists()


@pytest.mark.parametrize(
    "document",
    (
        '{"receipts":[],"receipts":[],"version":1}',
        '{"receipts":[],"version":1,"extra":true}',
        '{"receipts":[{}],"version":1}',
        '{"receipts":[],"version":NaN}',
    ),
)
def test_invalid_or_duplicate_json_shapes_restore_as_corrupt(
    tmp_path: Path,
    document: str,
) -> None:
    target = tmp_path / "delivery-ledger.json"
    target.write_text(document)

    restored = load_delivery_ledger(target)

    assert restored.ledger == DeliveryLedger(())
    assert restored.health is DeliveryRestoreHealth.CORRUPT


def test_atomic_replace_failure_preserves_previous_ledger(tmp_path: Path) -> None:
    target = tmp_path / "state" / "delivery-ledger.json"
    previous = DeliveryLedger(
        (_receipt("001", DeliveryChannel.MAILBOX_CUE, DeliveryDisposition.DELIVERED),)
    )
    replacement = _sample_ledger()
    save_delivery_ledger(target, previous)
    previous_bytes = target.read_bytes()

    with patch(
        "sidepulse.private_io._replace_private_leaf",
        side_effect=OSError("injected replace failure"),
    ):
        with pytest.raises(OSError):
            save_delivery_ledger(target, replacement)

    assert target.read_bytes() == previous_bytes
    assert load_delivery_ledger(target).ledger == previous


def test_store_contains_no_private_or_provider_payload_sentinels(tmp_path: Path) -> None:
    target = tmp_path / "delivery-ledger.json"
    save_delivery_ledger(target, _sample_ledger())
    serialized = target.read_text().lower()
    document = json.loads(serialized)

    def strings(value: object) -> tuple[str, ...]:
        if type(value) is dict:
            return tuple(value) + tuple(
                item
                for nested in value.values()
                for item in strings(nested)
            )
        if type(value) is list:
            return tuple(item for nested in value for item in strings(nested))
        return (value,) if type(value) is str else ()

    serialized_strings = strings(document)

    for sentinel in (
        "prompt sentinel",
        "/users/private/path",
        "raw error sentinel",
        "account@example.com",
        "display label sentinel",
        "credential sentinel",
        "payload sentinel",
    ):
        assert sentinel not in serialized
    for forbidden_field in (
        "prompt",
        "path",
        "error",
        "account",
        "display",
        "credential",
        "payload",
    ):
        assert all(forbidden_field not in item for item in serialized_strings)


def test_restart_dedupes_delivery_keeps_quiet_summary_and_isolates_channels(
    tmp_path: Path,
) -> None:
    target = tmp_path / "delivery-ledger.json"
    completion = _receipt(
        "001",
        DeliveryChannel.MAILBOX_CUE,
        DeliveryDisposition.DELIVERED,
    )
    quiet = _receipt(
        "002",
        DeliveryChannel.SOUND,
        DeliveryDisposition.SUPPRESSED_QUIET,
    )
    notification_failure = _receipt(
        "003",
        DeliveryChannel.SYSTEM_NOTIFICATION,
        DeliveryDisposition.FAILED,
    )
    same_event_mailbox = DeliveryReceipt(
        DeliveryKey(_event("003"), DeliveryChannel.MAILBOX_CUE, 0),
        DeliveryDisposition.DELIVERED,
        NOW + 3,
        0,
        None,
    )
    same_event_history = DeliveryReceipt(
        DeliveryKey(_event("003"), DeliveryChannel.HISTORY_FACT, 0),
        DeliveryDisposition.DELIVERED,
        NOW + 3,
        0,
        None,
    )
    ledger = DeliveryLedger(())
    for receipt in (
        completion,
        quiet,
        notification_failure,
        same_event_mailbox,
        same_event_history,
    ):
        ledger = record_delivery(ledger, receipt)

    save_delivery_ledger(target, ledger)
    restored = load_delivery_ledger(target)

    assert delivery_disposition(restored.ledger, completion.key) is DeliveryDisposition.DELIVERED
    assert pending_quiet_summary_keys(restored.ledger) == (_event("002"),)
    assert delivery_disposition(
        restored.ledger,
        notification_failure.key,
    ) is DeliveryDisposition.FAILED
    assert delivery_disposition(
        restored.ledger,
        same_event_mailbox.key,
    ) is DeliveryDisposition.DELIVERED
    assert delivery_disposition(
        restored.ledger,
        same_event_history.key,
    ) is DeliveryDisposition.DELIVERED
