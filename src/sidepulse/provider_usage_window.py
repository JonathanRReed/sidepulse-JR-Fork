"""Native AppKit host for the pure Usage Center projection.

Cards, not a text dump: each provider is a card with real meter bars
(brand-colored, amber/red past pace), reset countdowns, metrics, and
its next action as a button. Everything rendered comes from
project_usage_center -- this module stays a thin projection host.
"""

from __future__ import annotations

import time

import objc
from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSLayoutConstraint,
    NSScrollView,
    NSStackView,
    NSTextField,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)

from .colors import default_agent_color
from .provider_usage_center import project_usage_center, usage_center_text
from .provider_usage_runtime import ProviderUsageState

_ = usage_center_text  # retained for the why-panel text projection

_BAR_WIDTH = 170.0
_BAR_HEIGHT = 8.0


def _nscolor_from_hex(value: str):
    cleaned = value.lstrip("#")
    try:
        red = int(cleaned[0:2], 16) / 255.0
        green = int(cleaned[2:4], 16) / 255.0
        blue = int(cleaned[4:6], 16) / 255.0
    except (ValueError, IndexError):
        return NSColor.controlAccentColor()
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0)


class UsageMeterBarView(NSView):
    """A rounded meter: brand-colored fill over a quiet track; amber
    when the lane is projected to run dry before its reset."""

    def initWithFraction_color_alert_(self, fraction, color_hex, alert):
        self = objc.super(UsageMeterBarView, self).initWithFrame_(
            ((0.0, 0.0), (_BAR_WIDTH, _BAR_HEIGHT))
        )
        if self is None:
            return None
        self._fraction = None if fraction is None else max(0.0, min(1.0, float(fraction)))
        self._color_hex = str(color_hex)
        self._alert = bool(alert)
        self.setTranslatesAutoresizingMaskIntoConstraints_(False)
        NSLayoutConstraint.activateConstraints_(
            [
                self.widthAnchor().constraintEqualToConstant_(_BAR_WIDTH),
                self.heightAnchor().constraintEqualToConstant_(_BAR_HEIGHT),
            ]
        )
        return self

    def drawRect_(self, _rect):
        bounds = self.bounds()
        radius = bounds.size.height / 2.0
        track = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            bounds, radius, radius
        )
        NSColor.quaternaryLabelColor().set()
        track.fill()
        if self._fraction is None or self._fraction <= 0.0:
            return
        fill_width = max(bounds.size.height, bounds.size.width * self._fraction)
        fill_rect = ((0.0, 0.0), (fill_width, bounds.size.height))
        fill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            fill_rect, radius, radius
        )
        if self._alert:
            NSColor.systemOrangeColor().set()
        else:
            _nscolor_from_hex(self._color_hex).set()
        fill.fill()


def _label(text: str, *, secondary: bool = False, bold: bool = False, size: float = 13.0):
    field = NSTextField.labelWithString_(text)
    field.setTranslatesAutoresizingMaskIntoConstraints_(False)
    if bold:
        field.setFont_(NSFont.boldSystemFontOfSize_(size))
    else:
        field.setFont_(NSFont.systemFontOfSize_(size))
    if secondary:
        field.setTextColor_(NSColor.secondaryLabelColor())
    return field


def _hstack(*views, spacing: float = 8.0):
    stack = NSStackView.alloc().init()
    stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
    stack.setOrientation_(NSUserInterfaceLayoutOrientationHorizontal)
    stack.setAlignment_(10)  # NSLayoutAttributeCenterY: bar on the text midline
    stack.setSpacing_(spacing)
    for view in views:
        stack.addArrangedSubview_(view)
    return stack


_UUID_ISH = None


def _account_display(account: str) -> str:
    """A raw UUID is an implementation detail, not an account name."""
    import re

    global _UUID_ISH
    if _UUID_ISH is None:
        _UUID_ISH = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
    if _UUID_ISH.fullmatch(account.strip()):
        return f"account {account.strip()[:8]}…"
    return account


