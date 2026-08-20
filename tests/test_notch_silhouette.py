"""The measured hardware-notch silhouette and the body drawn from it.

Two real failures anchor these tests. Alcove's capsule composites pure
black over the exact rows the notch scan reads: it was measured at 266pt
over this panel's 186pt notch, passed the 120-320 sanity band, and was
cached as "the notch" -- the classic body grew ears. And the top-width
scan alone left straight body walls standing beside the physical notch's
curved bottom corners as dark slivers.
"""

from __future__ import annotations

from itertools import pairwise

from sidepulse.virtual_device import (
    LED_BAND_HEIGHT,
    _validated_notch_silhouette,
    notch_bar_path_from_insets,
)

SCALE = 2.0
FRAME_X = 0.0


def _notch_runs(top_width_pt=186.0, depth=32, radius=10, left_pt=663.0):
    """Synthetic per-row px runs shaped like a real notch: straight walls,
    then corners curving inward through the last `radius` rows."""
    runs = []
    left0 = left_pt * SCALE
    right0 = (left_pt + top_width_pt) * SCALE
    for row in range(depth):
        rise = max(0, row - (depth - radius))
        inset = (rise * rise) / max(1, radius) * SCALE * 0.6
        runs.append((left0 + inset, right0 - inset))
    return runs


def test_a_real_notch_shape_validates_and_narrows_toward_the_bottom() -> None:
    result = _validated_notch_silhouette(_notch_runs(), SCALE, FRAME_X, max_width=191.0)
    assert result is not None
    x, top_width, insets = result
    assert abs(top_width - 186.0) < 0.01
    assert abs(x - 663.0) < 0.01
    assert insets[0] == (0.0, 0.0)
    final_left, final_right = insets[-1]
    assert final_left > 1.0 and final_right > 1.0
    # Monotonic: no row ever pokes back OUT past a row above it.
    for (al, ar), (bl, br) in pairwise(insets):
        assert bl >= al and br >= ar


def test_an_alcove_wide_impostor_is_rejected_by_the_slot_ceiling() -> None:
    runs = _notch_runs(top_width_pt=266.0, left_pt=623.0)
    # Passes the generic sanity band (120-320)...
    assert _validated_notch_silhouette(runs, SCALE, FRAME_X) is not None
    # ...but the physical notch cannot exceed the auxiliary-area gap.
    assert (
        _validated_notch_silhouette(runs, SCALE, FRAME_X, max_width=191.0) is None
    )


def test_a_row_wider_than_the_top_row_is_not_a_notch() -> None:
    runs = _notch_runs()
    left, right = runs[10]
    runs[10] = (left - 6.0 * SCALE, right)
    assert _validated_notch_silhouette(runs, SCALE, FRAME_X, max_width=191.0) is None


def test_a_vanishing_row_is_not_a_notch() -> None:
    runs = _notch_runs()
    runs[20] = None
    assert _validated_notch_silhouette(runs, SCALE, FRAME_X, max_width=191.0) is None


def test_the_measured_body_never_pokes_past_the_real_corner_curve() -> None:
    result = _validated_notch_silhouette(_notch_runs(), SCALE, FRAME_X, max_width=191.0)
    assert result is not None
    _x, top_width, insets = result
    depth = len(insets)
    height = depth + LED_BAND_HEIGHT
    path = notch_bar_path_from_insets(((0.0, 0.0), (top_width, height)), insets)

    # Straight-wall region: the body still reaches the full width.
    assert path.containsPoint_((0.6, height - 2.0))
    assert path.containsPoint_((top_width - 0.6, height - 2.0))

    # Corner region: the real notch has curved inward here; the OLD
    # parametric body kept full width -- the visible sliver. A point
    # just outside the measured inset must NOT be in the body.
    corner_row = depth - 1
    inset_left, inset_right = insets[corner_row]
    probe_y = height - corner_row - 0.5
    assert not path.containsPoint_((inset_left - 1.0, probe_y))
    assert not path.containsPoint_((top_width - inset_right + 1.0, probe_y))
    assert path.containsPoint_((inset_left + 1.5, probe_y))

    # The band continues at the BOTTOM width, not the top width.
    band_y = LED_BAND_HEIGHT / 2.0
    assert not path.containsPoint_((inset_left - 1.0, band_y))
    assert path.containsPoint_((top_width / 2.0, 0.5))


def test_no_insets_falls_back_to_the_parametric_shape() -> None:
    path = notch_bar_path_from_insets(((0.0, 0.0), (186.0, 37.0)), ())
    assert path.containsPoint_((93.0, 18.0))
