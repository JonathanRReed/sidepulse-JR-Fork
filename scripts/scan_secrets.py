#!/usr/bin/env python3
"""Fail closed when tracked source contains a high-confidence secret token."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_SCAN_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SecretPattern:
    name: str
    expression: re.Pattern[str]


PATTERNS = (
    SecretPattern("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    SecretPattern("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    SecretPattern("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    SecretPattern("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b")),
    SecretPattern("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b")),
    SecretPattern("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    SecretPattern("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    SecretPattern("tailscale-key", re.compile(r"\btskey-[A-Za-z0-9_-]{20,}\b")),
)

_SKIP_SUFFIXES = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".wasm",
        ".pdf",
        ".pkg",
        ".dmg",
        ".zip",
        ".gz",
        ".whl",
    }
)


def tracked_files(root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return tuple(
        root / raw.decode("utf-8", errors="strict")
        for raw in completed.stdout.split(b"\0")
        if raw
    )


def scan_file(path: Path) -> tuple[tuple[str, int], ...]:
    if path.suffix.casefold() in _SKIP_SUFFIXES:
        return ()
    try:
        metadata = path.stat()
        if metadata.st_size > MAX_SCAN_BYTES:
            return ()
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ()
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            if pattern.expression.search(line):
                findings.append((pattern.name, line_number))
    return tuple(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    failures = []
    try:
        files = tracked_files(root)
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        print(f"secret scan could not enumerate tracked files: {exc}")
        return 2
    for path in files:
        for name, line_number in scan_file(path):
            failures.append(f"{path.relative_to(root)}:{line_number}: {name}")
    if failures:
        print("high-confidence secret material found:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"secret scan passed ({len(files)} tracked files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
