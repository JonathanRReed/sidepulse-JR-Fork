"""Reusable native-macOS UI building blocks for the Settings and Colors
windows: translucent glass materials, auto-sizing NSStackView rows/cards,
and a sidebar list matching System Settings' own navigation shape.

Two things this module exists to fix, both discovered the hard way over
several rounds of hand-placed-frame layout in status_bar.py:

1. Every "settings window" bug this app has shipped (a label wrapping
   into the control below it, a section overlapping the one after it, a
   window that doesn't scroll far enough to reach its own Done button)
   came from the same root cause: absolute pixel coordinates computed by
   hand and kept in sync with a *separate* height formula purely by
   convention. NSStackView doesn't have that failure mode -- it sizes and
   positions its arranged subviews itself, so there's no y-cursor
   arithmetic left to get out of sync.
2. A single 1800px scrolling column of unrelated settings doesn't read as
   a native macOS window; System Settings (and Mail, Notes, Music, ...)
   all use a sidebar of categories next to a detail pane that shows just
   the one you picked.

Glass materials: real Liquid Glass (NSGlassEffectView) is used when the
running macOS actually has it (26/"Tahoe" and later) -- it's genuinely
better, so use it where it exists. Everywhere else (every Mac from the
M1's launch OS, Big Sur, through Sequoia) falls back to
NSVisualEffectView's translucency, which has existed since 10.10 and
looks correct in both light and dark mode with zero extra work. Nothing
in this module ever hard-requires the newest API; the fallback is not a
degraded experience, just an older (still fully native) one.

This module only builds views. Wiring an NSTableView's dataSource/
delegate needs an actual NSObject-subclass target (PyObjC's Objective-C
bridge dispatches via respondsToSelector:, which a plain Python object
can't satisfy) -- that wiring belongs in status_bar.py, on
StatusBarController itself, the same way every other AppKit delegate
method in this app already lives there.

On the bare ``except Exception: pass`` blocks below, and why NONE of them
owes its caller a reason. Every one of them wraps an OPTIONAL AppKit
appearance setter -- a material, a corner radius, a table style, a track
fill colour, a monospaced font -- that does not exist on every macOS this
app runs on (see the Big Sur..Tahoe range above). The failure they absorb
has exactly one consequence: the control keeps the system default look.
There is no permission behind them, no measurement, no state the caller
could report and nothing a user could act on, so they are the one case
where swallowing genuinely is the whole answer. Anything here that DOES
carry a fact -- a permission, a reading, a device -- is required to say
so; that rule is why this paragraph exists rather than being re-derived
per handler.
"""

from __future__ import annotations

import objc

try:
    from AppKit import (
        NSBezelStyleRounded,
        NSButton,
        NSButtonTypeSwitch,
        NSClipView,
        NSColor,
        NSFont,
        NSFontWeightSemibold,
        NSImage,
        NSImageView,
        NSLayoutAttributeCenterY,
        NSLayoutConstraint,
        NSLayoutConstraintOrientationHorizontal,
        NSLayoutConstraintOrientationVertical,
        NSLayoutPriorityDefaultHigh,
        NSLineBreakByTruncatingTail,
        NSPopUpButton,
        NSScrollerStyleOverlay,
        NSScrollView,
        NSSlider,
        NSStackView,
        NSStackViewDistributionFill,
        NSSwitch,
        NSTableColumn,
        NSTableView,
        NSTableViewSelectionHighlightStyleSourceList,
        NSTableViewStylePlain,
        NSTextAlignmentCenter,
        NSTextField,
        NSTrackingActiveInKeyWindow,
        NSTrackingArea,
        NSTrackingMouseEnteredAndExited,
        NSUserInterfaceLayoutOrientationHorizontal,
        NSUserInterfaceLayoutOrientationVertical,
        NSView,
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectMaterialContentBackground,
        NSVisualEffectMaterialSidebar,
        NSVisualEffectStateActive,
        NSVisualEffectView,
    )
except ImportError as exc:  # pragma: no cover - only exercised on non-macOS setups.
    raise SystemExit(
        "The status-bar app requires PyObjC/AppKit:\n"
        "  python3 -m pip install pyobjc-framework-Cocoa"
    ) from exc

try:
    from AppKit import NSGlassEffectView

    _HAS_GLASS = True
except ImportError:
    _HAS_GLASS = False


# --- Sizing helpers ----------------------------------------------------


def constrain_width(view, width: float):
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    view.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return view


