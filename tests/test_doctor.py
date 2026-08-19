from __future__ import annotations

import json
import stat
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest.mock import patch

import pytest

from sidepulse.cli import sidepulse_main
from sidepulse.doctor import (
    DIAGNOSTIC_MANIFEST,
    DOCTOR_VERSION,
    MAX_DOCTOR_EXPORT_BYTES,
    PUBLIC_COLLECTION_ERROR_MESSAGE,
    DiagnosticCheck,
    DiagnosticCode,
    DiagnosticFinding,
    DiagnosticManifest,
    DiagnosticProbe,
    DiagnosticResult,
    DoctorExportError,
    SanitizedFailureClass,
    collect_diagnostics,
    encode_diagnostic_result,
    render_diagnostic_result,
    write_diagnostic_export,
)
from sidepulse.private_export import PUBLIC_EXPORT_ERROR_MESSAGE, PrivateExportError


def _finding(
    check: DiagnosticCheck,
    code: DiagnosticCode,
    count: int,
    limit: int,
) -> DiagnosticFinding:
    return DiagnosticFinding(check=check, code=code, count=count, limit=limit)


def _result() -> DiagnosticResult:
    return DiagnosticResult(
        manifest_version=DOCTOR_VERSION,
        findings=(
            _finding(DiagnosticCheck.PACKAGE_IMPORT_ROOT, DiagnosticCode.SOURCE_CHECKOUT, 1, 1),
            _finding(DiagnosticCheck.SIGNATURE_STATE, DiagnosticCode.NOT_APPLICABLE, 0, 1),
            _finding(DiagnosticCheck.LAUNCH_AGENT_STATE, DiagnosticCode.MISSING, 0, 1),
            _finding(DiagnosticCheck.PRIVATE_PATH_MODES, DiagnosticCode.PRIVATE, 3, 3),
            _finding(DiagnosticCheck.HOOK_DETECTOR_STATE, DiagnosticCode.PARTIAL, 2, 8),
            _finding(DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH, DiagnosticCode.HEALTHY, 17, 17),
            _finding(DiagnosticCheck.WORKER_REGISTRY_BOUNDS, DiagnosticCode.BOUNDED, 32, 32),
            _finding(DiagnosticCheck.TIMER_REGISTRY_BOUNDS, DiagnosticCode.BOUNDED, 22, 64),
            _finding(DiagnosticCheck.MOUNTED_DEVICE_HEALTH, DiagnosticCode.DISCONNECTED, 0, 16),
            _finding(DiagnosticCheck.ALCOVE_FOLLOW_STATE, DiagnosticCode.NOT_PERMITTED, 0, 1),
            _finding(DiagnosticCheck.EVENT_INTAKE_FRESHNESS, DiagnosticCode.HEALTHY, 1, 1),
        ),
        last_failure_class=SanitizedFailureClass.NONE,
    )


def test_manifest_and_result_are_frozen_exact_and_bounded() -> None:
    assert isinstance(DIAGNOSTIC_MANIFEST, DiagnosticManifest)
    # Adding a check changes the exported document's shape, so the version
    # moves with it -- a v1 reader must not silently miss a whole row.
    assert DIAGNOSTIC_MANIFEST.version == DOCTOR_VERSION == 3
    assert tuple(field.check for field in DIAGNOSTIC_MANIFEST.fields) == tuple(DiagnosticCheck)
    assert tuple(field.name for field in fields(DiagnosticResult)) == (
        "manifest_version",
        "findings",
        "last_failure_class",
    )
    result = _result()

    with pytest.raises(FrozenInstanceError):
        result.manifest_version = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        DIAGNOSTIC_MANIFEST.version = 2  # type: ignore[misc]

    with pytest.raises(ValueError, match="count"):
        _finding(DiagnosticCheck.MOUNTED_DEVICE_HEALTH, DiagnosticCode.CONNECTED, 17, 17)
    with pytest.raises(ValueError, match="code"):
        _finding(DiagnosticCheck.MOUNTED_DEVICE_HEALTH, DiagnosticCode.VERIFIED, 1, 1)
    with pytest.raises(ValueError, match="order"):
        DiagnosticResult(
            DOCTOR_VERSION,
            tuple(reversed(result.findings)),
            SanitizedFailureClass.NONE,
        )
    with pytest.raises(ValueError, match="manifest fields"):
        DiagnosticManifest(DOCTOR_VERSION, (object(),))  # type: ignore[arg-type]


def test_encoding_is_exact_deterministic_and_contains_only_codes_and_counts() -> None:
    encoded = encode_diagnostic_result(_result())
    document = json.loads(encoded)

    assert encoded.endswith(b"\n")
    assert len(encoded) <= MAX_DOCTOR_EXPORT_BYTES
    assert set(document) == {
        "document",
        "findings",
        "last_failure_class",
        "version",
    }
    assert document["document"] == "sidepulse-doctor"
    assert document["version"] == DOCTOR_VERSION
    assert document["last_failure_class"] == "none"
    assert document["findings"][0] == {
        "check": "package_import_root",
        "code": "source_checkout",
        "count": 1,
        "limit": 1,
    }
    assert tuple(row["check"] for row in document["findings"]) == tuple(
        check.value for check in DiagnosticCheck
    )


