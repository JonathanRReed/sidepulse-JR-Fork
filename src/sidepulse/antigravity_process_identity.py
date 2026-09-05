"""Verified identity for the installed Antigravity language server."""

from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path

ANTIGRAVITY_BUNDLE_ID = "com.google.antigravity"
ANTIGRAVITY_TEAM_ID = "EQHXZ8M8AV"
ANTIGRAVITY_APP = Path("/Applications/Antigravity.app")
ANTIGRAVITY_LANGUAGE_SERVER = Path(
    "/Applications/Antigravity.app/Contents/Resources/bin/language_server"
)

AntigravityProcessIdentity = tuple[int, str, int, int, int]

_PROC_PIDTBSDINFO = 3
_PROC_PIDPATHINFO_MAXSIZE = 4096
_PROCESS_REQUIREMENT = (
    'identifier "language_server" and anchor apple generic '
    'and certificate 1[field.1.2.840.113635.100.6.2.6] '
    'and certificate leaf[field.1.2.840.113635.100.6.1.13] '
    f'and certificate leaf[subject.OU] = "{ANTIGRAVITY_TEAM_ID}"'
)


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_int32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _process_facts(pid: int) -> tuple[str, int, int, int] | None:
    if type(pid) is not int or pid <= 0:
        return None
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libproc.proc_pidpath.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        libproc.proc_pidinfo.restype = ctypes.c_int
        path_buffer = ctypes.create_string_buffer(_PROC_PIDPATHINFO_MAXSIZE)
        if libproc.proc_pidpath(pid, path_buffer, len(path_buffer)) <= 0:
            return None
        info = _ProcBsdInfo()
        size = ctypes.sizeof(info)
        if libproc.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), size) != size:
            return None
        path = os.fsdecode(path_buffer.value)
        return (
            path,
            int(info.pbi_uid),
            int(info.pbi_start_tvsec),
            int(info.pbi_start_tvusec),
        )
    except (AttributeError, OSError, ValueError):
        return None


def _running_process_is_trusted(pid: int) -> bool:
    if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
        return False
    owned: list[int] = []
    core = None
    try:
        core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        pointer = ctypes.c_void_p
        core.CFRelease.argtypes = [pointer]
        core.CFRelease.restype = None
        core.CFNumberCreate.argtypes = [pointer, ctypes.c_long, pointer]
        core.CFNumberCreate.restype = pointer
        core.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
        core.CFStringCreateWithCString.restype = pointer
        core.CFDictionaryCreate.argtypes = [
            pointer, ctypes.POINTER(pointer), ctypes.POINTER(pointer),
            ctypes.c_long, pointer, pointer,
        ]
        core.CFDictionaryCreate.restype = pointer
        security.SecRequirementCreateWithString.argtypes = [
            pointer, ctypes.c_uint32, ctypes.POINTER(pointer),
        ]
        security.SecRequirementCreateWithString.restype = ctypes.c_int32
        security.SecCodeCopyGuestWithAttributes.argtypes = [
            pointer, pointer, ctypes.c_uint32, ctypes.POINTER(pointer),
        ]
        security.SecCodeCopyGuestWithAttributes.restype = ctypes.c_int32
        security.SecCodeCheckValidity.argtypes = [pointer, ctypes.c_uint32, pointer]
        security.SecCodeCheckValidity.restype = ctypes.c_int32

        pid_value = ctypes.c_int32(pid)
        pid_number = core.CFNumberCreate(None, 3, ctypes.byref(pid_value))
        if not pid_number:
            return False
        owned.append(pid_number)
        text = core.CFStringCreateWithCString(None, _PROCESS_REQUIREMENT.encode("utf-8"), 0x08000100)
        if not text:
            return False
        owned.append(text)
        requirement = pointer()
        if security.SecRequirementCreateWithString(text, 0, ctypes.byref(requirement)) != 0:
            return False
        owned.append(requirement.value)
        pid_key = pointer.in_dll(security, "kSecGuestAttributePid").value
        # The key is a framework constant and the value stays owned above.
        # Null callbacks avoid transferring or duplicating that ownership.
        attributes = core.CFDictionaryCreate(
            None, (pointer * 1)(pid_key), (pointer * 1)(pid_number), 1, None, None,
        )
        if not attributes:
            return False
        owned.append(attributes)
        guest = pointer()
        if security.SecCodeCopyGuestWithAttributes(None, attributes, 0, ctypes.byref(guest)) != 0:
            return False
        owned.append(guest.value)
        # Validate the running code object, not a replacement at its pathname.
        return security.SecCodeCheckValidity(guest, 0, requirement) == 0
    except (AttributeError, OSError, ValueError):
        return False
    finally:
        if core is not None:
            for reference in reversed(owned):
                core.CFRelease(reference)


def _canonical_language_server(path: str) -> bool:
    expected = ANTIGRAVITY_LANGUAGE_SERVER
    candidate = Path(path)
    try:
        if candidate != expected or candidate.resolve(strict=True) != expected:
            return False
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return False
    except OSError:
        return False
    return True


def verified_antigravity_process_identity(
    pid: int,
) -> AntigravityProcessIdentity | None:
    """Return stable OS facts only for the reviewed signed installation."""
    facts = _process_facts(pid)
    if facts is None:
        return None
    executable, uid, started_at, started_at_usec = facts
    if uid != os.getuid() or started_at <= 0 or not 0 <= started_at_usec < 1_000_000:
        return None
    if not _canonical_language_server(executable) or not _running_process_is_trusted(pid):
        return None
    if _process_facts(pid) != facts:
        return None
    return pid, executable, uid, started_at, started_at_usec


__all__ = [
    "ANTIGRAVITY_APP",
    "ANTIGRAVITY_BUNDLE_ID",
    "ANTIGRAVITY_LANGUAGE_SERVER",
    "ANTIGRAVITY_TEAM_ID",
    "AntigravityProcessIdentity",
    "verified_antigravity_process_identity",
]