def constrain_height(view, height: float):
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    view.heightAnchor().constraintEqualToConstant_(height).setActive_(True)
    return view


def _resist_vertical_stretch(view) -> None:
    """Tells `view` not to grow taller than its own content needs just
    because it happens to sit in extra space -- see make_stack's own use
    of the same priority for why this matters. Cards (NSGlassEffectView/
    NSVisualEffectView, not NSStackView, so this needs its own call
    outside make_stack) are the other place this shows up: a pane with
    one short card inside a scroll viewport taller than that card needs
    would otherwise get the card itself stretched to fill it.
    """
    view.setContentHuggingPriority_forOrientation_(NSLayoutPriorityDefaultHigh, NSLayoutConstraintOrientationVertical)


def _pin_edges(child, parent, *, insets=(0.0, 0.0, 0.0, 0.0)):
    top, left, bottom, right = insets
    NSLayoutConstraint.activateConstraints_(
        [
            child.topAnchor().constraintEqualToAnchor_constant_(parent.topAnchor(), top),
            child.leadingAnchor().constraintEqualToAnchor_constant_(parent.leadingAnchor(), left),
            child.trailingAnchor().constraintEqualToAnchor_constant_(parent.trailingAnchor(), -right),
            child.bottomAnchor().constraintEqualToAnchor_constant_(parent.bottomAnchor(), -bottom),
        ]
    )


# --- Glass / translucent surfaces ---------------------------------------


def make_glass_panel(*, corner_radius: float = 14.0, tint=None):
    """A translucent rounded panel to hold arbitrary content: real Liquid
    Glass on macOS 26+, an NSVisualEffectView (content-background
    material, matching a card sitting *on* the window rather than *being*
    the window chrome) everywhere else. Returns (outer, inner) --
    `outer` is what the caller adds to its own parent; subviews go in
    `inner`, which already fills `outer` edge-to-edge.
    """
    if _HAS_GLASS:
        outer = NSGlassEffectView.alloc().init()
        outer.setCornerRadius_(corner_radius)
        if tint is not None:
            outer.setTintColor_(tint)
        inner = NSView.alloc().init()
        outer.setContentView_(inner)
        outer.setTranslatesAutoresizingMaskIntoConstraints_(False)
        inner.setTranslatesAutoresizingMaskIntoConstraints_(False)
        # setContentView_ makes `inner` the glass view's content, but --
        # unlike NSVisualEffectView's addSubview_ below -- doesn't itself
        # establish any size constraints for it. Without an explicit pin
        # here, `inner`'s width is genuinely ambiguous to Auto Layout, and
        # a window whose size isn't otherwise pinned by something else
        # (e.g. no NSSplitView imposing a real frame) can resolve that
        # ambiguity by collapsing to a sliver.
        _pin_edges(inner, outer)
        _resist_vertical_stretch(outer)
        return outer, inner

    outer = NSVisualEffectView.alloc().init()
    try:
        outer.setMaterial_(NSVisualEffectMaterialContentBackground)
    except Exception:
        pass
    outer.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    outer.setState_(NSVisualEffectStateActive)
    outer.setWantsLayer_(True)
    try:
        outer.layer().setCornerRadius_(corner_radius)
        outer.layer().setMasksToBounds_(True)
    except Exception:
        pass
    outer.setTranslatesAutoresizingMaskIntoConstraints_(False)
    inner = NSView.alloc().init()
    outer.addSubview_(inner)
    inner.setTranslatesAutoresizingMaskIntoConstraints_(False)
    _pin_edges(inner, outer)
    _resist_vertical_stretch(outer)
    return outer, inner


def make_sidebar_background():
    """The sidebar's own translucent backing -- the .sidebar material is
    the same one Finder/Mail/System Settings use for their source lists,
    available since 10.10."""
    view = NSVisualEffectView.alloc().init()
    view.setMaterial_(NSVisualEffectMaterialSidebar)
    view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    view.setState_(NSVisualEffectStateActive)
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return view


# --- Rows and cards ------------------------------------------------------
#
# Spacing scale: every gap in this module comes from this 4pt-based set,
# not ad-hoc numbers -- rhythm comes from *varying* between these values
# (tight within a group, generous between groups), not from one uniform
# gap everywhere.

