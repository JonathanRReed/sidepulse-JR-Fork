#!/usr/bin/env python3
"""Define the authoritative JR-Bar macOS release artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PRODUCT_DISPLAY_NAME = "JR-Bar"
COMPATIBILITY_APP_BUNDLE = "SidePulse.app"
AUTHORITATIVE_ARTIFACT_KIND = "pkg"
REQUIRED_SIGNING_INPUTS = (
    "APP_SIGN_IDENTITY",
    "INSTALLER_SIGN_IDENTITY",
    "NOTARY_PROFILE",
    "SPARKLE_KEY_ACCOUNT",
)
UPDATER_KIND = "sparkle"
APPCAST_SUPPORTED = True
DEVELOPER_DISTRIBUTION_NAME = "sidepulse"
SPARKLE_VERSION = "2.9.6"
SPARKLE_ARCHIVE_URL = (
    "https://github.com/sparkle-project/Sparkle/releases/"
    "download/2.9.6/Sparkle-2.9.6.tar.xz"
)
SPARKLE_ARCHIVE_SHA256 = "52bf9e88cdd972fc0c81501377a880e90d47031bd8ca5462488f843e2609e192"
SPARKLE_FEED_URL = (
    "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
    "releases/download/updates/appcast.xml"
)
STABLE_CHANNEL = "stable"
BETA_CHANNEL = "beta"
STABLE_PHASED_ROLLOUT_INTERVAL = 86400
APPCAST_FILENAME = "appcast.xml"
CHANNEL_METADATA_FILENAME = "jr-bar-update-channel.json"

_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_ARCHITECTURE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def _safe_component(value: str, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value) or ".." in value:
        raise ValueError(f"unsafe release {label}: {value!r}")
    return value


def artifact_name(*, version: str, architecture: str) -> str:
    safe_version = _safe_component(
        version,
        label="version",
        pattern=_VERSION_PATTERN,
    )
    safe_architecture = _safe_component(
        architecture,
        label="architecture",
        pattern=_ARCHITECTURE_PATTERN,
    )
    return f"SidePulse-{safe_version}-{safe_architecture}.pkg"


def artifact_path(
    dist_dir: Path,
    *,
    version: str,
    architecture: str,
) -> Path:
    return Path(dist_dir) / artifact_name(
        version=version,
        architecture=architecture,
    )


def updater_archive_name(*, version: str, architecture: str) -> str:
    safe_version = _safe_component(
        version,
        label="version",
        pattern=_VERSION_PATTERN,
    )
    safe_architecture = _safe_component(
        architecture,
        label="architecture",
        pattern=_ARCHITECTURE_PATTERN,
    )
    return f"SidePulse-{safe_version}-{safe_architecture}.zip"


def updater_archive_path(
    dist_dir: Path,
    *,
    version: str,
    architecture: str,
) -> Path:
    return Path(dist_dir) / updater_archive_name(
        version=version,
        architecture=architecture,
    )


def appcast_name() -> str:
    return APPCAST_FILENAME


def appcast_path(dist_dir: Path) -> Path:
    return Path(dist_dir) / appcast_name()


def channel_metadata_name() -> str:
    return CHANNEL_METADATA_FILENAME


def channel_metadata_path(dist_dir: Path) -> Path:
    return Path(dist_dir) / channel_metadata_name()


def developer_artifact_names(*, version: str) -> tuple[str, str]:
    safe_version = _safe_component(
        version,
        label="version",
        pattern=_VERSION_PATTERN,
    )
    return (
        f"{DEVELOPER_DISTRIBUTION_NAME}-{safe_version}-py3-none-any.whl",
        f"{DEVELOPER_DISTRIBUTION_NAME}-{safe_version}.tar.gz",
    )


def developer_artifact_paths(
    dist_dir: Path,
    *,
    version: str,
) -> tuple[Path, Path]:
    wheel, sdist = developer_artifact_names(version=version)
    return Path(dist_dir) / wheel, Path(dist_dir) / sdist


def contract_document(*, version: str, architecture: str) -> dict[str, object]:
    return {
        "schema_version": 3,
        "product_display_name": PRODUCT_DISPLAY_NAME,
        "compatibility_app_bundle": COMPATIBILITY_APP_BUNDLE,
        "authoritative_macos_artifact": {
            "kind": AUTHORITATIVE_ARTIFACT_KIND,
            "name": artifact_name(version=version, architecture=architecture),
            "primary": True,
            "required": True,
        },
        "required_signing_inputs": list(REQUIRED_SIGNING_INPUTS),
        "developer_release_artifacts": [
            {
                "kind": "wheel",
                "name": developer_artifact_names(version=version)[0],
                "authoritative_macos_product": False,
                "required_for_github_release": True,
            },
            {
                "kind": "sdist",
                "name": developer_artifact_names(version=version)[1],
                "authoritative_macos_product": False,
                "required_for_github_release": True,
            },
        ],
        "supplemental_macos_artifacts": [
            {
                "kind": "sparkle_update_archive",
                "name": updater_archive_name(
                    version=version,
                    architecture=architecture,
                ),
                "authoritative_macos_product": False,
                "primary": False,
                "required_for_github_release": True,
                "contents": [COMPATIBILITY_APP_BUNDLE],
            },
            {
                "kind": "sparkle_appcast",
                "name": appcast_name(),
                "authoritative_macos_product": False,
                "primary": False,
                "required_for_github_release": True,
                "signed": True,
            },
            {
                "kind": "sparkle_channel_metadata",
                "name": channel_metadata_name(),
                "authoritative_macos_product": False,
                "primary": False,
                "required_for_github_release": True,
                "required_fields": [
                    "candidate_id",
                    "channel",
                    "version",
                    "build",
                    "architecture",
                    "feed_url",
                    "download_url",
                    "phased_rollout_interval_seconds",
                    "public_key_fingerprint_sha256",
                    "archive",
                    "appcast",
                ],
            },
        ],
        "updater": {
            "kind": UPDATER_KIND,
            "appcast_supported": APPCAST_SUPPORTED,
            "framework": {
                "version": SPARKLE_VERSION,
                "archive_url": SPARKLE_ARCHIVE_URL,
                "archive_sha256": SPARKLE_ARCHIVE_SHA256,
            },
            "feed_url": SPARKLE_FEED_URL,
            "channels": {
                STABLE_CHANNEL: {
                    "default": True,
                    "sparkle_channel": None,
                    "phased_rollout_interval": STABLE_PHASED_ROLLOUT_INTERVAL,
                },
                BETA_CHANNEL: {
                    "default": False,
                    "sparkle_channel": BETA_CHANNEL,
                    "phased_rollout_interval": None,
                },
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument(
        "--format",
        choices=(
            "json",
            "path",
            "developer-paths",
            "updater-path",
            "appcast-path",
            "channel-metadata-path",
        ),
        required=True,
    )
    args = parser.parse_args()

    try:
        if args.format == "path":
            print(
                artifact_path(
                    args.dist_dir,
                    version=args.version,
                    architecture=args.architecture,
                )
            )
        elif args.format == "developer-paths":
            for path in developer_artifact_paths(
                args.dist_dir,
                version=args.version,
            ):
                print(path)
        elif args.format == "updater-path":
            print(
                updater_archive_path(
                    args.dist_dir,
                    version=args.version,
                    architecture=args.architecture,
                )
            )
        elif args.format == "appcast-path":
            print(appcast_path(args.dist_dir))
        elif args.format == "channel-metadata-path":
            print(channel_metadata_path(args.dist_dir))
        else:
            print(
                json.dumps(
                    contract_document(
                        version=args.version,
                        architecture=args.architecture,
                    ),
                    sort_keys=True,
                )
            )
    except ValueError as exc:
        print(f"release artifact contract failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
