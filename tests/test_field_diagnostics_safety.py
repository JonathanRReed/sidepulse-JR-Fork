from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "field-diagnostics.sh"


def test_field_diagnostics_sanitizes_bounded_log_and_firmware_output(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".local" / "state" / "sidepulse" / "agent-monitor"
    state.mkdir(parents=True)
    log_line = (
        b"state=ready serial=private-device "
        b"\x1b]52;c;Y29weS10aGlz\x07 "
        + str(home).encode()
        + b" "
        + b"A" * 1_000
        + b"\n"
    )
    (state / "status-bar.out.log").write_bytes(log_line)

    volumes = tmp_path / "Volumes"
    volume = volumes / "SidePulse-test"
    volume.mkdir(parents=True)
    (volume / "STATUS.TXT").write_bytes(
        b"firmware_version=1.2.3\x1b]52;c;ZmlybXdhcmU=\x07"
        + b"B" * 1_000
        + b"\n"
        + b"serial=private-firmware-serial\n"
    )

    environment = os.environ.copy()
    environment.update(
        HOME=str(home),
        SIDEPULSE_TEST_VOLUME_ROOT=str(volumes),
        LC_ALL="C",
    )
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT)],
        check=True,
        capture_output=True,
        env=environment,
        timeout=10,
    )

    output = result.stdout
    assert b"\x1b" not in output
    assert b"\x07" not in output
    assert b"private-device" not in output
    assert b"private-firmware-serial" not in output
    assert str(home).encode() not in output
    assert b"state=ready serial=[redacted]" in output
    assert b"firmware_version=1.2.3?" in output
    assert max(map(len, output.splitlines())) <= 512
