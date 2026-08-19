"""Public SidePulse CLI router."""

from __future__ import annotations

import sys

from .cli import sidepulse_main as _legacy_sidepulse_main
from .integration_cli import main as integration_main
from .provider_usage_cli_router import main as provider_main


def sidepulse_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["integrations"]:
        return integration_main(args[1:])
    if args[:1] == ["providers"]:
        return provider_main(args[1:])
    # The source-checkout LaunchAgent and `sidepulse status-bar --foreground`
    # both enter through this router. Load the native provider wrapper before
    # starting AppKit so the menu, Usage Center, reset cues, and background
    # accounting service are present in development as well as packaged runs.
    if args[:1] == ["status-bar"] and "--foreground" in args:
        from .provider_usage_status_bar import main as status_bar_main

        return status_bar_main()
    return _legacy_sidepulse_main(args)


__all__ = ["sidepulse_main"]
