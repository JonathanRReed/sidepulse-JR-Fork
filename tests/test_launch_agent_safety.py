import subprocess
from pathlib import Path

from sidepulse import status_bar_launch


def test_launch_agent_declares_restart_throttle_and_exit_timeout() -> None:
    plist = status_bar_launch.build_launch_agent_plist(
        python_executable=Path("/usr/bin/python3")
    )

    assert plist["ThrottleInterval"] == status_bar_launch.LAUNCH_AGENT_THROTTLE_SECONDS
    assert plist["ExitTimeOut"] == status_bar_launch.LAUNCH_AGENT_EXIT_TIMEOUT_SECONDS
    assert plist["ProcessType"] == "Interactive"


def test_all_launchctl_operations_have_strict_timeouts(monkeypatch) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(status_bar_launch.subprocess, "run", run)
    monkeypatch.setattr(
        status_bar_launch._legacy,
        "trusted_system_tool",
        lambda _name: Path("/bin/launchctl"),
    )

    target = Path("/tmp/io.sidepulse.agentstatus.plist")
    status_bar_launch.restart_launch_agent(target)
    assert status_bar_launch.launch_agent_running() is True
    status_bar_launch.bootout_launch_agent(target)

    assert calls
    assert all(
        call[1]["timeout"] == status_bar_launch.LAUNCHCTL_TIMEOUT_SECONDS
        for call in calls
    )
    assert all(call[1]["stdin"] is subprocess.DEVNULL for call in calls)


def test_launch_agent_running_fails_closed_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        status_bar_launch,
        "_launchctl_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["launchctl"], 15)
        ),
    )

    assert status_bar_launch.launch_agent_running() is False
