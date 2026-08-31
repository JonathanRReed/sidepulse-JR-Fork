"""Pure identities, validation, persistence, and status for global actions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum


class GlobalActionID(str, Enum):
    REVEAL_CURRENT_ASK = "reveal_current_ask"


class ShortcutModifier(str, Enum):
    CONTROL = "control"
    OPTION = "option"
    SHIFT = "shift"
    COMMAND = "command"


class GlobalActionBindingState(str, Enum):
    UNASSIGNED = "unassigned"
    ACTIVE = "active"
    LOCAL_CONFLICT = "local_conflict"
    UNSUPPORTED = "unsupported"
    REGISTRATION_REFUSED = "registration_refused"
    CLOSED = "closed"


class ShortcutValidationCode(str, Enum):
    MALFORMED = "malformed"
    UNKNOWN_ACTION = "unknown_action"
    NO_MODIFIERS = "no_modifiers"
    OPTION_SHIFT_ONLY = "option_shift_only"
    COMMAND_OR_CONTROL_REQUIRED = "command_or_control_required"
    RESERVED_MENU_EQUIVALENT = "reserved_menu_equivalent"
    DUPLICATE_BINDING = "duplicate_binding"


class ShortcutValidationError(ValueError):
    def __init__(
        self,
        code: ShortcutValidationCode,
        message: str,
        *,
        action: str | None = None,
        conflicting_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.action = action
        self.conflicting_action = conflicting_action


@dataclass(frozen=True, slots=True)
class ShortcutChord:
    key_code: int
    key_label: str
    modifiers: frozenset[ShortcutModifier]

    def __post_init__(self) -> None:
        if type(self.key_code) is not int or not 0 <= self.key_code <= 127:
            raise ValueError("shortcut key code must be an integer from 0 through 127")
        if (
            type(self.key_label) is not str
            or not 1 <= len(self.key_label) <= 16
            or not self.key_label.isprintable()
            or self.key_label.isspace()
        ):
            raise ValueError("shortcut key label must be printable and 1 to 16 characters")
        if type(self.modifiers) is not frozenset or not all(
            type(modifier) is ShortcutModifier for modifier in self.modifiers
        ):
            raise ValueError("shortcut modifiers must be known immutable values")

    def to_dict(self) -> dict[str, object]:
        return {
            "key_code": self.key_code,
            "key_label": self.key_label,
            "modifiers": [
                modifier.value
                for modifier in _MODIFIER_ORDER
                if modifier in self.modifiers
            ],
        }


@dataclass(frozen=True, slots=True)
class PersistedShortcutRefusal:
    action_key: str
    code: ShortcutValidationCode


@dataclass(frozen=True, slots=True)
class ParsedGlobalActionShortcuts:
    bindings: tuple[tuple[GlobalActionID, ShortcutChord], ...]
    refusals: tuple[PersistedShortcutRefusal, ...]

    def binding_for(self, action: GlobalActionID) -> ShortcutChord | None:
        return next(
            (chord for candidate, chord in self.bindings if candidate is action),
            None,
        )


@dataclass(frozen=True, slots=True)
class GlobalActionStatusProjection:
    action: GlobalActionID
    state: GlobalActionBindingState
    value_text: str
    help_text: str


_MODIFIER_ORDER = (
    ShortcutModifier.CONTROL,
    ShortcutModifier.OPTION,
    ShortcutModifier.SHIFT,
    ShortcutModifier.COMMAND,
)
_MODIFIER_SYMBOLS = {
    ShortcutModifier.CONTROL: "⌃",
    ShortcutModifier.OPTION: "⌥",
    ShortcutModifier.SHIFT: "⇧",
    ShortcutModifier.COMMAND: "⌘",
}

# Exact macOS virtual-key-code plus modifier equivalents already owned by the
# JR Bar responder chain. The presentation label is deliberately absent from
# this identity because it is not execution authority.
_RESERVED_MENU_EQUIVALENTS = frozenset(
    {
        (12, frozenset({ShortcutModifier.COMMAND})),  # Quit, Command-Q
        (13, frozenset({ShortcutModifier.COMMAND})),  # Close, Command-W
        (43, frozenset({ShortcutModifier.COMMAND})),  # Settings, Command-,
        (7, frozenset({ShortcutModifier.COMMAND})),  # Cut, Command-X
        (8, frozenset({ShortcutModifier.COMMAND})),  # Copy, Command-C
        (9, frozenset({ShortcutModifier.COMMAND})),  # Paste, Command-V
        (0, frozenset({ShortcutModifier.COMMAND})),  # Select All, Command-A
        (6, frozenset({ShortcutModifier.COMMAND})),  # Undo, Command-Z
        (
            6,
            frozenset({ShortcutModifier.COMMAND, ShortcutModifier.SHIFT}),
        ),  # Redo, Command-Shift-Z
    }
)


def normalized_shortcut(
    chord: ShortcutChord,
) -> tuple[int, frozenset[ShortcutModifier]]:
    if type(chord) is not ShortcutChord:
        raise ShortcutValidationError(
            ShortcutValidationCode.MALFORMED,
            "shortcut must be a ShortcutChord",
        )
    return chord.key_code, chord.modifiers


def validate_shortcut(chord: ShortcutChord) -> None:
    key_code, modifiers = normalized_shortcut(chord)
    if not modifiers:
        raise ShortcutValidationError(
            ShortcutValidationCode.NO_MODIFIERS,
            "shortcut requires at least one modifier",
        )
    if modifiers == frozenset({ShortcutModifier.OPTION, ShortcutModifier.SHIFT}):
        raise ShortcutValidationError(
            ShortcutValidationCode.OPTION_SHIFT_ONLY,
            "Option-Shift alone is not a supported global shortcut",
        )
    if not modifiers.intersection(
        {ShortcutModifier.COMMAND, ShortcutModifier.CONTROL}
    ):
        raise ShortcutValidationError(
            ShortcutValidationCode.COMMAND_OR_CONTROL_REQUIRED,
            "shortcut must include Command or Control",
        )
    if (key_code, modifiers) in _RESERVED_MENU_EQUIVALENTS:
        raise ShortcutValidationError(
            ShortcutValidationCode.RESERVED_MENU_EQUIVALENT,
            "shortcut is reserved by a JR Bar menu command",
        )


def _action_key(action: object) -> str:
    if type(action) is GlobalActionID:
        return action.value
    if type(action) is str and action:
        return action
    raise ShortcutValidationError(
        ShortcutValidationCode.UNKNOWN_ACTION,
        "global action identifier is invalid",
    )


def _binding_items(
    bindings: Mapping[object, ShortcutChord]
    | Iterable[tuple[object, ShortcutChord]],
) -> tuple[tuple[object, ShortcutChord], ...]:
    if isinstance(bindings, Mapping):
        return tuple(bindings.items())
    try:
        return tuple(bindings)
    except TypeError as exc:
        raise ShortcutValidationError(
            ShortcutValidationCode.MALFORMED,
            "global action bindings must be a mapping or binding pairs",
        ) from exc


def validate_global_action_bindings(
    bindings: Mapping[object, ShortcutChord]
    | Iterable[tuple[object, ShortcutChord]],
) -> None:
    seen: dict[tuple[int, frozenset[ShortcutModifier]], str] = {}
    for action, chord in _binding_items(bindings):
        action_key = _action_key(action)
        try:
            validate_shortcut(chord)
        except ShortcutValidationError as exc:
            raise ShortcutValidationError(
                exc.code,
                str(exc),
                action=action_key,
                conflicting_action=exc.conflicting_action,
            ) from exc
        identity = normalized_shortcut(chord)
        conflicting_action = seen.get(identity)
        if conflicting_action is not None:
            raise ShortcutValidationError(
                ShortcutValidationCode.DUPLICATE_BINDING,
                "two JR Bar global actions cannot use the same shortcut",
                action=action_key,
                conflicting_action=conflicting_action,
            )
        seen[identity] = action_key


def format_shortcut(chord: ShortcutChord) -> str:
    normalized_shortcut(chord)
    symbols = "".join(
        _MODIFIER_SYMBOLS[modifier]
        for modifier in _MODIFIER_ORDER
        if modifier in chord.modifiers
    )
    return f"{symbols}{chord.key_label}"


def _parse_shortcut_chord(raw: object) -> ShortcutChord:
    if type(raw) is not dict or set(raw) != {"key_code", "key_label", "modifiers"}:
        raise ShortcutValidationError(
            ShortcutValidationCode.MALFORMED,
            "persisted shortcut must contain only known fields",
        )
    modifiers = raw["modifiers"]
    if (
        type(modifiers) is not list
        or not all(type(modifier) is str for modifier in modifiers)
        or len(modifiers) != len(set(modifiers))
    ):
        raise ShortcutValidationError(
            ShortcutValidationCode.MALFORMED,
            "persisted shortcut modifiers are malformed",
        )
    try:
        parsed_modifiers = frozenset(ShortcutModifier(value) for value in modifiers)
        chord = ShortcutChord(
            key_code=raw["key_code"],
            key_label=raw["key_label"],
            modifiers=parsed_modifiers,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ShortcutValidationError(
            ShortcutValidationCode.MALFORMED,
            "persisted shortcut is malformed",
        ) from exc
    validate_shortcut(chord)
    return chord


def parse_global_action_shortcuts(raw: object) -> ParsedGlobalActionShortcuts:
    if type(raw) is not dict:
        return ParsedGlobalActionShortcuts(
            (),
            (PersistedShortcutRefusal("*", ShortcutValidationCode.MALFORMED),),
        )

    bindings: list[tuple[GlobalActionID, ShortcutChord]] = []
    refusals: list[PersistedShortcutRefusal] = []
    seen: dict[tuple[int, frozenset[ShortcutModifier]], str] = {}
    for action_key, persisted in sorted(raw.items(), key=lambda item: str(item[0])):
        if type(action_key) is not str:
            refusals.append(
                PersistedShortcutRefusal(str(action_key), ShortcutValidationCode.UNKNOWN_ACTION)
            )
            continue
        try:
            action = GlobalActionID(action_key)
        except ValueError:
            refusals.append(
                PersistedShortcutRefusal(action_key, ShortcutValidationCode.UNKNOWN_ACTION)
            )
            continue
        try:
            chord = _parse_shortcut_chord(persisted)
        except ShortcutValidationError as exc:
            refusals.append(PersistedShortcutRefusal(action_key, exc.code))
            continue
        identity = normalized_shortcut(chord)
        if identity in seen:
            refusals.append(
                PersistedShortcutRefusal(
                    action_key,
                    ShortcutValidationCode.DUPLICATE_BINDING,
                )
            )
            continue
        seen[identity] = action_key
        bindings.append((action, chord))

    return ParsedGlobalActionShortcuts(tuple(bindings), tuple(refusals))


def serialize_global_action_shortcuts(
    bindings: Mapping[object, ShortcutChord]
    | Iterable[tuple[object, ShortcutChord]],
) -> dict[str, dict[str, object]]:
    items = _binding_items(bindings)
    validate_global_action_bindings(items)
    encoded: dict[str, dict[str, object]] = {}
    for action, chord in sorted(items, key=lambda item: _action_key(item[0])):
        action_key = _action_key(action)
        try:
            known_action = GlobalActionID(action_key)
        except ValueError as exc:
            raise ShortcutValidationError(
                ShortcutValidationCode.UNKNOWN_ACTION,
                "cannot serialize an unknown global action",
                action=action_key,
            ) from exc
        encoded[known_action.value] = chord.to_dict()
    return encoded


def project_global_action_status(
    action: GlobalActionID,
    state: GlobalActionBindingState,
    *,
    chord: ShortcutChord | None = None,
) -> GlobalActionStatusProjection:
    if type(action) is not GlobalActionID or type(state) is not GlobalActionBindingState:
        raise ValueError("global action status identity is invalid")
    if state is GlobalActionBindingState.ACTIVE and chord is None:
        raise ValueError("active status requires a shortcut chord")
    if chord is not None:
        validate_shortcut(chord)

    if state is GlobalActionBindingState.UNASSIGNED:
        value = "Not set"
        help_text = "Set a shortcut to reveal the current ask or Agent Browser."
    elif state is GlobalActionBindingState.ACTIVE:
        formatted = format_shortcut(chord)
        value = formatted
        help_text = (
            f"Use {formatted} to reveal the current ask or Agent Browser. "
            "JR Bar cannot detect every shortcut used by other apps."
        )
    elif state is GlobalActionBindingState.LOCAL_CONFLICT:
        value = "Already used by JR Bar"
        help_text = (
            "Choose another shortcut to reveal the current ask or Agent Browser."
        )
    elif state is GlobalActionBindingState.UNSUPPORTED:
        value = "Shortcut not supported"
        help_text = (
            "Choose a shortcut with Command or Control to reveal the current ask or "
            "Agent Browser."
        )
    elif state is GlobalActionBindingState.REGISTRATION_REFUSED:
        value = "macOS refused shortcut"
        help_text = (
            "The previous binding is unchanged. Choose another shortcut to reveal the "
            "current ask or Agent Browser."
        )
    else:
        value = "Unavailable"
        help_text = (
            "Global shortcut registration is closed. Use the menu to reveal the current "
            "ask or Agent Browser."
        )
    return GlobalActionStatusProjection(action, state, value, help_text)
