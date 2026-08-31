"""Thin hook admission entry point with a synchronous fail-open fallback."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from .hook_ingress_protocol import (
    MAX_HOOK_INGRESS_PAYLOAD_BYTES,
    HookIngressDisposition,
    HookIngressRequest,
    submit_hook_ingress,
)


def _synchronous_fallback(provider: str, log_path: Path, payload_text: str) -> None:
    from .hook import process_hook_payload

    process_hook_payload(provider, log_path, payload_text)


def run_hook_client(
    provider: str,
    log_path: Path,
    payload_text: str,
    *,
    submit: Callable[[HookIngressRequest], HookIngressDisposition] = submit_hook_ingress,
    fallback: Callable[[str, Path, str], object] = _synchronous_fallback,
) -> int:
    try:
        request = HookIngressRequest(provider, str(Path(log_path).expanduser()), payload_text)
    except (TypeError, ValueError):
        return 0

    try:
        disposition = submit(request)
        if disposition is HookIngressDisposition.UNAVAILABLE:
            fallback(provider, Path(log_path).expanduser(), payload_text)
    except Exception:
        try:
            fallback(provider, Path(log_path).expanduser(), payload_text)
        except Exception:
            pass
    return 0


def _read_bounded_payload() -> str | None:
    try:
        payload = sys.stdin.buffer.read(MAX_HOOK_INGRESS_PAYLOAD_BYTES + 1)
    except (AttributeError, OSError):
        return None
    if len(payload) > MAX_HOOK_INGRESS_PAYLOAD_BYTES:
        return None
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def hook_client_main(provider: str, log_path: Path) -> int:
    try:
        payload_text = _read_bounded_payload()
        if payload_text is None:
            return 0
        return run_hook_client(provider, Path(log_path).expanduser(), payload_text)
    finally:
        if provider == "cursor":
            try:
                sys.stdout.write("{}\n")
                sys.stdout.flush()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        provider = args[args.index("--provider") + 1]
        log_path = Path(args[args.index("--log") + 1]).expanduser()
    except (ValueError, IndexError):
        return 0

    return hook_client_main(provider, log_path)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["hook_client_main", "main", "run_hook_client"]
