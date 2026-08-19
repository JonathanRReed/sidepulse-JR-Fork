"""Behavioral coverage for the pure installed-agent surface registry."""

from __future__ import annotations

import pytest


def test_installed_surface_registry_exposes_typed_domain_boundary() -> None:
    """Removing the registry types would leave inventory without a pure boundary."""
    from sidepulse.installed_agents import (
        InstalledSurfaceKey,
        InstalledSurfaceKind,
        InstalledSurfaceObservation,
        InstalledSurfaceRegistration,
        SurfacePresence,
        SurfaceSupportLevel,
    )

    assert InstalledSurfaceKind.CLI.value == "cli"
    assert SurfaceSupportLevel.INVENTORY.value == "inventory"
    assert SurfacePresence.ABSENT.value == "absent"
    assert InstalledSurfaceKey.__name__ == "InstalledSurfaceKey"
    assert InstalledSurfaceRegistration.__name__ == "InstalledSurfaceRegistration"
    assert InstalledSurfaceObservation.__name__ == "InstalledSurfaceObservation"


def test_literal_registry_has_one_deterministic_row_per_supported_surface() -> None:
    """Reordering or omitting a surface would make installed-agent rows unstable."""
    from sidepulse.installed_agents import installed_surface_registrations

    assert tuple(
        (row.provider_id, row.surface_id, row.label, row.kind.value, row.support.value)
        for row in installed_surface_registrations()
    ) == (
        ("codex", "cli", "Codex CLI", "cli", "lifecycle"),
        ("claude", "cli", "Claude CLI", "cli", "lifecycle"),
        ("devin", "cli", "Devin CLI", "cli", "lifecycle"),
        ("grok", "cli", "Grok CLI", "cli", "lifecycle"),
        ("cursor", "ide", "Cursor", "ide_extension", "lifecycle"),
        ("hermes", "cli", "Hermes CLI", "cli", "lifecycle"),
        ("openclaw", "cli", "OpenClaw CLI", "cli", "lifecycle"),
        ("opencode", "cli", "OpenCode CLI", "cli", "inventory"),
        ("opencode", "sidepulse-plugin", "OpenCode SidePulse integration", "local_harness", "lifecycle"),
        ("opencode", "desktop", "OpenCode Desktop", "desktop", "inventory"),
        ("google", "antigravity-cli", "Google Antigravity CLI", "cli", "inventory"),
        ("google", "antigravity-desktop", "Google Antigravity Desktop", "desktop", "inventory"),
        ("google", "antigravity-ide", "Google Antigravity IDE", "ide_extension", "inventory"),
        ("google", "gemini-cli", "Gemini CLI", "cli", "inventory"),
        ("google", "gemini-desktop", "Gemini Desktop", "desktop", "inventory"),
        ("google", "gemini-code-assist-vscode", "Gemini Code Assist for VS Code", "ide_extension", "inventory"),
        ("github", "copilot-ide", "GitHub Copilot", "ide_extension", "inventory"),
        ("kiro", "cli", "Kiro CLI", "cli", "lifecycle"),
        ("kiro", "sidepulse-agent", "Kiro SidePulse agent", "local_harness", "lifecycle"),
    )


def test_registration_rejects_duplicate_surface_keys_and_executable_detectors() -> None:
    """A duplicate or executable detector could create ambiguous or active inventory work."""
    from sidepulse.installed_agents import (
        InstalledSurfaceKey,
        InstalledSurfaceKind,
        InstalledSurfaceRegistration,
        InstalledSurfaceValidationError,
        SurfaceDetectorKind,
        SurfaceSupportLevel,
        validate_installed_surface_registrations,
    )

    registration = InstalledSurfaceRegistration(
        provider_id="codex",
        surface_id="cli",
        label="Codex CLI",
        kind=InstalledSurfaceKind.CLI,
        support=SurfaceSupportLevel.LIFECYCLE,
        capability_ids=("live_agent_events",),
        hook_profile_id="codex-hooks-v1",
        capacity_profile_id=None,
        detector_kind=SurfaceDetectorKind.PATH_MARKER,
        detector_id="codex-cli",
    )
    assert registration.key == InstalledSurfaceKey("codex", "cli")
    with pytest.raises(InstalledSurfaceValidationError, match="duplicate"):
        validate_installed_surface_registrations((registration, registration))
    with pytest.raises(InstalledSurfaceValidationError, match="registration"):
        InstalledSurfaceRegistration(
            provider_id="codex",
            surface_id="cli",
            label="Codex CLI",
            kind=InstalledSurfaceKind.CLI,
            support=SurfaceSupportLevel.LIFECYCLE,
            capability_ids=("live_agent_events",),
            hook_profile_id="codex-hooks-v1",
            capacity_profile_id=None,
            detector_kind="executable",  # type: ignore[arg-type]
            detector_id="/usr/bin/codex",
        )


