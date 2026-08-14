"""Pure geometry for the Capacity card. No AppKit, no I/O, no clock.

A menu item backed by a custom NSView owns its own height: AppKit reserves
exactly what `view.frame` claims and clips anything a subview draws outside its
own frame. The card used to claim a height assembled from per-row literals --
18pt for a primary row, 16pt for a secondary one -- while the labels inside
those boxes wrapped to two and three lines. Nothing overlapped at the frame
level; what the owner saw was the top one or two pixel rows of each clipped
second line, drawn hard against the bottom edge of its box and a few pixels
above the next row's glyphs. That reads as "rows overlap" and "text struck
through", and it is the same defect either way: the claimed height did not come
from the content that was actually drawn.

So the height comes from the content now. Text measurement arrives as a
callable rather than an import, which is the whole point: the geometry can be
tested exhaustively -- zero rows, one, many, long labels, missing data -- with a
deterministic measurer, and the view stays a thin renderer that applies these
rectangles verbatim instead of computing a second, disagreeing set of its own.

Two properties are the contract, and `tests/test_usage_card_layout.py` asserts
them over every shape this card can take:

* no two row rectangles intersect, and
* every row rectangle lies inside the claimed card rectangle.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

# Horizontal. The card is at least as wide as it always was, and may grow to
# fit its widest single line rather than wrapping while the menu around it has
# room to spare -- vertical space in a dropdown is far more expensive than
# horizontal space.
CARD_MIN_WIDTH = 292
CARD_MAX_WIDTH = 380
CARD_INSET = 14

# Vertical. These are the paddings and gaps the literal geometry used, kept
# exactly so a card whose every line fits on one line lays out where it always
# did. Row heights are no longer among them.
CARD_TOP_PADDING = 6
CARD_BOTTOM_PADDING = 8
LINE_GAP = 1
PROVIDER_GAP = 4
HEADER_GAP = 5

MAX_ROWS = 64


class UsageCardLayoutError(ValueError):
    """The card was asked for a shape it cannot lay out honestly."""


@dataclass(frozen=True, slots=True)
class CardRowStyle:
    """How one row draws, and the smallest box it is ever given.

    `min_height` is a floor, never a ceiling: it preserves the card's original
    rhythm for one-line rows. `max_lines` is the row's honest bound -- text past
    it is truncated visibly by the renderer, which is a different thing from
    being clipped invisibly.
    """

    font_size: float
    bold: bool
    secondary: bool
    min_height: int
    max_lines: int = 2

    def __post_init__(self) -> None:
        if not (
            isinstance(self.font_size, (int, float))
            and math.isfinite(float(self.font_size))
            and float(self.font_size) > 0.0
            and isinstance(self.min_height, int)
            and self.min_height > 0
            and isinstance(self.max_lines, int)
            and self.max_lines >= 1
        ):
            raise UsageCardLayoutError("invalid card row style")


HEADER_STYLE = CardRowStyle(
    font_size=11.0,
    bold=True,
    secondary=False,
    min_height=17,
    max_lines=1,
)
PRIMARY_STYLE = CardRowStyle(
    font_size=11.0,
    bold=True,
    secondary=False,
    min_height=18,
    max_lines=2,
)
SECONDARY_STYLE = CardRowStyle(
    font_size=10.0,
    bold=False,
    secondary=True,
    min_height=16,
    max_lines=3,
)
WINDOW_STYLE = CardRowStyle(
    font_size=11.0,
    bold=False,
    secondary=False,
    min_height=16,
    max_lines=2,
)


@dataclass(frozen=True, slots=True)
class CardRow:
    """One row of the card, in top-to-bottom reading order."""

    key: str
    text: str
    style: CardRowStyle
    gap_above: int = 0

    def __post_init__(self) -> None:
        if not (
            isinstance(self.key, str)
            and self.key
            and isinstance(self.text, str)
            and isinstance(self.style, CardRowStyle)
            and isinstance(self.gap_above, int)
            and self.gap_above >= 0
        ):
            raise UsageCardLayoutError("invalid card row")


@dataclass(frozen=True, slots=True)
class TextMetrics:
    """What one string actually needs, measured at one width."""

    natural_width: float
    wrapped_height: float
    line_height: float

    def __post_init__(self) -> None:
        for value in (self.natural_width, self.wrapped_height, self.line_height):
            if not (
                isinstance(value, (int, float))
                and math.isfinite(float(value))
                and float(value) >= 0.0
            ):
                raise UsageCardLayoutError("invalid text metrics")
        if float(self.line_height) <= 0.0:
            raise UsageCardLayoutError("invalid text metrics")


@dataclass(frozen=True, slots=True)
class RowRect:
    """A row's frame in the card's own bottom-left-origin coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def top(self) -> int:
        return self.y + self.height

    @property
    def right(self) -> int:
        return self.x + self.width

    def intersects(self, other: RowRect) -> bool:
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.top
            and other.y < self.top
        )

    def contains(self, other: RowRect) -> bool:
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.right <= self.right
            and other.top <= self.top
        )


