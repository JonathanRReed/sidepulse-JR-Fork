from sidepulse.deck_actions import DeckAction
from sidepulse.deck_actions_macos import MacDeckActionExecutor
from sidepulse.deck_control_settings import DeckControlSettings
from sidepulse.deck_input_dispatch import DeckInputDispatch


class Target:
    def __init__(self):
        self.calls = []

    def performSelectorOnMainThread_withObject_waitUntilDone_(self, selector, payload, wait):
        assert selector == "applyDeckInput:"
        assert wait is False
        self.calls.append(payload)


def press(key=3, action=1):
    return {"method": "v.oai.hid", "params": {"k": f"AG{key:02d}", "act": action}}


def test_input_reaches_only_its_saved_action_through_main_thread_delivery():
    target, opened = Target(), []
    controls = DeckControlSettings(enabled=True, bindings=((3, DeckAction("open_usage")),))
    dispatch = DeckInputDispatch(target, controls)
    dispatch.receive([press(2), press(3)])
    assert len(target.calls) == 1
    assert opened == []
    executor = MacDeckActionExecutor(open_usage=lambda: opened.append("usage"))
    receipts = dispatch.deliver(target.calls[0], executor)
    assert opened == ["usage"]
    assert receipts[0].success
    assert dispatch.deliver(target.calls[0], executor) == ()
    assert opened == ["usage"]


def test_disabled_input_does_not_schedule_an_action():
    target = Target()
    dispatch = DeckInputDispatch(target, DeckControlSettings(bindings=((3, DeckAction("open_usage")),)))
    dispatch.receive([press()])
    assert not target.calls


def test_slow_main_thread_gets_one_bounded_batch_and_expired_actions_are_discarded():
    target, now, opened = Target(), [10.0], []
    controls = DeckControlSettings(enabled=True, bindings=((3, DeckAction("open_usage")),))
    dispatch = DeckInputDispatch(target, controls, clock=lambda: now[0])
    for _ in range(200):
        dispatch.receive([press(action=0), press()])
    assert len(target.calls) == 1
    now[0] = 10.6
    assert dispatch.deliver(target.calls[0], MacDeckActionExecutor(open_usage=lambda: opened.append(1))) == ()
    assert not opened
    dispatch.receive([press(action=0), press()])
    assert len(target.calls) == 2


def test_close_revokes_already_scheduled_actions():
    target, opened = Target(), []
    controls = DeckControlSettings(enabled=True, bindings=((3, DeckAction("open_usage")),))
    dispatch = DeckInputDispatch(target, controls)
    dispatch.receive([press()])
    dispatch.close()
    assert dispatch.deliver(target.calls[0], MacDeckActionExecutor(open_usage=lambda: opened.append(1))) == ()
    assert not opened
