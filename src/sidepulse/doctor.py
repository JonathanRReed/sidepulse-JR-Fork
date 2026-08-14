"""Typed, bounded, content-free diagnostics for local SidePulse health."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from .app_bundle import running_inside_bundle
from .device_writer import discover_devices
from .private_export import (
    PUBLIC_EXPORT_ERROR_MESSAGE,
    PrivateExportError,
    write_private_export,
)
from .providers import (
    HOOK_PROVIDERS,
    default_log_path,
    default_state_dir,
    detect_provider_configs,
    negotiated_provider_sources,
)
from .runtime_scheduler import MAX_RUNTIME_PENDING_KEYS, RuntimeFeature
from .status_bar_launch import launch_agent_path
from .trusted_tools import trusted_system_tool

DOCTOR_DOCUMENT: Final = "sidepulse-doctor"
DOCTOR_VERSION: Final = 1
MAX_DOCTOR_EXPORT_BYTES: Final = 64 * 1024
PUBLIC_COLLECTION_ERROR_MESSAGE: Final = "Diagnostics could not be collected."


class DiagnosticCheck(str, Enum):
    PACKAGE_IMPORT_ROOT = "package_import_root"
    SIGNATURE_STATE = "signature_state"
    LAUNCH_AGENT_STATE = "launch_agent_state"
    PRIVATE_PATH_MODES = "private_path_modes"
    HOOK_DETECTOR_STATE = "hook_detector_state"
    NEGOTIATED_SOURCE_HEALTH = "negotiated_source_health"
    WORKER_REGISTRY_BOUNDS = "worker_registry_bounds"
    TIMER_REGISTRY_BOUNDS = "timer_registry_bounds"
    MOUNTED_DEVICE_HEALTH = "mounted_device_health"


class DiagnosticCode(str, Enum):
    SOURCE_CHECKOUT = "source_checkout"
    INSTALLED_PACKAGE = "installed_package"
    PACKAGED_BUNDLE = "packaged_bundle"
    NOT_APPLICABLE = "not_applicable"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    INSTALLED = "installed"
    MISSING = "missing"
    UNSAFE = "unsafe"
    PRIVATE = "private"
    PERMISSIVE = "permissive"
    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"
    HEALTHY = "healthy"
    PARTIAL = "partial"
    BOUNDED = "bounded"
    EXCEEDED = "exceeded"
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    AMBIGUOUS = "ambiguous"
    UNAVAILABLE = "unavailable"


class SanitizedFailureClass(str, Enum):
    NONE = "none"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    TIMED_OUT = "timed_out"
    INVALID_DATA = "invalid_data"
    UNAVAILABLE = "unavailable"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class DiagnosticFieldManifest:
    check: DiagnosticCheck
    allowed_codes: tuple[DiagnosticCode, ...]
    max_count: int

    def __post_init__(self) -> None:
        if (
            type(self.check) is not DiagnosticCheck
            or type(self.allowed_codes) is not tuple
            or not self.allowed_codes
            or not all(type(code) is DiagnosticCode for code in self.allowed_codes)
            or len(set(self.allowed_codes)) != len(self.allowed_codes)
            or type(self.max_count) is not int
            or self.max_count <= 0
        ):
            raise ValueError("invalid diagnostic field manifest")


@dataclass(frozen=True, slots=True)
class DiagnosticManifest:
    version: int
    fields: tuple[DiagnosticFieldManifest, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version <= 0:
            raise ValueError("invalid diagnostic manifest version")
        if (
            type(self.fields) is not tuple
            or not all(type(field) is DiagnosticFieldManifest for field in self.fields)
            or tuple(field.check for field in self.fields) != tuple(DiagnosticCheck)
        ):
            raise ValueError("invalid diagnostic manifest fields")


DIAGNOSTIC_MANIFEST: Final = DiagnosticManifest(
    version=DOCTOR_VERSION,
    fields=(
        DiagnosticFieldManifest(
            DiagnosticCheck.PACKAGE_IMPORT_ROOT,
            (
                DiagnosticCode.SOURCE_CHECKOUT,
                DiagnosticCode.INSTALLED_PACKAGE,
                DiagnosticCode.PACKAGED_BUNDLE,
                DiagnosticCode.UNAVAILABLE,
            ),
            1,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.SIGNATURE_STATE,
            (
                DiagnosticCode.NOT_APPLICABLE,
                DiagnosticCode.VERIFIED,
                DiagnosticCode.UNVERIFIED,
                DiagnosticCode.UNAVAILABLE,
            ),
            1,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.LAUNCH_AGENT_STATE,
            (
                DiagnosticCode.INSTALLED,
                DiagnosticCode.MISSING,
                DiagnosticCode.UNSAFE,
                DiagnosticCode.UNAVAILABLE,
            ),
            1,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.PRIVATE_PATH_MODES,
            (
                DiagnosticCode.PRIVATE,
                DiagnosticCode.PERMISSIVE,
                DiagnosticCode.MISSING,
                DiagnosticCode.UNSAFE,
                DiagnosticCode.UNAVAILABLE,
            ),
            32,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.HOOK_DETECTOR_STATE,
            (
                DiagnosticCode.CONFIGURED,
                DiagnosticCode.PARTIAL,
                DiagnosticCode.NOT_CONFIGURED,
                DiagnosticCode.UNAVAILABLE,
            ),
            32,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH,
            (
                DiagnosticCode.HEALTHY,
                DiagnosticCode.PARTIAL,
                DiagnosticCode.UNAVAILABLE,
            ),
            32,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.WORKER_REGISTRY_BOUNDS,
            (
                DiagnosticCode.BOUNDED,
                DiagnosticCode.EXCEEDED,
                DiagnosticCode.UNAVAILABLE,
            ),
            32,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.TIMER_REGISTRY_BOUNDS,
            (
                DiagnosticCode.BOUNDED,
                DiagnosticCode.EXCEEDED,
                DiagnosticCode.UNAVAILABLE,
            ),
            64,
        ),
        DiagnosticFieldManifest(
            DiagnosticCheck.MOUNTED_DEVICE_HEALTH,
            (
                DiagnosticCode.DISCONNECTED,
                DiagnosticCode.CONNECTED,
                DiagnosticCode.AMBIGUOUS,
                DiagnosticCode.UNAVAILABLE,
            ),
            16,
        ),
    ),
)

_FIELD_BY_CHECK: Final = {field.check: field for field in DIAGNOSTIC_MANIFEST.fields}


@dataclass(frozen=True, slots=True)
class DiagnosticFinding:
    check: DiagnosticCheck
    code: DiagnosticCode
    count: int
    limit: int

    def __post_init__(self) -> None:
        if type(self.check) is not DiagnosticCheck or type(self.code) is not DiagnosticCode:
            raise ValueError("invalid diagnostic finding code")
        field = _FIELD_BY_CHECK[self.check]
        if self.code not in field.allowed_codes:
            raise ValueError("diagnostic code is not allowed for check")
        if (
            type(self.count) is not int
            or type(self.limit) is not int
            or self.count < 0
            or self.limit < 0
            or self.count > self.limit
            or self.limit > field.max_count
        ):
            raise ValueError("diagnostic count is outside its bound")


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    manifest_version: int
    findings: tuple[DiagnosticFinding, ...]
    last_failure_class: SanitizedFailureClass

    def __post_init__(self) -> None:
        if self.manifest_version != DIAGNOSTIC_MANIFEST.version:
            raise ValueError("invalid diagnostic result version")
        if (
            type(self.findings) is not tuple
            or not all(type(finding) is DiagnosticFinding for finding in self.findings)
            or tuple(finding.check for finding in self.findings) != tuple(DiagnosticCheck)
        ):
            raise ValueError("diagnostic findings are not in manifest order")
        if type(self.last_failure_class) is not SanitizedFailureClass:
            raise ValueError("invalid sanitized failure class")

    def finding(self, check: DiagnosticCheck) -> DiagnosticFinding:
        if type(check) is not DiagnosticCheck:
            raise ValueError("invalid diagnostic check")
        return self.findings[tuple(DiagnosticCheck).index(check)]


@dataclass(frozen=True, slots=True)
class DiagnosticProbe:
    check: DiagnosticCheck
    run: Callable[[], DiagnosticFinding]

    def __post_init__(self) -> None:
        if type(self.check) is not DiagnosticCheck or not callable(self.run):
            raise ValueError("invalid diagnostic probe")


class DoctorExportError(OSError):
    """A diagnostic export failed without exposing its selected destination."""

    @property
    def public_message(self) -> str:
        return PUBLIC_EXPORT_ERROR_MESSAGE


def _finding(
    check: DiagnosticCheck,
    code: DiagnosticCode,
    count: int,
    limit: int,
) -> DiagnosticFinding:
    return DiagnosticFinding(check=check, code=code, count=count, limit=limit)


def _package_import_root_probe() -> DiagnosticFinding:
    if running_inside_bundle():
        code = DiagnosticCode.PACKAGED_BUNDLE
    elif any(part in {"site-packages", "dist-packages"} for part in Path(__file__).parts):
        code = DiagnosticCode.INSTALLED_PACKAGE
    else:
        code = DiagnosticCode.SOURCE_CHECKOUT
    return _finding(DiagnosticCheck.PACKAGE_IMPORT_ROOT, code, 1, 1)


def _signature_state_probe() -> DiagnosticFinding:
    if not running_inside_bundle():
        return _finding(
            DiagnosticCheck.SIGNATURE_STATE,
            DiagnosticCode.NOT_APPLICABLE,
            0,
            1,
        )
    executable = Path(sys.executable or "")
    if executable.name != "SidePulse" or len(executable.parents) < 3:
        return _finding(
            DiagnosticCheck.SIGNATURE_STATE,
            DiagnosticCode.UNVERIFIED,
            0,
            1,
        )
    bundle = executable.parents[2]
    completed = subprocess.run(
        [str(trusted_system_tool("codesign")), "--verify", "--strict", str(bundle)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )
    verified = completed.returncode == 0
    return _finding(
        DiagnosticCheck.SIGNATURE_STATE,
        DiagnosticCode.VERIFIED if verified else DiagnosticCode.UNVERIFIED,
        int(verified),
        1,
    )


def _launch_agent_state_probe() -> DiagnosticFinding:
    path = launch_agent_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _finding(
            DiagnosticCheck.LAUNCH_AGENT_STATE,
            DiagnosticCode.MISSING,
            0,
            1,
        )
    safe = (
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.geteuid()
        and not info.st_mode & 0o022
    )
    return _finding(
        DiagnosticCheck.LAUNCH_AGENT_STATE,
        DiagnosticCode.INSTALLED if safe else DiagnosticCode.UNSAFE,
        int(safe),
        1,
    )


def _owned_private_paths() -> tuple[tuple[Path, int, Callable[[int], bool]], ...]:
    state = default_state_dir()
    selected: list[tuple[Path, int, Callable[[int], bool]]] = [
        (state, 0o700, stat.S_ISDIR),
    ]
    for provider in HOOK_PROVIDERS:
        path = default_log_path(provider)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        selected.append((path, 0o600, stat.S_ISREG))
    return tuple(selected[: _FIELD_BY_CHECK[DiagnosticCheck.PRIVATE_PATH_MODES].max_count])


def _private_path_modes_probe() -> DiagnosticFinding:
    paths = _owned_private_paths()
    private_count = 0
    permissive = False
    unsafe = False
    missing = False
    for path, expected_mode, expected_type in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            missing = True
            continue
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode) or not expected_type(info.st_mode) or info.st_uid != os.geteuid():
            unsafe = True
        elif mode == expected_mode:
            private_count += 1
        elif mode & 0o077:
            permissive = True
        else:
            unsafe = True
    if unsafe:
        code = DiagnosticCode.UNSAFE
    elif permissive:
        code = DiagnosticCode.PERMISSIVE
    elif missing:
        code = DiagnosticCode.MISSING
    else:
        code = DiagnosticCode.PRIVATE
    return _finding(
        DiagnosticCheck.PRIVATE_PATH_MODES,
        code,
        private_count,
        len(paths),
    )


def _hook_detector_state_probe() -> DiagnosticFinding:
    configs = detect_provider_configs()
    maximum = _FIELD_BY_CHECK[DiagnosticCheck.HOOK_DETECTOR_STATE].max_count
    total = min(len(configs), maximum)
    configured = min(sum(bool(config.hooks_enabled) for config in configs), maximum)
    if configured == total and total > 0 and len(configs) <= maximum:
        code = DiagnosticCode.CONFIGURED
    elif configured > 0 or len(configs) > maximum:
        code = DiagnosticCode.PARTIAL
    else:
        code = DiagnosticCode.NOT_CONFIGURED
    return _finding(
        DiagnosticCheck.HOOK_DETECTOR_STATE,
        code,
        min(configured, total),
        total,
    )


def _negotiated_source_health_probe() -> DiagnosticFinding:
    sources = negotiated_provider_sources()
    maximum = _FIELD_BY_CHECK[DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH].max_count
    total = min(len(sources), maximum)
    eligible = min(
        sum(bool(source.observation_invocation_allowed) for source in sources),
        total,
    )
    if eligible == total and total > 0 and len(sources) <= maximum:
        code = DiagnosticCode.HEALTHY
    elif eligible > 0 or len(sources) > maximum:
        code = DiagnosticCode.PARTIAL
    else:
        code = DiagnosticCode.UNAVAILABLE
    return _finding(
        DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH,
        code,
        eligible,
        total,
    )


def _worker_registry_bounds_probe() -> DiagnosticFinding:
    maximum = _FIELD_BY_CHECK[DiagnosticCheck.WORKER_REGISTRY_BOUNDS].max_count
    bounded = MAX_RUNTIME_PENDING_KEYS <= maximum
    return _finding(
        DiagnosticCheck.WORKER_REGISTRY_BOUNDS,
        DiagnosticCode.BOUNDED if bounded else DiagnosticCode.EXCEEDED,
        min(MAX_RUNTIME_PENDING_KEYS, maximum),
        maximum,
    )


def _timer_registry_bounds_probe() -> DiagnosticFinding:
    maximum = _FIELD_BY_CHECK[DiagnosticCheck.TIMER_REGISTRY_BOUNDS].max_count
    count = len(RuntimeFeature)
    bounded = count <= maximum
    return _finding(
        DiagnosticCheck.TIMER_REGISTRY_BOUNDS,
        DiagnosticCode.BOUNDED if bounded else DiagnosticCode.EXCEEDED,
        min(count, maximum),
        maximum,
    )


def _mounted_device_health_probe() -> DiagnosticFinding:
    maximum = _FIELD_BY_CHECK[DiagnosticCheck.MOUNTED_DEVICE_HEALTH].max_count
    devices = discover_devices()
    count = min(len(devices), maximum)
    if not devices:
        code = DiagnosticCode.DISCONNECTED
    elif len(devices) == 1:
        code = DiagnosticCode.CONNECTED
    else:
        code = DiagnosticCode.AMBIGUOUS
    return _finding(
        DiagnosticCheck.MOUNTED_DEVICE_HEALTH,
        code,
        count,
        maximum,
    )


def _default_probes() -> tuple[DiagnosticProbe, ...]:
    return (
        DiagnosticProbe(DiagnosticCheck.PACKAGE_IMPORT_ROOT, _package_import_root_probe),
        DiagnosticProbe(DiagnosticCheck.SIGNATURE_STATE, _signature_state_probe),
        DiagnosticProbe(DiagnosticCheck.LAUNCH_AGENT_STATE, _launch_agent_state_probe),
        DiagnosticProbe(DiagnosticCheck.PRIVATE_PATH_MODES, _private_path_modes_probe),
        DiagnosticProbe(DiagnosticCheck.HOOK_DETECTOR_STATE, _hook_detector_state_probe),
        DiagnosticProbe(DiagnosticCheck.NEGOTIATED_SOURCE_HEALTH, _negotiated_source_health_probe),
        DiagnosticProbe(DiagnosticCheck.WORKER_REGISTRY_BOUNDS, _worker_registry_bounds_probe),
        DiagnosticProbe(DiagnosticCheck.TIMER_REGISTRY_BOUNDS, _timer_registry_bounds_probe),
        DiagnosticProbe(DiagnosticCheck.MOUNTED_DEVICE_HEALTH, _mounted_device_health_probe),
    )


def _sanitized_failure_class(error: Exception) -> SanitizedFailureClass:
    if isinstance(error, PermissionError):
        return SanitizedFailureClass.PERMISSION_DENIED
    if isinstance(error, FileNotFoundError):
        return SanitizedFailureClass.NOT_FOUND
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return SanitizedFailureClass.TIMED_OUT
    if isinstance(error, (TypeError, ValueError, UnicodeError, json.JSONDecodeError)):
        return SanitizedFailureClass.INVALID_DATA
    if isinstance(error, OSError):
        return SanitizedFailureClass.UNAVAILABLE
    return SanitizedFailureClass.INTERNAL


def _unavailable_finding(check: DiagnosticCheck) -> DiagnosticFinding:
    field = _FIELD_BY_CHECK[check]
    return _finding(check, DiagnosticCode.UNAVAILABLE, 0, field.max_count)


def collect_diagnostics(
    *,
    probes: tuple[DiagnosticProbe, ...] | None = None,
) -> DiagnosticResult:
    """Run bounded local probes and discard every exception value."""
    selected = _default_probes() if probes is None else probes
    if (
        type(selected) is not tuple
        or not all(type(probe) is DiagnosticProbe for probe in selected)
        or tuple(probe.check for probe in selected) != tuple(DiagnosticCheck)
    ):
        raise ValueError("diagnostic probes are not in manifest order")
    findings: list[DiagnosticFinding] = []
    last_failure = SanitizedFailureClass.NONE
    for probe in selected:
        try:
            finding = probe.run()
            if type(finding) is not DiagnosticFinding or finding.check is not probe.check:
                raise TypeError("diagnostic probe returned an invalid finding")
        except Exception as error:
            finding = _unavailable_finding(probe.check)
            last_failure = _sanitized_failure_class(error)
        findings.append(finding)
    return DiagnosticResult(
        manifest_version=DIAGNOSTIC_MANIFEST.version,
        findings=tuple(findings),
        last_failure_class=last_failure,
    )


def encode_diagnostic_result(result: DiagnosticResult) -> bytes:
    """Encode only the fixed manifest's product-owned codes and bounded counts."""
    if type(result) is not DiagnosticResult:
        raise ValueError("invalid diagnostic result")
    document = {
        "document": DOCTOR_DOCUMENT,
        "findings": [
            {
                "check": finding.check.value,
                "code": finding.code.value,
                "count": finding.count,
                "limit": finding.limit,
            }
            for finding in result.findings
        ],
        "last_failure_class": result.last_failure_class.value,
        "version": result.manifest_version,
    }
    payload = (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    if len(payload) > MAX_DOCTOR_EXPORT_BYTES:
        raise ValueError("diagnostic export exceeds maximum size")
    return payload


def write_diagnostic_export(path: Path, result: DiagnosticResult) -> Path:
    """Publish one bounded JSON document through the private export boundary."""
    try:
        return write_private_export(
            path,
            encode_diagnostic_result(result),
            max_bytes=MAX_DOCTOR_EXPORT_BYTES,
        )
    except PrivateExportError:
        raise DoctorExportError("diagnostic export failed") from None


def render_diagnostic_result(result: DiagnosticResult) -> str:
    if type(result) is not DiagnosticResult:
        raise ValueError("invalid diagnostic result")
    lines = [f"SidePulse diagnostics (v{result.manifest_version})"]
    lines.extend(
        f"{finding.check.value.replace('_', ' ')}: "
        f"{finding.code.value} [{finding.count}/{finding.limit}]"
        for finding in result.findings
    )
    lines.append(f"last failure class: {result.last_failure_class.value}")
    return "\n".join(lines)


__all__ = [
    "DIAGNOSTIC_MANIFEST",
    "DOCTOR_DOCUMENT",
    "MAX_DOCTOR_EXPORT_BYTES",
    "PUBLIC_COLLECTION_ERROR_MESSAGE",
    "DiagnosticCheck",
    "DiagnosticCode",
    "DiagnosticFieldManifest",
    "DiagnosticFinding",
    "DiagnosticManifest",
    "DiagnosticProbe",
    "DiagnosticResult",
    "DoctorExportError",
    "SanitizedFailureClass",
    "collect_diagnostics",
    "encode_diagnostic_result",
    "render_diagnostic_result",
    "write_diagnostic_export",
]
