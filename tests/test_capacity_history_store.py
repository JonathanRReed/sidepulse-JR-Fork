from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from sidepulse.capacity_history import (
    ACTIVITY_HISTORY_SCHEMA_VERSION,
    CAPACITY_HISTORY_SCHEMA_VERSION,
    ActivityHistorySample,
    CapacityHistorySample,
    HistoryContinuity,
    HistoryInterval,
)
from sidepulse.capacity_history_store import (
    MAX_ACTIVITY_HISTORY_SAMPLES,
    MAX_CAPACITY_HISTORY_SAMPLES,
    MAX_HISTORY_STORE_BYTES,
    CapacityHistoryRestoreHealth,
    CapacityHistoryState,
    CapacityHistoryStore,
    default_capacity_history_path,
    load_capacity_history,
    save_capacity_history,
)
from sidepulse.capacity_types import (
    QuotaEffect,
    QuotaLaneKey,
    SampleDisposition,
    SourceHealthKind,
    SourceKey,
)

NOW = 1_800_000_000.0
DAY = 86_400.0


def _source() -> SourceKey:
    return SourceKey("codex", "quota", "source:local-01", "remote_quota_windows")


def _lane() -> QuotaLaneKey:
    return QuotaLaneKey(_source(), "all", "requests", None, "session", QuotaEffect.ALL_WORKLOADS)


def _capacity(
    *,
    observed_at: float = NOW,
    remaining: float = 45.0,
    account_discriminator: str = "acct:opaque-01",
) -> CapacityHistorySample:
    return CapacityHistorySample(
        CAPACITY_HISTORY_SCHEMA_VERSION,
        _lane(),
        account_discriminator,
        observed_at,
        remaining,
        NOW + 3_600.0,
        300.0,
        SourceHealthKind.HEALTHY,
        SampleDisposition.ACCEPTED,
        None,
    )


