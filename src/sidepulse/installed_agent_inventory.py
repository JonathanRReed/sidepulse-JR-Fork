"""Bounded, content-free host inventory for installed coding-agent surfaces.

This is deliberately a read-only lstat boundary.  It never enumerates a
directory, opens a marker, follows a link, reads configuration, executes a
candidate, or gives inventory observations lifecycle authority.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .installed_agents import (
    InstalledSurfaceEvidence,
    InstalledSurfaceKey,
    InstalledSurfaceReduction,
    InstalledSurfaceRegistration,
    SurfaceDetectorKind,
    installed_surface_registrations,
    reduce_installed_surface_evidence,
)
from .runtime_scheduler import RuntimeWorkCommand, RuntimeWorkerDomain

MAX_INVENTORY_CANDIDATES: Final = 64
MAX_INVENTORY_PATH_COMPONENTS: Final = 12
MAX_INVENTORY_PATH_COMPONENT_BYTES: Final = 96
MAX_INVENTORY_LINK_BYTES: Final = 1024
_SAFE_ROOT_ID: Final = frozenset({"home", "applications", "vscode", "homebrew", "local_bin"})


class InstalledAgentInventoryError(ValueError):
    """An inventory declaration or host observation was refused safely."""


class InventoryMarkerKind(str, Enum):
    EXECUTABLE_LINK_OR_FILE = "executable_link_or_file"
    REGULAR_FILE = "regular_file"
    DIRECTORY = "directory"


def _is_safe_component(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value.encode("utf-8")) <= MAX_INVENTORY_PATH_COMPONENT_BYTES
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
        and value.isprintable()
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (int(info.st_dev), int(info.st_ino))


@dataclass(frozen=True, slots=True)
class InventoryRoot:
    """One caller-supplied, trusted root.  Paths never leave this frame."""

    root_id: str
    path: Path
    owner_uids: frozenset[int]
    trusted_system_root: bool = False

    def __post_init__(self) -> None:
        if not (
            type(self.root_id) is str
            and self.root_id in _SAFE_ROOT_ID
            and isinstance(self.path, Path)
            and self.path.is_absolute()
            and type(self.owner_uids) is frozenset
            and bool(self.owner_uids)
            and all(type(uid) is int and uid >= 0 for uid in self.owner_uids)
            and type(self.trusted_system_root) is bool
            and (
                not self.trusted_system_root
                or (
                    (self.root_id, self.path)
                    in {
                        ("applications", Path("/Applications")),
                        ("homebrew", Path("/opt/homebrew")),
                        ("local_bin", Path("/usr/local")),
                    }
                    and 0 in self.owner_uids
                )
            )
        ):
            raise InstalledAgentInventoryError("invalid inventory system root")


@dataclass(frozen=True, slots=True)
class InventoryCandidate:
    """A reviewed literal marker, never a search pattern or executable probe."""

    key: InstalledSurfaceKey
    detector_kind: SurfaceDetectorKind
    detector_id: str
    root_id: str
    relative_path: tuple[str, ...]
    marker_kind: InventoryMarkerKind
    configured: bool
    alternate_locations: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not (
            type(self.key) is InstalledSurfaceKey
            and type(self.detector_kind) is SurfaceDetectorKind
            and type(self.detector_id) is str
            and self.detector_id
            and type(self.root_id) is str
            and self.root_id in _SAFE_ROOT_ID
            and type(self.relative_path) is tuple
            and 1 <= len(self.relative_path) <= MAX_INVENTORY_PATH_COMPONENTS
            and all(_is_safe_component(component) for component in self.relative_path)
            and type(self.marker_kind) is InventoryMarkerKind
            and type(self.configured) is bool
            and type(self.alternate_locations) is tuple
            and len(self.alternate_locations) <= MAX_INVENTORY_CANDIDATES - 1
            and all(
                type(location) is tuple
                and len(location) == 2
                and type(location[0]) is str
                and location[0] in _SAFE_ROOT_ID
                and type(location[1]) is tuple
                and 1 <= len(location[1]) <= MAX_INVENTORY_PATH_COMPONENTS
                and all(_is_safe_component(component) for component in location[1])
                for location in self.alternate_locations
            )
            and len(
                {(self.root_id, self.relative_path), *self.alternate_locations}
            )
            == len(self.alternate_locations) + 1
        ):
            raise InstalledAgentInventoryError("invalid inventory candidate")


@dataclass(frozen=True, slots=True)
class InstalledAgentInventoryResult:
    """Immutable inventory projection.  It contains no path or lifecycle state."""

    reduction: InstalledSurfaceReduction
    candidate_count: int
    rejected_count: int

    def __post_init__(self) -> None:
        if not (
            type(self.reduction) is InstalledSurfaceReduction
            and type(self.candidate_count) is int
            and 0 <= self.candidate_count <= MAX_INVENTORY_CANDIDATES
            and type(self.rejected_count) is int
            and 0 <= self.rejected_count <= self.candidate_count
        ):
            raise InstalledAgentInventoryError("invalid inventory result")


def _candidate(
    provider_id: str,
    surface_id: str,
    detector_kind: SurfaceDetectorKind,
    detector_id: str,
    root_id: str,
    relative_path: tuple[str, ...],
    marker_kind: InventoryMarkerKind,
    *,
    configured: bool = False,
    alternate_locations: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> InventoryCandidate:
    return InventoryCandidate(
        key=InstalledSurfaceKey(provider_id, surface_id),
        detector_kind=detector_kind,
        detector_id=detector_id,
        root_id=root_id,
        relative_path=relative_path,
        marker_kind=marker_kind,
        configured=configured,
        alternate_locations=alternate_locations,
    )


_INVENTORY_CANDIDATES: Final = (
    _candidate("codex", "cli", SurfaceDetectorKind.PATH_MARKER, "codex-cli", "home", (".local", "bin", "codex"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "codex")), ("local_bin", ("bin", "codex")))),
    _candidate("claude", "cli", SurfaceDetectorKind.PATH_MARKER, "claude-cli", "home", (".local", "bin", "claude"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "claude")), ("local_bin", ("bin", "claude")))),
    _candidate("devin", "cli", SurfaceDetectorKind.PATH_MARKER, "devin-cli", "home", (".local", "bin", "devin"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "devin")), ("local_bin", ("bin", "devin")))),
    _candidate("grok", "cli", SurfaceDetectorKind.PATH_MARKER, "grok-cli", "home", (".local", "bin", "grok"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "grok")), ("local_bin", ("bin", "grok")))),
    _candidate("cursor", "ide", SurfaceDetectorKind.BUNDLE_IDENTIFIER, "cursor", "applications", ("Cursor.app",), InventoryMarkerKind.DIRECTORY),
    _candidate("hermes", "cli", SurfaceDetectorKind.PATH_MARKER, "hermes-cli", "home", (".local", "bin", "hermes"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "hermes")), ("local_bin", ("bin", "hermes")))),
    _candidate("openclaw", "cli", SurfaceDetectorKind.CONFIG_MARKER, "openclaw-config", "home", (".config", "openclaw"), InventoryMarkerKind.DIRECTORY, configured=True),
    _candidate("opencode", "cli", SurfaceDetectorKind.PATH_MARKER, "opencode-cli", "home", (".local", "bin", "opencode"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "opencode")), ("local_bin", ("bin", "opencode")))),
    _candidate("opencode", "sidepulse-plugin", SurfaceDetectorKind.CONFIG_MARKER, "opencode-plugin", "home", (".config", "opencode", "plugins", "sidepulse.js"), InventoryMarkerKind.REGULAR_FILE, configured=True),
    _candidate("opencode", "desktop", SurfaceDetectorKind.BUNDLE_IDENTIFIER, "opencode", "applications", ("OpenCode.app",), InventoryMarkerKind.DIRECTORY),
    _candidate(
        "google",
        "antigravity-cli",
        SurfaceDetectorKind.PATH_MARKER,
        "antigravity-cli",
        "home",
        (".local", "bin", "agy"),
        InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE,
        alternate_locations=(
            ("homebrew", ("bin", "agy")),
            ("local_bin", ("bin", "agy")),
        ),
    ),
    _candidate("google", "antigravity-desktop", SurfaceDetectorKind.BUNDLE_IDENTIFIER, "google-antigravity", "applications", ("Antigravity.app",), InventoryMarkerKind.DIRECTORY),
    _candidate("google", "antigravity-ide", SurfaceDetectorKind.EXTENSION_IDENTIFIER, "google-antigravity", "vscode", ("google.antigravity",), InventoryMarkerKind.DIRECTORY),
    _candidate("google", "gemini-cli", SurfaceDetectorKind.PATH_MARKER, "gemini-cli", "home", (".local", "bin", "gemini"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "gemini")), ("local_bin", ("bin", "gemini")))),
    _candidate("google", "gemini-desktop", SurfaceDetectorKind.BUNDLE_IDENTIFIER, "gemini-desktop", "applications", ("Gemini.app",), InventoryMarkerKind.DIRECTORY),
    _candidate("google", "gemini-code-assist-vscode", SurfaceDetectorKind.EXTENSION_IDENTIFIER, "gemini-code-assist-vscode", "vscode", ("google.geminicodeassist",), InventoryMarkerKind.DIRECTORY),
    _candidate("github", "copilot-ide", SurfaceDetectorKind.EXTENSION_IDENTIFIER, "github-copilot", "vscode", ("github.copilot",), InventoryMarkerKind.DIRECTORY),
    _candidate("kiro", "cli", SurfaceDetectorKind.PATH_MARKER, "kiro-cli", "home", (".local", "bin", "kiro-cli"), InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE, alternate_locations=(("homebrew", ("bin", "kiro-cli")), ("local_bin", ("bin", "kiro-cli")))),
    _candidate("kiro", "sidepulse-agent", SurfaceDetectorKind.CONFIG_MARKER, "kiro-hooks-v1", "home", (".kiro", "agents", "sidepulse.json"), InventoryMarkerKind.REGULAR_FILE, configured=True),
)


def default_inventory_candidates() -> tuple[InventoryCandidate, ...]:
    """Return the reviewed literal candidates without looking at the host."""
    _validate_candidates(_INVENTORY_CANDIDATES, installed_surface_registrations())
    return _INVENTORY_CANDIDATES


def default_inventory_roots(*, home: Path | None = None) -> tuple[InventoryRoot, ...]:
    """Return literal roots only.  This function does not read the host."""
    user_home = Path.home() if home is None else home
    owner = frozenset({os.getuid()})
    system_or_owner = frozenset({0, os.getuid()})
    return (
        InventoryRoot("home", user_home, owner),
        InventoryRoot(
            "applications",
            Path("/Applications"),
            system_or_owner,
            trusted_system_root=True,
        ),
        InventoryRoot("vscode", user_home / ".vscode" / "extensions", owner),
        InventoryRoot(
            "homebrew",
            Path("/opt/homebrew"),
            system_or_owner,
            trusted_system_root=True,
        ),
        InventoryRoot(
            "local_bin",
            Path("/usr/local"),
            system_or_owner,
            trusted_system_root=True,
        ),
    )


def collect_installed_agent_inventory(
    roots: tuple[InventoryRoot, ...],
) -> InstalledAgentInventoryResult:
    """Collect only reviewed markers under supplied roots, and fail closed."""
    registrations = installed_surface_registrations()
    candidates = default_inventory_candidates()
    root_map = _validate_roots(roots)
    evidence: list[InstalledSurfaceEvidence] = []
    rejected_count = 0
    for candidate in candidates:
        detected = _candidate_is_present_in_any_location(root_map, candidate)
        if not detected:
            rejected_count += 1
        evidence.append(
            InstalledSurfaceEvidence(
                key=candidate.key,
                detector_kind=candidate.detector_kind,
                detector_id=candidate.detector_id,
                detected=detected,
                configured=detected and candidate.configured,
                version=None,
            )
        )
    return InstalledAgentInventoryResult(
        reduction=reduce_installed_surface_evidence(tuple(evidence), registrations),
        candidate_count=len(candidates),
        rejected_count=rejected_count,
    )


def execute_inventory_command(command: RuntimeWorkCommand) -> InstalledAgentInventoryResult:
    """Run exactly the inventory key on the controller-owned OS-poll worker."""
    if (
        type(command) is not RuntimeWorkCommand
        or command.domain is not RuntimeWorkerDomain.OS_POLL
        or command.key != "installed-agent-inventory"
        or type(command.payload) is not tuple
    ):
        raise ValueError("invalid installed-agent inventory command")
    return collect_installed_agent_inventory(command.payload)


def _validate_roots(roots: tuple[InventoryRoot, ...]) -> dict[str, InventoryRoot]:
    if type(roots) is not tuple or not all(type(root) is InventoryRoot for root in roots):
        raise InstalledAgentInventoryError("invalid inventory roots")
    if len(roots) > len(_SAFE_ROOT_ID):
        raise InstalledAgentInventoryError("invalid inventory roots")
    mapped = {root.root_id: root for root in roots}
    if len(mapped) != len(roots):
        raise InstalledAgentInventoryError("duplicate inventory root")
    return mapped


def _validate_candidates(
    candidates: object,
    registrations: tuple[InstalledSurfaceRegistration, ...],
) -> None:
    if (
        type(candidates) is not tuple
        or not candidates
        or len(candidates) > MAX_INVENTORY_CANDIDATES
        or not all(type(candidate) is InventoryCandidate for candidate in candidates)
    ):
        raise InstalledAgentInventoryError("invalid inventory candidate batch")
    registration_by_key = {registration.key: registration for registration in registrations}
    keys = tuple(candidate.key for candidate in candidates)
    if len(keys) != len(set(keys)):
        raise InstalledAgentInventoryError("duplicate inventory candidate")
    if set(keys) != set(registration_by_key):
        raise InstalledAgentInventoryError("unreviewed inventory candidate")
    location_count = sum(1 + len(candidate.alternate_locations) for candidate in candidates)
    if location_count > MAX_INVENTORY_CANDIDATES:
        raise InstalledAgentInventoryError("invalid inventory candidate batch")
    locations = tuple(
        location
        for candidate in candidates
        for location in ((candidate.root_id, candidate.relative_path), *candidate.alternate_locations)
    )
    if len(locations) != len(set(locations)):
        raise InstalledAgentInventoryError("duplicate inventory location")
    for candidate in candidates:
        registration = registration_by_key[candidate.key]
        if (
            candidate.detector_kind is not registration.detector_kind
            or candidate.detector_id != registration.detector_id
            or (candidate.configured and candidate.detector_kind is not SurfaceDetectorKind.CONFIG_MARKER)
        ):
            raise InstalledAgentInventoryError("unreviewed inventory candidate")


def _candidate_is_present(root: InventoryRoot, candidate: InventoryCandidate) -> bool:
    return _location_is_present(root, candidate.relative_path, candidate.marker_kind)


def _candidate_is_present_in_any_location(
    roots: dict[str, InventoryRoot],
    candidate: InventoryCandidate,
) -> bool:
    locations = ((candidate.root_id, candidate.relative_path), *candidate.alternate_locations)
    return any(
        root is not None and _location_is_present(root, relative_path, candidate.marker_kind)
        for root_id, relative_path in locations
        for root in (roots.get(root_id),)
    )


def _location_is_present(
    root: InventoryRoot,
    relative_path: tuple[str, ...],
    marker_kind: InventoryMarkerKind,
) -> bool:
    root_info = _lstat(root.path)
    if not _safe_directory(
        root_info,
        root.owner_uids,
        allow_group_writable=root.trusted_system_root,
    ):
        return False
    checked_directories: list[tuple[Path, tuple[int, int], bool]] = [
        (root.path, _identity(root_info), root.trusted_system_root)
    ]
    current = root.path
    for component in relative_path[:-1]:
        current = current / component
        info = _lstat(current)
        if not _safe_directory(
            info,
            root.owner_uids,
            allow_group_writable=root.trusted_system_root,
        ):
            return False
        checked_directories.append(
            (current, _identity(info), root.trusted_system_root)
        )
    marker = current / relative_path[-1]
    marker_info = _lstat(marker)
    if not _safe_marker(marker_info, marker_kind, root.owner_uids):
        return False
    return all(
        _directory_still_matches(
            path,
            identity,
            root.owner_uids,
            allow_group_writable=allow_group_writable,
        )
        for path, identity, allow_group_writable in checked_directories
    )


def _safe_directory(
    info: os.stat_result | None,
    owner_uids: frozenset[int],
    *,
    allow_group_writable: bool = False,
) -> bool:
    mode = stat.S_IMODE(info.st_mode) if info is not None else 0
    writable_bits = mode & 0o022
    permitted_writable_bits = {0, 0o020} if allow_group_writable else {0}
    return bool(
        info is not None
        and not stat.S_ISLNK(info.st_mode)
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid in owner_uids
        and writable_bits in permitted_writable_bits
        and not (mode & 0o7000)
    )


def _safe_marker(
    info: os.stat_result | None,
    kind: InventoryMarkerKind,
    owner_uids: frozenset[int],
) -> bool:
    if info is None or info.st_uid not in owner_uids:
        return False
    if kind is InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE and stat.S_ISLNK(info.st_mode):
        return 0 < info.st_size <= MAX_INVENTORY_LINK_BYTES
    if stat.S_ISLNK(info.st_mode):
        return False
    if kind is InventoryMarkerKind.DIRECTORY:
        return stat.S_ISDIR(info.st_mode) and not (stat.S_IMODE(info.st_mode) & 0o022)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022:
        return False
    return kind is not InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE or bool(info.st_mode & stat.S_IXUSR)


def _directory_still_matches(
    path: Path,
    expected_identity: tuple[int, int],
    owner_uids: frozenset[int],
    *,
    allow_group_writable: bool = False,
) -> bool:
    info = _lstat(path)
    return (
        _safe_directory(info, owner_uids, allow_group_writable=allow_group_writable)
        and _identity(info) == expected_identity
    )
