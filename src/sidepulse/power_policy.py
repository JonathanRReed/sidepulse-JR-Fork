"""Pure power-hold choices and deterministic caffeinate command policy."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_CAFFEINATE_ASSERTION_ORDER = "dimsu"
_CAFFEINATE_ASSERTIONS = frozenset(_CAFFEINATE_ASSERTION_ORDER)


@dataclass(frozen=True, slots=True)
class PowerHoldChoices:
    """The four owner decisions that must not imply one another."""

    agent_keep_awake_enabled: bool
    keep_display_awake: bool
    keep_awake_on_battery: bool
    closed_lid_awake_policy: str

    def __post_init__(self) -> None:
        if (
            type(self.agent_keep_awake_enabled) is not bool
            or type(self.keep_display_awake) is not bool
            or type(self.keep_awake_on_battery) is not bool
            or type(self.closed_lid_awake_policy) is not str
            or not self.closed_lid_awake_policy
        ):
            raise ValueError("invalid power hold choices")


def apply_power_hold_settings(ordinary, closed_lid, settings) -> PowerHoldChoices:
    """Apply persisted choices without coupling controller decisions."""
    choices = PowerHoldChoices(
        agent_keep_awake_enabled=settings.agent_keep_awake_enabled,
        keep_display_awake=settings.keep_display_awake,
        keep_awake_on_battery=settings.keep_awake_on_battery,
        closed_lid_awake_policy=settings.closed_lid_awake_policy,
    )
    ordinary.set_enabled(choices.agent_keep_awake_enabled)
    ordinary.set_keep_display_awake(choices.keep_display_awake)
    closed_lid.set_keep_display_awake(choices.keep_display_awake)
    return choices


def configure_caffeinate_display_assertion(
    command: Sequence[str],
    *,
    keep_display_awake: bool,
) -> tuple[str, ...]:
    """Return one canonical assertion bundle with the requested display flag."""
    if (
        isinstance(command, (str, bytes))
        or not isinstance(command, Sequence)
        or not command
        or any(type(part) is not str or not part for part in command)
        or type(keep_display_awake) is not bool
    ):
        raise ValueError("invalid caffeinate command")

    assertions: set[str] = set()
    first_assertion_index: int | None = None
    retained: list[str] = []
    parse_assertions = True
    for part in command:
        if part == "--":
            parse_assertions = False
            retained.append(part)
            continue
        flags = (
            part[1:]
            if parse_assertions and part.startswith("-") and not part.startswith("--")
            else ""
        )
        if flags and set(flags) <= _CAFFEINATE_ASSERTIONS:
            if first_assertion_index is None:
                first_assertion_index = len(retained)
            assertions.update(flags)
            continue
        retained.append(part)

    assertions.discard("d")
    if keep_display_awake:
        assertions.add("d")
    if not assertions:
        return tuple(retained)

    bundle = "-" + "".join(
        flag for flag in _CAFFEINATE_ASSERTION_ORDER if flag in assertions
    )
    insertion_index = 1 if first_assertion_index is None else first_assertion_index
    retained.insert(insertion_index, bundle)
    return tuple(retained)


__all__ = [
    "PowerHoldChoices",
    "apply_power_hold_settings",
    "configure_caffeinate_display_assertion",
]
