"""macOS execution boundary for validated JR-Bar deck actions."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .deck_actions import DeckAction

_APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"


def _is_accessibility_trusted() -> bool:
    application_services = ctypes.cdll.LoadLibrary(_APPLICATION_SERVICES)
    trust_check = application_services.AXIsProcessTrusted
    trust_check.argtypes = []
    trust_check.restype = ctypes.c_bool
    return bool(trust_check())


@dataclass(frozen=True, slots=True)
class DeckActionReceipt:
    code: str
    success: bool


class _MacBridge(Protocol):
    def open_app(self, bundle_id: str) -> str: ...

    def post_shortcut(self, bundle_id: str, key_code: int, modifiers: tuple[str, ...]) -> str: ...


class _NativeMacBridge:
    """Small lazy PyObjC boundary; importing this module remains portable."""

    def open_app(self, bundle_id: str) -> str:
        from AppKit import NSWorkspace

        workspace = NSWorkspace.sharedWorkspace()
        app_url = workspace.URLForApplicationWithBundleIdentifier_(bundle_id)
        if app_url is None:
            return "app_not_found"
        return "opened" if workspace.openURL_(app_url) else "open_failed"

    def post_shortcut(self, bundle_id: str, key_code: int, modifiers: tuple[str, ...]) -> str:
        from AppKit import NSRunningApplication, NSWorkspace
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPostToPid,
            CGEventSetFlags,
            kCGEventFlagMaskAlternate,
            kCGEventFlagMaskCommand,
            kCGEventFlagMaskControl,
            kCGEventFlagMaskShift,
        )

        if not _is_accessibility_trusted():
            return "accessibility_not_trusted"

        running = list(NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id))
        if not running:
            return "target_not_running"
        workspace = NSWorkspace.sharedWorkspace()
        frontmost = workspace.frontmostApplication()
        if frontmost is None:
            return "target_not_frontmost"
        target = next((app for app in running if app.processIdentifier() == frontmost.processIdentifier()), None)
        if target is None or frontmost.bundleIdentifier() != bundle_id:
            return "target_not_frontmost"

        flag_by_name = {
            "command": kCGEventFlagMaskCommand,
            "control": kCGEventFlagMaskControl,
            "option": kCGEventFlagMaskAlternate,
            "shift": kCGEventFlagMaskShift,
        }
        flags = 0
        for modifier in modifiers:
            flags |= flag_by_name[modifier]
        key_down = CGEventCreateKeyboardEvent(None, key_code, True)
        key_up = CGEventCreateKeyboardEvent(None, key_code, False)
        if key_down is None or key_up is None:
            return "event_creation_failed"
        CGEventSetFlags(key_down, flags)
        CGEventSetFlags(key_up, flags)
        pid = target.processIdentifier()
        frontmost = workspace.frontmostApplication()
        if (
            frontmost is None
            or frontmost.processIdentifier() != pid
            or frontmost.bundleIdentifier() != bundle_id
        ):
            return "target_not_frontmost"
        try:
            CGEventPostToPid(pid, key_down)
        finally:
            CGEventPostToPid(pid, key_up)
        return "sent"


class MacDeckActionExecutor:
    def __init__(
        self,
        *,
        reveal_current_ask: Callable[[], None] | None = None,
        open_agent_browser: Callable[[], None] | None = None,
        open_usage: Callable[[], None] | None = None,
        bridge: _MacBridge | None = None,
    ) -> None:
        self._bridge = bridge or _NativeMacBridge()
        self._callbacks = {
            "reveal_current_ask": (reveal_current_ask, "revealed_current_ask"),
            "open_agent_browser": (open_agent_browser, "opened_agent_browser"),
            "open_usage": (open_usage, "opened_usage"),
        }

    def execute(self, action: DeckAction) -> DeckActionReceipt:
        if type(action) is not DeckAction:
            raise TypeError("action must be a DeckAction")
        try:
            if action.kind == "open_app":
                code = self._bridge.open_app(action.bundle_id)
                return DeckActionReceipt(code=code, success=code == "opened")
            if action.kind == "shortcut":
                code = self._bridge.post_shortcut(action.bundle_id, action.key_code, action.modifiers)
                return DeckActionReceipt(code=code, success=code == "sent")

            callback, success_code = self._callbacks[action.kind]
            if callback is None:
                return DeckActionReceipt(code="callback_unavailable", success=False)
            callback()
            return DeckActionReceipt(code=success_code, success=True)
        except Exception:
            code = "callback_failed" if action.kind in self._callbacks else "native_error"
            return DeckActionReceipt(code=code, success=False)


__all__ = ["DeckActionReceipt", "MacDeckActionExecutor"]
