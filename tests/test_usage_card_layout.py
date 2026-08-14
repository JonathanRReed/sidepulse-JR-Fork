"""The Capacity card's geometry, and the property the owner watched break.

The card is a custom NSView inside an NSMenuItem, so it owns its own height:
AppKit reserves exactly what `view.frame` claims and clips anything a label
draws outside its own frame. The card used to claim a height assembled from
per-row literals while its labels wrapped to two and three lines inside them,
and the clipped slivers read on screen as rows overlapping and text struck
through.

PyObjC views resist unit tests, which is why that shipped. So the geometry is a
pure function of the content now, and this file tests THAT -- exhaustively, and
then once more against real AppKit measurement to prove the pure function is
being fed the truth.

Two properties, asserted over every shape the card can take:

  * no two row rectangles intersect, and
  * every row rectangle lies inside the claimed card rectangle.

Plus the one that closes the loop: for every row, the text AppKit will draw
fits inside the frame the layout gave it.
"""

from __future__ import annotations

import itertools

import pytest

from sidepulse import usage_card
from sidepulse.usage_card import (
    CardRow,
    TextMetrics,
    UsageCardLayoutError,
    capacity_card_rows,
    usage_card_layout,
)

status_bar = pytest.importorskip("sidepulse.status_bar")


# --------------------------------------------------------------------------
# Deterministic measurement: the geometry under a microscope.
# --------------------------------------------------------------------------


