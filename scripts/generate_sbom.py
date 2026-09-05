#!/usr/bin/env python3
"""Generate a deterministic CycloneDX SBOM for a SidePulse release environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(r"[-_.]+")


def _normalized_name(value: str) -> str:
    return _NAME.sub("-", value).casefold()


def _timestamp() -> str:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw:
        try:
            moment = datetime.fromtimestamp(int(raw), timezone.utc)
        except (OSError, OverflowError, ValueError):
            raise ValueError("SOURCE_DATE_EPOCH must be a valid Unix timestamp") from None
    else:
        moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_artifact_path(path: Path, *, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact is outside release root: {path}") from exc


def _license(metadata) -> list[dict[str, object]]:
    expression = metadata.get("License-Expression")
    if isinstance(expression, str) and expression.strip():
        return [{"expression": expression.strip()}]
    value = metadata.get("License")
    if isinstance(value, str) and value.strip() and value.strip() != "UNKNOWN":
        return [{"license": {"name": value.strip()[:256]}}]
    return []


def _component(distribution) -> dict[str, object] | None:
    metadata = distribution.metadata
    name = metadata.get("Name")
    version = distribution.version
    if not isinstance(name, str) or not name.strip() or not version:
        return None
    normalized = _normalized_name(name)
    component: dict[str, object] = {
        "type": "library",
        "name": name.strip(),
        "version": str(version),
        "bom-ref": f"pkg:pypi/{normalized}@{version}",
        "purl": f"pkg:pypi/{normalized}@{version}",
    }
    licenses = _license(metadata)
    if licenses:
        component["licenses"] = licenses
    homepage = metadata.get("Home-page")
    if isinstance(homepage, str) and homepage.startswith(("https://", "http://")):
        component["externalReferences"] = [
            {"type": "website", "url": homepage[:2048]}
        ]
    return component


def build_sbom(
    *,
    application_version: str,
    artifacts: tuple[Path, ...] = (),
    root: Path | None = None,
) -> dict[str, object]:
    components_by_ref: dict[str, dict[str, object]] = {}
    for distribution in importlib.metadata.distributions():
        component = _component(distribution)
        if component is not None:
            components_by_ref[str(component["bom-ref"])] = component
    components = [components_by_ref[key] for key in sorted(components_by_ref)]

    artifact_properties = []
    artifact_root = (root or Path.cwd()).resolve()
    for artifact in sorted(artifacts, key=lambda item: item.name):
        relative_path = _relative_artifact_path(artifact, root=artifact_root)
        artifact_properties.extend(
            (
                {
                    "name": f"sidepulse:artifact:{relative_path}:sha256",
                    "value": _sha256(artifact),
                },
                {
                    "name": f"sidepulse:artifact:{relative_path}:bytes",
                    "value": str(artifact.stat().st_size),
                },
            )
        )

    identity = "|".join(
        [application_version, *(str(component["bom-ref"]) for component in components)]
    )
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"https://sidepulse.io/sbom/{identity}")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "timestamp": _timestamp(),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "JR-Bar SBOM Generator",
                        "version": "1",
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": "JR-Bar",
                "version": application_version,
                "bom-ref": f"pkg:github/JonathanRReed/sidepulse-JR-Fork@{application_version}",
                "properties": [
                    {"name": "sidepulse:python", "value": platform.python_version()},
                    {"name": "sidepulse:platform", "value": platform.platform()},
                    *artifact_properties,
                ],
            },
        },
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--application-version", required=True)
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    args = parser.parse_args()
    try:
        artifacts = tuple(path.resolve() for path in args.artifact)
        missing = [str(path) for path in artifacts if not path.is_file()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
        document = build_sbom(
            application_version=str(args.application_version),
            artifacts=artifacts,
            root=args.root.resolve(),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        print(f"SBOM generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"SBOM written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
