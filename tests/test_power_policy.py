from __future__ import annotations

import pytest

from sidepulse.power_policy import (
    PowerHoldChoices,
    configure_caffeinate_display_assertion,
)


@pytest.mark.parametrize(
    ("command", "keep_display_awake", "expected"),
    [
        (
            ("/usr/bin/caffeinate", "-ims"),
            False,
            ("/usr/bin/caffeinate", "-ims"),
        ),
        (
            ("/usr/bin/caffeinate", "-ims"),
            True,
            ("/usr/bin/caffeinate", "-dims"),
        ),
        (
            ("/usr/bin/caffeinate", "-dimsu", "-t", "1800"),
            False,
            ("/usr/bin/caffeinate", "-imsu", "-t", "1800"),
        ),
        (
            ("/usr/bin/caffeinate", "-imsu", "-t", "1800"),
            True,
            ("/usr/bin/caffeinate", "-dimsu", "-t", "1800"),
        ),
        (
            ("/usr/bin/caffeinate", "-i", "-m", "-s", "-w", "42"),
            True,
            ("/usr/bin/caffeinate", "-dims", "-w", "42"),
        ),
        (
            ("/usr/bin/caffeinate", "-d", "-i", "-s", "--", "task"),
            False,
            ("/usr/bin/caffeinate", "-is", "--", "task"),
        ),
        (
            ("/usr/bin/caffeinate", "-d", "--", "-i", "-m"),
            False,
            ("/usr/bin/caffeinate", "--", "-i", "-m"),
        ),
        (
            ("/usr/bin/caffeinate", "-t", "30"),
            True,
            ("/usr/bin/caffeinate", "-d", "-t", "30"),
        ),
    ],
)
def test_display_assertion_compiles_to_one_canonical_flag_bundle(
    command: tuple[str, ...],
    keep_display_awake: bool,
    expected: tuple[str, ...],
) -> None:
    original = tuple(command)

    assert (
        configure_caffeinate_display_assertion(
            command,
            keep_display_awake=keep_display_awake,
        )
        == expected
    )
    assert command == original


@pytest.mark.parametrize(
    ("command", "keep_display_awake"),
    [
        ((), False),
        (("",), False),
        (("/usr/bin/caffeinate", 7), False),
        (("/usr/bin/caffeinate",), 1),
    ],
)
def test_display_assertion_rejects_invalid_inputs(
    command: object,
    keep_display_awake: object,
) -> None:
    with pytest.raises(ValueError, match="invalid caffeinate"):
        configure_caffeinate_display_assertion(  # type: ignore[arg-type]
            command,
            keep_display_awake=keep_display_awake,
        )


def test_power_hold_choices_keep_the_four_decisions_independent() -> None:
    choices = PowerHoldChoices(
        agent_keep_awake_enabled=False,
        keep_display_awake=True,
        keep_awake_on_battery=False,
        closed_lid_awake_policy="always",
    )

    assert choices.agent_keep_awake_enabled is False
    assert choices.keep_display_awake is True
    assert choices.keep_awake_on_battery is False
    assert choices.closed_lid_awake_policy == "always"


def test_default_agent_and_closed_lid_commands_allow_display_sleep() -> None:
    from sidepulse.keep_awake import CAFFEINATE_COMMAND
    from sidepulse.lid_sleep import CAFFEINATE_CLOSED_LID_COMMAND

    assert configure_caffeinate_display_assertion(
        CAFFEINATE_COMMAND,
        keep_display_awake=False,
    ) == tuple(CAFFEINATE_COMMAND)
    assert configure_caffeinate_display_assertion(
        CAFFEINATE_CLOSED_LID_COMMAND,
        keep_display_awake=False,
    ) == tuple(CAFFEINATE_CLOSED_LID_COMMAND)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agent_keep_awake_enabled": 1},
        {"keep_display_awake": None},
        {"keep_awake_on_battery": "yes"},
        {"closed_lid_awake_policy": ""},
    ],
)
def test_power_hold_choices_reject_ambiguous_values(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "agent_keep_awake_enabled": True,
        "keep_display_awake": False,
        "keep_awake_on_battery": True,
        "closed_lid_awake_policy": "never",
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match="invalid power hold choices"):
        PowerHoldChoices(**values)  # type: ignore[arg-type]
