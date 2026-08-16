"""Cross-process duplicate suppression for normalized provider hook events."""

from __future__ import annotations

import fcntl
import json
import os
import stat
from pathlib import Path

_STATE_VERSION = 1
_MAX_STATE_BYTES = 64 * 1024
_MAX_TOKEN_BYTES = 1024


class HookEventDeduplicator:
    def __init__(self, path: Path, *, max_tokens: int = 128) -> None:
        self.path = Path(path).expanduser()
        if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 1 and 4096")
        self.max_tokens = max_tokens

    @staticmethod
    def _valid_token(token: object) -> bool:
        if not isinstance(token, str) or not token:
            return False
        encoded = token.encode("utf-8", errors="strict")
        return (
            len(encoded) <= _MAX_TOKEN_BYTES
            and "\x00" not in token
            and all(ord(character) >= 32 for character in token)
        )

    def _open(self) -> int:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
        ):
            os.close(descriptor)
            raise OSError("unsafe hook dedupe state file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
        return descriptor

    @staticmethod
    def _read_locked(descriptor: int) -> list[str]:
        size = os.fstat(descriptor).st_size
        if size < 0 or size > _MAX_STATE_BYTES:
            return []
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, _MAX_STATE_BYTES + 1)
        if len(raw) > _MAX_STATE_BYTES or not raw:
            return []
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return []
        if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
            return []
        values = payload.get("tokens")
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            if HookEventDeduplicator._valid_token(value) and value not in result:
                result.append(value)
        return result

    @staticmethod
    def _write_locked(descriptor: int, tokens: list[str]) -> None:
        payload = json.dumps(
            {"version": _STATE_VERSION, "tokens": tokens},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _MAX_STATE_BYTES:
            raise OSError("hook dedupe state exceeds size limit")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, payload)
        os.ftruncate(descriptor, len(payload))
        os.fsync(descriptor)

    def accept(self, event_token: str) -> bool:
        if not self._valid_token(event_token):
            return False
        descriptor = self._open()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            tokens = self._read_locked(descriptor)
            if event_token in tokens:
                return False
            tokens.append(event_token)
            if len(tokens) > self.max_tokens:
                tokens = tokens[-self.max_tokens :]
            self._write_locked(descriptor, tokens)
            return True
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def tokens(self) -> tuple[str, ...]:
        if not self.path.exists():
            return ()
        descriptor = self._open()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            return tuple(self._read_locked(descriptor))
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


__all__ = ["HookEventDeduplicator"]
