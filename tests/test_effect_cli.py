from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from sidepulse.cli import build_sidepulse_parser, cmd_effects, sidepulse_main
from sidepulse.effect_cli import dispatch_effect_command
from sidepulse.effect_history import (
    EffectEvent,
    EffectHistory,
    EffectOutcome,
    EffectSemanticCategory,
    EffectSurface,
)
from sidepulse.effect_history_store import save_effect_history
from sidepulse.effect_pack_store import EffectPackStore


def _pack(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "calm-pack",
        "name": "Calm Pack",
        "version": 2,
        "effects": [{"id": "soft-pulse", "label": "Soft Pulse"}],
        "safety": {"data_only": True, "network": False},
        "accessibility": {"reduced_motion": True, "high_contrast": True},
    }
    payload.update(overrides)
    return payload


def _run(
    store: EffectPackStore,
    action: str,
    **fields: object,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    values: dict[str, object] = {"action": action, "json": False}
    values.update(fields)
    namespace = SimpleNamespace(**values)
    code = dispatch_effect_command(
        namespace,
        store=store,
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_cli_install_list_inspect_update_remove_json_round_trip(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pack.json"
    source.write_text(json.dumps(_pack()), encoding="utf-8")
    store = EffectPackStore(tmp_path / "store")

    code, output, error = _run(store, "install", source=source, json=True)
    assert code == 0
    assert error == ""
    assert json.loads(output)["status"] == "installed"

    code, output, _ = _run(store, "list", json=True)
    assert code == 0
    assert json.loads(output)["packs"][0]["pack_id"] == "calm-pack"

    code, output, _ = _run(store, "inspect", pack_id="calm-pack", json=True)
    assert code == 0
    assert json.loads(output)["effects"][0]["id"] == "soft-pulse"

    source.write_text(json.dumps(_pack(name="Changed")), encoding="utf-8")
    code, output, _ = _run(store, "update", source=source, json=True)
    assert code == 0
    assert json.loads(output)["status"] == "updated"

    code, output, _ = _run(store, "remove", pack_id="calm-pack", json=True)
    assert code == 0
    assert json.loads(output)["status"] == "removed"


def test_cli_collision_returns_refusal_code_and_deterministic_human_receipt(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    source = tmp_path / "pack.json"
    source.write_text(json.dumps(_pack(name="Collision")), encoding="utf-8")

    code, output, error = _run(store, "install", source=source)

    assert code == 2
    assert output == "calm-pack: refused (already_installed)\n"
    assert error == ""


def test_cli_export_writes_canonical_private_file(tmp_path: Path) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    target = tmp_path / "exports" / "calm-pack.json"
    target.parent.mkdir()

    code, output, error = _run(
        store,
        "export",
        pack_id="calm-pack",
        target=target,
    )

    assert code == 0
    assert output == f"calm-pack: exported to {target}\n"
    assert error == ""
    assert target.read_bytes() == store.canonical_export("calm-pack")


def test_cli_gallery_supports_installed_index_and_builtin_rows(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(
        _pack(license={"spdx_id": "MIT", "label": "MIT License"})
    )

    code, output, _ = _run(store, "gallery", json=True, builtin=False, query="")
    assert code == 0
    assert json.loads(output)["packs"][0]["license"]["spdx_id"] == "MIT"

    code, output, _ = _run(
        store,
        "gallery",
        json=True,
        builtin=True,
        query="pulse",
    )
    assert code == 0
    assert json.loads(output)["effects"][0]["effect_id"] == "pulse"


def test_cli_history_json_contains_only_content_free_projection(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "effect-history.json"
    save_effect_history(
        history_path,
        EffectHistory(
            (
                EffectEvent(
                    event_id="effect-event:one",
                    occurred_at_epoch=1_800_000_000.0,
                    effect_id="pulse",
                    semantic_category=EffectSemanticCategory.AMBIENT,
                    surface=EffectSurface.SCREEN_BAR,
                    outcome=EffectOutcome.SHOWN,
                ),
            )
        ),
    )
    store = EffectPackStore(tmp_path / "store")

    code, output, error = _run(
        store,
        "history",
        json=True,
        history_path=history_path,
    )

    assert code == 0
    assert error == ""
    document = json.loads(output)
    assert document["health"] == "healthy"
    assert document["events"][0]["effect_id"] == "pulse"
    lowered = output.casefold()
    for forbidden in (
        "prompt",
        "transcript",
        "session",
        "person@example.com",
        "/users/private",
        "https://private.example",
    ):
        assert forbidden not in lowered


def test_cli_history_rejects_store_dir_without_an_explicit_history_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    history_was_read = False

    def fail_if_read(*_args: object, **_kwargs: object) -> object:
        nonlocal history_was_read
        history_was_read = True
        raise AssertionError("default history must not be read")

    monkeypatch.setattr(EffectPackStore, "effect_history", fail_if_read)

    code = sidepulse_main(
        [
            "effects",
            "history",
            "--store-dir",
            str(tmp_path / "isolated-store"),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert captured.err == (
        "effect packs: --history-path is required with --store-dir\n"
    )
    assert history_was_read is False


def test_sidepulse_effects_duplicate_and_rename_are_user_reachable(
    tmp_path: Path,
    capsys,
) -> None:
    store_path = tmp_path / "store"
    store = EffectPackStore(store_path)
    store.install(_pack())

    duplicate_code = sidepulse_main(
        [
            "effects",
            "duplicate",
            "calm-pack",
            "calm-pack-copy",
            "Calm Pack Copy",
            "--store-dir",
            str(store_path),
        ]
    )
    duplicate_output = capsys.readouterr()

    assert duplicate_code == 0
    assert duplicate_output.out == "calm-pack-copy: duplicated\n"
    assert duplicate_output.err == ""
    assert store.inspect("calm-pack-copy").name == "Calm Pack Copy"

    rename_code = sidepulse_main(
        [
            "effects",
            "rename",
            "calm-pack-copy",
            "focused-pack",
            "Focused Pack",
            "--store-dir",
            str(store_path),
        ]
    )
    rename_output = capsys.readouterr()

    assert rename_code == 0
    assert rename_output.out == "focused-pack: renamed\n"
    assert rename_output.err == ""
    assert store.inspect("focused-pack").name == "Focused Pack"


def test_cli_duplicate_and_rename_refuse_target_collisions(
    tmp_path: Path,
) -> None:
    store = EffectPackStore(tmp_path / "store")
    store.install(_pack())
    store.install(_pack(id="other-pack", name="Other Pack"))
    original = store.canonical_export("calm-pack")
    other = store.canonical_export("other-pack")

    code, output, error = _run(
        store,
        "duplicate",
        pack_id="calm-pack",
        new_pack_id="other-pack",
        new_name="Replacement",
    )

    assert code == 2
    assert output == "other-pack: refused (already_installed)\n"
    assert error == ""

    code, output, error = _run(
        store,
        "rename",
        pack_id="calm-pack",
        new_pack_id="other-pack",
        new_name="Replacement",
    )

    assert code == 2
    assert output == "other-pack: refused (already_installed)\n"
    assert error == ""
    assert store.canonical_export("calm-pack") == original
    assert store.canonical_export("other-pack") == other


def test_cli_errors_do_not_echo_source_paths_or_file_content(tmp_path: Path) -> None:
    secret = "private-sentinel-token"
    source = tmp_path / f"{secret}.json"
    source.write_text(secret, encoding="utf-8")
    store = EffectPackStore(tmp_path / "store")

    code, output, error = _run(store, "install", source=source)

    assert code == 1
    assert output == ""
    assert error == "effect packs: install failed\n"
    assert secret not in error


def test_cli_rejects_unknown_action_without_mutating_store(tmp_path: Path) -> None:
    store = EffectPackStore(tmp_path / "store")

    code, output, error = _run(store, "unknown")

    assert code == 2
    assert output == ""
    assert error == "effect packs: unknown action\n"
    assert not (tmp_path / "store").exists()


def test_sidepulse_effects_parser_reaches_the_data_only_store(
    tmp_path: Path,
    capsys,
) -> None:
    source = tmp_path / "pack.json"
    source.write_text(json.dumps(_pack()), encoding="utf-8")
    store = tmp_path / "store"
    parser = build_sidepulse_parser()
    parsed = parser.parse_args(
        ["effects", "install", str(source), "--store-dir", str(store), "--json"]
    )

    assert parsed.func is cmd_effects
    assert parsed.action == "install"
    assert sidepulse_main(
        ["effects", "install", str(source), "--store-dir", str(store), "--json"]
    ) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "installed"
    assert EffectPackStore(store).inspect("calm-pack").name == "Calm Pack"
