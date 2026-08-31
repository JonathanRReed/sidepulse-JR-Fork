"""Behavioral coverage for bounded installed-agent host inventory."""

from __future__ import annotations

import os
import stat
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest


def test_inventory_exposes_a_content_free_read_only_collection_boundary() -> None:
    """Removing the collector would make host inventory impossible to fence."""
    from sidepulse.installed_agent_inventory import (
        InstalledAgentInventoryResult,
        InventoryCandidate,
        InventoryRoot,
        collect_installed_agent_inventory,
        default_inventory_candidates,
    )

    assert InventoryCandidate.__name__ == "InventoryCandidate"
    assert InventoryRoot.__name__ == "InventoryRoot"
    assert InstalledAgentInventoryResult.__name__ == "InstalledAgentInventoryResult"
    assert callable(collect_installed_agent_inventory)
    assert len(default_inventory_candidates()) == 19


def test_antigravity_cli_uses_the_official_agy_name_with_bounded_common_install_roots() -> None:
    """Using the obsolete antigravity name would silently miss the official CLI."""
    from sidepulse.installed_agent_inventory import default_inventory_candidates
    from sidepulse.installed_agents import InstalledSurfaceKey

    candidate = next(
        row
        for row in default_inventory_candidates()
        if row.key == InstalledSurfaceKey("google", "antigravity-cli")
    )

    assert candidate.relative_path == (".local", "bin", "agy")
    assert candidate.alternate_locations == (
        ("homebrew", ("bin", "agy")),
        ("local_bin", ("bin", "agy")),
    )


def test_reviewed_path_marker_surfaces_use_only_home_homebrew_and_usr_local_literals() -> None:
    """Dropping a package-manager location would regress normal installed-agent inventory."""
    from sidepulse.installed_agent_inventory import default_inventory_candidates
    from sidepulse.installed_agents import InstalledSurfaceKey

    candidates = {candidate.key: candidate for candidate in default_inventory_candidates()}
    expected = {
        InstalledSurfaceKey("codex", "cli"): "codex",
        InstalledSurfaceKey("claude", "cli"): "claude",
        InstalledSurfaceKey("devin", "cli"): "devin",
        InstalledSurfaceKey("grok", "cli"): "grok",
        InstalledSurfaceKey("hermes", "cli"): "hermes",
        InstalledSurfaceKey("opencode", "cli"): "opencode",
        InstalledSurfaceKey("google", "antigravity-cli"): "agy",
        InstalledSurfaceKey("google", "gemini-cli"): "gemini",
    }

    for key, executable in expected.items():
        candidate = candidates[key]
        assert candidate.relative_path[-1] == executable
        assert candidate.alternate_locations == (
            ("homebrew", ("bin", executable)),
            ("local_bin", ("bin", executable)),
        )
    plugin = candidates[InstalledSurfaceKey("opencode", "sidepulse-plugin")]
    assert plugin.marker_kind.value == "regular_file"
    assert plugin.alternate_locations == ()


def test_gemini_desktop_uses_the_exact_reviewed_macos_bundle_literal() -> None:
    from sidepulse.installed_agent_inventory import default_inventory_candidates
    from sidepulse.installed_agents import InstalledSurfaceKey

    candidates = {candidate.key: candidate for candidate in default_inventory_candidates()}
    desktop = candidates[InstalledSurfaceKey("google", "gemini-desktop")]
    assert desktop.root_id == "applications"
    assert desktop.relative_path == ("Gemini.app",)
    assert desktop.marker_kind.value == "directory"


def test_exact_package_manager_roots_allow_safe_group_writable_directories() -> None:
    from sidepulse.installed_agent_inventory import InventoryRoot

    owners = frozenset({0, os.getuid()})
    for root_id, path in (
        ("homebrew", Path("/opt/homebrew")),
        ("local_bin", Path("/usr/local")),
    ):
        root = InventoryRoot(
            root_id,
            path,
            owners,
            trusted_system_root=True,
        )
        assert root.trusted_system_root

    with pytest.raises(Exception, match="system root"):
        InventoryRoot(
            "homebrew",
            Path("/tmp/homebrew"),
            owners,
            trusted_system_root=True,
        )


