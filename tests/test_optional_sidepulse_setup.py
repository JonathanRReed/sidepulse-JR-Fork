from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sidepulse import cli


def test_bare_setup_does_not_request_the_sd_eject_guard() -> None:
    args = cli.build_sidepulse_parser().parse_args(["setup"])

    assert args.sd_eject_guard is False


def test_existing_guard_configuration_flags_remain_explicit_opt_ins() -> None:
    parser = cli.build_sidepulse_parser()

    assert parser.parse_args(["setup", "--sd-eject-guard"]).sd_eject_guard is True
    assert parser.parse_args(
        ["setup", "--sd-eject-guard-scope", "auto"]
    ).sd_eject_guard is True
    assert parser.parse_args(
        ["setup", "--sd-eject-guard-volume-uuid", "A1B2-C3D4"]
    ).sd_eject_guard is True


def test_bare_setup_starts_status_bar_without_installing_sd_eject_guard() -> None:
    args = cli.build_sidepulse_parser().parse_args(["setup"])
    hook_result = SimpleNamespace(
        provider="codex",
        config_path=Path("/tmp/codex.toml"),
        log_path=Path("/tmp/codex.jsonl"),
        changed=False,
        backup_path=None,
    )
    launch_result = SimpleNamespace(
        plist_path=Path("/tmp/io.sidepulse.agentstatus.plist"),
        changed=False,
        started=True,
    )

    with (
        patch.object(cli, "install_hook_results", return_value=[hook_result]),
        patch("sidepulse.sd_eject_guard_launch.install_sd_eject_guard") as guard,
        patch(
            "sidepulse.status_bar_launch.install_launch_agent",
            return_value=launch_result,
        ) as launch,
    ):
        result = cli.cmd_sidepulse_setup(args)

    assert result == 0
    guard.assert_not_called()
    launch.assert_called_once_with(start=True)


def test_no_sd_eject_guard_still_overrides_an_explicit_guard_request() -> None:
    args = cli.build_sidepulse_parser().parse_args(
        ["setup", "--sd-eject-guard", "--no-sd-eject-guard", "--no-status-bar"]
    )

    with (
        patch.object(cli, "install_hook_results", return_value=[]),
        patch("sidepulse.sd_eject_guard_launch.install_sd_eject_guard") as guard,
    ):
        result = cli.cmd_sidepulse_setup(args)

    assert result == 0
    guard.assert_not_called()
