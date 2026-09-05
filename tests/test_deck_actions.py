from __future__ import annotations

import pytest

from sidepulse.deck_actions import DeckAction


def test_open_app_round_trips_as_bounded_data() -> None:
    action = DeckAction(kind="open_app", bundle_id="com.apple.Terminal")

    assert action.to_dict() == {
        "kind": "open_app",
        "bundle_id": "com.apple.Terminal",
        "key_code": None,
        "modifiers": [],
    }
    assert DeckAction.from_dict(action.to_dict()) == action


def test_shortcut_round_trips_with_explicit_target_and_chord() -> None:
    action = DeckAction(
        kind="shortcut",
        bundle_id="com.openai.codex",
        key_code=45,
        modifiers=("command", "shift"),
    )

    assert DeckAction.from_dict(action.to_dict()) == action


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "shell", "bundle_id": None, "key_code": None, "modifiers": []},
        {"kind": "open_app", "bundle_id": "", "key_code": None, "modifiers": []},
        {"kind": "open_app", "bundle_id": "com.example.App", "key_code": 1, "modifiers": []},
        {"kind": "shortcut", "bundle_id": "com.example.App", "key_code": 128, "modifiers": []},
        {"kind": "shortcut", "bundle_id": "com.example.App", "key_code": True, "modifiers": []},
        {"kind": "shortcut", "bundle_id": "com.example.App", "key_code": 12, "modifiers": ["fn"]},
        {"kind": "shortcut", "bundle_id": "com.example.App", "key_code": 12, "modifiers": ["command", "command"]},
        {"kind": "open_usage", "bundle_id": "com.example.App", "key_code": None, "modifiers": []},
    ],
)
def test_rejects_actions_outside_the_bounded_contract(value: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        DeckAction.from_dict(value)


def test_from_dict_rejects_unknown_or_missing_fields() -> None:
    with pytest.raises(ValueError):
        DeckAction.from_dict({"kind": "open_usage", "bundle_id": None, "key_code": None})

    with pytest.raises(ValueError):
        DeckAction.from_dict(
            {"kind": "open_usage", "bundle_id": None, "key_code": None, "modifiers": [], "url": "https://example.com"}
        )


def test_bundle_identifier_is_bounded_to_macos_identifier_length() -> None:
    valid = "a." + ("b" * 253)
    assert len(valid) == 255
    assert DeckAction(kind="open_app", bundle_id=valid).bundle_id == valid

    with pytest.raises(ValueError):
        DeckAction(kind="open_app", bundle_id=valid + "b")


@pytest.mark.parametrize("kind", ["reveal_current_ask", "open_agent_browser", "open_usage"])
def test_jr_bar_actions_accept_no_external_payload(kind: str) -> None:
    action = DeckAction(kind=kind)

    assert action.bundle_id is None
    assert action.key_code is None
    assert action.modifiers == ()
