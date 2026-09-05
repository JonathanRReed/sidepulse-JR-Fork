#!/usr/bin/env python3
"""Perform a reversible physical SidePulse hardware release smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path

from sidepulse.device_writer import discover_devices, write_led_program

SMOKE_PROGRAM = "#00E5FF 1s pulse\noff 1s none"
MAX_BACKUP_BYTES = 512


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument(
        "--require",
        choices=("any", "pro", "dot", "both"),
        default="any",
    )
    args = parser.parse_args()
    if not args.confirm_write:
        print("hardware verification requires --confirm-write")
        return 2

    devices = discover_devices()
    names = {device.root.name.lower() for device in devices}
    has_pro = any("pro" in name for name in names)
    has_dot = any("dot" in name for name in names)
    required = {
        "any": bool(devices),
        "pro": has_pro,
        "dot": has_dot,
        "both": has_pro and has_dot,
    }[args.require]
    if not required:
        print(f"required hardware is not connected: {args.require}")
        return 1

    failures = []
    for device in devices:
        target = Path(device.target)
        try:
            if target.exists():
                with target.open("rb") as stream:
                    previous = stream.read(MAX_BACKUP_BYTES + 1)
            else:
                previous = None
            if previous is not None and len(previous) > MAX_BACKUP_BYTES:
                raise ValueError("existing LED program exceeds the firmware bound")
            previous_program = previous.decode("ascii") if previous is not None else None
        except Exception as exc:
            failures.append(f"{device.root.name}: backup failed: {exc}")
            continue

        smoke_failure = None
        restore_failure = None
        try:
            write_led_program(SMOKE_PROGRAM, device_path=target)
            observed = target.read_text(encoding="ascii")
            if observed != SMOKE_PROGRAM:
                raise OSError("firmware file readback did not match the smoke program")
        except Exception as exc:
            smoke_failure = exc
        finally:
            try:
                if previous_program is None:
                    target.unlink(missing_ok=True)
                else:
                    write_led_program(previous_program, device_path=target)
                if previous is not None:
                    if target.read_bytes() != previous:
                        raise OSError(
                            "firmware file restore readback did not match the backup"
                        )
                elif target.exists():
                    raise OSError("firmware file created by smoke test was not removed")
            except Exception as exc:
                restore_failure = exc

        if smoke_failure is not None or restore_failure is not None:
            details = []
            if smoke_failure is not None:
                details.append(f"smoke failed: {smoke_failure}")
            if restore_failure is not None:
                details.append(f"restore failed: {restore_failure}")
            failures.append(f"{device.root.name}: {'; '.join(details)}")
    if failures:
        print("hardware verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"hardware verification passed for {len(devices)} device(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
