from sidepulse.deck_input import DeckInputRouter


def event(key="AG03", action=1, **extra):
    return {"method": "v.oai.hid", "params": {"k": key, "act": action, **extra}}


def test_one_action_per_press_and_release_rearms_the_key():
    router = DeckInputRouter()
    assert router.accept(event()) == 3
    assert router.accept(event()) is None
    assert router.accept(event(action=0)) is None
    assert router.accept(event()) == 3


def test_keys_and_remapped_dial_inputs_keep_their_exact_logical_identity():
    router = DeckInputRouter()
    assert router.accept(event("AG00", ag=999)) == 0
    assert router.accept(event("AG13")) == 13
    assert router.accept(event("AG14")) == 14
    assert router.accept(event("AG19")) == 19


def test_untrusted_input_cannot_select_a_command_or_target():
    router = DeckInputRouter()
    for value in (
        None, {}, event("AG20"), event("AG1"), event("AG01\n"),
        event("AG01", True), event("AG01", "1"), event("AG01", 2),
        event("AG01", bundle_id="com.apple.Terminal"),
        {"method": "v.oai.rad", "params": {"a": 0.25, "d": 0.8}},
        {"method": "v.oai.hid", "params": {"k": "AG01"}},
    ):
        assert router.accept(value) is None
    assert router.accept(event("AG01")) == 1
