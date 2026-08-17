from __future__ import annotations

import os
from pathlib import Path


def test_collection_time_environment_is_not_the_real_home() -> None:
    home = Path(os.environ["HOME"])
    assert home.name == "home"
    assert home.parent.name.startswith("sidepulse-pytest-")
    assert os.environ["SIDEPULSE_TESTING"] == "1"
    assert Path(os.environ["XDG_CONFIG_HOME"]).is_relative_to(home.parent)
    assert Path(os.environ["XDG_STATE_HOME"]).is_relative_to(home.parent)
    assert Path(os.environ["XDG_CACHE_HOME"]).is_relative_to(home.parent)


def test_test_volume_root_is_not_real_volumes() -> None:
    root = Path(os.environ["SIDEPULSE_TEST_VOLUME_ROOT"])
    assert root != Path("/Volumes")
    assert root.name == "Volumes"
    assert root.is_dir()
