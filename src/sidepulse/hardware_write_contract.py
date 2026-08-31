"""Pure validation for hardware-write routing metadata."""

from __future__ import annotations


def validate_hardware_write_metadata(
    display_kind: object,
    coalesce_identity: object,
    preview_session_id: object,
) -> None:
    """Validate bounded routing identifiers without importing the controller."""

    if type(display_kind) is not str or not display_kind or len(display_kind) > 64:
        raise ValueError("invalid hardware display kind")
    if (
        type(coalesce_identity) is not str
        or not coalesce_identity
        or len(coalesce_identity) > 64
    ):
        raise ValueError("invalid hardware coalescing identity")
    if preview_session_id is not None and (
        type(preview_session_id) is not str
        or not preview_session_id
        or len(preview_session_id) > 64
    ):
        raise ValueError("invalid hardware preview session")


__all__ = ["validate_hardware_write_metadata"]
