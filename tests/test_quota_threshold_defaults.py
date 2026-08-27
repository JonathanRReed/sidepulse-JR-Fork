"""Threshold defaults and input coercion.

Edge detection itself already existed in `signals.quota_crossings` (upward
transitions only, silent on first sight). This
covers only what was actually missing: the owner's 90/95 defaults, coercion
of user input, and the burst budget.
"""

from __future__ import annotations

import pytest

from sidepulse.signals import (
    DEFAULT_ALERT_BURST,
    DEFAULT_QUOTA_THRESHOLDS,
    normalize_alert_burst,
    normalize_quota_thresholds,
    quota_crossings,
)


def test_defaults_are_the_owners_numbers() -> None:
    assert DEFAULT_QUOTA_THRESHOLDS == (90.0, 95.0)
    assert DEFAULT_ALERT_BURST == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_QUOTA_THRESHOLDS),
        ((), DEFAULT_QUOTA_THRESHOLDS),
        ("90,95", DEFAULT_QUOTA_THRESHOLDS),
        ((95, 90), (90.0, 95.0)),
        ((90, 90, 95), (90.0, 95.0)),
        ((0, -1, 101, 50), (50.0,)),
        ((10, 20, 30, 40, 50, 60), (10.0, 20.0, 30.0, 40.0)),
        ((float("nan"), 90), (90.0,)),
        ((True, 90), (90.0,)),
    ],
)
def test_threshold_normalization(raw: object, expected: tuple) -> None:
    assert normalize_quota_thresholds(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 3), (0, 3), (-4, 3), (1, 1), (5, 5), (999, 10), (True, 3)],
)
def test_burst_normalization(raw: object, expected: int) -> None:
    assert normalize_alert_burst(raw) == expected


def test_the_existing_detector_fires_on_the_edge_at_the_new_defaults() -> None:
    """A level that is merely high must not fire on every refresh."""
    thresholds = DEFAULT_QUOTA_THRESHOLDS
    assert quota_crossings({}, {"Claude weekly": 96.0}, thresholds) == []
    assert quota_crossings(
        {"Claude weekly": 88.0}, {"Claude weekly": 91.0}, thresholds
    ) == [("Claude weekly", 90.0)]
    # Still above 90, but no new threshold crossed.
    assert quota_crossings(
        {"Claude weekly": 91.0}, {"Claude weekly": 93.0}, thresholds
    ) == []
    assert quota_crossings(
        {"Claude weekly": 93.0}, {"Claude weekly": 96.0}, thresholds
    ) == [("Claude weekly", 95.0)]
