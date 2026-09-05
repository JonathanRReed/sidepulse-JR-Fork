#!/usr/bin/env python3
"""Assemble a fail-closed exact-candidate JR-Bar release manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from scripts import release_evidence
except ImportError:  # Direct execution adds scripts/, not the repository root.
    import release_evidence  # type: ignore[no-redef]


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return completed.stdout.strip()


def _artifact_record(path: Path, *, root: Path) -> dict[str, object]:
    """Compatibility shim for focused supply-chain tests."""
    return release_evidence.artifact_record(path, root=root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--receipt", action="append", type=Path, default=[])
    parser.add_argument("--performance-evidence", type=Path, required=True)
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument(
        "--hardware-profile",
        choices=sorted(release_evidence.HARDWARE_PROFILES),
        default="software",
    )
    args = parser.parse_args()

    try:
        root = args.root.resolve(strict=True)
        candidate = release_evidence.load_json_object(
            args.candidate.resolve(strict=True),
            label="candidate",
        )
        receipts = tuple(
            release_evidence.load_json_object(
                path.resolve(strict=True),
                label=f"release receipt {path.name}",
            )
            for path in args.receipt
        )
        commit = _git(root, "rev-parse", "HEAD")
        remote_commit = _git(root, "rev-parse", "origin/main")
        if commit != remote_commit:
            raise release_evidence.EvidenceError("release commit does not equal origin/main")
        if candidate.get("commit") != commit:
            raise release_evidence.EvidenceError("candidate commit does not equal the release checkout")
        document = release_evidence.build_manifest(
            root=root,
            candidate=candidate,
            receipts=receipts,
            sbom=args.sbom.resolve(strict=True),
            performance_evidence=args.performance_evidence.resolve(strict=True),
            artifacts=tuple(path.resolve(strict=True) for path in args.artifact),
            hardware_profile=args.hardware_profile,
        )
        release_evidence.write_json(args.output, document)
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as exc:
        print(f"release manifest generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"release manifest written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