SPACE_XS = 4.0  # inside a control cluster (field + unit label)
SPACE_S = 8.0  # between tightly-related rows
SPACE_M = 12.0  # default row spacing inside a card
SPACE_L = 24.0  # between cards / titled groups in a pane
CARD_PADDING = 16.0
ROW_MIN_HEIGHT = 26.0
# System Settings caps its detail content at roughly this width and
# centers the column -- form rows past ~620pt stop reading as rows and
# start reading as a label lost on the left and a control lost on the
# right, and a column hugging the leading edge of a wide pane looks
# off-center (it is).
CONTENT_MAX_WIDTH = 620.0


class _FillWidthStackView(NSStackView):
    """A vertical stack whose arranged subviews are stretched to its own
    full width -- the geometry System Settings' grouped forms are built
    on (rows span the group edge-to-edge; controls sit at the trailing
    edge because the *row* reaches it, not because anything is centered).

    NSStackView's default cross-axis alignment for a vertical stack is
    centerX, which is exactly the "content floating in the middle of the
    card with dead margins either side" look this exists to kill. The
    width constraint is priority 500 -- deliberately BELOW the 749
    column-width preference in wrap_in_scroll_pane. At 999 it sat above
    it, and the solver then found it cheaper to collapse an entire pane
    to its fitting width (breaking the one 749) than to stretch a card
    whose content includes a required fixed-width preview (breaking one
    999) -- the "card hugging the left of an empty pane" bug. At 500,
    the column always prefers its full width, everything stretchy still
    fills (500 beats the default 250 hugging), and required fixed-width
    content simply keeps its size, centered by the stack's alignment.
    """

    def addArrangedSubview_(self, view):
        objc.super(_FillWidthStackView, self).addArrangedSubview_(view)
        constraint = view.widthAnchor().constraintEqualToAnchor_(self.widthAnchor())
        constraint.setPriority_(getattr(self, "fill_priority", 999))
        constraint.setActive_(True)


def make_fill_stack(
    *, spacing: float = SPACE_M, fill_priority: int = 999, view_class=None
) -> NSStackView:
    """A vertical _FillWidthStackView with the shared stack defaults.

    Two tiers of fill_priority, and the difference matters:
    - 999 (default) for PANE-level columns of cards -- cards must always
      stretch to the column, and nothing at that level ever declares a
      required width, so 999 is safe.
    - 500 (used by make_card for its CONTENT) -- card interiors can hold
      required fixed-width previews, and a fill priority above the
      column's own 749 width preference lets the solver collapse the
      whole pane to fitting width rather than break one fill (the
      "card hugging the left of an empty pane" bug). At 500 the column
      wins, stretchy rows still fill (500 > default 250 hugging), and
      fixed previews keep their size, centered.
    """
    stack = (view_class or _FillWidthStackView).alloc().init()
    stack.fill_priority = fill_priority
    stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
    stack.setSpacing_(spacing)
    stack.setDistribution_(NSStackViewDistributionFill)
    stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
    stack.setContentHuggingPriority_forOrientation_(NSLayoutPriorityDefaultHigh, NSLayoutConstraintOrientationVertical)
    return stack


def make_stack(*, orientation: str = "vertical", spacing: float = 8.0, alignment=None) -> NSStackView:
    stack = NSStackView.alloc().init()
    stack.setOrientation_(
        NSUserInterfaceLayoutOrientationVertical
        if orientation == "vertical"
        else NSUserInterfaceLayoutOrientationHorizontal
    )
    stack.setSpacing_(spacing)
    stack.setDistribution_(NSStackViewDistributionFill)
    if alignment is not None:
        stack.setAlignment_(alignment)
    stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
    # Without this, a stack sitting in extra space it doesn't need (e.g.
    # a short pane's one card, inside a scroll viewport that's taller
    # than the card actually requires) has nothing telling it "don't
    # grow to fill that" -- NSStackView's default vertical hugging
    # (250, low) means *something* ends up absorbing the slack, and with
    # several same-priority rows/cards to choose from, Auto Layout picks
    # one arbitrarily rather than just leaving it as blank scrollable
    # space at the bottom, where it belongs. This affects both a
    # horizontal row (this is its cross axis) and a vertical column of
    # cards (this is its main axis) the same useful way.
    stack.setContentHuggingPriority_forOrientation_(NSLayoutPriorityDefaultHigh, NSLayoutConstraintOrientationVertical)
    return stack


def make_label(text: str, *, secondary: bool = False, size: float = 13.0, bold: bool = False) -> NSTextField:
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
    return label