class ProviderUsageWindowController:
    """Own one reusable window; all provider work happens before refresh."""

    def __init__(self, action_target=None) -> None:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (560.0, 640.0)),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("SidePulse Usage Center")
        # A code-created NSWindow is released-when-closed by default;
        # this controller is cached on the status-bar controller and
        # refresh() runs on every usage update, so the first close made
        # every later click (and background refresh) touch a dead
        # window -- hard SIGTRAP, no Python exception to catch.
        self.window.setReleasedWhenClosed_(False)
        self.window.setMinSize_((480.0, 420.0))
        self.window.center()
        self.action_target = action_target

        self.stack = NSStackView.alloc().init()
        self.stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        self.stack.setAlignment_(1)  # leading
        self.stack.setSpacing_(14.0)
        self.stack.setEdgeInsets_((20.0, 20.0, 20.0, 20.0))

        scroll = NSScrollView.alloc().initWithFrame_(((0.0, 0.0), (560.0, 640.0)))
        scroll.setHasVerticalScroller_(True)
        scroll.setDrawsBackground_(False)
        scroll.setDocumentView_(self.stack)
        clip = scroll.contentView()
        NSLayoutConstraint.activateConstraints_(
            [
                self.stack.leadingAnchor().constraintEqualToAnchor_(clip.leadingAnchor()),
                self.stack.trailingAnchor().constraintEqualToAnchor_(clip.trailingAnchor()),
                self.stack.topAnchor().constraintEqualToAnchor_(clip.topAnchor()),
                self.stack.widthAnchor().constraintEqualToAnchor_(clip.widthAnchor()),
            ]
        )
        self.window.setContentView_(scroll)
        self._last_state = ProviderUsageState((), None, None, False)

    def _clear(self) -> None:
        for view in list(self.stack.arrangedSubviews()):
            self.stack.removeArrangedSubview_(view)
            view.removeFromSuperview()

    def _card(self, title_views, body_views):
        card = NSStackView.alloc().init()
        card.setTranslatesAutoresizingMaskIntoConstraints_(False)
        card.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
        card.setAlignment_(1)
        card.setSpacing_(6.0)
        card.setEdgeInsets_((12.0, 14.0, 12.0, 14.0))
        card.setWantsLayer_(True)
        layer = card.layer()
        layer.setCornerRadius_(10.0)
        layer.setBackgroundColor_(
            NSColor.controlBackgroundColor()
            .colorWithAlphaComponent_(0.6)
            .CGColor()
        )
        for view in title_views:
            card.addArrangedSubview_(view)
        for view in body_views:
            card.addArrangedSubview_(view)
        self.stack.addArrangedSubview_(card)
        NSLayoutConstraint.activateConstraints_(
            [
                card.widthAnchor().constraintEqualToAnchor_constant_(
                    self.stack.widthAnchor(), -40.0
                )
            ]
        )

    def refresh(self, state: ProviderUsageState, *, now: float | None = None) -> None:
        if type(state) is not ProviderUsageState:
            raise ValueError("invalid provider usage state")
        self._last_state = state
        projection = project_usage_center(
            state,
            now=time.time() if now is None else float(now),
        )
        self._clear()

        header = _label(projection.subtitle, secondary=True, size=12.0)
        self.stack.addArrangedSubview_(header)
        for line in projection.aggregate_metrics:
            self.stack.addArrangedSubview_(_label(line, secondary=True, size=11.0))

        for section in projection.sections:
            title_row = _hstack(
                _label(section.title, bold=True, size=14.0),
                _label(section.status, secondary=True, size=12.0),
            )
            title_views = [title_row]
            if section.account:
                title_views.append(
                    _label(_account_display(section.account), secondary=True, size=11.0)
                )
            body: list = []
            for lane in section.lanes:
                bar = UsageMeterBarView.alloc().initWithFraction_color_alert_(
                    lane.fraction,
                    default_agent_color(lane.provider_id or section.provider_id),
                    lane.alert,
                )
                body.append(
                    _hstack(bar, _label(lane.plain_title or lane.title, size=12.0))
                )
                body.append(_label(lane.subtitle, secondary=True, size=11.0))
            if section.metrics:
                body.append(
                    _label(" · ".join(section.metrics), secondary=True, size=11.0)
                )
            if section.incident:
                body.append(
                    _label(f"Incident: {section.incident}", secondary=True, size=11.0)
                )
            if section.action_label and self.action_target is not None:
                button = NSButton.buttonWithTitle_target_action_(
                    section.action_label,
                    self.action_target,
                    "usageCenterAction:",
                )
                button.setTranslatesAutoresizingMaskIntoConstraints_(False)
                button.setIdentifier_(section.provider_id)
                body.append(button)
            self._card(title_views, body)

        title = "SidePulse Usage Center"
        if state.refreshing:
            title += " — Refreshing"
        self.window.setTitle_(title)

    def show(self, state: ProviderUsageState) -> None:
        self.refresh(state)
        self.window.makeKeyAndOrderFront_(None)
        try:
            NSApp.activateIgnoringOtherApps_(True)
        except Exception:
            # activateWithOptions: lives on NSRunningApplication, not
            # NSApplication -- the modern NSApplication API is activate().
            try:
                NSApp.activate()
            except Exception:
                pass

    def close(self) -> None:
        self.window.orderOut_(None)


__all__ = ["ProviderUsageWindowController", "UsageMeterBarView"]