def _measurer(width_per_char: float = 6.0, line_height: float | None = None):
    """A measurer with no AppKit in it and no rounding surprises."""

    def measure(text, style, width):
        height = 14.0 if style.font_size >= 11.0 else 13.0
        if line_height is not None:
            height = line_height
        natural = max(1.0, len(text) * width_per_char)
        lines = max(1, -(-int(natural) // max(1, int(width))))
        return TextMetrics(
            natural_width=natural,
            wrapped_height=height * lines,
            line_height=height,
        )

    return measure


def _assert_sound(layout) -> None:
    assert layout.overlapping_pairs() == (), layout.overlapping_pairs()
    assert layout.escaping_rows() == (), layout.escaping_rows()


_SHORT = "Codex · 5h 62% left"
_LONG = (
    "Claude · Claude, last 365 days: 2508 sessions · estimated $1.00 · "
    "saved $1.00 with caching (estimated)"
)
_HUGE = _LONG * 6


@pytest.mark.parametrize("window_count", [0, 1, 2, 4, 6])
@pytest.mark.parametrize("text", ["", _SHORT, _LONG, _HUGE])
def test_no_two_rows_ever_intersect_and_none_escapes_the_card(
    window_count, text
) -> None:
    """The exact property the owner saw violated, over the card's whole range."""
    rows = capacity_card_rows(
        (
            ("codex", text, text, tuple(text for _ in range(window_count))),
            ("claude", text, text, tuple(text for _ in range(window_count))),
        )
    )
    layout = usage_card_layout(rows, measure=_measurer())

    _assert_sound(layout)
    assert len(layout.rows) == 1 + 2 * (2 + window_count)
    assert layout.height >= usage_card.CARD_TOP_PADDING + usage_card.CARD_BOTTOM_PADDING


def test_every_row_is_tall_enough_for_the_lines_it_was_measured_to_need() -> None:
    """The height must come from the content, not from a per-row literal."""
    measure = _measurer()
    rows = capacity_card_rows((("codex", _LONG, _LONG, (_LONG,)),))
    layout = usage_card_layout(rows, measure=measure)

    _assert_sound(layout)
    for placed in layout.rows:
        metrics = measure(placed.text, placed.style, float(layout.field_width))
        drawn = min(placed.lines, placed.style.max_lines) * metrics.line_height
        assert placed.rect.height >= drawn
        assert placed.lines > 1 or placed.rect.height == placed.style.min_height


def test_a_wrapping_row_costs_a_whole_line_and_the_card_grows_by_it() -> None:
    """One extra line of text is one extra line of card, never zero."""
    measure = _measurer()
    one_line = usage_card_layout(
        capacity_card_rows((("codex", "x", "x", ()),)), measure=measure
    )
    two_lines = usage_card_layout(
        capacity_card_rows((("codex", "x" * 200, "x", ()),)), measure=measure
    )

    _assert_sound(two_lines)
    grew = two_lines.height - one_line.height
    assert grew >= 14
    assert two_lines.row("codex:primary").lines == 2


def test_text_past_the_line_bound_is_truncated_rather_than_overflowing() -> None:
    """A row cannot buy unbounded height, and must not clip to buy less."""
    layout = usage_card_layout(
        capacity_card_rows((("codex", _HUGE, _HUGE, ()),)),
        measure=_measurer(),
    )

    _assert_sound(layout)
    primary = layout.row("codex:primary")
    secondary = layout.row("codex:secondary")
    assert primary.lines == usage_card.PRIMARY_STYLE.max_lines
    assert secondary.lines == usage_card.SECONDARY_STYLE.max_lines
    assert primary.rect.height >= primary.lines * 14
    assert secondary.rect.height >= secondary.lines * 13


def test_the_card_spends_horizontal_room_before_it_spends_vertical_room() -> None:
    """264pt of field inside a menu with room to spare is wasted height."""
    narrow = usage_card_layout(
        capacity_card_rows((("codex", "x" * 10, "x", ()),)), measure=_measurer()
    )
    wide = usage_card_layout(
        capacity_card_rows((("codex", "x" * 55, "x", ()),)), measure=_measurer()
    )

    assert narrow.width == usage_card.CARD_MIN_WIDTH
    assert wide.width > narrow.width
    assert wide.width <= usage_card.CARD_MAX_WIDTH
    assert wide.field_width == wide.width - 2 * usage_card.CARD_INSET
    # And it never grows past the bound, however long the string.
    huge = usage_card_layout(
        capacity_card_rows((("codex", _HUGE, "x", ()),)), measure=_measurer()
    )
    assert huge.width == usage_card.CARD_MAX_WIDTH


def test_rows_read_downward_in_the_order_they_were_given() -> None:
    rows = capacity_card_rows(
        (
            ("codex", "a", "b", ("c", "d")),
            ("claude", "e", "f", ()),
        )
    )
    layout = usage_card_layout(rows, measure=_measurer())

    tops = [placed.rect.top for placed in layout.rows]
    assert tops == sorted(tops, reverse=True)
    assert [placed.key for placed in layout.rows][:3] == [
        "header",
        "codex:primary",
        "codex:secondary",
    ]


def test_an_empty_card_is_still_a_valid_rectangle() -> None:
    layout = usage_card_layout((), measure=_measurer())
    assert layout.rows == ()
    assert layout.height == (
        usage_card.CARD_TOP_PADDING + usage_card.CARD_BOTTOM_PADDING
    )
    _assert_sound(layout)


def test_the_layout_refuses_shapes_it_cannot_lay_out_honestly() -> None:
    with pytest.raises(UsageCardLayoutError):
        usage_card_layout((CardRow("a", "x", usage_card.PRIMARY_STYLE),), measure=None)
    with pytest.raises(UsageCardLayoutError):
        usage_card_layout(
            tuple(
                CardRow(f"r{index}", "x", usage_card.PRIMARY_STYLE)
                for index in range(usage_card.MAX_ROWS + 1)
            ),
            measure=_measurer(),
        )
    with pytest.raises(UsageCardLayoutError):
        usage_card_layout(
            (
                CardRow("a", "x", usage_card.PRIMARY_STYLE),
                CardRow("a", "y", usage_card.PRIMARY_STYLE),
            ),
            measure=_measurer(),
        )
    with pytest.raises(UsageCardLayoutError):
        TextMetrics(natural_width=1.0, wrapped_height=1.0, line_height=0.0)


# --------------------------------------------------------------------------
# The same properties, measured by the AppKit that actually draws the card.
# --------------------------------------------------------------------------


# The owner's dropdown, verbatim from the screenshot that started this.
_OWNERS_CARD = (
    (
        "codex",
        "Codex · 7d 100% left",
        "resets in 6d 23h · updated 2m ago · partial · "
        "Local transcripts · 2895 files · partial",
        (),
    ),
    (
        "claude",
        "Claude · Claude, last 365 days: 2508 sessions · estimated $1.00 · "
        "saved $1.00 with caching (estimated)",
        "updated 8m ago · partial · Local transcripts · 2707 files · partial",
        (),
    ),
)


def _real_layout(blocks):
    return usage_card_layout(
        capacity_card_rows(blocks),
        measure=status_bar.usage_card_text_metrics,
    )


def test_the_owners_card_fits_the_text_appkit_actually_draws() -> None:
    """The screenshot's own strings, measured by the framework that clipped them.

    Under the literal geometry the three long rows were short by 10, 24 and 10
    points -- 44 in total against a claimed height of 110.
    """
    layout = _real_layout(_OWNERS_CARD)

    _assert_sound(layout)
    for placed in layout.rows:
        metrics = status_bar.usage_card_text_metrics(
            placed.text,
            placed.style,
            float(layout.field_width),
        )
        bounded = min(
            metrics.wrapped_height,
            placed.style.max_lines * metrics.line_height,
        )
        assert placed.rect.height >= bounded, (
            f"{placed.key} was given {placed.rect.height}pt "
            f"for {bounded}pt of text"
        )


def test_the_built_card_hosts_every_label_inside_its_own_view() -> None:
    """Frames on the real NSView, not just numbers from the pure function."""
    target = _Target(_OWNERS_CARD)
    item = status_bar.build_usage_menu_item(target)
    view = item.view()

    frame = view.frame()
    assert (frame.size.width, frame.size.height) == (
        float(target.layout.width),
        float(target.layout.height),
    )
    boxes = []
    for key, field in target._usage_menu_fields.items():
        box = field.frame()
        assert box.origin.x >= 0.0
        assert box.origin.y >= 0.0
        assert box.origin.x + box.size.width <= frame.size.width, key
        assert box.origin.y + box.size.height <= frame.size.height, key
        cell = field.cell()
        assert cell.wraps() == (field.maximumNumberOfLines() > 1), key
        boxes.append((key, box))
    for (first_key, first), (second_key, second) in itertools.combinations(boxes, 2):
        overlaps = (
            first.origin.x < second.origin.x + second.size.width
            and second.origin.x < first.origin.x + first.size.width
            and first.origin.y < second.origin.y + second.size.height
            and second.origin.y < first.origin.y + first.size.height
        )
        assert not overlaps, f"{first_key} overlaps {second_key}"


def test_no_row_leaves_a_clipped_sliver_against_its_own_bottom_edge() -> None:
    """Render the card and read the pixels, the way the defect was reproduced.

    A clipped line does not draw OUTSIDE its frame -- AppKit cuts it at the
    boundary -- so "ink outside the frame" would find nothing. What it leaves
    is the top one or two pixel rows of the next line jammed against the
    frame's bottom edge, a few points above the neighbouring row's glyphs.
    That is the sliver the owner read as one row struck through by another, and
    it is what this looks for: a healthy row always keeps its descender slack
    empty, a clipped one always inks the last scanline it owns.
    """
    target = _Target(_OWNERS_CARD)
    item = status_bar.build_usage_menu_item(target)
    view = item.view()
    layout = target.layout

    rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    pixels_high = int(rep.pixelsHigh())
    scale = pixels_high / float(layout.height)

    inked = []
    for row in range(pixels_high):
        for column in range(0, int(rep.pixelsWide()), 2):
            color = rep.colorAtX_y_(column, row)
            if color is not None and color.alphaComponent() > 0.05:
                inked.append(layout.height - (row / scale))
                break

    assert inked, "the card drew nothing at all"
    outside = [
        y
        for y in inked
        if not any(
            placed.rect.y <= y <= placed.rect.top for placed in layout.rows
        )
    ]
    assert outside == [], f"{len(outside)} scanlines of ink outside every row frame"
    for placed in layout.rows:
        floor = [y for y in inked if placed.rect.y <= y <= placed.rect.y + 1.0]
        assert floor == [], (
            f"{placed.key} inked its own bottom edge at {floor}: "
            "a line was cut off there"
        )


def test_a_refresh_regrows_the_card_for_longer_copy_in_place() -> None:
    """New text on frames measured for the old text is the defect in miniature."""
    target = _Target((("codex", "Codex · 5h 62% left", "resets in 2h", ()),))
    status_bar.build_usage_menu_item(target)
    before = target._usage_menu_view.frame().size.height

    target.blocks = (("codex", _LONG, _LONG, ()),)
    status_bar.refresh_usage_menu_card(target, now=0.0, reset_now=0.0)

    after = target._usage_menu_view.frame().size.height
    assert after > before
    _assert_sound(target._usage_menu_layout)
    field = target._usage_menu_fields["codex:primary"]
    assert field.frame().size.height >= 28


def test_a_refresh_that_needs_a_new_row_asks_for_a_rebuild() -> None:
    """A row that does not exist cannot be given a frame; ask for the card back."""
    target = _Target((("codex", "a", "b", ()),))
    status_bar.build_usage_menu_item(target)
    target._menu_signature = "unchanged"

    target.blocks = (("codex", "a", "b", ("Weekly 80% left",)),)
    status_bar.refresh_usage_menu_card(target, now=0.0, reset_now=0.0)

    assert target._menu_signature is None


class _Target:
    """The smallest object `build_usage_menu_item` needs, with fixed copy."""

    def __init__(self, blocks):
        self.blocks = tuple(blocks)
        self._usage_provider_models = {}
        self._usage_provider_states = {}
        self._menu_signature = None
        self._usage_menu_view = None
        self._usage_menu_fields = {}

    @property
    def layout(self):
        return self._usage_menu_layout

    def __getattr__(self, name):
        raise AttributeError(name)


def _patched_rows(target, *, now, reset_now):
    del now, reset_now
    return capacity_card_rows(target.blocks)


@pytest.fixture(autouse=True)
def _fixed_card_copy(monkeypatch):
    """Pin the card's COPY so these tests measure geometry, not wording."""
    real = status_bar.usage_menu_card_rows

    def rows(target, *, now, reset_now):
        if isinstance(target, _Target):
            return _patched_rows(target, now=now, reset_now=reset_now)
        return real(target, now=now, reset_now=reset_now)

    monkeypatch.setattr(status_bar, "usage_menu_card_rows", rows)
