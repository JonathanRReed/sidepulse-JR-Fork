"""Compatibility launcher for installations that used sidepulse_cli."""

from __future__ import annotations

from sidepulse.hook_entry import main

if __name__ == "__main__":
    raise SystemExit(main())
