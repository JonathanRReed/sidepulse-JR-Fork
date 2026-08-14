"""Validated absolute paths for Apple-owned system tools.

Account-owned tools, including Codex and user-selected applications, do not
belong in this allowlist. Callers must resolve those separately and pass an
explicit absolute path.
"""

from __future__ import annotations

import stat
from pathlib import Path

TRUSTED_SYSTEM_TOOL_PATHS: dict[str, Path] = {
    "clang": Path("/usr/bin/clang"),
    "codesign": Path("/usr/bin/codesign"),
    "file": Path("/usr/bin/file"),
    "ioreg": Path("/usr/sbin/ioreg"),
    "launchctl": Path("/bin/launchctl"),
    "open": Path("/usr/bin/open"),
    "osascript": Path("/usr/bin/osascript"),
    "otool": Path("/usr/bin/otool"),
    "security": Path("/usr/bin/security"),
    "shortcuts": Path("/usr/bin/shortcuts"),
    "system_profiler": Path("/usr/sbin/system_profiler"),
    "tail": Path("/usr/bin/tail"),
}


def trusted_system_tool(name: str) -> Path:
    """Return a validated Apple system-tool path or fail closed."""
    try:
        path = TRUSTED_SYSTEM_TOOL_PATHS[name]
    except KeyError as exc:
        raise ValueError(f"{name!r} is not an allowed Apple system tool") from exc

    if not path.is_absolute():
        raise ValueError(f"trusted tool path must be absolute: {path}")

    try:
        path_stat = path.lstat()
    except FileNotFoundError as exc:
        raise OSError(f"trusted system tool is missing: {path}") from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise OSError(f"trusted system tool must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise OSError(f"trusted system tool must be a regular file: {path}")
    if path_stat.st_uid != 0:
        raise OSError(f"trusted system tool must be root-owned: {path}")
    if path_stat.st_mode & 0o022:
        raise OSError(f"trusted system tool must not be group/world-writable: {path}")
    if not path_stat.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise OSError(f"trusted system tool must be executable: {path}")

    ancestor = path.parent
    while True:
        try:
            ancestor_stat = ancestor.lstat()
        except FileNotFoundError as exc:
            raise OSError(f"trusted system tool ancestor is missing: {ancestor}") from exc
        if stat.S_ISLNK(ancestor_stat.st_mode):
            raise OSError(f"trusted system tool ancestor must not be a symlink: {ancestor}")
        if not stat.S_ISDIR(ancestor_stat.st_mode):
            raise OSError(f"trusted system tool ancestor must be a directory: {ancestor}")
        if ancestor_stat.st_uid != 0:
            raise OSError(f"trusted system tool ancestor must be root-owned: {ancestor}")
        if ancestor_stat.st_mode & 0o022:
            raise OSError(
                f"trusted system tool ancestor must not be group/world-writable: {ancestor}"
            )
        if ancestor == ancestor.parent:
            break
        ancestor = ancestor.parent

    return path