def test_system_applications_root_allows_only_the_reviewed_root_mode_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Treating arbitrary group-writable parents as trusted would allow attacker-controlled apps."""
    import sidepulse.installed_agent_inventory as inventory
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    system_root = inventory.InventoryRoot(
        "applications",
        Path("/Applications"),
        frozenset({0}),
        trusted_system_root=True,
    )
    directory = os.stat_result((stat.S_IFDIR | 0o775, 1, 1, 1, 0, 0, 0, 0, 0, 0))
    bundle = os.stat_result((stat.S_IFDIR | 0o755, 2, 1, 1, 0, 0, 0, 0, 0, 0))
    original_lstat = inventory._lstat
    monkeypatch.setattr(
        inventory,
        "_lstat",
        lambda path: directory if path == Path("/Applications") else bundle if path == Path("/Applications/OpenCode.app") else None,
    )

    result = inventory.collect_installed_agent_inventory((system_root,))
    rows = {row.key: row for row in result.reduction.observations}
    assert rows[InstalledSurfaceKey("opencode", "desktop")].presence is SurfacePresence.INSTALLED

    with pytest.raises(inventory.InstalledAgentInventoryError, match="system root"):
        inventory.InventoryRoot(
            "applications",
            Path("/tmp/Applications"),
            frozenset({os.getuid()}),
            trusted_system_root=True,
        )

    attacker_root_path = tmp_path / "attacker-applications"
    attacker_root_path.mkdir()
    attacker_root_path.chmod(0o777)
    attacker_bundle = attacker_root_path / "OpenCode.app"
    attacker_bundle.mkdir()
    attacker_bundle.chmod(0o755)
    attacker_root = inventory.InventoryRoot(
        "applications",
        attacker_root_path,
        frozenset({os.getuid()}),
    )
    monkeypatch.setattr(inventory, "_lstat", original_lstat)
    attacker_result = inventory.collect_installed_agent_inventory((attacker_root,))
    attacker_rows = {row.key: row for row in attacker_result.reduction.observations}
    assert attacker_rows[InstalledSurfaceKey("opencode", "desktop")].presence is SurfacePresence.ABSENT


def test_trusted_system_applications_root_refuses_world_writable_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowing 0777 would make the fixed system-root exception attacker writable."""
    import sidepulse.installed_agent_inventory as inventory
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    root = inventory.InventoryRoot(
        "applications",
        Path("/Applications"),
        frozenset({0}),
        trusted_system_root=True,
    )
    world_writable_root = os.stat_result(
        (stat.S_IFDIR | 0o777, 1, 1, 1, 0, 0, 0, 0, 0, 0)
    )
    bundle = os.stat_result((stat.S_IFDIR | 0o755, 2, 1, 1, 0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(
        inventory,
        "_lstat",
        lambda path: world_writable_root
        if path == Path("/Applications")
        else bundle
        if path == Path("/Applications/OpenCode.app")
        else None,
    )

    result = inventory.collect_installed_agent_inventory((root,))
    rows = {row.key: row for row in result.reduction.observations}

    assert rows[InstalledSurfaceKey("opencode", "desktop")].presence is SurfacePresence.ABSENT


def test_versioned_macos_intellij_plugin_layout_is_deferred_without_directory_enumeration(
    tmp_path: Path,
) -> None:
    """Guessing a JetBrains product version would make inventory both incomplete and unbounded."""
    from sidepulse.installed_agent_inventory import (
        InventoryRoot,
        collect_installed_agent_inventory,
        default_inventory_candidates,
    )
    from sidepulse.installed_agents import installed_surface_registrations

    home = tmp_path / "home"
    plugin = (
        home
        / "Library"
        / "Application Support"
        / "JetBrains"
        / "IntelliJIdea2026.2"
        / "plugins"
        / "gemini-code-assist"
    )
    plugin.mkdir(parents=True)
    for parent in (plugin, *plugin.parents):
        if parent == tmp_path:
            break
        parent.chmod(0o700)
    roots = (InventoryRoot("home", home, frozenset({os.getuid()})),)

    result = collect_installed_agent_inventory(roots)

    assert all(row.surface_id != "gemini-code-assist-intellij" for row in installed_surface_registrations())
    assert all(row.key.surface_id != "gemini-code-assist-intellij" for row in default_inventory_candidates())
    assert "IntelliJIdea2026.2" not in repr(result)


def test_alternate_literal_install_location_emits_one_surface_row_and_refuses_missing_or_unsafe_roots(
    tmp_path: Path,
) -> None:
    """Scanning alternates or emitting duplicate evidence would make installed state ambiguous."""
    from sidepulse.installed_agent_inventory import (
        InventoryRoot,
        collect_installed_agent_inventory,
        default_inventory_candidates,
    )
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    home = tmp_path / "home"
    homebrew = tmp_path / "homebrew"
    for root in (home, homebrew):
        root.mkdir()
        root.chmod(0o700)
    owner = frozenset({os.getuid()})
    roots = (InventoryRoot("home", home, owner), InventoryRoot("homebrew", homebrew, owner))
    candidate = next(
        row
        for row in default_inventory_candidates()
        if row.key == InstalledSurfaceKey("google", "antigravity-cli")
    )
    _materialize(homebrew, candidate.alternate_locations[0][1], mode=0o700)

    result = collect_installed_agent_inventory(roots)
    rows = [row for row in result.reduction.observations if row.key == candidate.key]

    assert len(rows) == 1
    assert rows[0].presence is SurfacePresence.INSTALLED
    assert str(homebrew) not in repr(result)

    homebrew.chmod(0o777)
    unsafe = collect_installed_agent_inventory(roots)
    unsafe_row = next(row for row in unsafe.reduction.observations if row.key == candidate.key)
    assert unsafe_row.presence is SurfacePresence.ABSENT

    missing = collect_installed_agent_inventory((InventoryRoot("home", home, owner),))
    missing_row = next(row for row in missing.reduction.observations if row.key == candidate.key)
    assert missing_row.presence is SurfacePresence.ABSENT


def test_reviewed_executable_marker_accepts_only_a_trusted_leaf_symlink_and_config_markers_still_refuse_links(
    tmp_path: Path,
) -> None:
    """Rejecting package-manager shims or accepting a config link would misstate safe presence."""
    from sidepulse.installed_agent_inventory import (
        InventoryRoot,
        collect_installed_agent_inventory,
        default_inventory_candidates,
    )
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    root = tmp_path / "home"
    root.mkdir()
    root.chmod(0o700)
    roots = (InventoryRoot("home", root, frozenset({os.getuid()})),)
    candidates = default_inventory_candidates()
    codex = next(row for row in candidates if row.key == InstalledSurfaceKey("codex", "cli"))
    plugin = next(
        row
        for row in candidates
        if row.key == InstalledSurfaceKey("opencode", "sidepulse-plugin")
    )
    codex_leaf = root.joinpath(*codex.relative_path)
    codex_leaf.parent.mkdir(parents=True)
    codex_leaf.symlink_to("package-manager-shim")
    config_leaf = root.joinpath(*plugin.relative_path)
    config_leaf.parent.mkdir(parents=True)
    config_leaf.symlink_to("outside-config")

    result = collect_installed_agent_inventory(roots)
    rows = {row.key: row for row in result.reduction.observations}

    assert rows[codex.key].presence is SurfacePresence.INSTALLED
    assert rows[plugin.key].presence is SurfacePresence.ABSENT
    assert str(root) not in repr(result)

    codex_leaf.parent.chmod(0o777)
    unsafe = collect_installed_agent_inventory(roots)
    unsafe_rows = {row.key: row for row in unsafe.reduction.observations}
    assert unsafe_rows[codex.key].presence is SurfacePresence.ABSENT


def test_inventory_candidate_locations_are_globally_bounded() -> None:
    """Allowing more than 64 literal locations would permit an unbounded host inventory."""
    from sidepulse.installed_agent_inventory import (
        MAX_INVENTORY_CANDIDATES,
        default_inventory_candidates,
    )

    candidates = default_inventory_candidates()
    assert sum(1 + len(candidate.alternate_locations) for candidate in candidates) <= MAX_INVENTORY_CANDIDATES


def test_inventory_rejects_duplicate_literal_locations_across_surface_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing a location for two surfaces would fabricate ambiguous inventory evidence."""
    import sidepulse.installed_agent_inventory as inventory
    from sidepulse.installed_agents import InstalledSurfaceKey

    candidates = inventory.default_inventory_candidates()
    agy = next(
        candidate
        for candidate in candidates
        if candidate.key == InstalledSurfaceKey("google", "antigravity-cli")
    )
    gemini = next(
        candidate
        for candidate in candidates
        if candidate.key == InstalledSurfaceKey("google", "gemini-cli")
    )
    replaced = tuple(
        replace(gemini, root_id=agy.root_id, relative_path=agy.relative_path)
        if candidate is gemini
        else candidate
        for candidate in candidates
    )
    monkeypatch.setattr(inventory, "_INVENTORY_CANDIDATES", replaced)

    with pytest.raises(inventory.InstalledAgentInventoryError, match="duplicate"):
        inventory.default_inventory_candidates()


def _roots(tmp_path: Path):
    from sidepulse.installed_agent_inventory import InventoryRoot

    names = ("home", "applications", "vscode")
    return tuple(
        InventoryRoot(name, tmp_path / name, frozenset({os.getuid()}))
        for name in names
    )


def _root_path(roots, root_id: str) -> Path:
    return next(root.path for root in roots if root.root_id == root_id)


def _materialize(root: Path, relative_path: tuple[str, ...], *, mode: int) -> Path:
    target = root.joinpath(*relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    target.chmod(mode)
    return target


def test_inventory_collects_only_matching_safe_markers_without_lifecycle_outputs(tmp_path: Path) -> None:
    """Replacing marker validation with a broad scan would leak unsafe host state."""
    from sidepulse.installed_agent_inventory import (
        InventoryMarkerKind,
        collect_installed_agent_inventory,
        default_inventory_candidates,
    )
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    roots = _roots(tmp_path)
    for root in roots:
        root.path.mkdir()
        root.path.chmod(0o700)
    candidates = default_inventory_candidates()
    codex = next(candidate for candidate in candidates if candidate.key == InstalledSurfaceKey("codex", "cli"))
    opencode = next(candidate for candidate in candidates if candidate.key == InstalledSurfaceKey("opencode", "cli"))
    plugin = next(
        candidate
        for candidate in candidates
        if candidate.key == InstalledSurfaceKey("opencode", "sidepulse-plugin")
    )
    desktop = next(candidate for candidate in candidates if candidate.key == InstalledSurfaceKey("opencode", "desktop"))
    extension = next(
        candidate
        for candidate in candidates
        if candidate.key == InstalledSurfaceKey("github", "copilot-ide")
    )
    assert codex.marker_kind is InventoryMarkerKind.EXECUTABLE_LINK_OR_FILE
    _materialize(_root_path(roots, codex.root_id), codex.relative_path, mode=0o700)
    _materialize(_root_path(roots, opencode.root_id), opencode.relative_path, mode=0o700)
    _materialize(_root_path(roots, plugin.root_id), plugin.relative_path, mode=0o600)
    _root_path(roots, desktop.root_id).joinpath(*desktop.relative_path).mkdir(parents=True)
    _root_path(roots, extension.root_id).joinpath(*extension.relative_path).mkdir(parents=True)

    result = collect_installed_agent_inventory(roots)
    observations = {row.key: row for row in result.reduction.observations}

    assert observations[codex.key].presence is SurfacePresence.INSTALLED
    assert observations[opencode.key].presence is SurfacePresence.INSTALLED
    assert observations[plugin.key].presence is SurfacePresence.CONFIGURED
    assert observations[desktop.key].presence is SurfacePresence.INSTALLED
    assert observations[extension.key].presence is SurfacePresence.INSTALLED
    assert result.reduction.provider_facts == ()
    assert result.reduction.canonical_events == ()
    assert result.reduction.work_rows == ()
    assert result.reduction.requests == ()
    assert result.reduction.notifications == ()
    assert result.reduction.completions == ()
    assert result.reduction.hardware_presentation_changes == ()
    assert str(tmp_path) not in repr(result)


@pytest.mark.parametrize("mutation", ("world_writable_parent", "wrong_owner", "not_executable"))
def test_inventory_refuses_untrusted_or_wrong_shape_markers(tmp_path: Path, mutation: str) -> None:
    """Accepting this marker would let a link or untrusted file claim installation."""
    from sidepulse.installed_agent_inventory import (
        InventoryRoot,
        collect_installed_agent_inventory,
        default_inventory_candidates,
    )
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    roots = _roots(tmp_path)
    for root in roots:
        root.path.mkdir()
        root.path.chmod(0o700)
    candidate = next(
        candidate
        for candidate in default_inventory_candidates()
        if candidate.key == InstalledSurfaceKey("codex", "cli")
    )
    target = _materialize(
        _root_path(roots, candidate.root_id),
        candidate.relative_path,
        mode=0o700,
    )
    if mutation == "world_writable_parent":
        target.parent.chmod(0o777)
    elif mutation == "wrong_owner":
        roots = tuple(
            InventoryRoot(root.root_id, root.path, frozenset({os.getuid() + 1}))
            for root in roots
        )
    else:
        target.chmod(0o600)

    result = collect_installed_agent_inventory(roots)

    observation = next(row for row in result.reduction.observations if row.key == candidate.key)
    assert observation.presence is SurfacePresence.ABSENT


def test_inventory_rejects_unbounded_or_unreviewed_candidate_declarations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allowing more or arbitrary candidates would turn inventory into a host scan."""
    import sidepulse.installed_agent_inventory as inventory

    roots = _roots(tmp_path)
    for root in roots:
        root.path.mkdir()
        root.path.chmod(0o700)
    candidates = inventory.default_inventory_candidates()
    monkeypatch.setattr(inventory, "_INVENTORY_CANDIDATES", candidates * 5)

    with pytest.raises(inventory.InstalledAgentInventoryError, match="candidate"):
        inventory.collect_installed_agent_inventory(roots)


def test_inventory_refuses_parent_replacement_before_emitting_an_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dropping identity revalidation would accept a path after its trusted parent changed."""
    import sidepulse.installed_agent_inventory as inventory
    from sidepulse.installed_agents import InstalledSurfaceKey, SurfacePresence

    roots = _roots(tmp_path)
    for root in roots:
        root.path.mkdir()
        root.path.chmod(0o700)
    candidate = next(
        candidate
        for candidate in inventory.default_inventory_candidates()
        if candidate.key == InstalledSurfaceKey("codex", "cli")
    )
    target = _materialize(
        _root_path(roots, candidate.root_id),
        candidate.relative_path,
        mode=0o700,
    )
    original_lstat = inventory._lstat
    calls = 0

    def swapping_lstat(path: Path):
        nonlocal calls
        calls += 1
        if calls == 3:
            replacement = target.parent.with_name(f"{target.parent.name}-replacement")
            target.parent.rename(replacement)
            target.parent.mkdir()
        return original_lstat(path)

    monkeypatch.setattr(inventory, "_lstat", swapping_lstat)
    result = inventory.collect_installed_agent_inventory(roots)

    observation = next(row for row in result.reduction.observations if row.key == candidate.key)
    assert observation.presence is SurfacePresence.ABSENT


def test_inventory_result_is_worker_payload_safe_and_rejects_noninventory_commands(tmp_path: Path) -> None:
    """Accepting another worker key would let unreviewed work enter OS polling."""
    from sidepulse.installed_agent_inventory import (
        InventoryRoot,
        execute_inventory_command,
    )
    from sidepulse.runtime_scheduler import RuntimeWorkCommand, RuntimeWorkerDomain

    root = tmp_path / "home"
    root.mkdir()
    root.chmod(0o700)
    payload = (InventoryRoot("home", root, frozenset({os.getuid()})),)
    command = RuntimeWorkCommand(
        RuntimeWorkerDomain.OS_POLL,
        "installed-agent-inventory",
        1,
        time.monotonic() + 10.0,
        payload,
    )

    result = execute_inventory_command(command)

    assert result.candidate_count == 19
    assert result.rejected_count == 19
    assert str(tmp_path) not in repr(result)
    invalid = RuntimeWorkCommand(
        RuntimeWorkerDomain.OS_POLL,
        "calendar-observation",
        1,
        time.monotonic() + 10.0,
        payload,
    )
    with pytest.raises(ValueError, match="inventory"):
        execute_inventory_command(invalid)


def test_one_hundred_inventory_refreshes_keep_the_worker_to_one_running_and_one_latest_pending(
    tmp_path: Path,
) -> None:
    """Removing the shared key would create an unbounded inventory queue."""
    from sidepulse.installed_agent_inventory import (
        InventoryRoot,
        execute_inventory_command,
    )
    from sidepulse.runtime_scheduler import (
        LatestWinsWorker,
        RuntimeWorkCommand,
        RuntimeWorkerDomain,
    )

    root = tmp_path / "home"
    root.mkdir()
    root.chmod(0o700)
    payload = (InventoryRoot("home", root, frozenset({os.getuid()})),)
    entered = threading.Event()
    release = threading.Event()
    results = []

    def execute(command):
        entered.set()
        assert release.wait(2.0)
        return execute_inventory_command(command)

    worker = LatestWinsWorker(
        RuntimeWorkerDomain.OS_POLL,
        executor=execute,
        result_handler=lambda _command, result: results.append(result),
        dispatch_main=lambda _drain: None,
    )
    for generation in range(1, 101):
        worker.submit(
            RuntimeWorkCommand(
                RuntimeWorkerDomain.OS_POLL,
                "installed-agent-inventory",
                generation,
                time.monotonic() + 10.0,
                payload,
            )
        )
        if generation == 1:
            assert entered.wait(1.0)
    snapshot = worker.snapshot()
    assert snapshot.running
    assert snapshot.pending_count == 1
    assert snapshot.result_count == 0
    assert snapshot.submitted == 100
    assert snapshot.replaced_pending == 98
    release.set()
    assert worker.wait_idle(timeout_seconds=2.0)
    assert worker.snapshot().completed >= 2
    assert results == []
    assert worker.close(timeout_seconds=1.0)
