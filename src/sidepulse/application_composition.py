"""Explicit production composition root for the JR-Bar AppKit application.

This module is deliberately inert on import. The foreground entrypoints call
``compose_status_bar_application`` immediately before the retained runtime
creates its AppKit delegate and enters the event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApplicationCompositionReceipt:
    """Stable identity of the controller and menu layers installed at boot."""

    controller: type
    final_controller: type
    menu_binding: Callable[..., object]
    steps: tuple[str, ...]


_receipt: ApplicationCompositionReceipt | None = None


def compose_status_bar_application() -> ApplicationCompositionReceipt:
    """Install the complete foreground runtime in one deterministic order."""
    global _receipt

    from . import _status_bar_production as production
    from . import provider_usage_status_bar as provider_host
    from . import settings_window
    from . import status_bar as public_status_bar
    from . import status_bar_legacy as legacy
    from .ambient_effect_runtime import install_ambient_effect_runtime
    from .screen_bar_runtime import install_screen_bar_runtime
    from .settings_category_runtime import install_settings_navigation

    if (
        _receipt is not None
        and legacy.StatusBarController is _receipt.final_controller
        and legacy.build_menu is _receipt.menu_binding
    ):
        return _receipt

    production_controller = production.install_status_bar_production()
    controller = public_status_bar.install_status_bar_facade()
    install_settings_navigation(legacy, settings_window)
    install_screen_bar_runtime()
    install_ambient_effect_runtime(controller)
    final_controller, menu_binding = (
        provider_host.install_provider_usage_status_bar()
    )

    if controller is not production_controller:
        raise RuntimeError("status-bar production controller composition drifted")

    _receipt = ApplicationCompositionReceipt(
        controller=controller,
        final_controller=final_controller,
        menu_binding=menu_binding,
        steps=(
            "production-controller",
            "status-bar-facade",
            "settings-navigation",
            "screen-bar-runtime",
            "ambient-effects-runtime",
            "provider-usage-controller",
        ),
    )
    return _receipt


__all__ = [
    "ApplicationCompositionReceipt",
    "compose_status_bar_application",
]