def _activity(*, observed_at: float = NOW) -> ActivityHistorySample:
    return ActivityHistorySample(
        ACTIVITY_HISTORY_SCHEMA_VERSION,
        _source(),
        observed_at,
        12,
        3,
        0.8,
        0.5,
        1.25,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_default_store_uses_sidepulse_application_support(tmp_path: Path) -> None:
    """Capacity metadata belongs in the private application-support area."""
    assert default_capacity_history_path(tmp_path) == (
        tmp_path / "Library" / "Application Support" / "SidePulse" / "capacity-history.json"
    )


def test_round_trip_uses_exact_v1_allowlists_and_owner_only_modes(tmp_path: Path) -> None:
    """Serialization must not gain undeclared payload fields or broad activity data."""
    target = tmp_path / "state" / "capacity-history.json"
    state = CapacityHistoryState((_capacity(),), (_activity(),))

    save_capacity_history(target, state, retention_days=7, now=NOW)
    restored = load_capacity_history(target)

    assert restored.state == state
    assert restored.health is CapacityHistoryRestoreHealth.HEALTHY
    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o600
    document = json.loads(target.read_text())
    assert set(document) == {"activity_samples", "capacity_samples", "version"}
    assert set(document["capacity_samples"][0]) == {
        "account_discriminator",
        "disposition",
        "lane_key",
        "observed_at",
        "refusal_code",
        "remaining",
        "reset_epoch",
        "schema_version",
        "source_health",
        "window_minutes",
    }
    assert set(document["activity_samples"][0]) == {
        "coverage",
        "estimated_cost",
        "event_count",
        "observed_at",
        "priced_coverage",
        "schema_version",
        "session_count",
        "source_key",
    }


@pytest.mark.parametrize(
    "forbidden",
    (
        "prompt",
        "response",
        "transcript",
        "path",
        "title",
        "display_account_name",
        "email",
        "raw_error",
        "credential",
        "access_token",
        "undeclared",
    ),
)
def test_load_rejects_forbidden_and_undeclared_sample_keys(
    tmp_path: Path,
    forbidden: str,
) -> None:
    """Unknown keys fail closed, including every known private-content category."""
    target = tmp_path / "state" / "capacity-history.json"
    save_capacity_history(
        target,
        CapacityHistoryState((_capacity(),), (_activity(),)),
        retention_days=7,
        now=NOW,
    )
    document = json.loads(target.read_text())
    document["capacity_samples"][0][forbidden] = "PRIVATE SENTINEL"
    target.write_text(json.dumps(document))

    restored = load_capacity_history(target)

    assert restored.state == CapacityHistoryState()
    assert restored.health is CapacityHistoryRestoreHealth.CORRUPT
    diagnostic = target.with_name("capacity-history.json.corrupt")
    assert json.loads(diagnostic.read_text()) == {
        "diagnostic": "capacity_history_corrupt",
        "version": 1,
    }
    assert "PRIVATE SENTINEL" not in diagnostic.read_text()
    assert _mode(diagnostic) == 0o600
    assert not target.exists()


def test_load_rejects_undeclared_local_activity_keys(tmp_path: Path) -> None:
    """The separate activity aggregate gets the same fail-closed schema boundary."""
    target = tmp_path / "state" / "capacity-history.json"
    save_capacity_history(
        target,
        CapacityHistoryState((_capacity(),), (_activity(),)),
        retention_days=7,
        now=NOW,
    )
    document = json.loads(target.read_text())
    document["activity_samples"][0]["transcript"] = "PRIVATE SENTINEL"
    target.write_text(json.dumps(document))

    restored = load_capacity_history(target)

    assert restored.state == CapacityHistoryState()
    assert restored.health is CapacityHistoryRestoreHealth.CORRUPT
    assert "PRIVATE SENTINEL" not in target.with_name("capacity-history.json.corrupt").read_text()


@pytest.mark.parametrize("sample_kind", ("capacity", "activity"))
def test_write_rejects_nonexact_sample_types(
    tmp_path: Path,
    sample_kind: str,
) -> None:
    """A mapping with extra provider data cannot bypass typed serialization."""
    target = tmp_path / "capacity-history.json"
    state = (
        CapacityHistoryState(capacity_samples=({"prompt": "private"},))
        if sample_kind == "capacity"
        else CapacityHistoryState(activity_samples=({"transcript": "private"},))
    )
    with pytest.raises(ValueError):
        save_capacity_history(
            target,
            state,  # type: ignore[arg-type]
            retention_days=7,
            now=NOW,
        )
    assert not target.exists()


@pytest.mark.parametrize("operation", ("load", "save"))
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_store_refuses_linked_leaf_without_touching_outside_file(
    tmp_path: Path,
    operation: str,
    link_kind: str,
) -> None:
    """Neither read nor write may follow a linked history leaf."""
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays unchanged")
    outside.chmod(0o644)
    target = tmp_path / "state" / "capacity-history.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)

    if operation == "load":
        restored = load_capacity_history(target)
        assert restored.health is CapacityHistoryRestoreHealth.UNAVAILABLE
    else:
        with pytest.raises(OSError):
            save_capacity_history(
                target,
                CapacityHistoryState((_capacity(),), ()),
                retention_days=7,
                now=NOW,
            )

    assert outside.read_text() == "outside stays unchanged"
    assert _mode(outside) == 0o644


def test_store_prunes_capacity_and_activity_by_age_and_count(tmp_path: Path) -> None:
    """Both record classes enforce independent 4,096 count and retention-age caps."""
    target = tmp_path / "capacity-history.json"
    capacity = (
        *(
            _capacity(observed_at=NOW - index, remaining=float(index % 101))
            for index in range(MAX_CAPACITY_HISTORY_SAMPLES + 2)
        ),
        _capacity(observed_at=NOW - 8 * DAY),
    )
    activity = (
        *(_activity(observed_at=NOW - index) for index in range(MAX_ACTIVITY_HISTORY_SAMPLES + 2)),
        _activity(observed_at=NOW - 8 * DAY),
    )

    save_capacity_history(
        target,
        CapacityHistoryState(capacity, activity),
        retention_days=7,
        now=NOW,
    )
    restored = load_capacity_history(target)

    assert 0 < len(restored.state.capacity_samples) <= MAX_CAPACITY_HISTORY_SAMPLES
    assert 0 < len(restored.state.activity_samples) <= MAX_ACTIVITY_HISTORY_SAMPLES
    assert restored.state.capacity_samples[-1].observed_at == NOW
    assert restored.state.activity_samples[-1].observed_at == NOW
    assert all(sample.observed_at >= NOW - 7 * DAY for sample in restored.state.capacity_samples)
    assert all(sample.observed_at >= NOW - 7 * DAY for sample in restored.state.activity_samples)
    assert target.stat().st_size <= MAX_HISTORY_STORE_BYTES


def test_oversize_store_is_degraded_and_replaced_by_content_free_diagnostic(
    tmp_path: Path,
) -> None:
    """Oversize corruption must not leak even a fragment into diagnostics."""
    target = tmp_path / "capacity-history.json"
    sentinel = b"PROMPT PRIVATE SENTINEL"
    target.write_bytes(b"{" + sentinel + b"x" * MAX_HISTORY_STORE_BYTES + b"}")

    restored = load_capacity_history(target)

    assert restored.health is CapacityHistoryRestoreHealth.UNAVAILABLE
    diagnostic = target.with_name("capacity-history.json.corrupt")
    assert sentinel not in diagnostic.read_bytes()
    assert not target.exists()


def test_store_batches_once_per_minute_and_avoids_idle_writes(tmp_path: Path) -> None:
    """Duplicates, reads, and countdown-like flush calls cannot rewrite the store."""
    target = tmp_path / "capacity-history.json"
    store = CapacityHistoryStore(target, retention_days=7)
    first = _capacity(observed_at=NOW)

    assert store.admit_capacity(first, HistoryContinuity.CONTINUOUS).sample == first
    assert store.flush(now=NOW)
    first_bytes = target.read_bytes()
    first_mtime = target.stat().st_mtime_ns
    assert store.summarize(HistoryInterval.DAY, now=NOW).observed_sample_count == 1
    assert not store.flush(now=NOW + 30.0)
    assert target.read_bytes() == first_bytes
    assert target.stat().st_mtime_ns == first_mtime
    assert store.admit_capacity(_capacity(observed_at=NOW + 10.0), HistoryContinuity.CONTINUOUS).sample is None
    assert not store.flush(now=NOW + 60.0)
    assert target.stat().st_mtime_ns == first_mtime

    second = _capacity(observed_at=NOW + 31.0, remaining=40.0)
    assert store.admit_capacity(second, HistoryContinuity.CONTINUOUS).sample == second
    assert not store.flush(now=NOW + 59.0)
    assert store.flush(now=NOW + 60.0)


def test_store_refuses_account_change_on_an_existing_lane(tmp_path: Path) -> None:
    """An account switch cannot silently start cross-account longitudinal history."""
    store = CapacityHistoryStore(tmp_path / "capacity-history.json", retention_days=7)
    first = _capacity(observed_at=NOW)
    changed = _capacity(
        observed_at=NOW + 60.0,
        remaining=40.0,
        account_discriminator="acct:opaque-02",
    )

    assert store.admit_capacity(first, HistoryContinuity.CONTINUOUS).sample == first
    result = store.admit_capacity(changed, HistoryContinuity.CONTINUOUS)

    assert result.sample is None
    assert result.disposition is SampleDisposition.IDENTITY_AMBIGUOUS
    assert result.refusal_code == "identity_changed"
    assert store.state.capacity_samples == (first,)


def test_orderly_shutdown_flushes_pending_samples(tmp_path: Path) -> None:
    """Shutdown is the only deliberate exception to the one-minute batching window."""
    target = tmp_path / "capacity-history.json"
    store = CapacityHistoryStore(target, retention_days=7)
    store.admit_capacity(_capacity(), HistoryContinuity.CONTINUOUS)
    store.admit_activity(_activity())

    assert store.shutdown(now=NOW)
    assert load_capacity_history(target).state == CapacityHistoryState((_capacity(),), (_activity(),))


def test_exact_deletion_clears_disk_pending_calibration_and_summary_cache(
    tmp_path: Path,
) -> None:
    """Revoking consent must survive reopen while provider and live state remain out of scope."""
    target = tmp_path / "capacity-history.json"
    store = CapacityHistoryStore(target, retention_days=7)
    store.admit_capacity(_capacity(), HistoryContinuity.CONTINUOUS)
    store.admit_activity(_activity())
    store.shutdown(now=NOW)
    provider_settings = tmp_path / "provider-settings.json"
    live_snapshot = tmp_path / "live-snapshot.json"
    provider_settings.write_text("provider stays")
    live_snapshot.write_text("live stays")
    store.summarize(HistoryInterval.DAY, now=NOW)
    assert store.calibration_inputs == (_capacity(),)
    assert store.cached_summaries

    assert store.delete_capacity_history()

    assert store.state == CapacityHistoryState()
    assert store.calibration_inputs == ()
    assert store.cached_summaries == {}
    reopened = CapacityHistoryStore(target, retention_days=7)
    assert reopened.restore().state == CapacityHistoryState()
    assert reopened.state == CapacityHistoryState()
    assert not target.exists()
    assert provider_settings.read_text() == "provider stays"
    assert live_snapshot.read_text() == "live stays"


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_deletion_refuses_linked_history_without_touching_outside_file(
    tmp_path: Path,
    link_kind: str,
) -> None:
    """Consent deletion must never turn into deletion of an attacker-linked file."""
    outside = tmp_path / "outside.json"
    outside.write_text("outside stays")
    target = tmp_path / "state" / "capacity-history.json"
    target.parent.mkdir()
    if link_kind == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    store = CapacityHistoryStore(target, retention_days=7)

    with pytest.raises(OSError):
        store.delete_capacity_history()

    assert outside.read_text() == "outside stays"


def test_deleting_never_created_history_does_not_create_idle_filesystem_state(
    tmp_path: Path,
) -> None:
    """Consent deletion against an empty install must perform no idle write."""
    target = tmp_path / "Application Support" / "SidePulse" / "capacity-history.json"
    store = CapacityHistoryStore(target, retention_days=7)

    assert not store.delete_capacity_history()
    assert not target.parent.exists()