def test_collection_sanitizes_probe_failures_and_never_copies_private_values() -> None:
    private_corpus = (
        "/Users/private-user/secret/project",
        "private-user@example.com",
        "host.private.example",
        "Bearer private-token",
        "rm -rf private-project",
        "private prompt and provider error",
        "--token private-token",
    )

    def denied_probe() -> DiagnosticFinding:
        raise PermissionError(" ".join(private_corpus))

    safe_findings = _result().findings
    probes = tuple(
        DiagnosticProbe(
            finding.check,
            denied_probe if finding.check is DiagnosticCheck.HOOK_DETECTOR_STATE else (lambda row=finding: row),
        )
        for finding in safe_findings
    )

    result = collect_diagnostics(probes=probes)
    encoded = encode_diagnostic_result(result).decode("ascii")
    rendered = render_diagnostic_result(result)

    assert result.last_failure_class is SanitizedFailureClass.PERMISSION_DENIED
    assert result.finding(DiagnosticCheck.HOOK_DETECTOR_STATE).code is DiagnosticCode.UNAVAILABLE
    for value in private_corpus:
        assert value not in encoded
        assert value not in rendered


def test_default_collection_uses_only_read_only_local_probes(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    launch_agent = tmp_path / "missing.plist"

    with (
        patch("sidepulse.doctor.running_inside_bundle", return_value=False),
        patch("sidepulse.doctor.launch_agent_path", return_value=launch_agent),
        patch("sidepulse.doctor.default_state_dir", return_value=state),
        patch("sidepulse.doctor.default_log_path", side_effect=lambda provider: state / f"{provider}.jsonl"),
        patch("sidepulse.doctor.detect_provider_configs", return_value=[]),
        patch("sidepulse.doctor.negotiated_provider_sources", return_value=()),
        patch("sidepulse.doctor.discover_devices", return_value=[]),
        # Otherwise this probe reads the real settings file and the live
        # window list, and the assertion below would depend on whether
        # Alcove happens to be running on the machine under test.
        patch("sidepulse.doctor._alcove_following_enabled", return_value=False),
        patch("sidepulse.doctor.subprocess.run") as run,
    ):
        result = collect_diagnostics()

    assert result.finding(DiagnosticCheck.PACKAGE_IMPORT_ROOT).code is DiagnosticCode.SOURCE_CHECKOUT
    assert result.finding(DiagnosticCheck.SIGNATURE_STATE).code is DiagnosticCode.NOT_APPLICABLE
    assert result.finding(DiagnosticCheck.LAUNCH_AGENT_STATE).code is DiagnosticCode.MISSING
    assert result.finding(DiagnosticCheck.PRIVATE_PATH_MODES).code is DiagnosticCode.PRIVATE
    assert result.finding(DiagnosticCheck.HOOK_DETECTOR_STATE).code is DiagnosticCode.NOT_CONFIGURED
    assert result.finding(DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH).code is DiagnosticCode.UNAVAILABLE
    assert result.finding(DiagnosticCheck.WORKER_REGISTRY_BOUNDS).code is DiagnosticCode.BOUNDED
    assert result.finding(DiagnosticCheck.TIMER_REGISTRY_BOUNDS).code is DiagnosticCode.BOUNDED
    assert result.finding(DiagnosticCheck.MOUNTED_DEVICE_HEALTH).code is DiagnosticCode.DISCONNECTED
    assert result.finding(DiagnosticCheck.ALCOVE_FOLLOW_STATE).code is DiagnosticCode.NOT_CONFIGURED
    assert result.last_failure_class is SanitizedFailureClass.NONE
    run.assert_not_called()


def test_private_export_writes_one_exact_0600_json_leaf(tmp_path: Path) -> None:
    parent = tmp_path / "selected"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    target = parent / "SidePulse-Doctor.json"
    result = _result()

    assert write_diagnostic_export(target, result) == target

    assert target.read_bytes() == encode_diagnostic_result(result)
    assert stat.S_IMODE(parent.lstat().st_mode) == 0o755
    assert stat.S_IMODE(target.lstat().st_mode) == 0o600
    assert list(parent.iterdir()) == [target]


def test_private_export_failure_has_stable_path_free_public_copy(tmp_path: Path) -> None:
    raw = f"could not replace {tmp_path}/private-user-token.json"
    with (
        patch("sidepulse.doctor.write_private_export", side_effect=PrivateExportError(raw)),
        pytest.raises(DoctorExportError) as raised,
    ):
        write_diagnostic_export(tmp_path / "doctor.json", _result())

    assert raised.value.public_message == PUBLIC_EXPORT_ERROR_MESSAGE
    assert raw not in raised.value.public_message
    assert raised.value.__cause__ is None


def test_sidepulse_doctor_cli_json_and_export_never_print_private_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "private-user" / "doctor.json"
    with (
        patch("sidepulse.cli.collect_diagnostics", return_value=_result()),
        patch("sidepulse.cli.write_diagnostic_export", return_value=target),
    ):
        exit_code = sidepulse_main(["doctor", "--json", "--export", str(target)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out.splitlines()[0])["document"] == "sidepulse-doctor"
    assert "diagnostic export: saved" in captured.out
    assert str(target) not in captured.out
    assert captured.err == ""


def test_sidepulse_doctor_cli_uses_stable_public_collection_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = f"provider error for /Users/private-user at {tmp_path}"
    with patch("sidepulse.cli.collect_diagnostics", side_effect=RuntimeError(raw)):
        exit_code = sidepulse_main(["doctor"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == f"sidepulse doctor: {PUBLIC_COLLECTION_ERROR_MESSAGE}"
    assert raw not in captured.err


def test_sidepulse_doctor_cli_sanitizes_encoding_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = f"encoding failed for /Users/private-user at {tmp_path}"
    with (
        patch("sidepulse.cli.collect_diagnostics", return_value=_result()),
        patch("sidepulse.cli.encode_diagnostic_result", side_effect=RuntimeError(raw)),
    ):
        exit_code = sidepulse_main(["doctor", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == f"sidepulse doctor: {PUBLIC_COLLECTION_ERROR_MESSAGE}"
    assert raw not in captured.err
