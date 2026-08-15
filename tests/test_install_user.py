from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_uses_an_isolated_virtual_environment() -> None:
    text = (ROOT / "scripts" / "install-user.sh").read_text(encoding="utf-8")

    assert '"$PYTHON_BIN" -m venv "$VENV_DIR"' in text
    assert '"$VENV_DIR/bin/python" -m pip install' in text
    assert "--break-system-packages" not in text


def test_project_bash_scripts_have_valid_syntax() -> None:
    scripts = sorted((ROOT / "scripts").glob("*.sh"))
    scripts.extend(sorted((ROOT / "packaging").glob("*.sh")))

    for path in scripts:
        result = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