@dataclass(frozen=True, slots=True)
class PlacedRow:
    key: str
    text: str
    style: CardRowStyle
    rect: RowRect
    lines: int


@dataclass(frozen=True, slots=True)
class CardLayout:
    """Everything the renderer needs and nothing it has to recompute."""

    width: int
    height: int
    field_width: int
    rows: tuple[PlacedRow, ...]

    @property
    def bounds(self) -> RowRect:
        return RowRect(0, 0, self.width, self.height)

    def row(self, key: str) -> PlacedRow | None:
        for placed in self.rows:
            if placed.key == key:
                return placed
        return None

    def overlapping_pairs(self) -> tuple[tuple[str, str], ...]:
        pairs: list[tuple[str, str]] = []
        for index, first in enumerate(self.rows):
            for second in self.rows[index + 1 :]:
                if first.rect.intersects(second.rect):
                    pairs.append((first.key, second.key))
        return tuple(pairs)

    def escaping_rows(self) -> tuple[str, ...]:
        bounds = self.bounds
        return tuple(
            placed.key for placed in self.rows if not bounds.contains(placed.rect)
        )


Measurer = Callable[[str, CardRowStyle, float], TextMetrics]


def _row_lines(metrics: TextMetrics, style: CardRowStyle) -> int:
    """How many lines this string actually occupies, bounded by the style.

    Rounding rather than ceiling: AppKit returns an exact multiple of the line
    height for wrapped text, and a float that lands a hair under would
    otherwise buy an entire extra line on every row.
    """
    raw = float(metrics.wrapped_height) / float(metrics.line_height)
    lines = max(1, int(round(raw - 1e-6)))
    return min(lines, int(style.max_lines))


def _row_height(metrics: TextMetrics, style: CardRowStyle, lines: int) -> int:
    """The box this row needs: its text, plus the slack a one-line row had.

    The slack is what is left of the old literal after one line of type, so a
    row that fits on one line keeps the exact height and rhythm it always had,
    and a row that wraps grows by a whole line rather than being clipped.
    """
    line_height = float(metrics.line_height)
    slack = max(0, style.min_height - math.ceil(line_height))
    needed = math.ceil(line_height * lines) + slack
    return max(style.min_height, needed)


