"""One interrupt budget, and Focus is an input to it.

The owner's law, locked:

  * Blocked agents and critical states blink until they are dealt with,
    and escalate THROUGH an active Focus. "I'm in a meeting" never means
    "let the thing that is blocked on me go dark."
  * Usage, weather, messages and completions must not. They get a burst
    of exactly three, and nothing at all while a Focus is on.
  * Nothing above 2Hz, ever.

Before this, Focus was a dimming afterthought (focus_sync_scale_factor
scaled brightness AFTER a signal had already won the bar) plus an opt-in
per-Focus policy that defaulted to "all" and therefore did nothing, and
"a burst of three" was re-derived at each effect site: a blink played 3,
a double-blink 2, and everything else held for "about two seconds",
which is a duration and not a burst at all.

Every test here fails without the change it names.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sidepulse import signals
from sidepulse.models import AgentMode, AgentStatus
from sidepulse.settings import AgentMonitorSettings

COURTESY_KINDS = (
    signals.SIGNAL_QUOTA,
    signals.SIGNAL_WEATHER,
    signals.SIGNAL_NOTIFICATION,
    signals.SIGNAL_COMPLETION,
    signals.SIGNAL_CALENDAR,
    signals.SIGNAL_REMINDERS,
)
CRITICAL_KINDS = (
    signals.INTERRUPT_ASK,
    signals.INTERRUPT_FAILURE,
    signals.INTERRUPT_ESCALATION,
    signals.SIGNAL_LOW_BATTERY,
)


def signal_hold_seconds(style, *, burst=signals.DEFAULT_ALERT_BURST):
    """Local oracle (the src helper was deleted 2026-08-26: tests were
    its only callers). Same arithmetic as grant_interrupt, for a caller
    holding a style rather than a grant."""
    return (
        signals.normalize_alert_burst(burst) * signals.budgeted_style(style).speed_seconds
        + signals.INTERRUPT_SETTLE_SECONDS
    )



def _focus() -> signals.InterruptBudget:
    return signals.InterruptBudget(focus_active=True)


# --- A. Focus is an input to the interrupt decision -------------------


@pytest.mark.parametrize("kind", COURTESY_KINDS)
def test_a_focus_holds_every_courtesy_signal(kind: str) -> None:
    """The owner's sentence, as an assertion: "I'm in a meeting -- do
    not blink at me." Usage, weather, messages and completions all sit
    on this rung."""
    grant = signals.grant_interrupt(kind, budget=_focus())
    assert not grant.allowed
    assert grant.reason == signals.INTERRUPT_REFUSED_FOCUS
    assert grant.repetitions == 0
    assert grant.hold_seconds == 0.0


@pytest.mark.parametrize("kind", CRITICAL_KINDS)
def test_a_focus_never_holds_a_critical_signal(kind: str) -> None:
    """A blocked agent is the one thing this app exists for. A meeting
    does not make it stop asking."""
    grant = signals.grant_interrupt(kind, budget=_focus())
    assert grant.allowed
    assert grant.reason == signals.INTERRUPT_GRANTED
    # Ratified 2026-08-19: finite arrival taps + a steady anchor that
    # STANDS until dealt with -- not perpetual blinking.
    assert grant.stands_until_dealt_with
    assert grant.repetitions == signals.ATTENTION_ARRIVAL_TAPS
    assert grant.hold_seconds is None


def test_the_owners_four_named_courtesy_signals_are_declared_courtesy() -> None:
    """Named one by one, because the list is the law and not something
    to be inferred: usage, weather, messages and completions must not
    escalate through a Focus."""
    assert signals.interrupt_class(signals.SIGNAL_QUOTA) == signals.INTERRUPT_COURTESY
    assert signals.interrupt_class(signals.SIGNAL_WEATHER) == signals.INTERRUPT_COURTESY
    assert (
        signals.interrupt_class(signals.SIGNAL_NOTIFICATION)
        == signals.INTERRUPT_COURTESY
    )
    assert (
        signals.interrupt_class(signals.SIGNAL_COMPLETION) == signals.INTERRUPT_COURTESY
    )


def test_only_an_explicit_silent_focus_reaches_a_critical_signal_and_only_its_sound() -> None:
    """The single crack Focus has in the critical rung: a per-Focus
    "Silent" hushes the escalation chime. It never takes the light."""
    ordinary = signals.grant_interrupt(
        signals.INTERRUPT_ESCALATION,
        budget=signals.InterruptBudget(
            focus_active=True, focus_policy=signals.FOCUS_POLICY_ASKS_ONLY
        ),
    )
    assert ordinary.allowed and ordinary.audible

    silent = signals.grant_interrupt(
        signals.INTERRUPT_ESCALATION,
        budget=signals.InterruptBudget(
            focus_active=True, focus_policy=signals.FOCUS_POLICY_SILENT
        ),
    )
    assert silent.allowed, "silent takes the sound, never the light"
    assert not silent.audible


def test_quiet_hour_still_lets_weather_through_but_a_focus_does_not() -> None:
    """Quiet Hour is the owner's own manual snooze and has always let a
    severe-weather warning through. Focus is not Quiet Hour: the owner
    put weather in the "must not" list by name. Both facts, one table."""
    snoozed = signals.grant_interrupt(
        signals.SIGNAL_WEATHER, budget=signals.InterruptBudget(quiet_hour=True)
    )
    assert snoozed.allowed

    in_a_meeting = signals.grant_interrupt(signals.SIGNAL_WEATHER, budget=_focus())
    assert not in_a_meeting.allowed
    assert in_a_meeting.reason == signals.INTERRUPT_REFUSED_FOCUS

    # And the exemption is weather's alone -- it does not leak to the
    # rest of the courtesy rung.
    for kind in (signals.SIGNAL_COMPLETION, signals.SIGNAL_QUOTA):
        refused = signals.grant_interrupt(
            kind, budget=signals.InterruptBudget(quiet_hour=True)
        )
        assert not refused.allowed
        assert refused.reason == signals.INTERRUPT_REFUSED_QUIET_HOUR


def test_no_focus_and_no_snooze_lets_the_courtesy_signals_through() -> None:
    """The negative guard: the hold must be caused by a Focus, not
    permanently on. A budget that refuses everything would pass every
    test above and be a broken product."""
    for kind in COURTESY_KINDS:
        grant = signals.grant_interrupt(kind, budget=signals.InterruptBudget())
        assert grant.allowed, kind
        assert grant.reason == signals.INTERRUPT_GRANTED


# --- B. One budget ----------------------------------------------------


def test_a_courtesy_burst_is_exactly_three_and_is_configurable() -> None:
    """"Everything else is a burst of exactly 3." One number, one place,
    and a dial for the owner who wants a different one."""
    default = signals.grant_interrupt(
        signals.SIGNAL_QUOTA,
        budget=signals.InterruptBudget(),
        style=signals.DEFAULT_SIGNAL_STYLES[signals.SIGNAL_QUOTA],
    )
    assert default.repetitions == 3
    assert default.hold_seconds == pytest.approx(
        3 * default.cycle_seconds + signals.INTERRUPT_SETTLE_SECONDS
    )

    louder = signals.grant_interrupt(
        signals.SIGNAL_QUOTA,
        budget=signals.InterruptBudget(burst=5),
        style=signals.DEFAULT_SIGNAL_STYLES[signals.SIGNAL_QUOTA],
    )
    assert louder.repetitions == 5
    assert louder.hold_seconds > default.hold_seconds


def test_a_nonsense_burst_falls_back_to_the_locked_default() -> None:
    for nonsense in (0, -4, None, "three", True, 10_000):
        budget = signals.InterruptBudget(burst=nonsense).normalized()
        assert 1 <= budget.burst <= signals.MAX_ALERT_BURST


def test_an_undeclared_kind_is_refused() -> None:
    """Fail closed. A signal added later that forgets to say which rung
    it is on does not get to blink at someone during a meeting by
    default -- it gets nothing until it declares itself."""
    grant = signals.grant_interrupt("brand-new-signal", budget=signals.InterruptBudget())
    assert not grant.allowed
    assert grant.reason == signals.INTERRUPT_REFUSED_UNDECLARED


def test_every_styled_signal_declares_an_interrupt_class() -> None:
    """The style catalogue and the class table are the same population.
    A style that can claim the bar with no declared class would be
    silently refused forever -- a feature nobody can use."""
    undeclared = sorted(
        key
        for key in signals.DEFAULT_SIGNAL_STYLES
        if signals.interrupt_class(key) is None
    )
    assert not undeclared, undeclared


def test_the_burst_law_and_the_hold_helper_agree() -> None:
    """signal_hold_seconds is the same arithmetic for callers holding a
    style rather than a grant -- not a second, drifting copy of it."""
    for key, style in signals.DEFAULT_SIGNAL_STYLES.items():
        if signals.interrupt_class(key) != signals.INTERRUPT_COURTESY:
            continue
        for burst in (1, 3, 7):
            grant = signals.grant_interrupt(
                key, budget=signals.InterruptBudget(burst=burst), style=style
            )
            assert grant.hold_seconds == pytest.approx(
                signal_hold_seconds(style, burst=burst)
            )


# --- Nothing above 2Hz, ever ------------------------------------------


def test_nothing_the_budget_grants_ever_repeats_faster_than_two_hertz() -> None:
    """Every kind, at every speed the settings pane can dial, including
    the 0.1s floor of the style catalogue (10Hz) and the notification
    default's 0.3s (3.33Hz). A cadence that would strobe is slowed;
    nothing else about the style moves."""
    speeds = (
        signals.MIN_SPEED_SECONDS,
        0.2,
        0.3,
        0.49,
        0.5,
        0.9,
        3.6,
        signals.MAX_SPEED_SECONDS,
    )
    for kind in (*COURTESY_KINDS, *CRITICAL_KINDS):
        for speed in speeds:
            for pattern in signals.SIGNAL_PATTERNS:
                style = signals.SignalStyle("#FFFFFF", pattern, speed, 1.0)
                grant = signals.grant_interrupt(
                    kind, budget=signals.InterruptBudget(), style=style
                )
                assert grant.hertz <= signals.MAX_INTERRUPT_HZ, (kind, speed, pattern)
                assert grant.cycle_seconds >= signals.MIN_INTERRUPT_CYCLE_SECONDS


def test_the_2hz_floor_slows_a_cadence_and_changes_nothing_else() -> None:
    fast = signals.SignalStyle("#34C759", signals.PATTERN_BLINK, 0.1, 0.6)
    slowed = signals.budgeted_style(fast)
    assert slowed.speed_seconds == signals.MIN_INTERRUPT_CYCLE_SECONDS
    assert (slowed.color, slowed.pattern, slowed.intensity) == (
        "#34C759",
        signals.PATTERN_BLINK,
        0.6,
    )
    # A style already inside the law is untouched -- every shipped
    # default is, so no default cadence moves.
    for style in signals.DEFAULT_SIGNAL_STYLES.values():
        if style.speed_seconds >= signals.MIN_INTERRUPT_CYCLE_SECONDS:
            assert signals.budgeted_style(style).speed_seconds == style.speed_seconds


def test_a_default_burst_of_three_at_the_2hz_floor_is_the_shortest_burst() -> None:
    """The floor is a floor, not a fixed cadence: a slow signal keeps
    its own pace."""
    fastest = signal_hold_seconds(
        signals.SignalStyle("#FFFFFF", signals.PATTERN_BLINK, 0.1, 1.0)
    )
    assert fastest == pytest.approx(3 * 0.5 + signals.INTERRUPT_SETTLE_SECONDS)
    slow = signal_hold_seconds(
        signals.SignalStyle("#FFFFFF", signals.PATTERN_BREATHE, 2.6, 1.0)
    )
    assert slow == pytest.approx(3 * 2.6 + signals.INTERRUPT_SETTLE_SECONDS)


# --- Settings ---------------------------------------------------------


def test_the_burst_budget_round_trips_and_a_junk_value_cannot_land() -> None:
    from sidepulse.settings import load_settings, save_settings

    assert AgentMonitorSettings().alert_burst == signals.DEFAULT_ALERT_BURST
    assert AgentMonitorSettings().with_alert_burst(5).alert_burst == 5
    assert (
        AgentMonitorSettings().with_alert_burst(0).alert_burst
        == signals.DEFAULT_ALERT_BURST
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        save_settings(AgentMonitorSettings().with_alert_burst(5), path)
        assert load_settings(path).alert_burst == 5


# --- Wired: the controller's effect sites all ask the one gate --------


@pytest.fixture()
def controller(tmp_path, monkeypatch):
    """A StatusBarController with nothing reaching real disk or hardware
    and no live Focus leaking in from the machine running the gate."""
    settings_path = tmp_path / "settings.json"
    for target in (
        "sidepulse.settings.default_settings_path",
        "sidepulse.status_bar.default_settings_path",
    ):
        monkeypatch.setattr(target, lambda _p=settings_path: _p)
    monkeypatch.setattr(
        "sidepulse.status_bar.default_latest_state_path",
        lambda: tmp_path / "latest.json",
    )
    monkeypatch.setattr("sidepulse.status_bar.discover_devices", lambda: [])
    monkeypatch.setattr(
        "sidepulse.focus_sync.active_focus_mode_identifiers", lambda: []
    )
    from sidepulse import status_bar

    built = status_bar.StatusBarController.alloc().init()
    yield built
    worker = getattr(built, "led_worker_thread", None)
    if worker is not None and worker.is_alive():
        worker.join(timeout=5.0)


def _turn_on_a_focus(controller) -> None:
    controller._focus_ids_cache = (float("inf"), ["com.apple.donotdisturb.mode.default"])


def _device(status_bar, display=None):
    return status_bar.StatusBarDevice(
        device_id="SidePulsePro",
        name="SidePulse Pro",
        root=Path("/Volumes/SidePulsePro"),
        target=Path("/Volumes/SidePulsePro/LEDS.LED"),
        connected=True,
        display=display or status_bar.LED_DISPLAY_AGENT,
    )


def _status(provider: str, mode: AgentMode) -> AgentStatus:
    return AgentStatus(
        provider=provider,
        agent_id=f"{provider}:session:main",
        display_name=provider.title(),
        mode=mode,
        updated_at=datetime.now(timezone.utc),
        event_name="Stop",
        session_id="main",
    )


def test_a_focus_takes_the_courtesy_claims_off_the_bar(controller) -> None:
    """The wired proof of A: with a Focus on, a live quota blink, a live
    reminder, a live completion sweep and a live calendar glow all stop
    claiming the LEDs -- and the SAME conditions claim them without
    one."""
    from sidepulse import status_bar

    device = _device(status_bar)
    controller.settings = (
        controller.settings.with_calendar_alerts_enabled(True)
        .with_reminder_alerts_enabled(True)
        .with_completion_sweep_enabled(True)
    )
    deadline = time.monotonic() + 60.0
    controller.completion_sweep_until = deadline
    controller.reminders_glow_until = deadline
    controller.calendar_glow_until = deadline

    # Reminders outranks completion in the precedence order, so that is
    # the claim standing before the Focus lands.
    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_REMINDERS
    )

    _turn_on_a_focus(controller)
    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_AGENT
    )
    for kind in (
        signals.SIGNAL_COMPLETION,
        signals.SIGNAL_REMINDERS,
        signals.SIGNAL_CALENDAR,
        signals.SIGNAL_QUOTA,
        signals.SIGNAL_WEATHER,
    ):
        assert not controller.may_interrupt(kind), kind


def test_a_focus_leaves_the_critical_claims_exactly_where_they_were(
    controller,
) -> None:
    """The wired proof that critical escalates through Focus: a failed
    session still claims the bar with a Focus on."""
    from sidepulse import status_bar

    device = _device(status_bar)
    _turn_on_a_focus(controller)
    for kind in (
        signals.INTERRUPT_ASK,
        signals.INTERRUPT_FAILURE,
        signals.INTERRUPT_ESCALATION,
        signals.SIGNAL_LOW_BATTERY,
    ):
        assert controller.may_interrupt(kind), kind

    with patch.object(
        type(controller), "escalation_takeover_active", lambda _self: True
    ):
        assert (
            controller.active_led_display_kind_for_device(device, None)
            == status_bar.LED_DISPLAY_ESCALATION
        )


def test_a_completion_during_a_focus_passes_unmarked(controller) -> None:
    """The sweep is a moment, and a moment the owner spent in a meeting
    is not replayed afterwards -- it simply never fires."""
    controller.settings = controller.settings.with_completion_sweep_enabled(True)
    _turn_on_a_focus(controller)
    controller.track_completions((_status("codex", AgentMode.WORKING),))
    controller.track_completions((_status("codex", AgentMode.COMPLETED),))
    assert controller.completion_sweep_until == 0.0
    assert controller.completion_sweep_color is None


def test_the_same_completion_without_a_focus_claims_the_bar(controller) -> None:
    """The negative guard for the test above: the suppression has to be
    caused by the Focus, not by the sweep being broken."""
    controller.settings = controller.settings.with_completion_sweep_enabled(True)
    controller.track_completions((_status("codex", AgentMode.WORKING),))
    controller.track_completions((_status("codex", AgentMode.COMPLETED),))
    assert controller.completion_sweep_until > time.monotonic()


def test_a_completion_banner_during_a_focus_is_not_delivered(controller) -> None:
    """A macOS banner is an interrupt too, and asks the same gate.

    Both directions, because the early-return has four reasons and only
    one of them is the Focus: without a Focus the SAME call delivers.
    """
    from sidepulse.operator_state import InterruptionClass

    delivered: list[object] = []
    controller.settings = (
        controller.settings.with_completion_notification_enabled(True)
    )
    controller._deliver_semantic_notification = (
        lambda *args, **kwargs: delivered.append(args)
    )
    status = _status("codex", AgentMode.COMPLETED)
    controller._notification_events_by_work_key = {
        getattr(status, "work_key", None): SimpleNamespace(
            key="event-key",
            interruption_class=InterruptionClass.COURTESY,
        )
    }

    _turn_on_a_focus(controller)
    controller.post_completion_notification(status)
    assert delivered == [], "a Focus holds the banner"

    controller._focus_ids_cache = (float("inf"), [])
    controller.post_completion_notification(status)
    assert delivered, "and without one, the very same call delivers"


def test_a_focus_takes_the_weather_heartbeat_off_the_bar(controller) -> None:
    """Weather is the one the owner had to name twice: it outranks every
    routine signal AND it is on the "do not blink at me in a meeting"
    list. Both, at the wired claim."""
    from sidepulse import status_bar

    device = _device(status_bar)
    controller.settings = controller.settings.with_weather_alerts_enabled(True)
    controller.weather_alert_active = True

    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_WEATHER
    )

    _turn_on_a_focus(controller)
    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_AGENT
    )

    # Quiet Hour is not Focus: the emergency still lands there.
    controller._focus_ids_cache = (float("inf"), [])
    controller.quiet_until_monotonic = time.monotonic() + 60.0
    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_WEATHER
    )


def test_the_escalation_chime_asks_the_gate_rather_than_re_deriving_it(
    controller,
) -> None:
    """The chime's rule ("a silent Focus hushes the sound") is today the
    same sentence the budget produces -- which is exactly why this needs
    a guard. An effect site that keeps its own copy of a rule agrees
    until the rule changes, and then disagrees silently. So: the site
    must consult the gate, and must honour what it says.
    """
    from sidepulse import status_bar

    asked: list[str] = []
    verdict = {"audible": False}
    real_grant = status_bar.StatusBarController.interrupt_grant

    def recording_grant(self, kind):
        grant = real_grant(self, kind)
        if kind == signals.INTERRUPT_ESCALATION:
            asked.append(kind)
            return replace(grant, audible=verdict["audible"])
        return grant

    controller.settings = controller.settings.with_escalation_tier(
        signals.ESCALATION_TIER_CHIME
    )
    controller.ask_blocked_since = time.monotonic() - 10_000.0
    controller.escalation_last_stage = 0
    controller.escalation_chimed = False
    sounds: list[str] = []

    with (
        patch.object(status_bar.StatusBarController, "interrupt_grant", recording_grant),
        patch.object(
            status_bar.StatusBarController, "fire_escalation_webhook", lambda *_a: None
        ),
        patch.dict(
            "sys.modules",
            {"AppKit": SimpleNamespace(NSSound=SimpleNamespace(
                soundNamed_=lambda name: SimpleNamespace(
                    play=lambda: sounds.append(name)
                )
            ))},
        ),
    ):
        controller.apply_escalation()
        assert asked, "the chime never asked the one gate"
        assert sounds == [], "and it honoured a refusal"

        verdict["audible"] = True
        controller.escalation_chimed = False
        controller.escalation_last_stage = 0
        controller.apply_escalation()
        assert sounds == ["Glass"], "and it honours a grant"


def test_the_burst_budget_sets_how_long_a_sweep_claims_the_bar(controller) -> None:
    """The wired proof of B: the "burst of exactly 3" number is the one
    in the budget, and moving it moves the surface."""
    controller.settings = controller.settings.with_completion_sweep_enabled(True)
    controller.track_completions((_status("codex", AgentMode.WORKING),))
    started = time.monotonic()
    controller.track_completions((_status("codex", AgentMode.COMPLETED),))
    style = controller.settings.signal_style(signals.SIGNAL_COMPLETION)
    three = controller.completion_sweep_until - started
    assert three == pytest.approx(
        signal_hold_seconds(style, burst=3), abs=0.5
    )

    controller.settings = controller.settings.with_alert_burst(6)
    controller.last_agent_modes = {}
    controller.track_completions((_status("codex", AgentMode.WORKING),))
    restarted = time.monotonic()
    controller.track_completions((_status("codex", AgentMode.COMPLETED),))
    six = controller.completion_sweep_until - restarted
    assert six > three
    assert six == pytest.approx(signal_hold_seconds(style, burst=6), abs=0.5)


def test_a_signal_dialled_into_strobe_range_is_slowed_before_the_hardware(
    controller,
) -> None:
    """The 2Hz law reaching an actual program. The settings pane still
    offers 0.1s (a preview thumbnail is not an interrupt); what plays AT
    the owner is floored at 500ms."""
    controller.settings = controller.settings.with_signal_style(
        signals.SIGNAL_QUOTA,
        signals.SignalStyle("#FFB020", signals.PATTERN_DOUBLE_BLINK, 0.1, 1.0),
    )
    assert controller.settings.signal_style(
        signals.SIGNAL_QUOTA
    ).speed_seconds == pytest.approx(0.1)
    played = controller.budgeted_signal_style(signals.SIGNAL_QUOTA)
    assert played.speed_seconds == signals.MIN_INTERRUPT_CYCLE_SECONDS

    from sidepulse.led_status import style_to_program

    program = style_to_program(played, 255)
    durations = [
        int(token.removesuffix("ms"))
        for line in program.splitlines()
        for token in line.split()
        if token.endswith("ms") and token.removesuffix("ms").isdigit()
    ]
    assert durations, program
    # The whole knock -- two taps, the gap between them, and the rest after
    # them -- is one 500ms cycle, which is the cadence the law permits. (It
    # used to be "durations[:2] == 500": a double-blink was two even
    # on/off halves, i.e. blink truncated. Wave 7 gave the knock its rest,
    # so the cycle is four spans rather than two, and the law is still the
    # sum of them.)
    assert sum(durations) == 500
    assert max(durations) < 500


def test_an_escalation_chime_is_hushed_only_by_an_explicit_silent_focus(
    controller,
) -> None:
    """Wired: an ordinary Focus does not take the chime (critical
    escalates through Focus); a per-Focus "Silent" does."""
    _turn_on_a_focus(controller)
    assert controller.interrupt_grant(signals.INTERRUPT_ESCALATION).audible

    controller.settings = controller.settings.with_focus_signal_policy(
        "com.apple.donotdisturb.mode.default", signals.FOCUS_POLICY_SILENT
    )
    assert not controller.interrupt_grant(signals.INTERRUPT_ESCALATION).audible
    # And the light is untouched either way.
    assert controller.may_interrupt(signals.INTERRUPT_ESCALATION)


def test_the_controller_budget_carries_the_three_inputs_the_law_needs(
    controller,
) -> None:
    """The gate is fed, not hardcoded: burst, Focus, Quiet Hour."""
    controller.settings = controller.settings.with_alert_burst(4)
    _turn_on_a_focus(controller)
    controller.quiet_until_monotonic = time.monotonic() + 60.0
    budget = controller.interrupt_budget()
    assert budget.burst == 4
    assert budget.focus_active is True
    assert budget.quiet_hour is True


def test_a_reminder_that_arrives_during_a_focus_is_not_replayed_later(
    controller,
) -> None:
    """The watermark advances even when the burst is refused, so the
    glow does not queue up and fire the moment the meeting ends."""
    from sidepulse.status_bar import RemindersObservationResult

    # Identifiers are opaque 64-char hex digests by contract.
    first = hashlib.sha256(b"reminder-1").hexdigest()
    second = hashlib.sha256(b"reminder-2").hexdigest()
    controller.settings = controller.settings.with_reminder_alerts_enabled(True)
    _turn_on_a_focus(controller)
    # refresh_ walks the live device inventory; this test is about the
    # glow deadline, not the redraw.
    with patch.object(type(controller), "refresh_", lambda _self, _sender: None):
        controller._apply_reminders_observation_result(
            RemindersObservationResult(available=True, identifiers=(first,))
        )
        assert controller.reminders_glow_until == 0.0
        assert first in controller.reminders_seen

        controller._focus_ids_cache = (float("inf"), [])
        controller._apply_reminders_observation_result(
            RemindersObservationResult(available=True, identifiers=(first,))
        )
        assert controller.reminders_glow_until == 0.0, "already seen; never replayed"

        controller._apply_reminders_observation_result(
            RemindersObservationResult(available=True, identifiers=(second,))
        )
        assert controller.reminders_glow_until > 0.0, "a NEW reminder still glows"


def test_courtesy_signals_held_is_derived_from_the_budget(controller) -> None:
    """The old scattered predicate still answers, but it no longer
    decides anything -- it reads the one gate."""
    assert not controller.courtesy_signals_held()
    _turn_on_a_focus(controller)
    assert controller.courtesy_signals_held()


def test_an_unreadable_focus_never_holds_a_signal(controller) -> None:
    """No Full Disk Access means "can't tell", and can't-tell must read
    as "no Focus" -- the same fail-safe every other Focus caller takes.
    A permission the owner never granted must not silently mute the
    app."""
    from sidepulse import focus_sync

    controller._focus_ids_cache = None
    with patch.object(
        focus_sync,
        "active_focus_mode_identifiers",
        side_effect=focus_sync.FocusSyncUnavailableError("no FDA"),
    ):
        assert not controller.focus_is_active()
        assert controller.may_interrupt(signals.SIGNAL_COMPLETION)


def test_the_dim_afterthought_and_the_budget_are_different_questions(
    controller,
) -> None:
    """Focus sync dims a signal that already won the bar. The budget
    decides whether it may fire at all. The regression this guards: the
    dimming toggle being off must not re-open the interrupt."""
    controller.settings = controller.settings.with_focus_sync_enabled(False)
    _turn_on_a_focus(controller)
    assert controller.focus_sync_scale_factor() == 1.0, "no dimming"
    assert not controller.may_interrupt(signals.SIGNAL_COMPLETION), "still held"


def test_a_stale_focus_reading_is_refreshed_within_a_second(controller) -> None:
    """The budget reads Focus through the 1s cache the LED path already
    uses -- it must not pin an ended Focus forever."""
    controller._focus_ids_cache = (time.monotonic() - 5.0, ["com.apple.focus.work"])
    assert not controller.focus_is_active()


def test_charging_trickle_claims_the_idle_strip_and_yields_to_agents(
    controller,
) -> None:
    """The ambient charging display: plugged-in and idle claims the
    battery display; ANY non-idle lifecycle -- working, asking, freshly
    done -- takes the strip back; full charge and the off-switch both
    drop the claim."""
    from sidepulse import status_bar
    from sidepulse.attention import LifecycleMode
    from sidepulse.battery import BatterySnapshot

    device = _device(status_bar)
    charging = BatterySnapshot(percent=60, is_charging=True, is_plugged=True)
    controller.current_attention_projection = None

    assert controller.settings.battery_charging_idle_enabled
    assert (
        controller.active_led_display_kind_for_device(device, charging)
        == status_bar.LED_DISPLAY_BATTERY
    )

    # Full: the claim drops, idle takes back over.
    full = BatterySnapshot(percent=100, is_charged=True, is_plugged=True)
    assert (
        controller.active_led_display_kind_for_device(device, full)
        == status_bar.LED_DISPLAY_AGENT
    )

    # On battery power: no claim.
    unplugged = BatterySnapshot(percent=60)
    assert (
        controller.active_led_display_kind_for_device(device, unplugged)
        == status_bar.LED_DISPLAY_AGENT
    )

    # Anything non-idle in the lifecycle outranks the trickle.
    class _Projection:
        pass

    for mode in (
        LifecycleMode.ACTIVE,
        LifecycleMode.WAITING,
        LifecycleMode.COMPLETED_RECENTLY,
        LifecycleMode.FAILED_VISIBLE,
    ):
        projection = _Projection()
        projection.lifecycle_mode = mode
        controller.current_attention_projection = projection
        assert (
            controller.active_led_display_kind_for_device(device, charging)
            == status_bar.LED_DISPLAY_AGENT
        ), mode

    projection = _Projection()
    projection.lifecycle_mode = LifecycleMode.IDLE
    controller.current_attention_projection = projection
    assert (
        controller.active_led_display_kind_for_device(device, charging)
        == status_bar.LED_DISPLAY_BATTERY
    )

    # The off-switch is respected.
    controller.settings = controller.settings.with_battery_charging_idle(False)
    assert (
        controller.active_led_display_kind_for_device(device, charging)
        == status_bar.LED_DISPLAY_AGENT
    )


def test_charging_trickle_never_steals_a_pinned_display_or_a_timebox(
    controller,
) -> None:
    """Hostile-review regression: the first draft of the trickle claim
    sat ABOVE Timer/Studio/Runway and ignored the per-device display
    pin, so every pinned device silently became a battery meter while
    the Mac charged. The trickle claims ONLY default-display devices
    and yields to a running timebox."""
    from unittest.mock import patch

    from sidepulse import status_bar
    from sidepulse.battery import BatterySnapshot

    charging = BatterySnapshot(percent=60, is_charging=True, is_plugged=True)
    controller.current_attention_projection = None
    assert controller.settings.battery_charging_idle_enabled

    for pinned in (
        status_bar.LED_DISPLAY_STUDIO,
        status_bar.LED_DISPLAY_TIMER,
    ):
        device = _device(status_bar, display=pinned)
        assert (
            controller.active_led_display_kind_for_device(device, charging)
            == pinned
        ), pinned

    # A running timebox owns the strip even on a default-display device.
    default_device = _device(status_bar)
    with patch.object(type(controller), "timebox_active", lambda _self: True):
        assert (
            controller.active_led_display_kind_for_device(
                default_device, charging
            )
            == status_bar.LED_DISPLAY_TIMER
        )


def test_reset_celebration_claims_the_strip_and_respects_focus(controller) -> None:
    """The confetti moment: claims the strip when armed, refuses under a
    Focus (courtesy), and renders a finite firmware-valid program that
    is NOT gated behind quota_alerts_enabled."""
    import time as time_module

    from sidepulse import status_bar
    from sidepulse.celebrations import reset_celebration_program
    from sidepulse.firmware_validation import validate_firmware_program

    device = _device(status_bar)
    controller.quota_reset_celebration_until = time_module.monotonic() + 6.0
    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_RESET_CELEBRATION
    )

    _turn_on_a_focus(controller)
    assert (
        controller.active_led_display_kind_for_device(device, None)
        == status_bar.LED_DISPLAY_AGENT
    )

    for led_count in (2, 8):
        program = reset_celebration_program(255, led_count=led_count)
        result = validate_firmware_program(program, led_count=led_count)
        assert result.accepted, (led_count, result.reason)
    assert "repeat 3" in reset_celebration_program(255)  # finite by design


def test_reset_celebration_claim_outlasts_the_full_program() -> None:
    """The display claim must cover ALL cycles of the finite program --
    a hand-kept constant drifted under the 2026-08-26 choreography and
    the steady status program clipped the third cycle's fade."""
    from sidepulse.animation import RepeatStep, parse_animation, step_duration_ms
    from sidepulse.celebrations import (
        RESET_CELEBRATION_SECONDS,
        reset_celebration_program,
    )

    animation = parse_animation(reset_celebration_program(1.0), led_count=8)
    repeat_at = next(
        i for i, step in enumerate(animation.steps) if type(step) is RepeatStep
    )
    count = animation.steps[repeat_at].count
    durations = [step_duration_ms(step) for step in animation.steps]
    runtime = (
        sum(durations[:repeat_at]) * count + sum(durations[repeat_at + 1 :])
    ) / 1000.0
    assert RESET_CELEBRATION_SECONDS >= runtime