def make_wrapping_label(
    text: str, *, secondary: bool = False, size: float = 13.0, max_width: float = 520.0
) -> NSTextField:
    """A label that word-wraps instead of clipping at its container's
    edge -- for explanatory sentences (a plain make_label is single-line
    and truncates)."""
    label = make_label(text, secondary=secondary, size=size)
    label.setUsesSingleLineMode_(False)
    label.cell().setWraps_(True)
    label.setPreferredMaxLayoutWidth_(max_width)
    return label


def make_section_title(text: str) -> NSTextField:
    """A group header in System Settings' own register: small, semibold,
    left-aligned -- the sidebar selection already names the pane, so
    in-content titles are quiet organizers, not display headlines."""
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.systemFontOfSize_weight_(13.0, NSFontWeightSemibold))
    label.setTextColor_(NSColor.labelColor())
    return label


def make_row(label_text: str, control, *, help_text: str | None = None, fill_control: bool = False) -> NSStackView:
    """A full-width "label ......... control" row in System Settings' own
    geometry: label at the leading edge (primary color -- it's the row's
    subject, not a caption), control at the trailing edge, the gap
    between them absorbing all the slack. fill_control=True instead lets
    the control itself stretch across that gap (sliders). help_text
    becomes the row's tooltip rather than a permanently-visible caption
    line -- detail on demand instead of clutter by default.
    """
    row = make_stack(orientation="horizontal", spacing=SPACE_M, alignment=NSLayoutAttributeCenterY)
    label = make_label(label_text)
    if fill_control:
        row.addArrangedSubview_(label)
        row.addArrangedSubview_(control)
        control.setContentHuggingPriority_forOrientation_(1, NSLayoutConstraintOrientationHorizontal)
    else:
        # label | <no-intrinsic spacer> | control: the spacer is the only
        # arranged view without an intrinsic width, so the stack's slack
        # lands entirely there and everything real keeps its natural
        # size. (Gravity areas were tried first and do NOT do this --
        # NSStackView still stretches the lowest-hugging real view, so a
        # button would balloon to fill the row.)
        row.addArrangedSubview_(label)
        row.addArrangedSubview_(make_hspacer())
        row.addArrangedSubview_(control)
    # A consistent minimum beat per row -- rhythm needs a steady baseline
    # even when a row's control is shorter than its neighbors'.
    row.heightAnchor().constraintGreaterThanOrEqualToConstant_(ROW_MIN_HEIGHT).setActive_(True)
    if help_text:
        row.setToolTip_(help_text)
        label.setToolTip_(help_text)
    return row


def make_checkbox(title: str, target, selector: str, *, help_text: str | None = None) -> NSButton:
    checkbox = NSButton.alloc().init()
    checkbox.setButtonType_(NSButtonTypeSwitch)
    checkbox.setTitle_(title)
    checkbox.setTarget_(target)
    if selector:
        checkbox.setAction_(selector)
    if help_text:
        checkbox.setToolTip_(help_text)
    return checkbox


def make_switch_row(
    title: str, target, selector: str, *, help_text: str | None = None
) -> tuple[NSStackView, NSSwitch]:
    """A "Title ......... [switch]" row -- the System Settings register
    for a boolean, replacing the box-on-the-left checkbox (which reads
    as a dialog control, not a settings row). Returns (row, switch);
    callers keep the switch for state refresh exactly as they kept the
    checkbox before -- NSSwitch shares NSButton's state()/setState_
    contract, so existing handlers and refresh paths work unchanged.
    """
    switch = NSSwitch.alloc().init()
    switch.setTarget_(target)
    if selector:
        switch.setAction_(selector)
    row = make_row(title, switch, help_text=help_text)
    if help_text:
        switch.setToolTip_(help_text)
    return row, switch


def make_hspacer() -> NSView:
    """A no-intrinsic-size view that soaks up leftover width. Append to a
    horizontal cluster that sits directly in a fill-width stack, so the
    stretch lands here instead of arbitrarily distorting the
    lowest-hugging real control."""
    spacer = NSView.alloc().init()
    spacer.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return spacer


class _RevealingStackView(_FillWidthStackView):
    """A card that makes itself visible before the window scrolls to it.

    The daily-tip "Show Me" button scrolls a registered anchor card into
    view and flashes it. In a pane whose sections are stacked and switched
    by ``setHidden_``, an anchor sitting in a section that is not the
    current one is scrolled to and flashed while invisible -- the tip
    silently lands on nothing, which is the exact "took me somewhere but I
    couldn't find it" failure the anchor mechanism exists to prevent.

    Assign a Python callable to ``on_reveal``; it runs first, then the
    normal scroll happens against a card that is actually on screen.
    """

    def scrollRectToVisible_(self, rect):
        handler = getattr(self, "on_reveal", None)
        if callable(handler):
            try:
                handler()
            except Exception:
                pass
        return objc.super(_RevealingStackView, self).scrollRectToVisible_(rect)


