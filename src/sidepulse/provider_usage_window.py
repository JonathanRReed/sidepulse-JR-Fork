"""Native AppKit host for the pure Usage Center projection."""

from __future__ import annotations

import time

from AppKit import (
    NSApp,
    NSBackingStoreBuffered,
    NSFont,
    NSScrollView,
    NSTextView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)

from .provider_usage_center import project_usage_center, usage_center_text
from .provider_usage_runtime import ProviderUsageState


class ProviderUsageWindowController:
    """Own one reusable window; all provider work happens before refresh."""

    def __init__(self) -> None:
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (720.0, 620.0)),
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("SidePulse Usage Center")
        self.window.setMinSize_((560.0, 420.0))
        self.window.center()

        scroll = NSScrollView.alloc().initWithFrame_(((0.0, 0.0), (720.0, 620.0)))
        scroll.setAutoresizingMask_(18)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setBorderType_(0)

        text = NSTextView.alloc().initWithFrame_(((0.0, 0.0), (700.0, 620.0)))
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setRichText_(False)
        text.setHorizontallyResizable_(False)
        text.setVerticallyResizable_(True)
        text.setAutoresizingMask_(2)
        text.setTextContainerInset_((24.0, 24.0))
        try:
            text.setFont_(NSFont.monospacedSystemFontOfSize_weight_(13.0, 0.0))
        except Exception:
            text.setFont_(NSFont.systemFontOfSize_(13.0))
        scroll.setDocumentView_(text)
        self.window.setContentView_(scroll)
        self.text_view = text
        self._last_state = ProviderUsageState((), None, None, False)

    def refresh(self, state: ProviderUsageState, *, now: float | None = None) -> None:
        if type(state) is not ProviderUsageState:
            raise ValueError("invalid provider usage state")
        self._last_state = state
        projection = project_usage_center(
            state,
            now=time.time() if now is None else float(now),
        )
        self.text_view.setString_(usage_center_text(projection))
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


__all__ = ["ProviderUsageWindowController"]
