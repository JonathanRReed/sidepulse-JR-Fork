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

try:
    from AppKit import (
        NSBezelStyleRounded,
        NSButton,
        NSButtonTypeSwitch,
        NSColor,
        NSFont,
        NSLayoutAttributeCenterY,
        NSLayoutConstraint,
        NSPopUpButton,
        NSScrollView,
        NSSlider,
        NSStackView,
        NSStackViewDistributionFill,
        NSTableColumn,
        NSTableView,
        NSTableViewSelectionHighlightStyleSourceList,
        NSTableViewStylePlain,
        NSTextAlignmentRight,
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

ROW_LABEL_WIDTH = 176.0
CARD_PADDING = 16.0


def make_stack(*, orientation: str = "vertical", spacing: float = 8.0, alignment=None) -> "NSStackView":
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
    return stack


def make_label(text: str, *, secondary: bool = False, size: float = 13.0, bold: bool = False) -> "NSTextField":
    label = NSTextField.labelWithString_(text)
    label.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    label.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
    return label


def make_section_title(text: str) -> "NSTextField":
    return make_label(text, size=18.0, bold=True)


def make_row(label_text: str, control, *, help_text: str | None = None) -> "NSStackView":
    """A "label ... control" row matching System Settings' own alignment:
    a fixed-width, right-aligned label on the left, the control filling
    the remaining width on the right. help_text (if given) becomes the
    row's tooltip rather than a permanently-visible caption line -- detail
    on demand instead of clutter by default.
    """
    row = make_stack(orientation="horizontal", spacing=12.0, alignment=NSLayoutAttributeCenterY)
    label = make_label(label_text, secondary=True)
    label.setAlignment_(NSTextAlignmentRight)
    constrain_width(label, ROW_LABEL_WIDTH)
    row.addArrangedSubview_(label)
    row.addArrangedSubview_(control)
    if help_text:
        row.setToolTip_(help_text)
        label.setToolTip_(help_text)
    return row


def make_checkbox(title: str, target, selector: str, *, help_text: str | None = None) -> "NSButton":
    checkbox = NSButton.alloc().init()
    checkbox.setButtonType_(NSButtonTypeSwitch)
    checkbox.setTitle_(title)
    checkbox.setTarget_(target)
    if selector:
        checkbox.setAction_(selector)
    if help_text:
        checkbox.setToolTip_(help_text)
    return checkbox


def make_card(title: str | None = None) -> tuple["NSView", "NSStackView"]:
    """A translucent, rounded, padded card -- the grouped-box unit both
    windows are built from. Returns (outer_view_to_add_to_parent_stack,
    inner_content_stack) -- callers append rows to the inner stack."""
    outer, inner_container = make_glass_panel(corner_radius=14.0)
    content = make_stack(orientation="vertical", spacing=12.0)
    inner_container.addSubview_(content)
    _pin_edges(
        content,
        inner_container,
        insets=(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING),
    )
    if title:
        content.addArrangedSubview_(make_section_title(title))
    return outer, content


def add_separator(stack: "NSStackView") -> None:
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


def make_button(title: str, target, selector: str) -> "NSButton":
    button = NSButton.alloc().init()
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(target)
    button.setAction_(selector)
    return button


def make_popup_button(target, selector: str) -> "NSPopUpButton":
    popup = NSPopUpButton.alloc().init()
    popup.setTarget_(target)
    popup.setAction_(selector)
    return popup


def make_field(text: str = "") -> "NSTextField":
    field = NSTextField.alloc().init()
    field.setStringValue_(text)
    field.setEditable_(True)
    field.setSelectable_(True)
    return field


def make_slider(*, min_value: float, max_value: float, value: float, target, action: str, identifier=None):
    slider = NSSlider.alloc().init()
    slider.setMinValue_(min_value)
    slider.setMaxValue_(max_value)
    slider.setDoubleValue_(value)
    slider.setContinuous_(False)
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


def wrap_in_scroll_pane(stack: "NSStackView", *, padding: float = 20.0) -> "NSScrollView":
    """Wraps a vertical content stack (typically a column of make_card()
    results) in its own independently-scrolling NSScrollView, pinned to
    the scroll view's content width so rows/cards fill it, with height
    left free to grow to whatever the content actually needs. This is
    the reason none of the panes built from this need a hand-computed
    "document height" formula at all -- each one just scrolls exactly as
    far as its own content requires, entirely on its own.
    """
    padded = make_stack(orientation="vertical", spacing=0.0)
    padded.addArrangedSubview_(stack)

    scroll = NSScrollView.alloc().init()
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(False)
    scroll.setDrawsBackground_(False)
    scroll.setTranslatesAutoresizingMaskIntoConstraints_(False)
    scroll.setDocumentView_(padded)
    content_view = scroll.contentView()
    NSLayoutConstraint.activateConstraints_(
        [
            padded.leadingAnchor().constraintEqualToAnchor_constant_(content_view.leadingAnchor(), padding),
            padded.trailingAnchor().constraintEqualToAnchor_constant_(content_view.trailingAnchor(), -padding),
            padded.topAnchor().constraintEqualToAnchor_constant_(content_view.topAnchor(), padding),
            padded.bottomAnchor().constraintGreaterThanOrEqualToAnchor_constant_(content_view.bottomAnchor(), -padding),
        ]
    )
    return scroll


def stretch_to_stack_width(stack: "NSStackView", view) -> None:
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


def make_fixed_area(width: float, height: float) -> "NSView":
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


def sidebar_cell_view(label_text: str) -> "NSView":
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
