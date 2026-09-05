#!/usr/bin/env python3
"""Build and validate exact-candidate JR-Bar release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

DOCUMENT_NAME = "jr-bar-release-evidence"
SCHEMA_VERSION = 3
RECEIPT_SCHEMA_VERSION = 1
MAX_OUTPUT_BYTES = 32 * 1024
EXPECTED_BUNDLE_IDENTIFIER = "io.sidepulse.app"

REQUIRED_RECEIPT_KINDS = frozenset(
    {
        "app-gatekeeper",
        "app-notarization",
        "app-signature",
        "app-stapling",
        "bundle-closure",
        "clean-install",
        "entitlements",
        "hardware-smoke",
        "installed-upgrade",
        "notarization",
        "package-contents",
        "performance",
        "pkg-gatekeeper",
        "pkg-signature",
        "sbom",
        "settings-preservation",
        "signed-appcast",
        "source-gate",
        "sparkle-nested-signing",
        "stapling",
        "uninstall",
        "update-archive",
    }
)
HARDWARE_PROFILES = frozenset({"software", "any", "pro", "dot", "both"})
SOFTWARE_RECEIPT_KINDS = REQUIRED_RECEIPT_KINDS - {"hardware-smoke"}
APP_INPUT_RECEIPTS = frozenset(
    {
        "app-gatekeeper",
        "app-notarization",
        "app-signature",
        "app-stapling",
        "bundle-closure",
        "entitlements",
        "sparkle-nested-signing",
    }
)
UPDATE_ARCHIVE_INPUT_RECEIPTS = frozenset({"update-archive"})
APPCAST_INPUT_RECEIPTS = frozenset({"signed-appcast"})
SPECIAL_INPUT_RECEIPTS = frozenset({"performance", "sbom"})

_HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_TEAM_ID = re.compile(r"[A-Z0-9]{10}\Z")
_SAFE_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[A-Za-z0-9.-]*)?\Z")
_SAFE_ARCHITECTURE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
_ORDERED_VERSION = re.compile(r"(?P<release>[0-9]+(?:\.[0-9]+)*)(?P<prerelease>.*)\Z")
_SUBMISSION_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_INSTALLER_IDENTITY = re.compile(r"(Developer ID Installer:[^\r\n]*?\(([A-Z0-9]{10})\))")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\btskey-[A-Za-z0-9_-]{20,}\b"),
)
_SECRET_ARG_FLAGS = frozenset(
    {
        "--api-key",
        "--ed-key-file",
        "--key-file",
        "--notary-password",
        "--password",
        "--private-key",
        "--sparkle-private-key",
    }
)
_PRERELEASE_RANK = {
    "dev": 0,
    "snapshot": 0,
    "a": 10,
    "alpha": 10,
    "b": 20,
    "beta": 20,
    "pre": 30,
    "preview": 30,
    "rc": 40,
}


class EvidenceError(ValueError):
    """Raised when release evidence is incomplete or inconsistent."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_entries(root: Path) -> tuple[Path, ...]:
    return (root, *sorted(root.rglob("*"), key=lambda item: item.as_posix()))


def sha256_tree(path: Path) -> str:
    try:
        original_metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"tree input cannot be inspected: {path}") from exc
    if stat.S_ISLNK(original_metadata.st_mode):
        raise EvidenceError(f"tree input must not be a symlink: {path}")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise EvidenceError(f"tree input is not a directory: {path}")
    digest = hashlib.sha256()
    for item in _tree_entries(root):
        metadata = item.lstat()
        relative = "." if item == root else item.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
        digest.update(b"\0")
        if stat.S_ISDIR(metadata.st_mode):
            digest.update(b"directory\0")
        elif stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0")
            digest.update(os.readlink(item).encode("utf-8"))
            digest.update(b"\0")
        elif stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise EvidenceError(f"tree contains a hard-linked file: {item}")
            digest.update(b"file\0")
            digest.update(str(metadata.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(sha256_file(item)))
        else:
            raise EvidenceError(f"tree contains an unsupported filesystem entry: {item}")
    return digest.hexdigest()


def digest_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if resolved.is_file():
        return sha256_file(resolved)
    if resolved.is_dir():
        return sha256_tree(resolved)
    raise EvidenceError(f"evidence input is not a regular file or directory: {path}")


def _tree_bytes(path: Path) -> int:
    return sum(
        item.lstat().st_size for item in _tree_entries(path.resolve(strict=True)) if stat.S_ISREG(item.lstat().st_mode)
    )


def _relative_path(path: Path, *, root: Path) -> str:
    resolved = path.resolve(strict=True)
    try:
        return resolved.relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise EvidenceError(f"artifact is outside release root: {path}") from exc


def _path_record(path: Path, *, root: Path) -> dict[str, object]:
    try:
        original_metadata = path.lstat()
    except OSError as exc:
        raise EvidenceError(f"evidence input cannot be inspected: {path}") from exc
    if stat.S_ISLNK(original_metadata.st_mode):
        raise EvidenceError(f"evidence input must not be a symlink: {path}")
    resolved = path.resolve(strict=True)
    if resolved.is_file():
        kind = "file"
        size = resolved.stat().st_size
    elif resolved.is_dir():
        kind = "tree"
        size = _tree_bytes(resolved)
    else:
        raise EvidenceError(f"evidence input has unsupported type: {path}")
    return {
        "path": _relative_path(resolved, root=root),
        "kind": kind,
        "bytes": size,
        "sha256": digest_path(resolved),
    }


def _canonical_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{name} must be a non-empty string")
    return value


def _assert_sha256(value: object, name: str) -> str:
    text = _require_text(value, name)
    if _HEX_64.fullmatch(text) is None:
        raise EvidenceError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _assert_no_secret(value: object) -> None:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=True)
    if any(pattern.search(serialized) for pattern in _SECRET_PATTERNS):
        raise EvidenceError("release evidence contains high-confidence secret material")


