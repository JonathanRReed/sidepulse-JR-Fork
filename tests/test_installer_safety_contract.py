from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_installs_payload_without_mutating_external_integrations() -> None:
    text = (ROOT / "packaging" / "scripts" / "postinstall").read_text()

    assert "setup --sd-eject-guard-scope user" not in text
    assert "status-bar install-sleep-helper" not in text
    assert "agent-monitor" not in text
    assert "sdejectguard" not in text
    assert "setup-pending" in text
    assert "setup-complete" in text
    assert "explicit user action" in text


def test_package_never_replaces_an_unowned_cli_path() -> None:
    text = (ROOT / "packaging" / "scripts" / "postinstall").read_text()

    assert "readlink" in text
    assert 'left existing $CLI_LINK unchanged' in text
    assert 'ln -sfn "$APP_BINARY" "$CLI_LINK"' in text
    assert 'elif [ -L "$CLI_LINK" ]' in text


def test_supported_uninstaller_removes_only_owned_integrations() -> None:
    text = (ROOT / "scripts" / "uninstall-macos.sh").read_text()

    for command in (
        "status-bar stop",
        "agent-monitor uninstall all",
        "sdejectguard uninstall --scope user",
        "status-bar uninstall-sleep-helper",
        "sdejectguard uninstall --scope system",
    ):
        assert command in text
    assert 'readlink "$CLI_LINK"' in text
    assert "--purge-state" in text
    assert "--keep-app" in text
