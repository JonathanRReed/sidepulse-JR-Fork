from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from sidepulse.cli import build_sidepulse_parser, cmd_effects, sidepulse_main
from sidepulse.effect_cli import dispatch_effect_command
from sidepulse.scene_pack_store import ScenePackStore


def _pack(*, name: str = "Quiet Work") -> dict[str, object]:
    return {
        "id": "quiet-work",
        "name": name,
        "version": 2,
        "scenes": [
            {
                "scene": "demo",
                "label": "Quiet Demo",
                "surface_role": "preview",
                "brightness": 0.75,
                "motion": "full",
                "notifications": "all",
                "display_admission": "all",
                "device_selection": "all",
            }
        ],
        "safety": {"data_only": True, "network": False},
        "accessibility": {
            "reduced_motion": True,
            "high_contrast": True,
            "non_color_cues": True,
        },
    }


def _run(
    store: ScenePackStore,
    action: str,
    **fields: object,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    values: dict[str, object] = {"action": action, "json": True}
    values.update(fields)
    code = dispatch_effect_command(
        SimpleNamespace(**values),
        scene_store=store,
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def test_scene_pack_cli_manages_and_previews_data_only_packs(tmp_path: Path) -> None:
    source = tmp_path / "scene-pack.json"
    source.write_text(json.dumps(_pack()), encoding="utf-8")
    store = ScenePackStore(tmp_path / "store")

    code, output, error = _run(store, "scene-install", source=source)
    assert (code, error) == (0, "")
    assert json.loads(output)["status"] == "installed"

    code, output, _ = _run(store, "scene-list")
    assert code == 0
    assert json.loads(output)["packs"][0]["scene_count"] == 1

    code, output, _ = _run(store, "scene-inspect", pack_id="quiet-work")
    assert code == 0
    assert json.loads(output)["scenes"][0]["scene"] == "demo"

    code, output, _ = _run(store, "scene-preview", source=source)
    preview = json.loads(output)
    assert code == 0
    assert preview["scenes"][0]["normal"]["effective_motion"] == "full"
    assert preview["scenes"][0]["reduced_motion"]["effective_motion"] == "static"

    source.write_text(json.dumps(_pack(name="Quiet Work 2")), encoding="utf-8")
    assert _run(store, "scene-update", source=source)[0] == 0

    target = tmp_path / "exported.json"
    assert _run(
        store,
        "scene-export",
        pack_id="quiet-work",
        target=target,
    )[0] == 0
    assert target.exists()

    assert _run(store, "scene-remove", pack_id="quiet-work")[0] == 0


def test_sidepulse_parser_reaches_scene_pack_store(tmp_path: Path, capsys) -> None:
    source = tmp_path / "scene-pack.json"
    source.write_text(json.dumps(_pack()), encoding="utf-8")
    store = tmp_path / "store"
    arguments = [
        "effects",
        "scene-install",
        str(source),
        "--store-dir",
        str(store),
        "--json",
    ]

    parsed = build_sidepulse_parser().parse_args(arguments)

    assert parsed.func is cmd_effects
    assert parsed.action == "scene-install"
    assert sidepulse_main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "installed"
    assert ScenePackStore(store).inspect("quiet-work").name == "Quiet Work"
