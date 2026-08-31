#!/usr/bin/env python3
"""Validate and assemble candidate-bound Screen Bar profiling evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidepulse.screen_bar_profile import (
    ProfileEvidenceError,
    build_profile_matrix,
    create_instruments_profile,
    validate_profile_matrix,
    validate_runtime_profile,
    write_json,
)


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileEvidenceError(f"could not read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileEvidenceError(f"{label} must be a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    runtime = commands.add_parser("validate-runtime")
    runtime.add_argument("profile", type=Path)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--root", type=Path, required=True)
    finalize.add_argument("--runtime", type=Path, required=True)
    finalize.add_argument("--instruments", type=Path, required=True)
    finalize.add_argument("--trace", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)

    matrix = commands.add_parser("matrix")
    matrix.add_argument("--root", type=Path, required=True)
    matrix.add_argument("--profile", type=Path, action="append", required=True)
    matrix.add_argument("--output", type=Path, required=True)

    validate_matrix = commands.add_parser("validate-matrix")
    validate_matrix.add_argument("--root", type=Path, required=True)
    validate_matrix.add_argument("matrix", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-runtime":
            validate_runtime_profile(_load_object(args.profile, "runtime profile"))
            print("Screen Bar runtime profile passed")
        elif args.command == "finalize":
            runtime = _load_object(args.runtime, "runtime profile")
            instruments = _load_object(args.instruments, "Instruments metrics")
            profile = create_instruments_profile(
                root=args.root,
                runtime=runtime,
                instruments=instruments,
                trace=args.trace,
            )
            write_json(args.output, profile)
            print(f"Screen Bar Instruments profile written: {args.output}")
        elif args.command == "matrix":
            profiles = [_load_object(path, f"profile {path}") for path in args.profile]
            matrix = build_profile_matrix(root=args.root, profiles=profiles)
            write_json(args.output, matrix)
            print(f"Screen Bar profile matrix written: {args.output}")
        else:
            validate_profile_matrix(
                _load_object(args.matrix, "profile matrix"),
                root=args.root,
            )
            print("Screen Bar profile matrix passed")
    except (OSError, ProfileEvidenceError, ValueError) as exc:
        print(f"Screen Bar profile evidence rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
