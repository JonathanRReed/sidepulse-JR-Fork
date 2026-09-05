"""Decode configured Creator Micro agent-key presses, never device commands."""

from __future__ import annotations

import re


class DeckInputRouter:
    """Logical AG keys also cover dial/joystick slots assigned in device firmware."""

    def __init__(self) -> None:
        self._held: set[int] = set()

    def accept(self, message: object) -> int | None:
        if type(message) is not dict or set(message) != {"method", "params"}:
            return None
        if message["method"] != "v.oai.hid":
            return None
        params = message["params"]
        if type(params) is not dict or set(params) - {"k", "act", "ag"}:
            return None
        key, action = params.get("k"), params.get("act")
        if (
            type(key) is not str or re.fullmatch(r"AG[01][0-9]", key) is None
            or type(action) is not int or action not in (0, 1)
        ):
            return None
        index = int(key[2:])
        if action == 0:
            self._held.discard(index)
            return None
        if index in self._held:
            return None
        self._held.add(index)
        return index