def assert_safe_release_command(command: Sequence[str]) -> None:
    """Reject secret-bearing release command arguments before process launch."""

    for argument in command:
        if not isinstance(argument, str):
            raise EvidenceError("release command arguments must be strings")
        flag = argument.split("=", 1)[0].casefold()
        if flag in _SECRET_ARG_FLAGS:
            raise EvidenceError("release command contains secret material in argv")
        try:
            _assert_no_secret(argument)
        except EvidenceError as exc:
            raise EvidenceError("release command contains secret material in argv") from exc


def _parsed_ordered_version(version: str) -> tuple[tuple[int, ...], tuple[tuple[int, int, str], ...] | None]:
    if not isinstance(version, str) or not version:
        raise EvidenceError("release version must be a non-empty string")
    match = _ORDERED_VERSION.fullmatch(version)
    if match is None:
        raise EvidenceError(f"release version is not numerically comparable: {version!r}")
    release = tuple(int(component, 10) for component in match.group("release").split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    raw_prerelease = match.group("prerelease")
    if not raw_prerelease:
        return release, None
    if raw_prerelease[0] in ".-_":
        raw_prerelease = raw_prerelease[1:]
    if not raw_prerelease or re.fullmatch(r"[A-Za-z0-9._-]+", raw_prerelease) is None:
        raise EvidenceError(f"release prerelease is not comparable: {version!r}")
    raw_tokens = re.findall(r"[A-Za-z]+|[0-9]+", raw_prerelease.casefold())
    if not raw_tokens:
        raise EvidenceError(f"release prerelease is not comparable: {version!r}")
    prerelease: list[tuple[int, int, str]] = []
    for token in raw_tokens:
        if token.isdigit():
            prerelease.append((1, int(token, 10), ""))
        else:
            prerelease.append((0, _PRERELEASE_RANK.get(token, 25), token))
    return release, tuple(prerelease)


def compare_release_versions(left: str, right: str) -> int:
    """Compare numerical release versions with prereleases ordered below finals."""

    left_release, left_prerelease = _parsed_ordered_version(left)
    right_release, right_prerelease = _parsed_ordered_version(right)
    width = max(len(left_release), len(right_release))
    normalized_left = left_release + (0,) * (width - len(left_release))
    normalized_right = right_release + (0,) * (width - len(right_release))
    if normalized_left != normalized_right:
        return -1 if normalized_left < normalized_right else 1
    if left_prerelease is None and right_prerelease is None:
        return 0
    if left_prerelease is None:
        return 1
    if right_prerelease is None:
        return -1
    if left_prerelease == right_prerelease:
        return 0
    return -1 if left_prerelease < right_prerelease else 1


def require_strict_version_upgrade(previous_version: str, candidate_version: str) -> None:
    if compare_release_versions(previous_version, candidate_version) >= 0:
        raise EvidenceError(
            "candidate version must be strictly newer than the previously installed version"
        )


def create_candidate(
    *,
    root: Path,
    version: str,
    architecture: str,
    commit: str,
    pkg: Path,
    app: Path,
    update_archive: Path,
    bundle_identifier: str,
    team_identifier: str,
) -> dict[str, object]:
    release_root = root.resolve(strict=True)
    if _SAFE_VERSION.fullmatch(version) is None:
        raise EvidenceError("candidate version is invalid")
    if _SAFE_ARCHITECTURE.fullmatch(architecture) is None:
        raise EvidenceError("candidate architecture is invalid")
    if _HEX_40.fullmatch(commit) is None:
        raise EvidenceError("candidate commit must be a full lowercase Git SHA")
    if bundle_identifier != EXPECTED_BUNDLE_IDENTIFIER:
        raise EvidenceError("candidate bundle identifier is invalid")
    if _TEAM_ID.fullmatch(team_identifier) is None:
        raise EvidenceError("candidate team identifier is invalid")
    pkg_record = _path_record(pkg, root=release_root)
    app_record = _path_record(app, root=release_root)
    update_archive_record = _path_record(update_archive, root=release_root)
    if pkg_record["kind"] != "file" or not str(pkg_record["path"]).endswith(".pkg"):
        raise EvidenceError("candidate PKG must be a regular .pkg file")
    if app_record["kind"] != "tree" or not str(app_record["path"]).endswith(".app"):
        raise EvidenceError("candidate app must be an .app tree")
    expected_archive_name = f"SidePulse-{version}-{architecture}.zip"
    if (
        update_archive_record["kind"] != "file"
        or Path(str(update_archive_record["path"])).name != expected_archive_name
    ):
        raise EvidenceError(
            f"candidate update archive must be the exact supplemental ZIP: {expected_archive_name}"
        )
    identity = {
        "version": version,
        "architecture": architecture,
        "commit": commit,
        "pkg_sha256": pkg_record["sha256"],
        "app_sha256": app_record["sha256"],
        "update_archive_sha256": update_archive_record["sha256"],
        "bundle_identifier": bundle_identifier,
        "team_identifier": team_identifier,
    }
    return {
        "candidate_id": _canonical_digest(identity),
        "version": version,
        "architecture": architecture,
        "commit": commit,
        "pkg": pkg_record,
        "app": app_record,
        "update_archive": update_archive_record,
        "bundle_identifier": bundle_identifier,
        "team_identifier": team_identifier,
    }


def create_receipt(
    *,
    root: Path,
    candidate: Mapping[str, object],
    kind: str,
    tool: str,
    input_path: Path,
    output_text: str,
    details: Mapping[str, object] | None = None,
    observed_at: str | None = None,
) -> dict[str, object]:
    if kind not in REQUIRED_RECEIPT_KINDS:
        raise EvidenceError(f"unknown release receipt kind: {kind}")
    if not isinstance(tool, str) or not tool.strip() or len(tool) > 128:
        raise EvidenceError("receipt tool must be a short non-empty string")
    encoded_output = output_text.encode("utf-8", errors="replace")
    if len(encoded_output) > MAX_OUTPUT_BYTES:
        raise EvidenceError("receipt output exceeds the evidence size limit")
    candidate_id = _require_text(candidate.get("candidate_id"), "candidate_id")
    pkg = candidate.get("pkg")
    if not isinstance(pkg, Mapping):
        raise EvidenceError("candidate PKG record is missing")
    pkg_sha256 = _assert_sha256(pkg.get("sha256"), "candidate PKG SHA-256")
    update_archive = candidate.get("update_archive")
    if not isinstance(update_archive, Mapping):
        raise EvidenceError("candidate update archive record is missing")
    update_archive_sha256 = _assert_sha256(
        update_archive.get("sha256"),
        "candidate update archive SHA-256",
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": kind,
        "candidate_id": candidate_id,
        "candidate_pkg_sha256": pkg_sha256,
        "candidate_update_archive_sha256": update_archive_sha256,
        "result": "passed",
        "tool": tool.strip(),
        "observed_at": observed_at or _utc_now(),
        "input": _path_record(input_path, root=root),
        "output": {
            "sha256": hashlib.sha256(encoded_output).hexdigest(),
            "text": output_text,
        },
        "details": dict(details or {}),
    }
    _assert_no_secret(receipt)
    return receipt


def installer_signature_details(output_text: str) -> dict[str, str]:
    match = _INSTALLER_IDENTITY.search(output_text)
    if match is None:
        raise EvidenceError("PKG signature output has no Developer ID Installer identity")
    return {
        "installer_identity": match.group(1).strip(),
        "team_identifier": match.group(2),
    }


def notarization_details(
    *,
    response: Mapping[str, object],
    log: Mapping[str, object],
    submitted_sha256: str,
    pkg_name: str,
    log_sha256: str,
) -> dict[str, object]:
    status_value = response.get("status")
    submission_id = response.get("id")
    if status_value != "Accepted":
        raise EvidenceError("notarization response is not accepted")
    if not isinstance(submission_id, str) or _SUBMISSION_ID.fullmatch(submission_id) is None:
        raise EvidenceError("notarization response has no valid submission ID")
    _assert_sha256(submitted_sha256, "submitted PKG SHA-256")
    _assert_sha256(log_sha256, "notarization log SHA-256")
    if log.get("jobId") != submission_id:
        raise EvidenceError("notarization log belongs to another submission")
    if log.get("status") != "Accepted":
        raise EvidenceError("notarization log is not accepted")
    if log.get("sha256") != submitted_sha256:
        raise EvidenceError("notarization log belongs to another PKG digest")
    if log.get("archiveFilename") != pkg_name:
        raise EvidenceError("notarization log belongs to another PKG name")
    raw_issues = log.get("issues")
    if raw_issues is None:
        issues: list[object] = []
    elif isinstance(raw_issues, list):
        issues = raw_issues
    else:
        raise EvidenceError("notarization log issues must be an array")
    if any(isinstance(issue, Mapping) and str(issue.get("severity", "")).casefold() == "error" for issue in issues):
        raise EvidenceError("notarization log contains an error")
    return {
        "status": status_value,
        "submission_id": submission_id,
        "submitted_pkg_sha256": submitted_sha256,
        "log_sha256": log_sha256,
        "notary_created_at": response.get("createdDate"),
        "log_status_summary": log.get("statusSummary"),
        "log_status_code": log.get("statusCode"),
        "log_issue_count": len(issues),
    }


def app_notarization_details(
    *,
    response: Mapping[str, object],
    log: Mapping[str, object],
    submitted_sha256: str,
    log_sha256: str,
) -> dict[str, object]:
    status_value = response.get("status")
    submission_id = response.get("id")
    if status_value != "Accepted":
        raise EvidenceError("app notarization response is not accepted")
    if not isinstance(submission_id, str) or _SUBMISSION_ID.fullmatch(submission_id) is None:
        raise EvidenceError("app notarization response has no valid submission ID")
    _assert_sha256(submitted_sha256, "submitted app ZIP SHA-256")
    _assert_sha256(log_sha256, "app notarization log SHA-256")
    if log.get("jobId") != submission_id:
        raise EvidenceError("app notarization log belongs to another submission")
    if log.get("status") != "Accepted":
        raise EvidenceError("app notarization log is not accepted")
    if log.get("sha256") != submitted_sha256:
        raise EvidenceError("app notarization log belongs to another ZIP digest")
    archive_name = log.get("archiveFilename")
    if not isinstance(archive_name, str) or not archive_name.endswith(".zip"):
        raise EvidenceError("app notarization log has no submitted ZIP name")
    raw_issues = log.get("issues")
    if raw_issues is None:
        issues: list[object] = []
    elif isinstance(raw_issues, list):
        issues = raw_issues
    else:
        raise EvidenceError("app notarization log issues must be an array")
    if any(isinstance(issue, Mapping) and str(issue.get("severity", "")).casefold() == "error" for issue in issues):
        raise EvidenceError("app notarization log contains an error")
    return {
        "status": status_value,
        "submission_id": submission_id,
        "submitted_app_zip_sha256": submitted_sha256,
        "submitted_archive_name": archive_name,
        "log_sha256": log_sha256,
        "notary_created_at": response.get("createdDate"),
        "log_issue_count": len(issues),
    }


def _bounded_excerpt(output_text: str) -> tuple[str, dict[str, object]]:
    complete_bytes = output_text.encode("utf-8", errors="replace")
    if len(complete_bytes) <= MAX_OUTPUT_BYTES:
        return output_text, {}
    marker = b"\n[output truncated in manifest]\n"
    half = (MAX_OUTPUT_BYTES - len(marker)) // 2
    excerpt = (
        complete_bytes[:half].decode("utf-8", errors="replace")
        + marker.decode("ascii")
        + complete_bytes[-half:].decode("utf-8", errors="replace")
    )
    return excerpt, {
        "manifest_output_bytes": len(complete_bytes),
        "manifest_output_sha256": hashlib.sha256(complete_bytes).hexdigest(),
        "output_state": "bounded-excerpt",
    }


def _sanitized_command_output(output_text: str, *, root: Path) -> str:
    _assert_no_secret(output_text)
    sanitized = output_text.replace(str(root.resolve()), "$RELEASE_ROOT")
    home = str(Path.home().resolve())
    if home != str(root.resolve()):
        sanitized = sanitized.replace(home, "$USER_HOME")
    return sanitized


def _record_at_root(record: Mapping[str, object], *, root: Path) -> Path:
    relative = _require_text(record.get("path"), "evidence path")
    if Path(relative).is_absolute():
        raise EvidenceError("evidence path must be relative to the release root")
    candidate = (root / relative).resolve(strict=True)
    _relative_path(candidate, root=root)
    return candidate


def _validate_candidate(candidate: Mapping[str, object], *, root: Path) -> None:
    pkg = candidate.get("pkg")
    app = candidate.get("app")
    update_archive = candidate.get("update_archive")
    if not isinstance(pkg, Mapping) or not isinstance(app, Mapping) or not isinstance(update_archive, Mapping):
        raise EvidenceError("candidate PKG, app, and update archive records are required")
    pkg_path = _record_at_root(pkg, root=root)
    app_path = _record_at_root(app, root=root)
    update_archive_path = _record_at_root(update_archive, root=root)
    if digest_path(pkg_path) != pkg.get("sha256"):
        raise EvidenceError("candidate PKG no longer matches its recorded SHA-256")
    if digest_path(app_path) != app.get("sha256"):
        raise EvidenceError("candidate app no longer matches its recorded SHA-256")
    if digest_path(update_archive_path) != update_archive.get("sha256"):
        raise EvidenceError("candidate update archive no longer matches its recorded SHA-256")
    expected = create_candidate(
        root=root,
        version=_require_text(candidate.get("version"), "candidate version"),
        architecture=_require_text(candidate.get("architecture"), "candidate architecture"),
        commit=_require_text(candidate.get("commit"), "candidate commit"),
        pkg=pkg_path,
        app=app_path,
        update_archive=update_archive_path,
        bundle_identifier=_require_text(candidate.get("bundle_identifier"), "bundle identifier"),
        team_identifier=_require_text(candidate.get("team_identifier"), "team identifier"),
    )
    if expected != dict(candidate):
        raise EvidenceError("candidate identity is malformed or inconsistent")


def _receipt_input_sha(receipt: Mapping[str, object]) -> str:
    input_record = receipt.get("input")
    if not isinstance(input_record, Mapping):
        raise EvidenceError("receipt input record is missing")
    return _assert_sha256(input_record.get("sha256"), "receipt input SHA-256")


def _validate_details(
    kind: str,
    details: Mapping[str, object],
    *,
    candidate: Mapping[str, object],
    hardware_profile: str,
) -> None:
    app = candidate["app"]
    update_archive = candidate["update_archive"]
    assert isinstance(app, Mapping) and isinstance(update_archive, Mapping)
    if kind == "hardware-smoke":
        if details.get("hardware_profile") != hardware_profile:
            raise EvidenceError("hardware-smoke receipt has another hardware profile")
    elif kind == "pkg-signature":
        identity = _require_text(details.get("installer_identity"), "installer identity")
        team = _require_text(details.get("team_identifier"), "installer team identifier")
        if "Developer ID Installer:" not in identity:
            raise EvidenceError("PKG signing receipt has no Developer ID Installer identity")
        if team != candidate["team_identifier"]:
            raise EvidenceError("PKG and app signing team identifiers do not match")
    elif kind == "notarization":
        if details.get("status") != "Accepted":
            raise EvidenceError("notarization receipt is not accepted")
        submission_id = _require_text(details.get("submission_id"), "submission ID")
        if _SUBMISSION_ID.fullmatch(submission_id) is None:
            raise EvidenceError("notarization submission ID is invalid")
        _assert_sha256(details.get("submitted_pkg_sha256"), "submitted PKG SHA-256")
        _assert_sha256(details.get("log_sha256"), "notarization log SHA-256")
    elif kind == "stapling":
        _assert_sha256(details.get("notarized_pkg_sha256"), "notarized PKG SHA-256")
        if details.get("stapled_pkg_sha256") != candidate["pkg"]["sha256"]:
            raise EvidenceError("stapling receipt does not identify the final candidate PKG")
    elif kind == "package-contents":
        if details.get("packaged_app_sha256") != app["sha256"]:
            raise EvidenceError("packaged app does not match the candidate app")
        _assert_sha256(details.get("payload_sha256"), "package payload SHA-256")
    elif kind == "settings-preservation":
        if details.get("settings_state") != "preserved":
            raise EvidenceError("settings preservation receipt did not preserve settings")
        _assert_sha256(details.get("before_settings_sha256"), "pre-upgrade settings SHA-256")
        _assert_sha256(details.get("after_settings_sha256"), "post-upgrade settings SHA-256")
    elif kind == "installed-upgrade":
        if details.get("installed_app_sha256") != app["sha256"]:
            raise EvidenceError("upgraded app does not match the candidate app")
        previous_version = _require_text(details.get("previous_version"), "previous installed version")
        require_strict_version_upgrade(previous_version, str(candidate["version"]))
        _assert_sha256(details.get("previous_app_sha256"), "previous app SHA-256")
        _assert_sha256(
            details.get("previous_package_receipt_sha256"),
            "previous package receipt SHA-256",
        )
    elif kind == "uninstall":
        expected = {
            "app_state": "removed",
            "owned_cli_link_state": "removed-or-not-present",
            "package_receipt_state": "removed",
            "user_state": "preserved",
            "owned_integration_state": "removed",
        }
        if any(details.get(key) != value for key, value in expected.items()):
            raise EvidenceError("uninstall receipt did not verify the supported cleanup path")
    elif kind == "clean-install":
        if details.get("installed_app_sha256") != app["sha256"]:
            raise EvidenceError("clean-installed app does not match the candidate app")
    elif kind == "update-archive":
        if details.get("archive_sha256") != update_archive["sha256"]:
            raise EvidenceError("update archive receipt does not match the candidate archive")
        if details.get("archived_app_sha256") != app["sha256"]:
            raise EvidenceError("update archive receipt contains another app")
    elif kind == "sparkle-nested-signing":
        if details.get("team_identifier") != candidate["team_identifier"]:
            raise EvidenceError("nested Sparkle signing uses another signing team")
    elif kind == "app-notarization":
        if details.get("status") != "Accepted":
            raise EvidenceError("app notarization receipt is not accepted")
        submission_id = _require_text(details.get("submission_id"), "app notarization submission ID")
        if _SUBMISSION_ID.fullmatch(submission_id) is None:
            raise EvidenceError("app notarization submission ID is invalid")
        _assert_sha256(details.get("submitted_app_zip_sha256"), "submitted app ZIP SHA-256")
        _assert_sha256(details.get("log_sha256"), "app notarization log SHA-256")
    elif kind == "app-stapling":
        submission_id = _require_text(details.get("submission_id"), "app stapling submission ID")
        if _SUBMISSION_ID.fullmatch(submission_id) is None:
            raise EvidenceError("app stapling submission ID is invalid")
        if details.get("stapled_app_sha256") != app["sha256"]:
            raise EvidenceError("app stapling receipt does not identify the candidate app")
    elif kind == "signed-appcast":
        if details.get("archive_sha256") != update_archive["sha256"]:
            raise EvidenceError("signed appcast receipt references another update archive")


def _validate_receipts(
    receipts: Sequence[Mapping[str, object]],
    *,
    root: Path,
    candidate: Mapping[str, object],
    sbom_sha256: str,
    performance_sha256: str,
    appcast_sha256: str,
    hardware_profile: str,
) -> tuple[dict[str, object], ...]:
    if hardware_profile not in HARDWARE_PROFILES:
        raise EvidenceError(f"unknown release hardware profile: {hardware_profile}")
    by_kind: dict[str, Mapping[str, object]] = {}
    for receipt in receipts:
        kind = _require_text(receipt.get("kind"), "receipt kind")
        if kind not in REQUIRED_RECEIPT_KINDS:
            raise EvidenceError(f"unknown release receipt kind: {kind}")
        if kind in by_kind:
            raise EvidenceError(f"duplicate release receipt kind: {kind}")
        by_kind[kind] = receipt
    required_kinds = (
        SOFTWARE_RECEIPT_KINDS
        if hardware_profile == "software"
        else REQUIRED_RECEIPT_KINDS
    )
    missing = sorted(required_kinds - by_kind.keys())
    if missing:
        raise EvidenceError(f"missing required release receipt: {', '.join(missing)}")

    candidate_id = candidate["candidate_id"]
    pkg = candidate["pkg"]
    app = candidate["app"]
    update_archive = candidate["update_archive"]
    assert isinstance(pkg, Mapping) and isinstance(app, Mapping) and isinstance(update_archive, Mapping)
    for kind in sorted(by_kind):
        receipt = by_kind[kind]
        if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
            raise EvidenceError(f"{kind} receipt schema is unsupported")
        if receipt.get("candidate_id") != candidate_id:
            raise EvidenceError(f"{kind} receipt belongs to another candidate")
        if receipt.get("candidate_pkg_sha256") != pkg["sha256"]:
            raise EvidenceError(f"{kind} receipt has another candidate PKG")
        if receipt.get("candidate_update_archive_sha256") != update_archive["sha256"]:
            raise EvidenceError(f"{kind} receipt has another candidate update archive")
        if receipt.get("result") != "passed":
            raise EvidenceError(f"{kind} receipt did not pass")
        input_record = receipt.get("input")
        if not isinstance(input_record, Mapping):
            raise EvidenceError(f"{kind} receipt has no input record")
        input_path = _record_at_root(input_record, root=root)
        if digest_path(input_path) != _receipt_input_sha(receipt):
            raise EvidenceError(f"{kind} receipt input has changed")
        if kind in APP_INPUT_RECEIPTS:
            expected_input_sha = app["sha256"]
        elif kind in UPDATE_ARCHIVE_INPUT_RECEIPTS:
            expected_input_sha = update_archive["sha256"]
        elif kind in APPCAST_INPUT_RECEIPTS:
            expected_input_sha = appcast_sha256
        elif kind == "performance":
            expected_input_sha = performance_sha256
        elif kind == "sbom":
            expected_input_sha = sbom_sha256
        else:
            expected_input_sha = pkg["sha256"]
        if _receipt_input_sha(receipt) != expected_input_sha:
            raise EvidenceError(f"{kind} receipt has the wrong input")
        output = receipt.get("output")
        if not isinstance(output, Mapping):
            raise EvidenceError(f"{kind} receipt has no output record")
        output_text = _require_text(output.get("text"), f"{kind} output")
        output_sha = hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        if output.get("sha256") != output_sha:
            raise EvidenceError(f"{kind} receipt output digest is invalid")
        details = receipt.get("details")
        if not isinstance(details, Mapping):
            raise EvidenceError(f"{kind} receipt details are invalid")
        _validate_details(
            kind,
            details,
            candidate=candidate,
            hardware_profile=hardware_profile,
        )
        _assert_no_secret(receipt)
    notarization_details_value = by_kind["notarization"].get("details")
    stapling_details_value = by_kind["stapling"].get("details")
    assert isinstance(notarization_details_value, Mapping)
    assert isinstance(stapling_details_value, Mapping)
    if notarization_details_value.get("submitted_pkg_sha256") != stapling_details_value.get("notarized_pkg_sha256"):
        raise EvidenceError("notarization and stapling receipts describe different PKG inputs")
    app_notarization_details = by_kind["app-notarization"].get("details")
    app_stapling_details = by_kind["app-stapling"].get("details")
    assert isinstance(app_notarization_details, Mapping)
    assert isinstance(app_stapling_details, Mapping)
    if app_notarization_details.get("submission_id") != app_stapling_details.get("submission_id"):
        raise EvidenceError("app notarization and stapling receipts describe different submissions")
    return tuple(dict(by_kind[kind]) for kind in sorted(by_kind))


def artifact_record(path: Path, *, root: Path) -> dict[str, object]:
    record = _path_record(path, root=root)
    if record["kind"] != "file":
        raise EvidenceError(f"release artifact must be a regular file: {path}")
    return record


def _single_named_artifact(
    records: Sequence[Mapping[str, object]],
    *,
    name: str,
    label: str,
) -> Mapping[str, object]:
    matches = [record for record in records if Path(str(record.get("path", ""))).name == name]
    if not matches:
        raise EvidenceError(f"required {label} is absent from release artifacts")
    if len(matches) != 1:
        raise EvidenceError(f"duplicate {label} release artifacts are not allowed")
    return matches[0]


def _validate_channel_metadata(
    *,
    root: Path,
    record: Mapping[str, object],
    candidate: Mapping[str, object],
    archive_record: Mapping[str, object],
    appcast_record: Mapping[str, object],
) -> None:
    path = _record_at_root(record, root=root)
    channel = load_json_object(path, label="JR-Bar update channel metadata")
    if channel.get("document") != "jr-bar-update-channel":
        raise EvidenceError("update channel metadata document type is invalid")
    if channel.get("schema_version") != 1:
        raise EvidenceError("update channel metadata schema is unsupported")
    if channel.get("candidate_id") != candidate["candidate_id"]:
        raise EvidenceError("update channel metadata belongs to another candidate")
    if channel.get("channel") not in {"stable", "beta"}:
        raise EvidenceError("update channel metadata has an invalid channel")
    archive = channel.get("archive")
    appcast = channel.get("appcast")
    if not isinstance(archive, Mapping) or not isinstance(appcast, Mapping):
        raise EvidenceError("update channel metadata has no archive or appcast binding")
    if archive.get("name") != Path(str(archive_record["path"])).name:
        raise EvidenceError("update channel metadata archive name is wrong")
    if archive.get("bytes") != archive_record["bytes"]:
        raise EvidenceError("update channel metadata archive size is wrong")
    if archive.get("sha256") != archive_record["sha256"]:
        raise EvidenceError("update channel metadata archive SHA-256 is wrong")
    if appcast.get("name") != Path(str(appcast_record["path"])).name:
        raise EvidenceError("update channel metadata appcast name is wrong")
    if appcast.get("bytes") != appcast_record["bytes"]:
        raise EvidenceError("update channel metadata appcast size is wrong")
    if appcast.get("sha256") != appcast_record["sha256"]:
        raise EvidenceError("update channel metadata appcast SHA-256 is wrong")


def build_manifest(
    *,
    root: Path,
    candidate: Mapping[str, object],
    receipts: Sequence[Mapping[str, object]],
    sbom: Path,
    performance_evidence: Path,
    artifacts: Sequence[Path],
    hardware_profile: str = "software",
    generated_at: str | None = None,
) -> dict[str, object]:
    release_root = root.resolve(strict=True)
    _validate_candidate(candidate, root=release_root)
    sbom_record = artifact_record(sbom, root=release_root)
    performance_record = artifact_record(performance_evidence, root=release_root)
    artifact_records = [
        artifact_record(path, root=release_root)
        for path in sorted(artifacts, key=lambda item: item.resolve().as_posix())
    ]
    artifact_paths = [str(record["path"]) for record in artifact_records]
    if len(artifact_paths) != len(set(artifact_paths)):
        raise EvidenceError("duplicate release artifact paths are not allowed")
    pkg = candidate["pkg"]
    update_archive = candidate["update_archive"]
    assert isinstance(pkg, Mapping) and isinstance(update_archive, Mapping)
    if not any(record["sha256"] == pkg["sha256"] for record in artifact_records):
        raise EvidenceError("authoritative candidate PKG is absent from release artifacts")
    archive_name = Path(str(update_archive["path"])).name
    archive_record = _single_named_artifact(
        artifact_records,
        name=archive_name,
        label="update archive",
    )
    if dict(archive_record) != dict(update_archive):
        raise EvidenceError("release update archive does not match the candidate archive")
    appcast_record = _single_named_artifact(
        artifact_records,
        name="appcast.xml",
        label="appcast",
    )
    channel_record = _single_named_artifact(
        artifact_records,
        name="jr-bar-update-channel.json",
        label="channel metadata",
    )
    _validate_channel_metadata(
        root=release_root,
        record=channel_record,
        candidate=candidate,
        archive_record=archive_record,
        appcast_record=appcast_record,
    )
    validated_receipts = _validate_receipts(
        receipts,
        root=release_root,
        candidate=candidate,
        sbom_sha256=str(sbom_record["sha256"]),
        performance_sha256=str(performance_record["sha256"]),
        appcast_sha256=str(appcast_record["sha256"]),
        hardware_profile=hardware_profile,
    )
    document = {
        "document": DOCUMENT_NAME,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or _utc_now(),
        "hardware_profile": hardware_profile,
        "candidate": dict(candidate),
        "sbom": sbom_record,
        "performance_evidence": performance_record,
        "artifacts": artifact_records,
        "receipts": list(validated_receipts),
    }
    _assert_no_secret(document)
    return document


def load_json_object(path: Path, *, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be a JSON object")
    return value


def write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_receipt_command(args: argparse.Namespace) -> int:
    candidate = load_json_object(args.candidate, label="candidate")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise EvidenceError("receipt command is required")
    assert_safe_release_command(command)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout,
        check=False,
    )
    complete_output = (completed.stdout or "") + (completed.stderr or "")
    _assert_no_secret(complete_output)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode
    details: dict[str, object] = {}
    if args.kind == "pkg-signature":
        details.update(installer_signature_details(complete_output))
    elif args.kind == "hardware-smoke":
        try:
            require_index = command.index("--require")
            hardware_profile = command[require_index + 1]
        except (ValueError, IndexError) as exc:
            raise EvidenceError("hardware-smoke command has no hardware profile") from exc
        if hardware_profile not in HARDWARE_PROFILES - {"software"}:
            raise EvidenceError("hardware-smoke command has an invalid hardware profile")
        details["hardware_profile"] = hardware_profile
    candidate_app = candidate.get("app")
    candidate_archive = candidate.get("update_archive")
    if not isinstance(candidate_app, Mapping) or not isinstance(candidate_archive, Mapping):
        raise EvidenceError("candidate app or update archive record is missing")
    if args.kind == "update-archive":
        details.update(
            {
                "archive_sha256": candidate_archive.get("sha256"),
                "archived_app_sha256": candidate_app.get("sha256"),
            }
        )
    elif args.kind == "sparkle-nested-signing":
        details["team_identifier"] = candidate.get("team_identifier")
    elif args.kind == "signed-appcast":
        details["archive_sha256"] = candidate_archive.get("sha256")
    command_bytes = complete_output.encode("utf-8", errors="replace")
    details.update(
        {
            "command_output_bytes": len(command_bytes),
            "command_output_sha256": hashlib.sha256(command_bytes).hexdigest(),
        }
    )
    sanitized_output = _sanitized_command_output(complete_output, root=args.root)
    output_text, bounded_details = _bounded_excerpt(sanitized_output)
    details.update(bounded_details)
    receipt = create_receipt(
        root=args.root,
        candidate=candidate,
        kind=args.kind,
        tool=Path(command[0]).name,
        input_path=args.input,
        output_text=output_text or f"{args.kind} passed",
        details=details,
    )
    write_json(args.output, receipt)
    return 0


def _package_contents_receipt(args: argparse.Namespace) -> int:
    candidate = load_json_object(args.candidate, label="candidate")
    app_record = candidate.get("app")
    if not isinstance(app_record, Mapping):
        raise EvidenceError("candidate app record is missing")
    with tempfile.TemporaryDirectory(prefix="jr-bar-pkg-evidence-") as temporary:
        expanded = Path(temporary) / "expanded"
        completed = subprocess.run(
            ["/usr/sbin/pkgutil", "--expand-full", str(args.pkg), str(expanded)],
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        if completed.returncode != 0:
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode
        apps = tuple(expanded.rglob("SidePulse.app"))
        if len(apps) != 1:
            raise EvidenceError("expanded PKG must contain exactly one SidePulse.app")
        packaged_app_sha256 = sha256_tree(apps[0])
        payload_entries = []
        for item in _tree_entries(expanded):
            relative = "." if item == expanded else item.relative_to(expanded).as_posix()
            payload_entries.append(relative)
        payload_text = "\n".join(payload_entries) + "\n"
        output_text, bounded_details = _bounded_excerpt(payload_text)
        receipt = create_receipt(
            root=args.root,
            candidate=candidate,
            kind="package-contents",
            tool="pkgutil",
            input_path=args.pkg,
            output_text=output_text,
            details={
                "packaged_app_sha256": packaged_app_sha256,
                "payload_sha256": hashlib.sha256(payload_text.encode("utf-8")).hexdigest(),
                "payload_entry_count": len(payload_entries),
                **bounded_details,
            },
        )
        if packaged_app_sha256 != app_record.get("sha256"):
            raise EvidenceError("packaged app does not match the candidate app")
        write_json(args.output, receipt)
    return 0


def _notarization_receipt(args: argparse.Namespace) -> int:
    candidate = load_json_object(args.candidate, label="candidate")
    response = load_json_object(args.response, label="notarytool response")
    log = load_json_object(args.log, label="notarytool log")
    submitted_sha = args.submitted_sha256.read_text(encoding="utf-8").strip()
    details = notarization_details(
        response=response,
        log=log,
        submitted_sha256=submitted_sha,
        pkg_name=args.pkg.name,
        log_sha256=sha256_file(args.log),
    )
    submission_id = str(details["submission_id"])
    receipt = create_receipt(
        root=args.root,
        candidate=candidate,
        kind="notarization",
        tool="notarytool",
        input_path=args.pkg,
        output_text=f"submission {submission_id}: Accepted",
        details=details,
    )
    write_json(args.output, receipt)
    return 0


def _app_notarization_receipt(args: argparse.Namespace) -> int:
    candidate = load_json_object(args.candidate, label="candidate")
    response = load_json_object(args.response, label="app notarytool response")
    log = load_json_object(args.log, label="app notarytool log")
    submitted_sha = args.submitted_sha256.read_text(encoding="utf-8").strip()
    details = app_notarization_details(
        response=response,
        log=log,
        submitted_sha256=submitted_sha,
        log_sha256=sha256_file(args.log),
    )
    receipt = create_receipt(
        root=args.root,
        candidate=candidate,
        kind="app-notarization",
        tool="notarytool",
        input_path=args.app,
        output_text=f"app submission {details['submission_id']}: Accepted",
        details=details,
    )
    write_json(args.output, receipt)
    return 0


def _notary_submission_id(args: argparse.Namespace) -> int:
    response = load_json_object(args.response, label="notarytool response")
    submission_id = response.get("id")
    if not isinstance(submission_id, str) or _SUBMISSION_ID.fullmatch(submission_id) is None:
        raise EvidenceError("notarization response has no valid submission ID")
    if response.get("status") != "Accepted":
        raise EvidenceError("notarization response is not accepted")
    print(submission_id)
    return 0


def _stapling_receipt(args: argparse.Namespace) -> int:
    candidate = load_json_object(args.candidate, label="candidate")
    submitted_sha = args.submitted_sha256.read_text(encoding="utf-8").strip()
    _assert_sha256(submitted_sha, "notarized PKG SHA-256")
    completed = subprocess.run(
        ["/usr/bin/xcrun", "stapler", "validate", str(args.pkg)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode
    pkg_record = candidate.get("pkg")
    if not isinstance(pkg_record, Mapping):
        raise EvidenceError("candidate PKG record is missing")
    receipt = create_receipt(
        root=args.root,
        candidate=candidate,
        kind="stapling",
        tool="stapler",
        input_path=args.pkg,
        output_text=_sanitized_command_output(
            (completed.stdout or "") + (completed.stderr or ""),
            root=args.root,
        )
        or "stapled candidate validated",
        details={
            "notarized_pkg_sha256": submitted_sha,
            "stapled_pkg_sha256": pkg_record.get("sha256"),
        },
    )
    write_json(args.output, receipt)
    return 0


def _app_stapling_receipt(args: argparse.Namespace) -> int:
    candidate = load_json_object(args.candidate, label="candidate")
    response = load_json_object(args.response, label="app notarytool response")
    submission_id = response.get("id")
    if response.get("status") != "Accepted":
        raise EvidenceError("app notarization response is not accepted")
    if not isinstance(submission_id, str) or _SUBMISSION_ID.fullmatch(submission_id) is None:
        raise EvidenceError("app notarization response has no valid submission ID")
    completed = subprocess.run(
        ["/usr/bin/xcrun", "stapler", "validate", str(args.app)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=args.timeout,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        return completed.returncode
    app = candidate.get("app")
    if not isinstance(app, Mapping):
        raise EvidenceError("candidate app record is missing")
    receipt = create_receipt(
        root=args.root,
        candidate=candidate,
        kind="app-stapling",
        tool="stapler",
        input_path=args.app,
        output_text=_sanitized_command_output(
            (completed.stdout or "") + (completed.stderr or ""),
            root=args.root,
        )
        or "stapled candidate app validated",
        details={
            "submission_id": submission_id,
            "stapled_app_sha256": app.get("sha256"),
        },
    )
    write_json(args.output, receipt)
    return 0


def _candidate_command(args: argparse.Namespace) -> int:
    candidate = create_candidate(
        root=args.root,
        version=args.version,
        architecture=args.architecture,
        commit=args.commit,
        pkg=args.pkg,
        app=args.app,
        update_archive=args.update_archive,
        bundle_identifier=args.bundle_identifier,
        team_identifier=args.team_identifier,
    )
    write_json(args.output, candidate)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    candidate = subparsers.add_parser("candidate")
    candidate.add_argument("--root", type=Path, required=True)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--version", required=True)
    candidate.add_argument("--architecture", required=True)
    candidate.add_argument("--commit", required=True)
    candidate.add_argument("--pkg", type=Path, required=True)
    candidate.add_argument("--app", type=Path, required=True)
    candidate.add_argument("--update-archive", type=Path, required=True)
    candidate.add_argument("--bundle-identifier", default=EXPECTED_BUNDLE_IDENTIFIER)
    candidate.add_argument("--team-identifier", required=True)
    candidate.set_defaults(handler=_candidate_command)

    run = subparsers.add_parser("run-receipt")
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--candidate", type=Path, required=True)
    run.add_argument("--kind", choices=sorted(REQUIRED_RECEIPT_KINDS), required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--timeout", type=int, default=1800)
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=_run_receipt_command)

    contents = subparsers.add_parser("package-contents-receipt")
    contents.add_argument("--root", type=Path, required=True)
    contents.add_argument("--candidate", type=Path, required=True)
    contents.add_argument("--pkg", type=Path, required=True)
    contents.add_argument("--output", type=Path, required=True)
    contents.add_argument("--timeout", type=int, default=300)
    contents.set_defaults(handler=_package_contents_receipt)

    notary = subparsers.add_parser("notarization-receipt")
    notary.add_argument("--root", type=Path, required=True)
    notary.add_argument("--candidate", type=Path, required=True)
    notary.add_argument("--pkg", type=Path, required=True)
    notary.add_argument("--response", type=Path, required=True)
    notary.add_argument("--log", type=Path, required=True)
    notary.add_argument("--submitted-sha256", type=Path, required=True)
    notary.add_argument("--output", type=Path, required=True)
    notary.set_defaults(handler=_notarization_receipt)

    app_notary = subparsers.add_parser("app-notarization-receipt")
    app_notary.add_argument("--root", type=Path, required=True)
    app_notary.add_argument("--candidate", type=Path, required=True)
    app_notary.add_argument("--app", type=Path, required=True)
    app_notary.add_argument("--response", type=Path, required=True)
    app_notary.add_argument("--log", type=Path, required=True)
    app_notary.add_argument("--submitted-sha256", type=Path, required=True)
    app_notary.add_argument("--output", type=Path, required=True)
    app_notary.set_defaults(handler=_app_notarization_receipt)

    notary_id = subparsers.add_parser("notary-submission-id")
    notary_id.add_argument("--response", type=Path, required=True)
    notary_id.set_defaults(handler=_notary_submission_id)

    stapling = subparsers.add_parser("stapling-receipt")
    stapling.add_argument("--root", type=Path, required=True)
    stapling.add_argument("--candidate", type=Path, required=True)
    stapling.add_argument("--pkg", type=Path, required=True)
    stapling.add_argument("--submitted-sha256", type=Path, required=True)
    stapling.add_argument("--output", type=Path, required=True)
    stapling.add_argument("--timeout", type=int, default=300)
    stapling.set_defaults(handler=_stapling_receipt)

    app_stapling = subparsers.add_parser("app-stapling-receipt")
    app_stapling.add_argument("--root", type=Path, required=True)
    app_stapling.add_argument("--candidate", type=Path, required=True)
    app_stapling.add_argument("--app", type=Path, required=True)
    app_stapling.add_argument("--response", type=Path, required=True)
    app_stapling.add_argument("--output", type=Path, required=True)
    app_stapling.add_argument("--timeout", type=int, default=300)
    app_stapling.set_defaults(handler=_app_stapling_receipt)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        EvidenceError,
        FileNotFoundError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"release evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
