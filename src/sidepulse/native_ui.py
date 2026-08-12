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
        NSLayoutAttributeCenterY,
        NSLayoutConstraint,
        NSLayoutConstraintOrientationHorizontal,
        NSLayoutConstraintOrientationVertical,
        NSLayoutPriorityDefaultHigh,
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
        NSTextField,
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


def glass_available() -> bool:
    """True on macOS 26+ (Tahoe and later), where real Liquid Glass
    (NSGlassEffectView) exists. Every caller in this module already
    degrades gracefully when this is False -- callers outside this
    module normally don't need to check it themselves."""
    return _HAS_GLASS


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


def make_fill_stack(*, spacing: float = SPACE_M, fill_priority: int = 999) -> NSStackView:
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
    stack = _FillWidthStackView.alloc().init()
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


def make_card(title: str | None = None) -> tuple[NSView, NSStackView]:
    """A translucent, rounded, padded card -- the grouped-box unit both
    windows are built from. Returns (outer_view_to_add_to_parent_stack,
    inner_content_stack) -- callers append rows to the inner stack.

    A title renders as a small left-aligned header *above* the panel
    (System Settings' own placement for group titles), never as a
    display-size headline inside it. Panes whose sidebar selection
    already says the same thing should pass no title at all.
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
    outer = make_fill_stack(spacing=SPACE_S)
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


def stretch_to_stack_width(stack: NSStackView, view) -> None:
    """Forces `view` (already added via stack.addArrangedSubview_(view))
    to fill the vertical stack's own width exactly, instead of relying on
    NSStackView's own cross-axis sizing. That default is normally fine
    (every Settings pane's cards pick it up for free), but once a card's
    content includes a fixed-width custom area -- e.g. a swatch-grid row
    built on make_fixed_area -- the cross-axis size can come out
    genuinely ambiguous rather than merely "an unexpected but determined"
    width, and the window collapses to a sliver. This pins it explicitly
    so there's nothing left for Auto Layout to guess.
    """
    NSLayoutConstraint.activateConstraints_(
        [
            view.leadingAnchor().constraintEqualToAnchor_(stack.leadingAnchor()),
            view.trailingAnchor().constraintEqualToAnchor_(stack.trailingAnchor()),
        ]
    )


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


def sidebar_cell_view(label_text: str) -> NSView:
    """One row's content view for the sidebar table -- plain label, no
    disclosure/icon complexity needed for a flat single-level list."""
    label = NSTextField.labelWithString_(label_text)
    label.setFont_(NSFont.systemFontOfSize_(13.0))
    container = NSView.alloc().init()
    container.addSubview_(label)
    label.setTranslatesAutoresizingMaskIntoConstraints_(False)
    NSLayoutConstraint.activateConstraints_(
        [
            label.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), 8.0),
            label.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(container.trailingAnchor(), -8.0),
            label.centerYAnchor().constraintEqualToAnchor_(container.centerYAnchor()),
        ]
    )
    return container
