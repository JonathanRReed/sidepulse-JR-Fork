#!/usr/bin/env python3
"""Verify the source and signed SidePulse entitlement sets are exact."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
from collections.abc import Mapping
from pathlib import Path

REQUIRED_ENTITLEMENTS = {
    "com.apple.security.automation.apple-events": True,
    # JavaScriptCore executes the packaged sdled.wasm firmware model. PyObjC's
    # JavaScriptCore bridge requires JIT and executable-memory permissions
    # under the hardened runtime. No other dynamic-code entitlement is allowed.
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
}
FORBIDDEN_ENTITLEMENTS = frozenset(
    {
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.cs.allow-dyld-environment-variables",
        "com.apple.security.get-task-allow",
        "com.apple.security.network.server",
        "com.apple.security.files.user-selected.read-write",
    }
)


def _dictionary(payload: bytes, *, label: str) -> dict[str, object]:
    start = payload.find(b"<?xml")
    if start < 0:
        start = payload.find(b"<plist")
    if start < 0:
        raise ValueError(f"{label} did not contain an entitlement plist")
    value = plistlib.loads(payload[start:])
    if not isinstance(value, dict):
        raise ValueError(f"{label} entitlement root is not a dictionary")
    return {str(key): item for key, item in value.items()}


def source_entitlements(path: Path) -> dict[str, object]:
    return _dictionary(path.read_bytes(), label="source")


def signed_entitlements(app: Path) -> dict[str, object]:
    completed = subprocess.run(
        ["/usr/bin/codesign", "-d", "--entitlements", ":-", str(app)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("codesign could not read signed entitlements")
    payload = completed.stdout or completed.stderr
    return _dictionary(payload, label="signed app")


def validate_entitlements(value: Mapping[str, object]) -> tuple[str, ...]:
    failures = []
    actual = dict(value)
    for key, expected in REQUIRED_ENTITLEMENTS.items():
        if actual.get(key) is not expected:
            failures.append(f"missing or invalid required entitlement: {key}")
    forbidden = sorted(FORBIDDEN_ENTITLEMENTS & actual.keys())
    for key in forbidden:
        failures.append(f"forbidden entitlement: {key}")
    extras = sorted(set(actual) - set(REQUIRED_ENTITLEMENTS))
    for key in extras:
        failures.append(f"unreviewed entitlement: {key}")
    return tuple(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).with_name("entitlements.plist"),
    )
    args = parser.parse_args()
    try:
        source = source_entitlements(args.source)
        signed = signed_entitlements(args.app)
        failures = [
            *(f"source: {failure}" for failure in validate_entitlements(source)),
            *(f"signed: {failure}" for failure in validate_entitlements(signed)),
        ]
        if source != signed:
            failures.append("signed entitlements differ from the reviewed source plist")
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        print(f"entitlement verification failed: {exc}")
        return 2
    if failures:
        print("entitlement verification failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("entitlement verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