def test_reduction_maps_bounded_read_only_evidence_without_lifecycle_leakage() -> None:
    """Treating installation as activity would fabricate agent work and alerts."""
    from sidepulse.installed_agents import (
        InstalledSurfaceEvidence,
        InstalledSurfaceKey,
        SurfaceDetectorKind,
        SurfacePresence,
        reduce_installed_surface_evidence,
    )

    reduction = reduce_installed_surface_evidence(
        (
            InstalledSurfaceEvidence(
                key=InstalledSurfaceKey("opencode", "sidepulse-plugin"),
                detector_kind=SurfaceDetectorKind.CONFIG_MARKER,
                detector_id="opencode-plugin",
                detected=True,
                configured=True,
                version="1.15.13",
            ),
            InstalledSurfaceEvidence(
                key=InstalledSurfaceKey("google", "gemini-cli"),
                detector_kind=SurfaceDetectorKind.PATH_MARKER,
                detector_id="gemini-cli",
                detected=True,
                configured=False,
                migration_required=True,
                version="v0.9.0",
            ),
        )
    )

    observations = {row.key: row for row in reduction.observations}
    plugin_key = InstalledSurfaceKey("opencode", "sidepulse-plugin")
    assert observations[plugin_key].presence is SurfacePresence.CONFIGURED
    assert observations[plugin_key].capability_ids == (
        "live_agent_events",
        "actionable_requests",
    )
    assert observations[InstalledSurfaceKey("google", "gemini-cli")].presence is SurfacePresence.MIGRATION_REQUIRED
    assert observations[InstalledSurfaceKey("codex", "cli")].presence is SurfacePresence.ABSENT
    assert reduction.provider_facts == ()
    assert reduction.canonical_events == ()
    assert reduction.work_rows == ()
    assert reduction.requests == ()
    assert reduction.interruptions == ()
    assert reduction.notifications == ()
    assert reduction.completions == ()
    assert reduction.hardware_presentation_changes == ()


def test_reduction_rejects_unknown_detectors_duplicate_evidence_and_path_or_secret_values() -> None:
    """Accepting untrusted detector data could expose host paths or activate unknown surfaces."""
    from sidepulse.installed_agents import (
        InstalledSurfaceEvidence,
        InstalledSurfaceKey,
        InstalledSurfaceValidationError,
        SurfaceDetectorKind,
        reduce_installed_surface_evidence,
    )

    opencode_evidence = InstalledSurfaceEvidence(
        key=InstalledSurfaceKey("opencode", "sidepulse-plugin"),
        detector_kind=SurfaceDetectorKind.CONFIG_MARKER,
        detector_id="opencode-plugin",
        detected=True,
        configured=True,
        version="1.15.13",
    )
    with pytest.raises(InstalledSurfaceValidationError, match="duplicate"):
        reduce_installed_surface_evidence((opencode_evidence, opencode_evidence))
    with pytest.raises(InstalledSurfaceValidationError, match="detector"):
        reduce_installed_surface_evidence(
            (
                InstalledSurfaceEvidence(
                    key=InstalledSurfaceKey("opencode", "sidepulse-plugin"),
                    detector_kind=SurfaceDetectorKind.PATH_MARKER,
                    detector_id="opencode-plugin",
                    detected=True,
                    configured=False,
                    version="1.15.13",
                ),
            )
        )
    with pytest.raises(InstalledSurfaceValidationError, match="evidence"):
        InstalledSurfaceEvidence(
            key=InstalledSurfaceKey("opencode", "sidepulse-plugin"),
            detector_kind=SurfaceDetectorKind.CONFIG_MARKER,
            detector_id="opencode-plugin",
            detected=True,
            configured=False,
            version="/Users/example/.config/opencode",
        )
    with pytest.raises(InstalledSurfaceValidationError, match="evidence"):
        InstalledSurfaceEvidence(
            key=InstalledSurfaceKey("opencode", "sidepulse-plugin"),
            detector_kind=SurfaceDetectorKind.CONFIG_MARKER,
            detector_id="api-key",
            detected=True,
            configured=False,
            version="1.15.13",
        )


def test_observation_rejects_raw_paths_credentials_and_oversized_product_values() -> None:
    """Persisting detector internals would disclose private host or account data."""
    from sidepulse.installed_agents import (
        InstalledSurfaceKey,
        InstalledSurfaceKind,
        InstalledSurfaceObservation,
        InstalledSurfaceRegistration,
        InstalledSurfaceValidationError,
        SurfaceDetectorKind,
        SurfacePresence,
        SurfaceSupportLevel,
    )

    key = InstalledSurfaceKey("opencode", "cli")
    with pytest.raises(InstalledSurfaceValidationError, match="observation"):
        InstalledSurfaceObservation(
            key=key,
            presence=SurfacePresence.INSTALLED,
            version="1.15.13",
            capability_ids=(),
            reason_code="/Users/example/.config/opencode",
        )
    with pytest.raises(InstalledSurfaceValidationError, match="observation"):
        InstalledSurfaceObservation(
            key=key,
            presence=SurfacePresence.INSTALLED,
            version="1.15.13",
            capability_ids=("api-key",),
            reason_code=None,
        )
    with pytest.raises(InstalledSurfaceValidationError, match="registration"):
        InstalledSurfaceRegistration(
            provider_id="opencode",
            surface_id="cli",
            label="O" * 129,
            kind=InstalledSurfaceKind.CLI,
            support=SurfaceSupportLevel.LIFECYCLE,
            capability_ids=(),
            hook_profile_id=None,
            capacity_profile_id=None,
            detector_kind=SurfaceDetectorKind.CONFIG_MARKER,
            detector_id="opencode-plugin",
        )


def test_reduction_fails_closed_for_malformed_rows() -> None:
    """A malformed caller result must raise the registry error, not leak an attribute error."""
    from sidepulse.installed_agents import (
        InstalledSurfaceReduction,
        InstalledSurfaceValidationError,
    )

    with pytest.raises(InstalledSurfaceValidationError, match="reduction"):
        InstalledSurfaceReduction(observations=(object(),))  # type: ignore[arg-type]