def make_card(title: str | None = None, *, revealing: bool = False) -> tuple[NSView, NSStackView]:
    """A translucent, rounded, padded card -- the grouped-box unit both
    windows are built from. Returns (outer_view_to_add_to_parent_stack,
    inner_content_stack) -- callers append rows to the inner stack.

    A title renders as a small left-aligned header *above* the panel
    (System Settings' own placement for group titles), never as a
    display-size headline inside it. Panes whose sidebar selection
    already says the same thing should pass no title at all.

    ``revealing=True`` (titled cards only) returns an outer view that can
    un-hide itself when something asks to scroll it into view -- see
    _RevealingStackView. Used for cards registered as daily-tip anchors.
    """
    panel, inner_container = make_glass_panel(corner_radius=14.0)
    content = make_fill_stack(spacing=SPACE_M, fill_priority=500)
    inner_container.addSubview_(content)
    _pin_edges(
        content,
        inner_container,
        insets=(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING),
    )
    if not title:
        return panel, content
    outer = make_fill_stack(spacing=SPACE_S, view_class=_RevealingStackView if revealing else None)
    outer.addArrangedSubview_(make_section_title(title))
    outer.addArrangedSubview_(panel)
    return outer, content


def add_separator(stack: NSStackView) -> None:
    line = NSView.alloc().init()
    line.setWantsLayer_(True)
    line.layer().setBackgroundColor_(NSColor.separatorColor().CGColor())
    constrain_height(line, 1.0)
    stack.addArrangedSubview_(line)


# --- Sidebar list ----------------------------------------------------------


def build_sidebar_table(width: float = 200.0):
    """A plain single-column source-list-style table (no data source
    wired here -- see this module's docstring). Caller sets
    table.setDataSource_(target)/setDelegate_(target) where target is an
    NSObject subclass implementing the standard NSTableViewDataSource/
    NSTableViewDelegate selectors (StatusBarController does, for the one
    already in this app)."""
    table = NSTableView.alloc().init()
    table.setHeaderView_(None)
    table.setRowHeight_(28.0)
    try:
        table.setStyle_(NSTableViewStylePlain)
    except Exception:
        pass
    table.setBackgroundColor_(NSColor.clearColor())
    try:
        table.setSelectionHighlightStyle_(NSTableViewSelectionHighlightStyleSourceList)
    except Exception:
        pass
    column = NSTableColumn.alloc().initWithIdentifier_("main")
    column.setWidth_(width - 20.0)
    table.addTableColumn_(column)
    table.setIntercellSpacing_((0.0, 2.0))

    scroll = NSScrollView.alloc().init()
    scroll.setDocumentView_(table)
    scroll.setHasVerticalScroller_(True)
    scroll.setDrawsBackground_(False)
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return scroll, table


# --- Plain controls for stack-view content --------------------------------
#
# Unlike this file's frame-based predecessors in status_bar.py
# (add_button/add_checkbox/add_editable_field/add_slider, still used by
# the menu-bar dropdown and the first-launch setup window -- neither of
# those changed here), these take no x/y: NSStackView positions its
# arranged subviews itself, so a fixed origin would just be discarded.
# Controls with their own intrinsic content size (buttons, popups, text
# fields) need no size hint at all; NSSlider has no useful intrinsic
# width, so callers should wrap it in constrain_width().


def make_button(title: str, target, selector: str) -> NSButton:
    button = NSButton.alloc().init()
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(selector)
    return button


def make_popup_button(target, selector: str) -> NSPopUpButton:
    popup = NSPopUpButton.alloc().init()
    popup.setTarget_(target)
    popup.setAction_(selector)
    return popup


def make_field(text: str = "", *, target=None, action: str | None = None) -> NSTextField:
    """An editable text field. Passing target/action wires the field to
    commit on Return AND on end-of-editing (tabbing/clicking away) --
    the instant-apply contract every field in this app follows: a Mac
    settings surface has no Apply buttons, a value you typed is a value
    that took effect the moment you left the field.
    """
    field = NSTextField.alloc().init()
    field.setStringValue_(text)
    field.setEditable_(True)
    field.setSelectable_(True)
    if target is not None and action:
        field.setTarget_(target)
        field.setAction_(action)
        field.cell().setSendsActionOnEndEditing_(True)
    return field


