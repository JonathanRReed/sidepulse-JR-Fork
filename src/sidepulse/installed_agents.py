"""Pure, bounded inventory declarations for installed coding-agent surfaces.

This module validates caller-supplied discovery evidence only.  It never reads
the host, invokes a process, imports provider code, or projects lifecycle,
capacity, notification, or presentation state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .product_identity import PRODUCT_DISPLAY_NAME

MAX_IDENTIFIER_LENGTH: Final = 64
MAX_LABEL_LENGTH: Final = 128
MAX_CAPABILITIES_PER_SURFACE: Final = 16
_SLUG_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_VERSION_TOKEN: Final = re.compile(r"v?[0-9][A-Za-z0-9._-]{0,63}\Z")
_PRIVATE_IDENTIFIER_COMPONENT: Final = re.compile(
    r"(?:^|[._~:-])"
    r"(?:api[_-]?key|authorization|bearer|cookie|credential|password|passwd|"
    r"private[_-]?key)"
    r"(?:$|[._~:-])",
    re.IGNORECASE,
)


class InstalledSurfaceValidationError(ValueError):
    """An installed-surface declaration or observation failed closed."""


class InstalledSurfaceKind(str, Enum):
    CLI = "cli"
    DESKTOP = "desktop"
    IDE_EXTENSION = "ide_extension"
    LOCAL_HARNESS = "local_harness"


class SurfaceSupportLevel(str, Enum):
    FULL = "full"
    LIFECYCLE = "lifecycle"
    CAPACITY = "capacity"
    INVENTORY = "inventory"
    UNSUPPORTED = "unsupported"


class SurfacePresence(str, Enum):
    ABSENT = "absent"
    INSTALLED = "installed"
    CONFIGURED = "configured"
    MIGRATION_REQUIRED = "migration_required"
    UNSUPPORTED_VERSION = "unsupported_version"


class SurfaceDetectorKind(str, Enum):
    """Read-only detector classes that the inventory boundary may accept."""

    PATH_MARKER = "path_marker"
    BUNDLE_IDENTIFIER = "bundle_identifier"
    EXTENSION_IDENTIFIER = "extension_identifier"
    CONFIG_MARKER = "config_marker"


def _valid_slug(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
        and _SLUG_IDENTIFIER.fullmatch(value) is not None
        and _PRIVATE_IDENTIFIER_COMPONENT.search(value) is None
    )


def _valid_label(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= MAX_LABEL_LENGTH and value == value.strip() and value.isprintable()


def _valid_version(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
        and _VERSION_TOKEN.fullmatch(value) is not None
        and _PRIVATE_IDENTIFIER_COMPONENT.search(value) is None
    )


@dataclass(frozen=True, order=True, slots=True)
class InstalledSurfaceKey:
    provider_id: str
    surface_id: str

    def __post_init__(self) -> None:
        if not (_valid_slug(self.provider_id) and _valid_slug(self.surface_id)):
            raise InstalledSurfaceValidationError("invalid installed surface key")


@dataclass(frozen=True, slots=True)
class InstalledSurfaceRegistration:
    provider_id: str
    surface_id: str
    label: str
    kind: InstalledSurfaceKind
    support: SurfaceSupportLevel
    capability_ids: tuple[str, ...]
    hook_profile_id: str | None
    capacity_profile_id: str | None
    detector_kind: SurfaceDetectorKind
    detector_id: str

    def __post_init__(self) -> None:
        valid_profiles = all(
            profile is None or _valid_slug(profile) for profile in (self.hook_profile_id, self.capacity_profile_id)
        )
        valid_capabilities = (
            type(self.capability_ids) is tuple
            and len(self.capability_ids) <= MAX_CAPABILITIES_PER_SURFACE
            and all(_valid_slug(capability) for capability in self.capability_ids)
            and len(self.capability_ids) == len(set(self.capability_ids))
        )
        if not (
            _valid_slug(self.provider_id)
            and _valid_slug(self.surface_id)
            and _valid_label(self.label)
            and type(self.kind) is InstalledSurfaceKind
            and type(self.support) is SurfaceSupportLevel
            and valid_capabilities
            and valid_profiles
            and type(self.detector_kind) is SurfaceDetectorKind
            and _valid_slug(self.detector_id)
        ):
            raise InstalledSurfaceValidationError("invalid installed surface registration")

    @property
    def key(self) -> InstalledSurfaceKey:
        return InstalledSurfaceKey(self.provider_id, self.surface_id)


def validate_installed_surface_registrations(
    registrations: tuple[InstalledSurfaceRegistration, ...],
) -> tuple[InstalledSurfaceRegistration, ...]:
    """Validate one deterministic static registry without inspecting the host."""
    if type(registrations) is not tuple or not registrations:
        raise InstalledSurfaceValidationError("invalid installed surface registry")
    if not all(type(row) is InstalledSurfaceRegistration for row in registrations):
        raise InstalledSurfaceValidationError("invalid installed surface registry")
    keys = tuple(row.key for row in registrations)
    if len(keys) != len(set(keys)):
        raise InstalledSurfaceValidationError("duplicate installed surface key")
    return registrations


def _registration(
    provider_id: str,
    surface_id: str,
    label: str,
    kind: InstalledSurfaceKind,
    support: SurfaceSupportLevel,
    capability_ids: tuple[str, ...],
    hook_profile_id: str | None,
    detector_kind: SurfaceDetectorKind,
    detector_id: str,
) -> InstalledSurfaceRegistration:
    return InstalledSurfaceRegistration(
        provider_id=provider_id,
        surface_id=surface_id,
        label=label,
        kind=kind,
        support=support,
        capability_ids=capability_ids,
        hook_profile_id=hook_profile_id,
        capacity_profile_id=None,
        detector_kind=detector_kind,
        detector_id=detector_id,
    )


_INSTALLED_SURFACE_REGISTRATIONS: Final = validate_installed_surface_registrations(
    (
        _registration(
            "codex",
            "cli",
            "Codex CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "codex-hooks-v1",
            SurfaceDetectorKind.PATH_MARKER,
            "codex-cli",
        ),
        _registration(
            "claude",
            "cli",
            "Claude CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "claude-hooks-v1",
            SurfaceDetectorKind.PATH_MARKER,
            "claude-cli",
        ),
        _registration(
            "devin",
            "cli",
            "Devin CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "devin-hooks-v1",
            SurfaceDetectorKind.PATH_MARKER,
            "devin-cli",
        ),
        _registration(
            "grok",
            "cli",
            "Grok CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "grok-hooks-v1",
            SurfaceDetectorKind.PATH_MARKER,
            "grok-cli",
        ),
        _registration(
            "cursor",
            "ide",
            "Cursor",
            InstalledSurfaceKind.IDE_EXTENSION,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "cursor-hooks-v1",
            SurfaceDetectorKind.BUNDLE_IDENTIFIER,
            "cursor",
        ),
        _registration(
            "hermes",
            "cli",
            "Hermes CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "hermes-hooks-v1",
            SurfaceDetectorKind.PATH_MARKER,
            "hermes-cli",
        ),
        _registration(
            "openclaw",
            "cli",
            "OpenClaw CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "openclaw-hooks-v1",
            SurfaceDetectorKind.CONFIG_MARKER,
            "openclaw-config",
        ),
        _registration(
            "opencode",
            "cli",
            "OpenCode CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.PATH_MARKER,
            "opencode-cli",
        ),
        _registration(
            "opencode",
            "sidepulse-plugin",
            f"OpenCode {PRODUCT_DISPLAY_NAME} integration",
            InstalledSurfaceKind.LOCAL_HARNESS,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events", "actionable_requests"),
            "opencode-hooks-v1",
            SurfaceDetectorKind.CONFIG_MARKER,
            "opencode-plugin",
        ),
        _registration(
            "opencode",
            "desktop",
            "OpenCode Desktop",
            InstalledSurfaceKind.DESKTOP,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.BUNDLE_IDENTIFIER,
            "opencode",
        ),
        _registration(
            "google",
            "antigravity-cli",
            "Google Antigravity CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.PATH_MARKER,
            "antigravity-cli",
        ),
        _registration(
            "google",
            "antigravity-desktop",
            "Google Antigravity Desktop",
            InstalledSurfaceKind.DESKTOP,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.BUNDLE_IDENTIFIER,
            "google-antigravity",
        ),
        _registration(
            "google",
            "antigravity-ide",
            "Google Antigravity IDE",
            InstalledSurfaceKind.IDE_EXTENSION,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.EXTENSION_IDENTIFIER,
            "google-antigravity",
        ),
        _registration(
            "google",
            "gemini-cli",
            "Gemini CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.PATH_MARKER,
            "gemini-cli",
        ),
        _registration(
            "google",
            "gemini-desktop",
            "Gemini Desktop",
            InstalledSurfaceKind.DESKTOP,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.BUNDLE_IDENTIFIER,
            "gemini-desktop",
        ),
        _registration(
            "google",
            "gemini-code-assist-vscode",
            "Gemini Code Assist for VS Code",
            InstalledSurfaceKind.IDE_EXTENSION,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.EXTENSION_IDENTIFIER,
            "gemini-code-assist-vscode",
        ),
        _registration(
            "github",
            "copilot-ide",
            "GitHub Copilot",
            InstalledSurfaceKind.IDE_EXTENSION,
            SurfaceSupportLevel.INVENTORY,
            (),
            None,
            SurfaceDetectorKind.EXTENSION_IDENTIFIER,
            "github-copilot",
        ),
        _registration(
            "kiro",
            "cli",
            "Kiro CLI",
            InstalledSurfaceKind.CLI,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "kiro-hooks-v1",
            SurfaceDetectorKind.PATH_MARKER,
            "kiro-cli",
        ),
        _registration(
            "kiro",
            "sidepulse-agent",
            f"Kiro {PRODUCT_DISPLAY_NAME} agent",
            InstalledSurfaceKind.LOCAL_HARNESS,
            SurfaceSupportLevel.LIFECYCLE,
            ("live_agent_events",),
            "kiro-hooks-v1",
            SurfaceDetectorKind.CONFIG_MARKER,
            "kiro-hooks-v1",
        ),
    )
)


def installed_surface_registrations() -> tuple[InstalledSurfaceRegistration, ...]:
    """Return the immutable literal registry in product-owned display order."""
    return _INSTALLED_SURFACE_REGISTRATIONS


@dataclass(frozen=True, slots=True)
class InstalledSurfaceObservation:
    key: InstalledSurfaceKey
    presence: SurfacePresence
    version: str | None
    capability_ids: tuple[str, ...]
    reason_code: str | None

    def __post_init__(self) -> None:
        if not (
            type(self.key) is InstalledSurfaceKey
            and type(self.presence) is SurfacePresence
            and (self.version is None or _valid_version(self.version))
            and type(self.capability_ids) is tuple
            and len(self.capability_ids) <= MAX_CAPABILITIES_PER_SURFACE
            and all(_valid_slug(capability) for capability in self.capability_ids)
            and len(self.capability_ids) == len(set(self.capability_ids))
            and (self.reason_code is None or _valid_slug(self.reason_code))
        ):
            raise InstalledSurfaceValidationError("invalid installed surface observation")


@dataclass(frozen=True, slots=True)
class InstalledSurfaceEvidence:
    """Content-free outcome from a read-only, caller-owned detector."""

    key: InstalledSurfaceKey
    detector_kind: SurfaceDetectorKind
    detector_id: str
    detected: bool
    configured: bool
    version: str | None
    migration_required: bool = False
    unsupported_version: bool = False

    def __post_init__(self) -> None:
        mutually_exclusive = not (self.migration_required and self.unsupported_version)
        state_requires_detection = not (
            (self.configured or self.migration_required or self.unsupported_version) and not self.detected
        )
        if not (
            type(self.key) is InstalledSurfaceKey
            and type(self.detector_kind) is SurfaceDetectorKind
            and _valid_slug(self.detector_id)
            and type(self.detected) is bool
            and type(self.configured) is bool
            and type(self.migration_required) is bool
            and type(self.unsupported_version) is bool
            and (self.version is None or _valid_version(self.version))
            and mutually_exclusive
            and state_requires_detection
        ):
            raise InstalledSurfaceValidationError("invalid installed surface evidence")


@dataclass(frozen=True, slots=True)
class InstalledSurfaceReduction:
    """Inventory result that deliberately has no lifecycle authority."""

    observations: tuple[InstalledSurfaceObservation, ...]
    provider_facts: tuple[object, ...] = ()
    canonical_events: tuple[object, ...] = ()
    work_rows: tuple[object, ...] = ()
    requests: tuple[object, ...] = ()
    interruptions: tuple[object, ...] = ()
    notifications: tuple[object, ...] = ()
    completions: tuple[object, ...] = ()
    hardware_presentation_changes: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        no_lifecycle_outputs = (
            self.provider_facts,
            self.canonical_events,
            self.work_rows,
            self.requests,
            self.interruptions,
            self.notifications,
            self.completions,
            self.hardware_presentation_changes,
        )
        valid_observations = type(self.observations) is tuple and all(
            type(row) is InstalledSurfaceObservation for row in self.observations
        )
        keys = tuple(row.key for row in self.observations) if valid_observations else ()
        if not (
            valid_observations
            and len(keys) == len(set(keys))
            and all(type(value) is tuple and not value for value in no_lifecycle_outputs)
        ):
            raise InstalledSurfaceValidationError("invalid installed surface reduction")


def reduce_installed_surface_evidence(
    evidence: tuple[InstalledSurfaceEvidence, ...],
    registrations: tuple[InstalledSurfaceRegistration, ...] = _INSTALLED_SURFACE_REGISTRATIONS,
) -> InstalledSurfaceReduction:
    """Reduce bounded detector outputs into inventory rows, without lifecycle facts."""
    validated_registrations = validate_installed_surface_registrations(registrations)
    if (
        type(evidence) is not tuple
        or len(evidence) > len(validated_registrations)
        or not all(type(row) is InstalledSurfaceEvidence for row in evidence)
    ):
        raise InstalledSurfaceValidationError("invalid installed surface evidence batch")
    evidence_keys = tuple(row.key for row in evidence)
    if len(evidence_keys) != len(set(evidence_keys)):
        raise InstalledSurfaceValidationError("duplicate installed surface evidence")
    rows_by_key = {row.key: row for row in validated_registrations}
    for item in evidence:
        registration = rows_by_key.get(item.key)
        if registration is None or (
            item.detector_kind is not registration.detector_kind or item.detector_id != registration.detector_id
        ):
            raise InstalledSurfaceValidationError("invalid installed surface detector evidence")
    evidence_by_key = {row.key: row for row in evidence}
    observations = tuple(
        _observation_from_evidence(registration, evidence_by_key.get(registration.key))
        for registration in validated_registrations
    )
    return InstalledSurfaceReduction(observations=observations)


def _observation_from_evidence(
    registration: InstalledSurfaceRegistration,
    evidence: InstalledSurfaceEvidence | None,
) -> InstalledSurfaceObservation:
    if evidence is None or not evidence.detected:
        return InstalledSurfaceObservation(registration.key, SurfacePresence.ABSENT, None, (), None)
    if evidence.unsupported_version:
        presence = SurfacePresence.UNSUPPORTED_VERSION
        reason_code = "unsupported_version"
    elif evidence.migration_required:
        presence = SurfacePresence.MIGRATION_REQUIRED
        reason_code = "migration_required"
    elif evidence.configured:
        presence = SurfacePresence.CONFIGURED
        reason_code = None
    else:
        presence = SurfacePresence.INSTALLED
        reason_code = None
    capability_ids = (
        registration.capability_ids
        if presence is SurfacePresence.CONFIGURED
        and registration.support in {SurfaceSupportLevel.FULL, SurfaceSupportLevel.LIFECYCLE}
        else ()
    )
    return InstalledSurfaceObservation(
        registration.key,
        presence,
        evidence.version,
        capability_ids,
        reason_code,
    )