def usage_card_layout(
    rows: Iterable[CardRow],
    *,
    measure: Measurer,
    min_width: int = CARD_MIN_WIDTH,
    max_width: int = CARD_MAX_WIDTH,
    inset: int = CARD_INSET,
    top_padding: int = CARD_TOP_PADDING,
    bottom_padding: int = CARD_BOTTOM_PADDING,
) -> CardLayout:
    """Lay one Capacity card out from the text it is actually going to draw.

    Rows arrive in reading order, top to bottom, each carrying the gap it wants
    above it. Width is chosen first -- the card spends horizontal room before it
    spends vertical room -- then every row is measured at the resulting field
    width and given a box big enough for the lines that measurement found.
    """
    ordered = tuple(rows)
    if len(ordered) > MAX_ROWS:
        raise UsageCardLayoutError("too many card rows")
    if not all(type(row) is CardRow for row in ordered):
        raise UsageCardLayoutError("invalid card row")
    if not callable(measure):
        raise UsageCardLayoutError("card layout needs a text measurer")
    if not (
        isinstance(min_width, int)
        and isinstance(max_width, int)
        and isinstance(inset, int)
        and inset >= 0
        and min_width > 2 * inset
        and max_width >= min_width
    ):
        raise UsageCardLayoutError("invalid card width bounds")
    if not (
        isinstance(top_padding, int)
        and isinstance(bottom_padding, int)
        and top_padding >= 0
        and bottom_padding >= 0
    ):
        raise UsageCardLayoutError("invalid card padding")

    if not ordered:
        return CardLayout(
            width=min_width,
            height=top_padding + bottom_padding,
            field_width=min_width - 2 * inset,
            rows=(),
        )

    # Pass one: how wide does the widest line want to be? Measuring at the
    # minimum field width is enough to learn each string's natural width; the
    # measurer reports it independently of the wrap.
    floor_field = min_width - 2 * inset
    natural = max(
        (
            float(measure(row.text, row.style, float(floor_field)).natural_width)
            for row in ordered
            if row.text
        ),
        default=0.0,
    )
    width = min(max_width, max(min_width, math.ceil(natural) + 2 * inset))
    field_width = width - 2 * inset

    # Pass two: measure every row at the width it will really be drawn at.
    cursor = top_padding
    tops: list[tuple[CardRow, int, int, int]] = []
    for row in ordered:
        metrics = measure(row.text, row.style, float(field_width))
        lines = _row_lines(metrics, row.style)
        height = _row_height(metrics, row.style, lines)
        cursor += row.gap_above
        tops.append((row, cursor, height, lines))
        cursor += height
    total_height = cursor + bottom_padding

    placed = tuple(
        PlacedRow(
            key=row.key,
            text=row.text,
            style=row.style,
            rect=RowRect(
                x=inset,
                y=total_height - top - height,
                width=field_width,
                height=height,
            ),
            lines=lines,
        )
        for row, top, height, lines in tops
    )
    keys = tuple(placed_row.key for placed_row in placed)
    if len(set(keys)) != len(keys):
        raise UsageCardLayoutError("duplicate card row key")
    return CardLayout(
        width=width,
        height=total_height,
        field_width=field_width,
        rows=placed,
    )


def capacity_card_rows(
    provider_blocks: Sequence[tuple[str, str, str, Sequence[str]]],
    *,
    header: str = "Capacity",
) -> tuple[CardRow, ...]:
    """Build the card's rows from per-provider copy, in reading order.

    Each block is `(provider_id, primary, secondary, window_lines)`. Gaps are
    the card's own rhythm: a hair between a provider's own lines, a little more
    between providers, and a little more again under the header.
    """
    rows: list[CardRow] = [CardRow("header", header, HEADER_STYLE, 0)]
    for index, block in enumerate(provider_blocks):
        provider_id, primary, secondary, window_lines = block
        rows.append(
            CardRow(
                f"{provider_id}:primary",
                primary,
                PRIMARY_STYLE,
                HEADER_GAP if index == 0 else PROVIDER_GAP,
            )
        )
        rows.append(
            CardRow(
                f"{provider_id}:secondary",
                secondary,
                SECONDARY_STYLE,
                LINE_GAP,
            )
        )
        for window_index, line in enumerate(window_lines):
            rows.append(
                CardRow(
                    f"{provider_id}:window:{window_index}",
                    line,
                    WINDOW_STYLE,
                    LINE_GAP,
                )
            )
    return tuple(rows)


def rows_with_text(
    rows: Iterable[CardRow],
    text_by_key: dict[str, str],
) -> tuple[CardRow, ...]:
    """Re-text an existing row set, keeping its shape. For in-place refreshes."""
    return tuple(
        replace(row, text=text_by_key.get(row.key, row.text)) for row in rows
    )


__all__ = [
    "CARD_BOTTOM_PADDING",
    "CARD_INSET",
    "CARD_MAX_WIDTH",
    "CARD_MIN_WIDTH",
    "CARD_TOP_PADDING",
    "CardLayout",
    "CardRow",
    "CardRowStyle",
    "HEADER_GAP",
    "HEADER_STYLE",
    "LINE_GAP",
    "PRIMARY_STYLE",
    "PROVIDER_GAP",
    "PlacedRow",
    "RowRect",
    "SECONDARY_STYLE",
    "TextMetrics",
    "UsageCardLayoutError",
    "WINDOW_STYLE",
    "capacity_card_rows",
    "rows_with_text",
    "usage_card_layout",
]
