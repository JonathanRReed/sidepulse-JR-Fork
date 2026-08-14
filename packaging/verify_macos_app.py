#!/usr/bin/env python3
"""Fail-closed verification for a packaged SidePulse.app candidate."""

from __future__ import annotations

import argparse
import os
import plistlib
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sidepulse.trusted_tools import trusted_system_tool

EXPECTED_BUNDLE_IDENTIFIER = "io.sidepulse.app"
EXPECTED_EXECUTABLE_NAME = "SidePulse"
APPLE_LIBRARY_ROOTS = (Path("/System/Library"), Path("/usr/lib"))
DANGEROUS_ENVIRONMENT_PREFIXES = ("PYTHON", "DYLD_")
DANGEROUS_ENVIRONMENT_NAMES = {"LD_LIBRARY_PATH"}

CommandRunner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class BundleVerification:
    accepted: bool
    errors: tuple[str, ...]
    bundle_path: Path
    executable_path: Path | None
    macho_files: tuple[Path, ...] = ()
    dependencies: tuple[str, ...] = ()
    rpaths: tuple[str, ...] = ()
    import_roots: tuple[str, ...] = ()


def verify_packaged_app(
    bundle: Path,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> BundleVerification:
    """Inspect identity, runtime closure, signature, and Mach-O load roots."""
    bundle_path = Path(bundle)
    contents = bundle_path / "Contents"
    info_path = contents / "Info.plist"
    errors: list[str] = []
    executable_path: Path | None = None
    macho_files: list[Path] = []
    dependencies: list[str] = []
    rpaths: list[str] = []
    import_roots: list[str] = []

    if not bundle_path.is_dir():
        errors.append(f"bundle is missing or not a directory: {bundle_path}")
        return BundleVerification(False, tuple(errors), bundle_path, None)

    _inspect_bundle_symlinks(bundle_path, errors)
    payload_files = _bundle_payload_files(bundle_path, errors)
    payload_file_set = frozenset(payload_files)

    info = _read_info_plist(info_path, payload_file_set, errors)
    executable_name = info.get("CFBundleExecutable")
    if info.get("CFBundleIdentifier") != EXPECTED_BUNDLE_IDENTIFIER:
        errors.append(
            "CFBundleIdentifier must be "
            f"{EXPECTED_BUNDLE_IDENTIFIER!r}, got {info.get('CFBundleIdentifier')!r}"
        )
    if executable_name != EXPECTED_EXECUTABLE_NAME:
        errors.append(
            "CFBundleExecutable must be "
            f"{EXPECTED_EXECUTABLE_NAME!r}, got {executable_name!r}"
        )
    if isinstance(executable_name, str):
        executable_path = contents / "MacOS" / executable_name
        if not _is_executable_regular_file(executable_path, payload_file_set):
            errors.append(f"bundle executable is missing or not executable: {executable_path}")

    environment = info.get("LSEnvironment")
    if environment is not None and not isinstance(environment, dict):
        errors.append("LSEnvironment must be a dictionary when present")
    elif isinstance(environment, dict):
        for raw_name, value in environment.items():
            name = str(raw_name).upper()
            if name.startswith(DANGEROUS_ENVIRONMENT_PREFIXES) or name in DANGEROUS_ENVIRONMENT_NAMES:
                errors.append(f"dangerous LSEnvironment variable {raw_name}={value!r}")

    runtime_files = _internal_runtime_files(payload_files)
    if not runtime_files:
        errors.append("bundle is missing an internal Python runtime payload")

    for import_file in payload_files:
        if import_file.suffix != ".pth" and import_file.name != "pyvenv.cfg":
            continue
        _inspect_import_file(import_file, bundle_path, import_roots, errors)

    codesign = trusted_system_tool("codesign")
    signature = _run(
        command_runner,
        [codesign, "--verify", "--deep", "--strict", str(bundle_path)],
    )
    if signature.returncode != 0:
        detail = _text(signature.stderr).strip()
        errors.append(f"bundle signature verification failed: {detail or 'codesign rejected bundle'}")

    file_tool = trusted_system_tool("file")
    for candidate in payload_files:
        identified = _run(command_runner, [file_tool, "-b", str(candidate)])
        if identified.returncode != 0:
            errors.append(f"file inspection failed for {candidate}")
            continue
        if "Mach-O" in _text(identified.stdout):
            macho_files.append(candidate)

    if runtime_files and not any(path in macho_files for path in runtime_files):
        errors.append("internal Python runtime is not Mach-O")
    if executable_path is not None and executable_path not in macho_files:
        errors.append(f"bundle executable is not a Mach-O file: {executable_path}")

    otool = trusted_system_tool("otool")
    for macho in macho_files:
        linked = _run(command_runner, [otool, "-L", str(macho)])
        if linked.returncode != 0:
            errors.append(f"otool dependency inspection failed for {macho}")
        else:
            for dependency in _parse_otool_dependencies(_text(linked.stdout)):
                dependencies.append(dependency)
                if not _dependency_is_allowed(
                    dependency,
                    bundle_path,
                    loader=macho,
                    executable=executable_path,
                ):
                    errors.append(f"external Mach-O dependency in {macho}: {dependency}")

        load_commands = _run(command_runner, [otool, "-l", str(macho)])
        if load_commands.returncode != 0:
            errors.append(f"otool load-command inspection failed for {macho}")
        else:
            for rpath in _parse_rpaths(_text(load_commands.stdout)):
                rpaths.append(rpath)
                if not _rpath_is_allowed(
                    rpath,
                    bundle_path,
                    loader=macho,
                    executable=executable_path,
                ):
                    errors.append(f"external LC_RPATH in {macho}: {rpath}")

    return BundleVerification(
        accepted=not errors,
        errors=tuple(errors),
        bundle_path=bundle_path,
        executable_path=executable_path,
        macho_files=tuple(macho_files),
        dependencies=tuple(dict.fromkeys(dependencies)),
        rpaths=tuple(dict.fromkeys(rpaths)),
        import_roots=tuple(dict.fromkeys(import_roots)),
    )


def _read_info_plist(
    path: Path,
    payload_files: frozenset[Path],
    errors: list[str],
) -> dict:
    if path not in payload_files:
        errors.append(f"bundle Info.plist is missing or not a safe regular payload: {path}")
        return {}
    try:
        value = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        errors.append(f"cannot read bundle Info.plist: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append("bundle Info.plist root must be a dictionary")
        return {}
    return value


def _is_executable_regular_file(path: Path, payload_files: frozenset[Path]) -> bool:
    if path not in payload_files:
        return False
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and bool(mode & 0o111)


def _inspect_bundle_symlinks(bundle: Path, errors: list[str]) -> None:
    root = bundle.resolve()
    for path in bundle.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            errors.append(f"broken bundle symlink {path}: {exc}")
            continue
        if resolved != root and root not in resolved.parents:
            errors.append(f"bundle symlink escapes candidate: {path} -> {resolved}")


def _bundle_payload_files(bundle: Path, errors: list[str]) -> tuple[Path, ...]:
    payload_files: list[Path] = []
    for path in sorted(bundle.rglob("*")):
        try:
            metadata = path.lstat()
        except OSError as exc:
            errors.append(f"cannot inspect bundle payload {path}: {exc}")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        if metadata.st_nlink != 1:
            errors.append(
                f"bundle payload has unsafe hard-link count {metadata.st_nlink}: {path}"
            )
            continue
        payload_files.append(path)
    return tuple(payload_files)


def _internal_runtime_files(payload_files: Sequence[Path]) -> tuple[Path, ...]:
    matches: list[Path] = []
    for path in payload_files:
        if path.name == "Python" and "Python.framework" in path.parts:
            matches.append(path)
        elif path.name.startswith("libpython") and path.suffix == ".dylib":
            matches.append(path)
    return tuple(matches)


def _inspect_import_file(
    path: Path,
    bundle: Path,
    roots: list[str],
    errors: list[str],
) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        errors.append(f"cannot inspect import-root file {path}: {exc}")
        return
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value == "import" or value.startswith(("import ", "import\t")):
            errors.append(f"executable .pth directive is not allowed in {path}: {value}")
            continue
        if "=" in value and path.name == "pyvenv.cfg":
            _, value = value.split("=", 1)
            value = value.strip()
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            inspected_root = candidate
            roots.append(value)
        else:
            inspected_root = (path.parent / candidate).resolve(strict=False)
            roots.append(str(inspected_root))
        if not _absolute_path_is_allowed(inspected_root, bundle):
            errors.append(f"external Python import root in {path}: {value}")


def _dependency_is_allowed(
    dependency: str,
    bundle: Path,
    *,
    loader: Path,
    executable: Path | None,
) -> bool:
    if dependency.startswith("@"):
        return _loader_reference_is_allowed(
            dependency,
            bundle=bundle,
            loader=loader,
            executable=executable,
            tokens=("@executable_path", "@loader_path", "@rpath"),
        )
    path = Path(dependency)
    return path.is_absolute() and _absolute_path_is_allowed(path, bundle)


def _rpath_is_allowed(
    rpath: str,
    bundle: Path,
    *,
    loader: Path,
    executable: Path | None,
) -> bool:
    if rpath.startswith("@"):
        return _loader_reference_is_allowed(
            rpath,
            bundle=bundle,
            loader=loader,
            executable=executable,
            tokens=("@executable_path", "@loader_path"),
        )
    path = Path(rpath)
    return path.is_absolute() and _absolute_path_is_allowed(path, bundle)


def _loader_reference_is_allowed(
    value: str,
    *,
    bundle: Path,
    loader: Path,
    executable: Path | None,
    tokens: tuple[str, ...],
) -> bool:
    for token in tokens:
        if value == token:
            suffix = ""
        elif value.startswith(f"{token}/"):
            suffix = value[len(token) + 1 :]
        else:
            continue
        if suffix.startswith("/"):
            return False
        if token == "@rpath":
            return bool(suffix) and ".." not in Path(suffix).parts
        base = executable.parent if token == "@executable_path" and executable else loader.parent
        return _path_is_inside_bundle((base / suffix).resolve(strict=False), bundle)
    return False


def _absolute_path_is_allowed(path: Path, bundle: Path) -> bool:
    normalized = Path(os.path.normpath(str(path)))
    if any(
        normalized == allowed or allowed in normalized.parents
        for allowed in APPLE_LIBRARY_ROOTS
    ):
        return True
    candidate = normalized.resolve(strict=False)
    return _path_is_inside_bundle(candidate, bundle)


def _path_is_inside_bundle(path: Path, bundle: Path) -> bool:
    root = bundle.resolve(strict=False)
    return path == root or root in path.parents


def _parse_otool_dependencies(output: str) -> tuple[str, ...]:
    lines = output.splitlines()[1:]
    return tuple(line.strip().split(" (", 1)[0] for line in lines if line.strip())


def _parse_rpaths(output: str) -> tuple[str, ...]:
    lines = output.splitlines()
    result: list[str] = []
    saw_rpath = False
    for line in lines:
        stripped = line.strip()
        if stripped == "cmd LC_RPATH":
            saw_rpath = True
            continue
        if saw_rpath and stripped.startswith("path "):
            result.append(stripped[5:].split(" (offset ", 1)[0])
            saw_rpath = False
    return tuple(result)


def _run(command_runner: CommandRunner, command: Sequence[object]) -> subprocess.CompletedProcess:
    try:
        return command_runner(
            [str(part) for part in command],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            [str(part) for part in command],
            1,
            "",
            str(exc),
        )


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    result = verify_packaged_app(args.bundle)
    if result.accepted:
        print(f"accepted: {result.bundle_path}")
        print(f"Mach-O files inspected: {len(result.macho_files)}")
        print(f"dependencies inspected: {len(result.dependencies)}")
        print(f"rpaths inspected: {len(result.rpaths)}")
        print(f"import roots inspected: {len(result.import_roots)}")
        return 0
    print(f"rejected: {result.bundle_path}")
    for error in result.errors:
        print(f"error: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