def make_slider(
    *, min_value: float, max_value: float, value: float, target, action: str, identifier=None, continuous: bool = False
):
    slider = NSSlider.alloc().init()
    slider.setMinValue_(min_value)
    slider.setMaxValue_(max_value)
    slider.setDoubleValue_(value)
    slider.setContinuous_(continuous)
    slider.setTarget_(target)
    slider.setAction_(action)
    if identifier is not None:
        slider.setIdentifier_(identifier)
    return slider


def make_text_editor(text: str, *, height: float = 90.0):
    """A monospaced multi-line text editor (for LED DSL program editing)
    with a fixed height -- unlike buttons/fields/popups, NSTextView has no
    useful intrinsic height for a stack view to size it by, so this is
    the one control here that needs an explicit height constraint.
    Returns (scroll_view_to_add_to_a_stack, text_view_for_get/set_value).
    """
    from AppKit import NSFont as _NSFont
    from AppKit import NSTextView as _NSTextView

    text_view = _NSTextView.alloc().init()
    text_view.setString_(text)
    text_view.setVerticallyResizable_(True)
    text_view.setHorizontallyResizable_(False)
    try:
        text_view.setFont_(_NSFont.monospacedSystemFontOfSize_weight_(11.0, 0.0))
    except Exception:
        pass

    scroll = NSScrollView.alloc().init()
    scroll.setDocumentView_(text_view)
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(1)  # NSBezelBorder -- a visible edge, since this sits directly in a card
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    constrain_height(scroll, height)
    return scroll, text_view


class _FlippedClipView(NSClipView):
    """A clip view with the origin at the top-left instead of AppKit's
    default bottom-left. Without this, a document view shorter than the
    scroll view's own visible height -- true for most of these panes,
    now that they size to their real content instead of being stretched
    to fill (see wrap_in_scroll_pane) -- renders pinned to the *bottom*
    of the visible area with all the slack space floating above it,
    which reads as broken/uninitialized rather than as a short pane.
    Top-down document flow is what every one of these panes actually
    wants, and is the standard fix AppKit itself expects you to reach
    for (NSClipView explicitly documents overriding isFlipped for this).
    """

    def isFlipped(self):
        return True


def wrap_in_scroll_pane(stack: NSStackView, *, padding: float = 20.0) -> NSScrollView:
    """Wraps a vertical content stack (typically a column of make_card()
    results) in its own independently-scrolling NSScrollView, pinned to
    the scroll view's content width so rows/cards fill it, with height
    left free to grow to whatever the content actually needs. This is
    the reason none of the panes built from this need a hand-computed
    "document height" formula at all -- each one just scrolls exactly as
    far as its own content requires, entirely on its own.
    """
    padded = make_fill_stack(spacing=0.0)
    padded.addArrangedSubview_(stack)

    # The padding and centering live INSIDE the document view, never
    # between the document and the clip. NSClipView constrains its
    # bounds to the document's frame, so a document smaller than the
    # clip gets scrolled TO ITS OWN ORIGIN -- a column placed at
    # (padding, padding) inside the clip's coordinate space rendered
    # flush against the top-left edge with both paddings dumped on the
    # right, which read as "everything is off-center" (it was).
    document = NSView.alloc().init()
    document.setTranslatesAutoresizingMaskIntoConstraints_(False)
    document.addSubview_(padded)

    scroll = NSScrollView.alloc().init()
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(False)
    scroll.setDrawsBackground_(False)
    # Overlay scrollers ALWAYS -- with the system's "Show scroll bars:
    # Always" preference, legacy scrollers reserve a permanent gutter on
    # the right that narrows the clip view, and a column centered on the
    # narrowed clip sits visibly LEFT of the pane's true center.
    scroll.setScrollerStyle_(NSScrollerStyleOverlay)
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    scroll.setContentView_(_FlippedClipView.alloc().init())
    scroll.setDocumentView_(document)
    content_view = scroll.contentView()
    # A centered column capped at CONTENT_MAX_WIDTH (preferring exactly
    # that width when there's room -- the 749 preference loses only to
    # the required minimum margins in a narrow pane). The document spans
    # the clip's full width, so its origin is always the clip's origin
    # and the clip has nothing to mis-scroll to; the overlay-scroller
    # guarantee above keeps the clip the pane's true width.
    width_preference = padded.widthAnchor().constraintEqualToConstant_(CONTENT_MAX_WIDTH)
    width_preference.setPriority_(749)
    NSLayoutConstraint.activateConstraints_(
        [
            document.leadingAnchor().constraintEqualToAnchor_(content_view.leadingAnchor()),
            document.topAnchor().constraintEqualToAnchor_(content_view.topAnchor()),
            document.widthAnchor().constraintEqualToAnchor_(content_view.widthAnchor()),
            padded.centerXAnchor().constraintEqualToAnchor_(document.centerXAnchor()),
            padded.topAnchor().constraintEqualToAnchor_constant_(document.topAnchor(), padding),
            padded.bottomAnchor().constraintEqualToAnchor_constant_(
                document.bottomAnchor(), -padding
            ),
            padded.widthAnchor().constraintLessThanOrEqualToConstant_(CONTENT_MAX_WIDTH),
            padded.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
                document.leadingAnchor(), padding
            ),
            width_preference,
        ]
    )
    # The document's height comes entirely from the column's top+bottom
    # pins (content height plus both paddings) and needs no relationship
    # to the clip's height: shorter content leaves blank space below
    # (the flipped clip keeps it top-aligned), taller content scrolls.
    # A `<=` bound against the clip here once capped scrollable content
    # at the visible viewport (nothing could ever scroll); a `>=` forced
    # short panes to inflate and dump the slack into one arbitrary card.
    return scroll


