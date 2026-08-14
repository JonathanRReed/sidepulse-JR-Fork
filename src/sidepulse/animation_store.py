"""The owner's personal animation library: bounded, private, and honest.

Three disciplines, all copied from the ledgers rather than reinvented:
``atomic_private_write`` for the bytes, an exact-field strict decode for the
document, and typed restore health instead of an exception -- a library that
cannot be read must never take the settings window down with it.

Two bounds, not one. A count cap alone lets 24 animations of 512 bytes plus
24 names of any length grow the file without limit; a byte cap alone lets a
thousand tiny entries turn a popup menu into a scroll. Both are enforced on
the way in and re-checked on the way out, because this is the last place
before the bytes hit the disk and a caller that hand-built a library must
not be able to write a file the loader will then call corrupt.

The library stores the compiled PROGRAM TEXT, not the step model. The text
is the durable artifact -- it is what the firmware eats, it is what a person
can read in a file, and ``animation.parse_animation`` recovers the model
from it losslessly. Storing the model would mean every future step type
becomes a migration.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from .animation import (
    MAX_PROGRAM_BYTES,
    Animation,
    AnimationValidationError,
    compile_animation,
    parse_animation,
)
from .private_io import atomic_private_write, read_private_text
from .providers import default_state_dir

ANIMATION_LIBRARY_NAME: Final = "animation-library.json"
MAX_SAVED_ANIMATIONS: Final = 24
MAX_ANIMATION_NAME_LENGTH: Final = 48
MAX_LIBRARY_BYTES: Final = 32 * 1024

_STORE_VERSION: Final = 1
_DOCUMENT_FIELDS: Final = frozenset({"animations", "version"})
_ENTRY_FIELDS: Final = frozenset({"name", "program", "updated_at_epoch"})


class AnimationLibraryError(ValueError):
    """A refusal the owner is meant to read. Every message is a sentence."""


class LibraryHealth(str, Enum):
    HEALTHY = "healthy"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class _CorruptLibrary(ValueError):
    pass


class _UnsupportedLibrary(ValueError):
    pass


def normalize_animation_name(value: object) -> str:
    """The name as it will be stored, or a refusal explaining why not."""
    name = str(value or "").strip()
    if not name:
        raise AnimationLibraryError("An animation needs a name.")
    if len(name) > MAX_ANIMATION_NAME_LENGTH:
        raise AnimationLibraryError(
            f"“{name[:MAX_ANIMATION_NAME_LENGTH]}…” is longer than "
            f"{MAX_ANIMATION_NAME_LENGTH} characters."
        )
    if any(ord(character) < 32 for character in name):
        raise AnimationLibraryError("An animation name cannot contain control characters.")
    return name


@dataclass(frozen=True, slots=True)
class SavedAnimation:
    name: str
    program: str
    updated_at_epoch: float = 0.0

    def __post_init__(self) -> None:
        if type(self.name) is not str or type(self.program) is not str:
            raise AnimationLibraryError("A saved animation needs a name and a program.")
        if not self.name.strip():
            raise AnimationLibraryError("An animation needs a name.")
        if len(self.name) > MAX_ANIMATION_NAME_LENGTH:
            raise AnimationLibraryError(
                f"An animation name may be at most {MAX_ANIMATION_NAME_LENGTH} "
                "characters."
            )
        if len(self.program.encode("utf-8")) > MAX_PROGRAM_BYTES:
            raise AnimationLibraryError(
                f"“{self.name}” is larger than the {MAX_PROGRAM_BYTES} "
                "bytes the device accepts."
            )
        if type(self.updated_at_epoch) not in {int, float} or isinstance(
            self.updated_at_epoch, bool
        ):
            raise AnimationLibraryError("A saved animation needs a numeric timestamp.")

    def to_animation(self, *, led_count: int = 8) -> Animation:
        """The step model behind this entry. Raises if the stored text has
        stopped being valid -- which is information, not an inconvenience."""
        return parse_animation(self.program, name=self.name, led_count=led_count)


@dataclass(frozen=True, slots=True)
class AnimationLibrary:
    """An ordered, bounded set of saved animations, newest edit last."""

    entries: tuple[SavedAnimation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        names = [entry.name for entry in self.entries]
        if len(set(names)) != len(names):
            raise AnimationLibraryError("Two saved animations cannot share a name.")
        if len(self.entries) > MAX_SAVED_ANIMATIONS:
            raise AnimationLibraryError(
                f"A library holds at most {MAX_SAVED_ANIMATIONS} animations."
            )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)

    def get(self, name: str) -> SavedAnimation:
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise AnimationLibraryError(f"There is no saved animation called “{name}”.")

    def has(self, name: str) -> bool:
        return any(entry.name == name for entry in self.entries)

    def with_program(
        self,
        name: str,
        program: str,
        *,
        led_count: int = 8,
        now_epoch: float | None = None,
    ) -> AnimationLibrary:
        """Save text under a name, replacing any entry with that name IN PLACE.

        The program is parsed before it is stored: a library entry that the
        firmware would reject is a trap set for the person who loads it a
        month later and presses Burn.
        """
        cleaned = normalize_animation_name(name)
        try:
            parse_animation(program, name=cleaned, led_count=led_count)
        except AnimationValidationError as error:
            raise AnimationLibraryError(
                f"“{cleaned}” cannot be saved: {error}"
            ) from error
        stamp = time.time() if now_epoch is None else float(now_epoch)
        entry = SavedAnimation(cleaned, program, stamp)
        if self.has(cleaned):
            return AnimationLibrary(
                tuple(entry if item.name == cleaned else item for item in self.entries)
            )
        if len(self.entries) >= MAX_SAVED_ANIMATIONS:
            raise AnimationLibraryError(
                f"Your library already holds {MAX_SAVED_ANIMATIONS} animations. "
                "Delete one before saving another."
            )
        return AnimationLibrary((*self.entries, entry))

    def with_animation(
        self,
        animation: Animation,
        *,
        name: str | None = None,
        led_count: int = 8,
        now_epoch: float | None = None,
    ) -> AnimationLibrary:
        return self.with_program(
            name if name is not None else animation.name,
            compile_animation(animation, led_count=led_count),
            led_count=led_count,
            now_epoch=now_epoch,
        )

    def renamed(self, old: str, new: str) -> AnimationLibrary:
        """Rename in place. Order is identity here: the popup is a list the
        owner has learned the shape of, and a rename that jumps an entry to
        the bottom is a rename that loses it."""
        cleaned = normalize_animation_name(new)
        current = self.get(old)
        if cleaned == old:
            return self
        if self.has(cleaned):
            raise AnimationLibraryError(
                f"“{cleaned}” is already taken by another animation."
            )
        replacement = SavedAnimation(cleaned, current.program, current.updated_at_epoch)
        return AnimationLibrary(
            tuple(replacement if item.name == old else item for item in self.entries)
        )

    def without(self, name: str) -> AnimationLibrary:
        if not self.has(name):
            raise AnimationLibraryError(
                f"There is no saved animation called “{name}”."
            )
        return AnimationLibrary(
            tuple(item for item in self.entries if item.name != name)
        )


@dataclass(frozen=True, slots=True)
class AnimationLibraryRestore:
    library: AnimationLibrary
    health: LibraryHealth


def default_animation_library_path(home: Path | None = None) -> Path:
    return default_state_dir(home) / ANIMATION_LIBRARY_NAME


def load_animation_library(path: Path) -> AnimationLibraryRestore:
    """Load the library, reporting degraded health instead of raising.

    The worst honest outcome is an empty library and a settings window that
    still opens.
    """
    try:
        raw = read_private_text(Path(path), max_bytes=MAX_LIBRARY_BYTES)
        document = _decode_document(raw)
        library = _library_from_document(document)
    except FileNotFoundError:
        return AnimationLibraryRestore(AnimationLibrary(), LibraryHealth.MISSING)
    except _UnsupportedLibrary:
        return AnimationLibraryRestore(AnimationLibrary(), LibraryHealth.UNSUPPORTED)
    except OSError:
        return AnimationLibraryRestore(AnimationLibrary(), LibraryHealth.UNAVAILABLE)
    except (RecursionError, TypeError, UnicodeError, ValueError):
        return AnimationLibraryRestore(AnimationLibrary(), LibraryHealth.CORRUPT)
    return AnimationLibraryRestore(library, LibraryHealth.HEALTHY)


def save_animation_library(path: Path, library: AnimationLibrary) -> Path:
    if type(library) is not AnimationLibrary:
        raise AnimationLibraryError("That is not an animation library.")
    encoded = _encode_document(library)
    if len(encoded.encode("utf-8")) > MAX_LIBRARY_BYTES:
        raise AnimationLibraryError(
            f"Your library is larger than the {MAX_LIBRARY_BYTES}-byte limit. "
            "Delete an animation and try again."
        )
    return atomic_private_write(Path(path), encoded)


def _encode_document(library: AnimationLibrary) -> str:
    document = {
        "version": _STORE_VERSION,
        "animations": [
            {
                "name": entry.name,
                "program": entry.program,
                "updated_at_epoch": float(entry.updated_at_epoch),
            }
            for entry in library.entries
        ],
    }
    return (
        json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    )


def _strict_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _CorruptLibrary
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _CorruptLibrary


def _decode_document(raw: str) -> object:
    return json.loads(raw, object_pairs_hook=_strict_object, parse_constant=_reject_constant)


def _has_exact_fields(value: object, fields: frozenset[str]) -> bool:
    return type(value) is dict and frozenset(value) == fields


def _library_from_document(document: object) -> AnimationLibrary:
    if not _has_exact_fields(document, _DOCUMENT_FIELDS):
        raise _CorruptLibrary
    version = document["version"]
    if type(version) is not int:
        raise _CorruptLibrary
    if version != _STORE_VERSION:
        raise _UnsupportedLibrary
    animations = document["animations"]
    if type(animations) is not list or len(animations) > MAX_SAVED_ANIMATIONS:
        raise _CorruptLibrary
    return AnimationLibrary(tuple(_entry_from_payload(item) for item in animations))


def _entry_from_payload(payload: object) -> SavedAnimation:
    if not _has_exact_fields(payload, _ENTRY_FIELDS):
        raise _CorruptLibrary
    name = payload["name"]
    program = payload["program"]
    stamp = payload["updated_at_epoch"]
    if (
        type(name) is not str
        or type(program) is not str
        or type(stamp) not in {int, float}
        or isinstance(stamp, bool)
    ):
        raise _CorruptLibrary
    try:
        return SavedAnimation(name, program, float(stamp))
    except AnimationLibraryError as error:
        raise _CorruptLibrary from error


__all__ = [
    "ANIMATION_LIBRARY_NAME",
    "MAX_ANIMATION_NAME_LENGTH",
    "MAX_LIBRARY_BYTES",
    "MAX_SAVED_ANIMATIONS",
    "AnimationLibrary",
    "AnimationLibraryError",
    "AnimationLibraryRestore",
    "LibraryHealth",
    "SavedAnimation",
    "default_animation_library_path",
    "load_animation_library",
    "normalize_animation_name",
    "save_animation_library",
]
