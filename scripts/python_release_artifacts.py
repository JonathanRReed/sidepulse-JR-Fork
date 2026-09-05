#!/usr/bin/env python3
"""Build exact JR-Bar wheel and source-distribution release assets."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts import release_artifact_contract
except ImportError:  # Direct execution adds scripts/, not the repository root.
    import release_artifact_contract  # type: ignore[no-redef]


class PythonReleaseArtifactError(RuntimeError):
    """Raised when exact developer release artifacts cannot be built."""


@dataclass(frozen=True)
class PythonReleaseRequest:
    root: Path
    staging_dir: Path
    output_dir: Path
    version: str
    python: Path


def _require_contained(path: Path, *, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PythonReleaseArtifactError(f"{label} is outside the release root: {path}") from None
    return resolved


def _bounded_error(stderr: str | None) -> str:
    detail = " ".join((stderr or "").split())
    return detail[:500] if detail else "no diagnostic output"


def _run(stage: str, command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        raise PythonReleaseArtifactError(
            f"{stage} failed with exit code {exc.returncode}: {_bounded_error(exc.stderr)}"
        ) from None
    except subprocess.TimeoutExpired:
        raise PythonReleaseArtifactError(f"{stage} timed out after 600 seconds") from None
    except OSError as exc:
        raise PythonReleaseArtifactError(f"{stage} could not start: {exc}") from None
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def _publish_file(source: Path, destination: Path) -> None:
    temporary_name: str | None = None
    try:
        with source.open("rb") as source_handle:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                shutil.copyfileobj(source_handle, temporary, length=1024 * 1024)
                temporary.flush()
                os.fsync(temporary.fileno())
                os.fchmod(temporary.fileno(), 0o644)
        Path(temporary_name).replace(destination)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def build_artifacts(request: PythonReleaseRequest) -> tuple[Path, Path]:
    root = request.root.resolve(strict=True)
    if not root.is_dir():
        raise PythonReleaseArtifactError(f"release root is not a directory: {request.root}")
    python = request.python.absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise PythonReleaseArtifactError(f"Python is missing or not executable: {request.python}")
    staging = _require_contained(request.staging_dir, root=root, label="staging directory")
    output = _require_contained(request.output_dir, root=root, label="output directory")
    if staging.exists() and any(staging.iterdir()):
        raise PythonReleaseArtifactError(f"staging directory must be empty: {staging}")
    staging.mkdir(parents=True, mode=0o700, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    staged_paths = release_artifact_contract.developer_artifact_paths(
        staging,
        version=request.version,
    )
    output_paths = release_artifact_contract.developer_artifact_paths(
        output,
        version=request.version,
    )
    _run(
        "Python distribution build",
        [
            str(python),
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(staging),
            str(root),
        ],
    )
    actual_names = {path.name for path in staging.iterdir()}
    expected_names = {path.name for path in staged_paths}
    if actual_names != expected_names or not all(path.is_file() for path in staged_paths):
        raise PythonReleaseArtifactError(
            "Python distribution build did not create exactly the reviewed wheel and sdist"
        )
    _run(
        "Twine metadata validation",
        [str(python), "-m", "twine", "check", *(str(path) for path in staged_paths)],
    )
    for source, destination in zip(staged_paths, output_paths, strict=True):
        _publish_file(source, destination)
    return output_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        artifacts = build_artifacts(
            PythonReleaseRequest(
                root=args.root,
                staging_dir=args.staging_dir,
                output_dir=args.output_dir,
                version=args.version,
                python=Path(sys.executable),
            )
        )
    except (OSError, ValueError, PythonReleaseArtifactError) as exc:
        print(f"Python release artifact build failed: {exc}", file=sys.stderr)
        return 2
    for artifact in artifacts:
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