def make_fixed_area(width: float, height: float) -> NSView:
    """A plain NSView sized by Auto Layout constraints (not an intrinsic
    content size) so it can sit as one arranged subview in a stack --
    while anything added inside it via addSubview_ with an explicit frame
    positions itself the old, frame-based way. This is the sanctioned
    escape hatch for content that's inherently a custom-drawn strip
    rather than a column of controls, e.g. the Colors window's palette
    swatch grids and animated LED preview dots.
    """
    view = NSView.alloc().init()
    constrain_width(view, width)
    constrain_height(view, height)
    return view


class _HoverRowView(NSView):
    """Raycast's defining micro-interaction: rows acknowledge the
    pointer. ActiveInKeyWindow (not ActiveAlways) so hover clears for
    free whenever the window loses key."""

    def updateTrackingAreas(self):
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow,
                self,
                None,
            )
        )

    def _row_is_selected(self) -> bool:
        """False on any failure, and that is the whole answer.

        The only consumer is the hover paint below. "We could not ask
        whether this row is selected" and "it is not selected" lead to
        the identical, correct-either-way behaviour: draw the hover. No
        caller could do anything with a reason, and there is no surface
        that would show one.
        """
        view = self.superview()
        while view is not None:
            if hasattr(view, "isSelected"):
                try:
                    return bool(view.isSelected())
                except Exception:
                    return False
            view = view.superview()
        return False

    def mouseEntered_(self, _event):
        # Never paint hover over the system selection pill -- the two
        # stacked highlights read as a rendering bug, not a state.
        if self._row_is_selected():
            return
        self.setWantsLayer_(True)
        layer = self.layer()
        if layer is not None:
            layer.setCornerRadius_(6.0)
            layer.setBackgroundColor_(
                NSColor.controlColor().colorWithAlphaComponent_(0.22).CGColor()
            )

    def mouseExited_(self, _event):
        layer = self.layer()
        if layer is not None:
            layer.setBackgroundColor_(None)


class SwatchButton(NSButton):
    """A colour swatch that reports the pointer.

    The Studio previews a colour on the real Screen Bar while you hover it
    and takes it back when you leave, so a swatch has to be able to say
    "the pointer is on me" -- a plain NSButton only ever says "I was
    clicked", by which point the choice has already been made. Assign
    Python callables to ``hover_enter``/``hover_exit``; both receive the
    button, and both are optional.

    ActiveInKeyWindow, like _HoverRowView: hover ends for free when the
    window stops being key, which is one of the ways a preview would
    otherwise be left holding a surface with nobody watching.
    """

    def updateTrackingAreas(self):
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow,
                self,
                None,
            )
        )

    @objc.python_method
    def _fire(self, name):
        handler = getattr(self, name, None)
        if callable(handler):
            handler(self)

    def mouseEntered_(self, _event):
        self._fire("hover_enter")

    def mouseExited_(self, _event):
        self._fire("hover_exit")


