#!/usr/bin/env python3
"""Generate and validate JR-Bar's signed Sparkle appcast and channel metadata."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

try:
    from scripts import package_sparkle_archive
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    import package_sparkle_archive

SPARKLE_VERSION = "2.9.6"
EXPECTED_SPARKLE_DISTRIBUTION_SHA256 = (
    "a57379fc39978044fe38787bda8ca8613d48bc9da48296514622be83651d17ce"
)
DEFAULT_KEYCHAIN_ACCOUNT = "io.sidepulse.app"
EXPECTED_PUBLIC_KEY = "IlvZMoPh67naKxN2ZvlnfdHildsgGxPWeEi8IOhVQ+8="
EXPECTED_BUNDLE_IDENTIFIER = "io.sidepulse.app"
FEED_URL = (
    "https://github.com/JonathanRReed/sidepulse-JR-Fork/"
    "releases/download/updates/appcast.xml"
)
VERSION_RELEASE_PREFIX = (
    "https://github.com/JonathanRReed/sidepulse-JR-Fork/releases/download"
)
SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
METADATA_NAME = "jr-bar-update-channel.json"
APPCAST_NAME = "appcast.xml"
PHASED_ROLLOUT_INTERVAL = 86400
MINIMUM_SUPPORTED_MACOS = "11.0"

_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SAFE_ARCHITECTURE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_SAFE_BUILD = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}\Z")
_SIGNED_FEED_MARKER = re.compile(
    r"<!--\s*sparkle-signatures:\s*"
    r"edSignature:\s*(?P<signature>\S+)\s*"
    r"length:\s*(?P<length>[0-9]+)\s*-->",
    re.DOTALL,
)


class SparkleChannelError(RuntimeError):
    """Raised when generation inputs or signed outputs fail closed validation."""


@dataclass(frozen=True)
class ChannelOutputs:
    appcast: Path
    metadata: Path


@dataclass(frozen=True)
class ArchiveIdentity:
    version: str
    build: str
    architecture: str
    minimum_system_version: str
    public_key_fingerprint: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sparkle_distribution_digest(path: Path) -> str:
    """Hash the exact official framework, helper tools, and license tree."""

    distribution = _require_plain_directory(path, label="Sparkle distribution")
    selected = {
        distribution / "Sparkle.framework",
        distribution / "bin",
        distribution / "bin" / "generate_appcast",
        distribution / "bin" / "generate_keys",
        distribution / "bin" / "sign_update",
        distribution / "LICENSE",
    }
    framework = distribution / "Sparkle.framework"
    try:
        selected.update(framework.rglob("*"))
    except OSError as exc:
        raise SparkleChannelError(f"Sparkle distribution cannot be inspected: {exc}") from None

    records: list[dict[str, object]] = []
    for candidate in sorted(
        selected,
        key=lambda item: item.relative_to(distribution).as_posix(),
    ):
        relative = candidate.relative_to(distribution).as_posix()
        try:
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode):
                record: dict[str, object] = {
                    "path": relative,
                    "type": "symlink",
                    "target": os.readlink(candidate),
                }
            elif stat.S_ISDIR(mode):
                record = {"path": relative, "type": "directory"}
            elif stat.S_ISREG(mode):
                record = {
                    "path": relative,
                    "type": "file",
                    "executable": bool(mode & 0o111),
                    "sha256": _sha256_file(candidate),
                }
            else:
                raise SparkleChannelError(
                    f"Sparkle distribution has an unsupported member: {relative}"
                )
        except OSError as exc:
            raise SparkleChannelError(
                f"Sparkle distribution member cannot be inspected: {relative}: {exc}"
            ) from None
        records.append(record)
    encoded = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_plain_directory(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SparkleChannelError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise SparkleChannelError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SparkleChannelError(f"{label} is not a directory: {path}")
    return path.resolve(strict=True)


def _require_regular_file(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SparkleChannelError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise SparkleChannelError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise SparkleChannelError(f"{label} is not a regular file: {path}")
    return path.resolve(strict=True)


def _prepare_output_directory(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        parent = _require_plain_directory(path.parent, label="channel output parent")
        created = parent / path.name
        try:
            created.mkdir(mode=0o755)
        except OSError as exc:
            raise SparkleChannelError(f"channel output directory could not be created: {path}") from exc
        return created.resolve(strict=True)
    except OSError as exc:
        raise SparkleChannelError(f"channel output directory cannot be inspected: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise SparkleChannelError(f"channel output directory must not be a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise SparkleChannelError(f"channel output directory is not a directory: {path}")
    return path.resolve(strict=True)


def _validate_output_targets(output_dir: Path) -> None:
    for name in (APPCAST_NAME, METADATA_NAME):
        target = output_dir / name
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SparkleChannelError(f"channel output cannot be inspected: {target}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise SparkleChannelError(f"channel output must be a regular file, not a symlink: {target}")


def _validate_sparkle_distribution(path: Path) -> tuple[Path, Path, Path]:
    distribution = _require_plain_directory(path, label="Sparkle distribution")
    generate_appcast = _require_regular_file(
        distribution / "bin" / "generate_appcast",
        label="Sparkle generate_appcast tool",
    )
    sign_update = _require_regular_file(
        distribution / "bin" / "sign_update",
        label="Sparkle sign_update tool",
    )
    generate_keys = _require_regular_file(
        distribution / "bin" / "generate_keys",
        label="Sparkle generate_keys tool",
    )
    if not os.access(generate_appcast, os.X_OK):
        raise SparkleChannelError(f"Sparkle generate_appcast tool is not executable: {generate_appcast}")
    if not os.access(sign_update, os.X_OK):
        raise SparkleChannelError(f"Sparkle sign_update tool is not executable: {sign_update}")
    if not os.access(generate_keys, os.X_OK):
        raise SparkleChannelError(f"Sparkle generate_keys tool is not executable: {generate_keys}")
    framework_plists = tuple(
        sorted(
            (distribution / "Sparkle.framework" / "Versions").glob("*/Resources/Info.plist"),
            key=lambda item: item.as_posix(),
        )
    )
    if not framework_plists:
        raise SparkleChannelError(f"supplied Sparkle distribution is not pinned to {SPARKLE_VERSION}")
    versions: set[tuple[object, object]] = set()
    for plist_path in framework_plists:
        if plist_path.is_symlink():
            continue
        try:
            with plist_path.open("rb") as stream:
                document = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException) as exc:
            raise SparkleChannelError(f"Sparkle framework metadata is invalid: {plist_path}") from exc
        if isinstance(document, dict):
            versions.add(
                (
                    document.get("CFBundleIdentifier"),
                    document.get("CFBundleShortVersionString"),
                )
            )
    if versions != {("org.sparkle-project.Sparkle", SPARKLE_VERSION)}:
        raise SparkleChannelError(f"supplied Sparkle distribution is not pinned to {SPARKLE_VERSION}")
    if _sparkle_distribution_digest(distribution) != EXPECTED_SPARKLE_DISTRIBUTION_SHA256:
        raise SparkleChannelError(
            "Sparkle distribution bytes do not match the pinned Sparkle release"
        )
    return generate_appcast, sign_update, generate_keys


def _decode_signature(value: object, *, label: str, byte_length: int) -> str:
    if not isinstance(value, str) or not value:
        raise SparkleChannelError(f"{label} is missing")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SparkleChannelError(f"{label} is not valid base64") from None
    if len(decoded) != byte_length:
        raise SparkleChannelError(f"{label} has the wrong byte length")
    return value


def _archive_identity(archive: Path) -> ArchiveIdentity:
    resolved_archive = _require_regular_file(archive, label="Sparkle update archive")
    try:
        package_sparkle_archive.validate_archive(archive=resolved_archive)
    except package_sparkle_archive.SparkleArchiveError as exc:
        raise SparkleChannelError(f"Sparkle update archive is unsafe: {exc}") from None
    try:
        with zipfile.ZipFile(resolved_archive) as bundle_zip:
            raw_plist = bundle_zip.read("SidePulse.app/Contents/Info.plist")
        document = plistlib.loads(raw_plist)
    except (KeyError, OSError, plistlib.InvalidFileException, zipfile.BadZipFile) as exc:
        raise SparkleChannelError("Sparkle update archive has no valid SidePulse.app Info.plist") from exc
    if not isinstance(document, dict) or document.get("CFBundleIdentifier") != EXPECTED_BUNDLE_IDENTIFIER:
        raise SparkleChannelError("Sparkle update archive has the wrong bundle identifier")
    version = document.get("CFBundleShortVersionString")
    build = document.get("CFBundleVersion")
    if not isinstance(version, str) or _SAFE_VERSION.fullmatch(version) is None or ".." in version:
        raise SparkleChannelError("Sparkle update archive has an unsafe current version")
    if not isinstance(build, str) or _SAFE_BUILD.fullmatch(build) is None:
        raise SparkleChannelError("Sparkle update archive has an unsafe current build")
    prefix = f"SidePulse-{version}-"
    if not resolved_archive.name.startswith(prefix) or not resolved_archive.name.endswith(".zip"):
        raise SparkleChannelError("Sparkle update archive name does not match its current version")
    architecture = resolved_archive.name[len(prefix) : -len(".zip")]
    if _SAFE_ARCHITECTURE.fullmatch(architecture) is None or ".." in architecture:
        raise SparkleChannelError("Sparkle update archive name has an unsafe architecture")
    if document.get("SUFeedURL") != FEED_URL:
        raise SparkleChannelError("Sparkle update archive has the wrong feed URL")
    if document.get("SURequireSignedFeed") is not True:
        raise SparkleChannelError("Sparkle update archive does not require a signed feed")
    public_key = document.get("SUPublicEDKey")
    if public_key != EXPECTED_PUBLIC_KEY:
        raise SparkleChannelError("Sparkle update archive has the wrong Ed25519 public key")
    _decode_signature(public_key, label="Sparkle public key", byte_length=32)
    minimum_system_version = document.get("LSMinimumSystemVersion")
    if minimum_system_version != MINIMUM_SUPPORTED_MACOS:
        raise SparkleChannelError(
            f"Sparkle update archive must require macOS {MINIMUM_SUPPORTED_MACOS}"
        )
    public_key_bytes = base64.b64decode(public_key, validate=True)
    return ArchiveIdentity(
        version=version,
        build=build,
        architecture=architecture,
        minimum_system_version=minimum_system_version,
        public_key_fingerprint=hashlib.sha256(public_key_bytes).hexdigest(),
    )


def _credentials(*, keychain_account: str | None) -> list[str]:
    account = keychain_account or DEFAULT_KEYCHAIN_ACCOUNT
    if _SAFE_ACCOUNT.fullmatch(account) is None or ".." in account:
        raise SparkleChannelError("Keychain account name is unsafe")
    return ["--account", account]


def _validate_keychain_public_key(
    generate_keys: Path,
    *,
    credential_arguments: list[str],
) -> None:
    command = [str(generate_keys), *credential_arguments, "-p"]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise SparkleChannelError("generate_keys public-key lookup timed out after 30 seconds") from None
    except OSError as exc:
        raise SparkleChannelError(f"generate_keys public-key lookup could not start: {exc}") from None
    if result.returncode != 0:
        raise SparkleChannelError(
            f"generate_keys public-key lookup failed with exit code {result.returncode}"
        )
    if result.stdout.strip() != EXPECTED_PUBLIC_KEY:
        raise SparkleChannelError("Keychain account does not contain JR-Bar's pinned Sparkle key")


def _copy_verified(source: Path, destination: Path, *, label: str) -> None:
    before = _sha256_file(source)
    try:
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise SparkleChannelError(f"{label} could not be staged") from exc
    after = _sha256_file(source)
    if before != after or _sha256_file(destination) != before:
        raise SparkleChannelError(f"{label} changed while it was staged")


def _validate_prior_appcast(path: Path) -> tuple[Path, dict[str, tuple[str, int]]]:
    appcast = _require_regular_file(path, label="previous appcast")
    try:
        payload = appcast.read_bytes()
        text = payload.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise SparkleChannelError("previous appcast contains unsafe XML declarations")
        root = ET.fromstring(payload)
    except SparkleChannelError:
        raise
    except (OSError, UnicodeError, ET.ParseError) as exc:
        raise SparkleChannelError("previous appcast is invalid XML") from exc
    if root.tag != "rss" or root.find("channel") is None or _SIGNED_FEED_MARKER.search(text) is None:
        raise SparkleChannelError("previous appcast is not a signed Sparkle feed")
    archives: dict[str, tuple[str, int]] = {}
    for enclosure in root.findall("./channel/item/enclosure"):
        raw_url = enclosure.get("url")
        parsed = urlsplit(raw_url or "")
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(
                "/JonathanRReed/sidepulse-JR-Fork/releases/download/v"
            )
        ):
            raise SparkleChannelError("previous appcast contains an untrusted archive URL")
        name = Path(parsed.path).name
        if not name.startswith("SidePulse-") or not name.endswith(".zip"):
            raise SparkleChannelError("previous appcast contains an invalid archive name")
        if name in archives:
            raise SparkleChannelError("previous appcast repeats an archive name")
        signature = _decode_signature(
            enclosure.get(f"{{{SPARKLE_NAMESPACE}}}edSignature"),
            label="previous appcast archive EdDSA signature",
            byte_length=64,
        )
        try:
            length = int(enclosure.get("length", ""), 10)
        except ValueError:
            raise SparkleChannelError("previous appcast archive length is invalid") from None
        if length <= 0:
            raise SparkleChannelError("previous appcast archive length is invalid")
        archives[name] = (signature, length)
    return appcast, archives


def _run_tool(stage: str, command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise SparkleChannelError(f"{stage} timed out after 300 seconds") from None
    except OSError as exc:
        raise SparkleChannelError(f"{stage} could not start: {exc}") from None
    if result.returncode != 0:
        raise SparkleChannelError(f"{stage} failed with exit code {result.returncode}")


def _element_text(item: ET.Element, local_name: str) -> str | None:
    element = item.find(f"{{{SPARKLE_NAMESPACE}}}{local_name}")
    if element is None or element.text is None:
        return None
    return element.text.strip()


def _parse_signed_feed(appcast: Path) -> tuple[ET.Element, str]:
    try:
        payload = appcast.read_bytes()
        text = payload.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SparkleChannelError("generated appcast is missing or unreadable") from exc
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise SparkleChannelError("generated appcast contains unsafe XML declarations")
    marker = _SIGNED_FEED_MARKER.search(text)
    if marker is None:
        raise SparkleChannelError("generated appcast has no signed-feed marker")
    _decode_signature(marker.group("signature"), label="signed-feed marker EdDSA signature", byte_length=64)
    if int(marker.group("length")) <= 0:
        raise SparkleChannelError("signed-feed marker length is invalid")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise SparkleChannelError("generated appcast is invalid XML") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise SparkleChannelError("generated appcast is not an RSS feed")
    return root, text


def _validate_current_item(
    *,
    appcast: Path,
    archive: Path,
    identity: ArchiveIdentity,
    channel: str,
) -> tuple[str, str]:
    root, _ = _parse_signed_feed(appcast)
    items = root.findall("./channel/item")
    version_matches = [item for item in items if _element_text(item, "shortVersionString") == identity.version]
    if len(version_matches) != 1:
        raise SparkleChannelError("generated appcast must contain exactly one current version item")
    item = version_matches[0]
    if _element_text(item, "version") != identity.build:
        raise SparkleChannelError("generated appcast current build does not match the archive")
    if _element_text(item, "minimumSystemVersion") != identity.minimum_system_version:
        raise SparkleChannelError(
            "generated appcast minimum system version does not match the archive"
        )
    build_matches = [candidate for candidate in items if _element_text(candidate, "version") == identity.build]
    if build_matches != [item]:
        raise SparkleChannelError("generated appcast must contain exactly one current build item")

    encoded_channel = _element_text(item, "channel")
    phased_interval = _element_text(item, "phasedRolloutInterval")
    if channel == "stable":
        if encoded_channel is not None:
            raise SparkleChannelError("stable channel must be encoded by omitting sparkle:channel")
        if phased_interval != str(PHASED_ROLLOUT_INTERVAL):
            raise SparkleChannelError("stable channel phased rollout interval must be 86400")
        pub_date = item.findtext("pubDate")
        if not isinstance(pub_date, str) or not pub_date.strip():
            raise SparkleChannelError("stable channel phased rollout requires a publication date")
    else:
        if encoded_channel != "beta":
            raise SparkleChannelError("beta channel must use the exact beta encoding")
        if phased_interval is not None:
            raise SparkleChannelError("beta channel must not use phased rollout")

    enclosure = item.find("enclosure")
    if enclosure is None:
        raise SparkleChannelError("current appcast item has no enclosure")
    download_url = (
        f"{VERSION_RELEASE_PREFIX}/v{identity.version}/{archive.name}"
    )
    if enclosure.get("url") != download_url:
        raise SparkleChannelError("current appcast enclosure has the wrong download URL")
    if enclosure.get("length") != str(archive.stat().st_size):
        raise SparkleChannelError("current appcast enclosure length does not match the archive")
    signature = _decode_signature(
        enclosure.get(f"{{{SPARKLE_NAMESPACE}}}edSignature"),
        label="current appcast enclosure EdDSA signature",
        byte_length=64,
    )
    return download_url, signature


def _metadata_document(
    *,
    archive: Path,
    appcast: Path,
    identity: ArchiveIdentity,
    candidate_id: str,
    channel: str,
    download_url: str,
    enclosure_signature: str,
) -> dict[str, object]:
    return {
        "document": "jr-bar-update-channel",
        "schema_version": 1,
        "candidate_id": candidate_id,
        "channel": channel,
        "version": identity.version,
        "build": identity.build,
        "architecture": identity.architecture,
        "feed_url": FEED_URL,
        "download_url": download_url,
        "phased_rollout_interval_seconds": PHASED_ROLLOUT_INTERVAL if channel == "stable" else None,
        "public_key_fingerprint_sha256": identity.public_key_fingerprint,
        "archive": {
            "name": archive.name,
            "bytes": archive.stat().st_size,
            "sha256": _sha256_file(archive),
            "ed_signature": enclosure_signature,
        },
        "appcast": {
            "name": APPCAST_NAME,
            "bytes": appcast.stat().st_size,
            "sha256": _sha256_file(appcast),
        },
    }


def _require_exact_keys(document: dict[str, object], expected: set[str], *, label: str) -> None:
    if set(document) != expected:
        raise SparkleChannelError(f"{label} has unexpected or missing fields")


def validate_channel_outputs(
    *,
    archive: Path,
    appcast: Path,
    metadata: Path,
    candidate_id: str,
    sparkle_distribution: Path,
    keychain_account: str | None = None,
) -> None:
    """Cryptographically verify and rehash a candidate-bound channel output set."""

    if _HEX_64.fullmatch(candidate_id) is None:
        raise SparkleChannelError("candidate ID must be a lowercase SHA-256 digest")
    _, sign_update, generate_keys = _validate_sparkle_distribution(
        Path(sparkle_distribution)
    )
    credential_arguments = _credentials(keychain_account=keychain_account)
    _validate_keychain_public_key(
        generate_keys,
        credential_arguments=credential_arguments,
    )
    resolved_archive = _require_regular_file(archive, label="Sparkle update archive")
    resolved_appcast = _require_regular_file(appcast, label="generated appcast")
    resolved_metadata = _require_regular_file(metadata, label="update channel metadata")
    identity = _archive_identity(resolved_archive)
    try:
        document = json.loads(resolved_metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SparkleChannelError("update channel metadata is invalid JSON") from exc
    if not isinstance(document, dict):
        raise SparkleChannelError("update channel metadata must be an object")
    _require_exact_keys(
        document,
        {
            "document",
            "schema_version",
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
        },
        label="update channel metadata",
    )
    if document.get("document") != "jr-bar-update-channel" or document.get("schema_version") != 1:
        raise SparkleChannelError("update channel metadata has the wrong document identity")
    if document.get("candidate_id") != candidate_id:
        raise SparkleChannelError("update channel metadata belongs to another candidate ID")
    channel = document.get("channel")
    if channel not in {"stable", "beta"}:
        raise SparkleChannelError("update channel metadata has an invalid channel")
    download_url, signature = _validate_current_item(
        appcast=resolved_appcast,
        archive=resolved_archive,
        identity=identity,
        channel=channel,
    )
    expected_scalars = {
        "version": identity.version,
        "build": identity.build,
        "architecture": identity.architecture,
        "feed_url": FEED_URL,
        "download_url": download_url,
        "phased_rollout_interval_seconds": PHASED_ROLLOUT_INTERVAL if channel == "stable" else None,
        "public_key_fingerprint_sha256": identity.public_key_fingerprint,
    }
    for field, expected in expected_scalars.items():
        if document.get(field) != expected:
            raise SparkleChannelError(f"update channel metadata {field} does not match the candidate")
    archive_record = document.get("archive")
    appcast_record = document.get("appcast")
    if not isinstance(archive_record, dict) or not isinstance(appcast_record, dict):
        raise SparkleChannelError("update channel artifact records must be objects")
    _require_exact_keys(
        archive_record,
        {"name", "bytes", "sha256", "ed_signature"},
        label="update archive metadata",
    )
    _require_exact_keys(
        appcast_record,
        {"name", "bytes", "sha256"},
        label="appcast metadata",
    )
    expected_archive = {
        "name": resolved_archive.name,
        "bytes": resolved_archive.stat().st_size,
        "sha256": _sha256_file(resolved_archive),
        "ed_signature": signature,
    }
    if archive_record != expected_archive:
        raise SparkleChannelError("update archive metadata hash or signature does not match")
    expected_appcast = {
        "name": APPCAST_NAME,
        "bytes": resolved_appcast.stat().st_size,
        "sha256": _sha256_file(resolved_appcast),
    }
    if appcast_record != expected_appcast:
        raise SparkleChannelError("appcast metadata hash or length does not match")
    _run_tool(
        "sign_update final appcast verification",
        [
            str(sign_update),
            *credential_arguments,
            "--verify",
            str(resolved_appcast),
        ],
    )
    _run_tool(
        "sign_update final update archive verification",
        [
            str(sign_update),
            *credential_arguments,
            "--verify",
            str(resolved_archive),
            signature,
        ],
    )


def generate_channel(
    *,
    sparkle_distribution: Path,
    archive: Path,
    output_dir: Path,
    candidate_id: str,
    channel: str = "stable",
    keychain_account: str | None = None,
    previous_appcast: Path | None = None,
    previous_archives: tuple[Path, ...] = (),
) -> ChannelOutputs:
    """Generate, sign, validate, and atomically install one update channel."""

    if _HEX_64.fullmatch(candidate_id) is None:
        raise SparkleChannelError("candidate ID must be a lowercase SHA-256 digest")
    if channel not in {"stable", "beta"}:
        raise SparkleChannelError("channel must be stable or beta")
    generate_appcast, sign_update, generate_keys = _validate_sparkle_distribution(
        Path(sparkle_distribution)
    )
    resolved_archive = _require_regular_file(Path(archive), label="Sparkle update archive")
    identity = _archive_identity(resolved_archive)
    credential_arguments = _credentials(keychain_account=keychain_account)
    _validate_keychain_public_key(
        generate_keys,
        credential_arguments=credential_arguments,
    )
    previous_feed = (
        _validate_prior_appcast(Path(previous_appcast)) if previous_appcast is not None else None
    )
    resolved_previous_appcast = previous_feed[0] if previous_feed is not None else None
    previous_archive_records = previous_feed[1] if previous_feed is not None else {}
    if resolved_previous_appcast is not None:
        _run_tool(
            "sign_update previous appcast verification",
            [
                str(sign_update),
                *credential_arguments,
                "--verify",
                str(resolved_previous_appcast),
            ],
        )
    retained_archives: list[Path] = []
    retained_names: set[str] = {resolved_archive.name}
    for previous_archive in previous_archives:
        resolved_previous = _require_regular_file(Path(previous_archive), label="previous update archive")
        try:
            package_sparkle_archive.validate_archive(archive=resolved_previous)
        except package_sparkle_archive.SparkleArchiveError as exc:
            raise SparkleChannelError(f"previous update archive is unsafe: {exc}") from None
        if resolved_previous.name in retained_names:
            raise SparkleChannelError(f"update archive staging repeats a name: {resolved_previous.name}")
        retained_names.add(resolved_previous.name)
        record = previous_archive_records.get(resolved_previous.name)
        if record is None:
            raise SparkleChannelError(
                f"previous update archive is not referenced by the signed prior appcast: {resolved_previous.name}"
            )
        signature, expected_length = record
        if resolved_previous.stat().st_size != expected_length:
            raise SparkleChannelError(
                f"previous update archive length does not match its signed prior appcast: {resolved_previous.name}"
            )
        _run_tool(
            "sign_update previous archive verification",
            [
                str(sign_update),
                *credential_arguments,
                "--verify",
                str(resolved_previous),
                signature,
            ],
        )
        retained_archives.append(resolved_previous)

    resolved_output_dir = _prepare_output_directory(Path(output_dir))
    _validate_output_targets(resolved_output_dir)
    final_appcast = resolved_output_dir / APPCAST_NAME
    final_metadata = resolved_output_dir / METADATA_NAME
    with tempfile.TemporaryDirectory(
        dir=resolved_output_dir,
        prefix=".sparkle-channel.",
    ) as staging_directory:
        stage = Path(staging_directory)
        staged_archive = stage / resolved_archive.name
        _copy_verified(resolved_archive, staged_archive, label="Sparkle update archive")
        for previous_archive in retained_archives:
            _copy_verified(
                previous_archive,
                stage / previous_archive.name,
                label="previous update archive",
            )
        staged_appcast = stage / APPCAST_NAME
        if resolved_previous_appcast is not None:
            _copy_verified(resolved_previous_appcast, staged_appcast, label="previous appcast")

        download_url_prefix = f"{VERSION_RELEASE_PREFIX}/v{identity.version}/"
        generate_command = [
            str(generate_appcast),
            *credential_arguments,
            "--download-url-prefix",
            download_url_prefix,
            "--versions",
            identity.build,
            "--maximum-versions",
            "0",
            "--maximum-deltas",
            "0",
        ]
        if channel == "stable":
            generate_command.extend(
                ["--phased-rollout-interval", str(PHASED_ROLLOUT_INTERVAL)]
            )
        else:
            generate_command.extend(["--channel", "beta"])
        generate_command.extend(["-o", str(staged_appcast), str(stage)])
        _run_tool("generate_appcast", generate_command)
        if not staged_appcast.is_file():
            raise SparkleChannelError("generate_appcast reported success without creating appcast.xml")
        if not staged_archive.is_file() or _sha256_file(staged_archive) != _sha256_file(resolved_archive):
            raise SparkleChannelError("generate_appcast changed or removed the current update archive")

        _run_tool(
            "sign_update",
            [str(sign_update), *credential_arguments, str(staged_appcast)],
        )
        download_url, enclosure_signature = _validate_current_item(
            appcast=staged_appcast,
            archive=staged_archive,
            identity=identity,
            channel=channel,
        )
        metadata_document = _metadata_document(
            archive=staged_archive,
            appcast=staged_appcast,
            identity=identity,
            candidate_id=candidate_id,
            channel=channel,
            download_url=download_url,
            enclosure_signature=enclosure_signature,
        )
        staged_metadata = stage / METADATA_NAME
        staged_metadata.write_text(
            json.dumps(metadata_document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(staged_appcast, 0o644)
        os.chmod(staged_metadata, 0o644)
        validate_channel_outputs(
            archive=resolved_archive,
            appcast=staged_appcast,
            metadata=staged_metadata,
            candidate_id=candidate_id,
            sparkle_distribution=Path(sparkle_distribution),
            keychain_account=keychain_account,
        )
        os.replace(staged_appcast, final_appcast)
        os.replace(staged_metadata, final_metadata)

    validate_channel_outputs(
        archive=resolved_archive,
        appcast=final_appcast,
        metadata=final_metadata,
        candidate_id=candidate_id,
        sparkle_distribution=Path(sparkle_distribution),
        keychain_account=keychain_account,
    )
    return ChannelOutputs(appcast=final_appcast, metadata=final_metadata)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sparkle-distribution", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--keychain-account")
    parser.add_argument("--previous-appcast", type=Path)
    parser.add_argument("--previous-archive", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        outputs = generate_channel(
            sparkle_distribution=args.sparkle_distribution,
            archive=args.archive,
            output_dir=args.output_dir,
            candidate_id=args.candidate_id,
            channel=args.channel,
            keychain_account=args.keychain_account,
            previous_appcast=args.previous_appcast,
            previous_archives=tuple(args.previous_archive),
        )
    except SparkleChannelError as exc:
        print(f"Sparkle channel generation failed: {exc}", file=sys.stderr)
        return 2
    print(outputs.appcast)
    print(outputs.metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
