"""Owner-private, versioned mappings from logical device keys to local actions."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from .deck_actions import DeckAction
from .integration_settings import default_integration_settings_path
from .private_io import atomic_private_write, read_private_text

_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class DeckControlSettings:
    enabled: bool = False
    bindings: tuple[tuple[int, DeckAction], ...] = ()

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.bindings) is not tuple or len(self.bindings) > 20:
            raise ValueError("invalid deck control settings")
        seen = set()
        for binding in self.bindings:
            if type(binding) is not tuple or len(binding) != 2:
                raise ValueError("invalid deck binding")
            key, action = binding
            if type(key) is not int or not 0 <= key <= 19 or key in seen or type(action) is not DeckAction:
                raise ValueError("invalid deck binding")
            seen.add(key)

    def action_for(self, key: int) -> DeckAction | None:
        if not self.enabled or type(key) is not int:
            return None
        return next((action for index, action in self.bindings if index == key), None)

    def with_binding(self, key: int, action: DeckAction | None) -> DeckControlSettings:
        if type(key) is not int or not 0 <= key <= 19:
            raise ValueError("invalid deck key")
        bindings = tuple((index, value) for index, value in self.bindings if index != key)
        if action is not None:
            bindings += ((key, action),)
        return replace(self, bindings=tuple(sorted(bindings)))

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1, "enabled": self.enabled,
            "bindings": [{"key": key, "action": action.to_dict()} for key, action in self.bindings],
        }


def load_deck_controls(path: Path | None = None) -> DeckControlSettings:
    target = path or default_integration_settings_path().with_name("deck-controls.json")
    try:
        raw = read_private_text(target, max_bytes=32 * 1024)
    except FileNotFoundError:
        return DeckControlSettings()
    document = json.loads(raw)
    if (
        type(document) is not dict or set(document) != {"version", "enabled", "bindings"}
        or type(document["version"]) is not int or document["version"] != 1
        or type(document["bindings"]) is not list or len(document["bindings"]) > 20
    ):
        raise ValueError("unsupported deck settings document")
    bindings = []
    for binding in document["bindings"]:
        if type(binding) is not dict or set(binding) != {"key", "action"}:
            raise ValueError("invalid deck binding")
        bindings.append((binding["key"], DeckAction.from_dict(binding["action"])))
    return DeckControlSettings(document["enabled"], tuple(bindings))


def save_deck_controls(
    settings: DeckControlSettings, path: Path | None = None, *, expected: DeckControlSettings,
) -> Path:
    if type(settings) is not DeckControlSettings or type(expected) is not DeckControlSettings:
        raise ValueError("invalid deck settings")
    target = path or default_integration_settings_path().with_name("deck-controls.json")
    with _WRITE_LOCK:
        if load_deck_controls(target) != expected:
            raise ValueError("deck settings changed; reload before saving")
        return atomic_private_write(target, json.dumps(settings.to_dict(), indent=2) + "\n")