def make_swatch_button(
    hex_color: str,
    *,
    size: float,
    target,
    selector: str,
    represented: dict,
    color_for_hex,
) -> SwatchButton:
    """One round colour chip. ``color_for_hex`` converts a hex string to an
    NSColor -- passed in rather than imported so this module keeps its "only
    builds views" contract and stays free of the colour model."""
    button = SwatchButton.alloc().initWithFrame_(((0, 0), (size, size)))
    button.setTitle_("")
    button.setBordered_(False)
    button.setTarget_(target)
    button.setAction_(selector)
    button.setRepresentedObject_(dict(represented))
    button.setTranslatesAutoresizingMaskIntoConstraints_(False)
    constrain_width(button, size)
    constrain_height(button, size)
    try:
        button.setWantsLayer_(True)
        layer = button.layer()
        layer.setBackgroundColor_(color_for_hex(hex_color).CGColor())
        layer.setCornerRadius_(size / 2.0)
        layer.setBorderWidth_(0.0)
    except Exception:
        pass
    return button


class HoverView(NSView):
    """A transparent wrapper that reports the pointer for a view that has no
    hover of its own.

    SwatchButton can do this itself because it is a button. An animated LED
    thumbnail is a plain NSView with no tracking area, which is why the
    Studio could promise "hover any color or animation to try it here first"
    while exactly zero animation controls had any hover wiring. Wrapping is
    used rather than teaching VirtualLedView about tracking areas, because
    that same class draws the always-on Screen Bar overlay, which must never
    start accepting mouse tracking.

    Assign Python callables to ``hover_enter``/``hover_exit``; both receive
    this view, and both are optional. ``hover_payload`` is a plain dict the
    handler reads -- no ObjC bridging, so it can hold anything.
    """

    def updateTrackingAreas(self):
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow,
                self,
                None,
            )
        )

    @objc.python_method
    def _fire(self, name):
        handler = getattr(self, name, None)
        if callable(handler):
            handler(self)

    def mouseEntered_(self, _event):
        self._fire("hover_enter")

    def mouseExited_(self, _event):
        self._fire("hover_exit")


def make_hover_area(child, payload: dict | None = None) -> HoverView:
    """Wrap ``child`` in a HoverView pinned to its edges, so the child keeps
    its own size and gains a pointer-enter/exit report."""
    area = HoverView.alloc().init()
    area.setTranslatesAutoresizingMaskIntoConstraints_(False)
    area.hover_payload = dict(payload or {})
    child.setTranslatesAutoresizingMaskIntoConstraints_(False)
    area.addSubview_(child)
    _pin_edges(child, area)
    return area


def make_caption(text: str) -> NSTextField:
    """The word under a swatch. Small, secondary, centered -- and never
    optional: an unnamed colour square is a guess, not a choice."""
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.systemFontOfSize_(9.0))
    label.setTextColor_(NSColor.secondaryLabelColor())
    label.setAlignment_(NSTextAlignmentCenter)
    label.setLineBreakMode_(NSLineBreakByTruncatingTail)
    return label


def sidebar_cell_view(label_text: str, symbol: str | None = None) -> NSView:
    """One row's content view for the sidebar table. With a symbol name
    the row leads with a template SF Symbol at secondary weight -- the
    Raycast/System Settings idiom that makes a sidebar scannable by
    shape before you read a single word."""
    label = NSTextField.labelWithString_(label_text)
    label.setFont_(NSFont.systemFontOfSize_(13.0))
    container = _HoverRowView.alloc().init()
    container.addSubview_(label)
    label.setTranslatesAutoresizingMaskIntoConstraints_(False)
    text_leading = 8.0
    if symbol:
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol, label_text
        )
        if image is not None:
            icon = NSImageView.imageViewWithImage_(image)
            icon.setContentTintColor_(NSColor.secondaryLabelColor())
            icon.setTranslatesAutoresizingMaskIntoConstraints_(False)
            container.addSubview_(icon)
            NSLayoutConstraint.activateConstraints_(
                [
                    icon.leadingAnchor().constraintEqualToAnchor_constant_(
                        container.leadingAnchor(), 8.0
                    ),
                    icon.centerYAnchor().constraintEqualToAnchor_(
                        container.centerYAnchor()
                    ),
                    icon.widthAnchor().constraintEqualToConstant_(18.0),
                ]
            )
            text_leading = 32.0
    NSLayoutConstraint.activateConstraints_(
        [
            label.leadingAnchor().constraintEqualToAnchor_constant_(
                container.leadingAnchor(), text_leading
            ),
            label.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                container.trailingAnchor(), -8.0
            ),
            label.centerYAnchor().constraintEqualToAnchor_(container.centerYAnchor()),
        ]
    )
    return container
