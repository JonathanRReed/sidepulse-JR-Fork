"""Route native provider commands and their cross-Mac sync subcommands."""

from __future__ import annotations

import sys

from .provider_usage_cli import main as provider_main
from .provider_usage_sync_cli import main as sync_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["sync"]:
        return sync_main(args[1:])
    return provider_main(args)


__all__ = ["main"]
