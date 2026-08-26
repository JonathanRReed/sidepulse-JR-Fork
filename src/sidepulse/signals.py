"""The Signal Engine's data model: what may claim the light, and how it
looks while it does.

Everything that lights the bar -- low battery, notification blinks, the
calendar/reminders glows, and (via its own richer renderer) agent
status -- is a *signal* with a `SignalStyle`: color, pattern, speed,
intensity. One renderer (`led_status.style_to_program`) turns any style
into the device DSL, one precedence order decides which signal wins,
and the Signals pane's style cards edit these values with live
previews. See docs/superpowers/specs/2026-08-11-signal-engine-design.md.

The DEFAULT_SIGNAL_STYLES here are chosen so that rendering them
reproduces the pre-engine bespoke programs BYTE-FOR-BYTE -- the
migration is invisible, and a snapshot test holds it that way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

PATTERN_BREATHE = "breathe"
PATTERN_BLINK = "blink"
PATTERN_DOUBLE_BLINK = "double-blink"
PATTERN_SOLID = "solid"
PATTERN_SWEEP = "sweep"
PATTERN_RIPPLE = "ripple"
PATTERN_COMET = "comet"
PATTERN_SPARKLE = "sparkle"
PATTERN_HEARTBEAT = "heartbeat"
SIGNAL_PATTERNS = (
    PATTERN_BREATHE,
    PATTERN_BLINK,
    PATTERN_DOUBLE_BLINK,
    PATTERN_SOLID,
    PATTERN_SWEEP,
    PATTERN_RIPPLE,
    PATTERN_COMET,
    PATTERN_SPARKLE,
    PATTERN_HEARTBEAT,
)
# Patterns that play once and end (the rest loop with `repeat`).
ONE_SHOT_PATTERNS = (PATTERN_BLINK, PATTERN_DOUBLE_BLINK)
# Signals whose claim HOLDS while a condition lasts (vs. moment
# signals that fire and expire). A one-shot pattern on one of these
# would flash three times and leave the bar dark for the rest of a
# multi-hour condition -- settings.signal_style falls such choices
# back to breathe, and the style cards don't offer them.
CONTINUOUS_SIGNALS = ("low_battery", "calendar", "weather")

MIN_SPEED_SECONDS = 0.1
MAX_SPEED_SECONDS = 10.0
MIN_INTENSITY = 0.05
MAX_INTENSITY = 1.0

# Signal keys, in precedence order (first active claim wins). "agent"
# is the default and always active; battery sits just above it as a
# data display rather than a styled signal.
SIGNAL_LOW_BATTERY = "low_battery"
SIGNAL_NOTIFICATION = "notification"
SIGNAL_REMINDERS = "reminders"
SIGNAL_CALENDAR = "calendar"
SIGNAL_WEATHER = "weather"
SIGNAL_QUOTA = "quota"
SIGNAL_COMPLETION = "completion"

_HEX_RE = re.compile(r"#?[0-9a-fA-F]{6}")


def _normalize_color(raw: object, fallback: str) -> str:
    if isinstance(raw, str) and _HEX_RE.fullmatch(raw.strip()):
        return "#" + raw.strip().lstrip("#").upper()
    return fallback


@dataclass(frozen=True)
class SignalStyle:
    color: str
    pattern: str
    speed_seconds: float
    intensity: float
    finite_repetitions: int | None = None

    def normalized(self) -> SignalStyle:
        return replace(
            self,
            color=_normalize_color(self.color, "#FFFFFF"),
            pattern=self.pattern if self.pattern in SIGNAL_PATTERNS else PATTERN_BREATHE,
            speed_seconds=max(MIN_SPEED_SECONDS, min(MAX_SPEED_SECONDS, float(self.speed_seconds))),
            intensity=max(MIN_INTENSITY, min(MAX_INTENSITY, float(self.intensity))),
            finite_repetitions=(
                self.finite_repetitions
                if type(self.finite_repetitions) is int
                and 1 <= self.finite_repetitions <= 2
                else None
            ),
        )

    def to_dict(self) -> dict:
        return {
            "color": self.color,
            "pattern": self.pattern,
            "speed_seconds": self.speed_seconds,
            "intensity": self.intensity,
        }

    @classmethod
    def from_dict(cls, raw: object, fallback: SignalStyle) -> SignalStyle:
        if not isinstance(raw, dict):
            return fallback
        try:
            return cls(
                # An invalid color falls back to the SIGNAL's default,
                # not normalized()'s generic white -- {"color": "red"}
                # must yield the documented default, not a white glow.
                color=_normalize_color(raw.get("color", fallback.color), fallback.color),
                pattern=str(raw.get("pattern", fallback.pattern)),
                speed_seconds=float(raw.get("speed_seconds", fallback.speed_seconds)),
                intensity=float(raw.get("intensity", fallback.intensity)),
                finite_repetitions=fallback.finite_repetitions,
            ).normalized()
        except (TypeError, ValueError):
            return fallback


# Byte-identical to the pre-engine bespoke programs (snapshot-tested):
#   low_battery: "off 400ms cosine\n#E01010 3600ms pulse\nrepeat"
#   calendar:    "off 400ms cosine\n#A45CFF 2600ms pulse\nrepeat"
#   notification (blink, 0.3s cycle): 170ms flash / 130ms gap x3
DEFAULT_SIGNAL_STYLES: dict[str, SignalStyle] = {
    SIGNAL_LOW_BATTERY: SignalStyle("#E01010", PATTERN_BREATHE, 3.6, 1.0),
    SIGNAL_NOTIFICATION: SignalStyle("#34C759", PATTERN_BLINK, 0.3, 1.0),
    SIGNAL_REMINDERS: SignalStyle("#FFB340", PATTERN_BREATHE, 1.5, 1.0),
    SIGNAL_CALENDAR: SignalStyle("#A45CFF", PATTERN_BREATHE, 2.6, 1.0),
    # Emergency weather: an urgent heartbeat in warning pink-red --
    # unmistakable, and unlike any agent state's rhythm or hue.
    SIGNAL_WEATHER: SignalStyle("#FF2D55", PATTERN_HEARTBEAT, 1.4, 1.0),
    # Completion sweep: ANY agent finishing claims the bar briefly, in
    # that agent's identity color when several are running -- without
    # this, a completion was invisible whenever another agent's
    # WORKING state outranked it in the aggregate.
    SIGNAL_COMPLETION: SignalStyle(
        "#00FF66",
        PATTERN_SWEEP,
        0.8,
        1.0,
        finite_repetitions=2,
    ),
    # Quota threshold crossed: a double-tap in caution amber (or the
    # provider's own color at fire time) -- a nudge, not an alarm.
    SIGNAL_QUOTA: SignalStyle("#FFB020", PATTERN_DOUBLE_BLINK, 0.9, 1.0),
}


# --- Ask escalation ---------------------------------------------------
# How loud "an agent needs you" may get as it's ignored. Stages:
#   0  fresh ask, normal rendering
#   1  ramp: brightness boost on the light surfaces
#   2  + menu-bar icon flash (catches full-screen apps)
#   3  + the user's opt-in finale: a single chime, or a full takeover
# The tier is the user's chosen CEILING; stages above it never fire.
ESCALATION_TIER_LIGHT = "light"
ESCALATION_TIER_MENU_BAR = "menu_bar"
ESCALATION_TIER_CHIME = "chime"
ESCALATION_TIER_TAKEOVER = "takeover"
# Tiers are a ceiling: every tier at or above menu_bar includes the
# stage-2 re-announcement flash on the status surfaces.
ESCALATION_TIERS_WITH_MENU_BAR = frozenset(
    {ESCALATION_TIER_MENU_BAR, ESCALATION_TIER_CHIME, ESCALATION_TIER_TAKEOVER}
)
ESCALATION_TIERS = (
    ESCALATION_TIER_LIGHT,
    ESCALATION_TIER_MENU_BAR,
    ESCALATION_TIER_CHIME,
    ESCALATION_TIER_TAKEOVER,
)
_TIER_CEILING = {
    ESCALATION_TIER_LIGHT: 1,
    ESCALATION_TIER_MENU_BAR: 2,
    ESCALATION_TIER_CHIME: 3,
    ESCALATION_TIER_TAKEOVER: 3,
}
ESCALATION_RAMP_BRIGHTNESS_BOOST = 1.15


def escalation_stage(
    blocked_elapsed_seconds: float | None,
    *,
    ramp_seconds: float,
    menu_bar_seconds: float,
    final_seconds: float,
    tier: str,
) -> int:
    """Pure: how escalated an ignored ask currently is (0-3)."""
    if blocked_elapsed_seconds is None or blocked_elapsed_seconds < ramp_seconds:
        return 0
    stage = 1
    if blocked_elapsed_seconds >= menu_bar_seconds:
        stage = 2
    if blocked_elapsed_seconds >= final_seconds:
        stage = 3
    return min(stage, _TIER_CEILING.get(tier, 2))


# The owner's numbers: a nudge at 90, a real warning at 95. Edge detection
# itself already lives in quota_crossings/quota_resets below -- these are the
# thresholds it is fed, and the burst budget spent when one fires ("a couple
# of times means a burst of three"). The budget that SPENDS it is the
# interrupt budget further down; DEFAULT_ALERT_BURST is its default.
DEFAULT_QUOTA_THRESHOLDS: tuple[float, ...] = (90.0, 95.0)
DEFAULT_ALERT_BURST = 3
MAX_QUOTA_THRESHOLDS = 4
MAX_ALERT_BURST = 10


def normalize_quota_thresholds(value: object) -> tuple[float, ...]:
    """Coerce user input into sane, ordered, deduplicated thresholds."""
    from collections.abc import Iterable

    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return DEFAULT_QUOTA_THRESHOLDS
    cleaned: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        number = float(item)
        if number != number or number <= 0.0 or number > 100.0:
            continue
        rounded = round(number, 2)
        if rounded not in cleaned:
            cleaned.append(rounded)
    if not cleaned:
        return DEFAULT_QUOTA_THRESHOLDS
    return tuple(sorted(cleaned)[:MAX_QUOTA_THRESHOLDS])


def normalize_alert_burst(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_ALERT_BURST
    number = int(value)
    return DEFAULT_ALERT_BURST if number < 1 else min(number, MAX_ALERT_BURST)


def quota_crossings(
    previous: dict[str, float],
    current: dict[str, float],
    thresholds: tuple[float, ...],
) -> list[tuple[str, float]]:
    """(window key, threshold) pairs newly crossed UPWARD since the last
    observation. A key seen for the first time never fires -- the T3
    rule: transitions alert, repaints and restarts never do."""
    fired: list[tuple[str, float]] = []
    for key, percent in current.items():
        prior = previous.get(key)
        if prior is None:
            continue
        for threshold in thresholds:
            if prior < threshold <= percent:
                fired.append((key, float(threshold)))
    return fired


def quota_resets(
    previous: dict[str, float],
    current: dict[str, float],
) -> list[str]:
    """Window keys whose usage just RESET: a large downward transition
    (>=50% before, near zero now). The mirror image of quota_crossings,
    same first-observation silence -- restarts never celebrate."""
    reset = []
    for key, percent in current.items():
        prior = previous.get(key)
        if prior is None:
            continue
        if prior >= 50.0 and percent <= 10.0:
            reset.append(key)
    return reset


# --- The interrupt budget ---------------------------------------------
#
# ONE gate. Every request to interrupt the owner -- an LED claim, a
# burst, the escalation chime -- passes through grant_interrupt() and
# gets back an InterruptGrant saying whether it may fire, for how many
# repetitions, at what cadence, and whether it may make a sound.
#
# The owner's law, stated once here instead of re-derived at each
# effect site:
#
#   CRITICAL -- an agent blocked on you, a failed session, the
#     ignored-ask takeover, a dying battery. Blinks until it is dealt
#     with, and escalates THROUGH an active Focus. "I'm in a meeting"
#     never means "let the thing that is blocked on me go dark".
#
#   COURTESY -- usage, weather, messages, completions, calendar,
#     reminders. A burst of exactly `burst` repetitions, then back to
#     normal -- and nothing at all while a Focus is on. That is the
#     owner's sentence: "I'm in a meeting -- do not blink at me."
#
#   Neither class ever repeats faster than MAX_INTERRUPT_HZ.
#
# Focus is an INPUT here, not a dimming afterthought applied later:
# focus_sync_scale_factor decides how bright a signal that already won
# is, which is a different question from whether it may fire at all.
INTERRUPT_CRITICAL = "critical"
INTERRUPT_COURTESY = "courtesy"

# Interrupt kinds. Most are the styled signals above; three are claims
# that carry urgency but own no SignalStyle -- they render from the
# agent colour language rather than from this catalogue.
INTERRUPT_ASK = "ask"
INTERRUPT_FAILURE = "failure"
INTERRUPT_ESCALATION = "escalation"
INTERRUPT_TIMEBOX = "timebox"

# RATIFIED 2026-08-19: a critical arrival announces itself with exactly
# this many finite taps, then holds a STEADY unmissable anchor until
# dealt with. "Until dealt with" is carried by the standing anchor plus
# the escalation ladder -- never by perpetual blinking, which is the
# phantom-ask fatigue this product exists to avoid. Every renderer must
# consume THIS constant rather than hard-coding its own count.
ATTENTION_ARRIVAL_TAPS = 2

# The one table that says which rung a signal sits on. A kind with no
# entry here is REFUSED: a signal added later that forgets to declare
# itself does not get to blink at someone during a meeting by default.
INTERRUPT_CLASS_BY_KIND: dict[str, str] = {
    INTERRUPT_ASK: INTERRUPT_CRITICAL,
    INTERRUPT_FAILURE: INTERRUPT_CRITICAL,
    INTERRUPT_ESCALATION: INTERRUPT_CRITICAL,
    SIGNAL_LOW_BATTERY: INTERRUPT_CRITICAL,
    SIGNAL_QUOTA: INTERRUPT_COURTESY,
    SIGNAL_WEATHER: INTERRUPT_COURTESY,
    SIGNAL_NOTIFICATION: INTERRUPT_COURTESY,
    SIGNAL_COMPLETION: INTERRUPT_COURTESY,
    SIGNAL_CALENDAR: INTERRUPT_COURTESY,
    SIGNAL_REMINDERS: INTERRUPT_COURTESY,
    # The timebox chime used to be the one raw NSSound outside this
    # table -- the only sound the budget could not hush during a Focus.
    INTERRUPT_TIMEBOX: INTERRUPT_COURTESY,
}

# Quiet Hour is the owner's own manual snooze and predates this budget.
# It has always let a severe-weather warning through. Focus does NOT --
# the owner named weather in the "must not escalate through Focus"
# list. Both facts live in this table so the difference is a
# declaration rather than an accident at one call site.
QUIET_HOUR_EXEMPT_KINDS: frozenset[str] = frozenset({SIGNAL_WEATHER})

# Per-Focus signal policies, strictest last. "silent" is the only one
# that reaches a CRITICAL interrupt, and even then only its sound.
FOCUS_POLICY_ALL = "all"
FOCUS_POLICY_ASKS_ONLY = "asks_only"
FOCUS_POLICY_SILENT = "silent"
FOCUS_SIGNAL_POLICIES = (
    FOCUS_POLICY_ALL,
    FOCUS_POLICY_ASKS_ONLY,
    FOCUS_POLICY_SILENT,
)

# "Nothing above 2Hz, ever." The style catalogue lets a speed be dialled
# down to MIN_SPEED_SECONDS (0.1s = 10Hz) because a settings-pane preview
# thumbnail is not an interrupt. Anything that INTERRUPTS the owner is
# floored here instead.
MAX_INTERRUPT_HZ = 2.0
MIN_INTERRUPT_CYCLE_SECONDS = 1.0 / MAX_INTERRUPT_HZ
# One settle beat after the last repetition, so a burst ends on dark
# rather than being cut off mid-flash.
INTERRUPT_SETTLE_SECONDS = 0.4

# Why a request was granted or refused. Carried on the grant so a
# refusal can be explained rather than merely observed.
INTERRUPT_GRANTED = "granted"
INTERRUPT_REFUSED_FOCUS = "focus"
INTERRUPT_REFUSED_QUIET_HOUR = "quiet-hour"
INTERRUPT_REFUSED_UNDECLARED = "undeclared-kind"


@dataclass(frozen=True)
class InterruptBudget:
    """What the owner's environment permits right now.

    Exactly the three things the law needs: how loud a courtesy burst
    may be, whether a Focus is on, and whether the owner has snoozed by
    hand. focus_policy is the per-Focus refinement (see
    FOCUS_SIGNAL_POLICIES) and only ever makes things stricter.
    """

    burst: int = DEFAULT_ALERT_BURST
    focus_active: bool = False
    focus_policy: str = FOCUS_POLICY_ALL
    quiet_hour: bool = False

    def normalized(self) -> InterruptBudget:
        return replace(
            self,
            burst=normalize_alert_burst(self.burst),
            focus_active=bool(self.focus_active),
            focus_policy=(
                self.focus_policy
                if self.focus_policy in FOCUS_SIGNAL_POLICIES
                else FOCUS_POLICY_ALL
            ),
            quiet_hour=bool(self.quiet_hour),
        )


@dataclass(frozen=True)
class InterruptGrant:
    """The budget's answer to one request. `repetitions is None` means
    "until it is dealt with" -- the critical class's whole point."""

    kind: str
    interrupt_class: str
    allowed: bool
    repetitions: int | None
    cycle_seconds: float
    hold_seconds: float | None
    audible: bool
    reason: str

    @property
    def stands_until_dealt_with(self) -> bool:
        """The ratified critical shape: finite arrival taps, then a
        steady anchor that holds (hold_seconds=None) until resolved --
        never perpetual blinking."""
        return self.allowed and self.hold_seconds is None

    @property
    def hertz(self) -> float:
        """How fast this grant actually repeats. Never above
        MAX_INTERRUPT_HZ -- that is the law, and a test asserts it over
        every kind at every speed the user can dial."""
        return 1.0 / self.cycle_seconds


def budgeted_style(style: SignalStyle) -> SignalStyle:
    """The style a signal may actually PLAY at: the configured style
    with the 2Hz floor applied. Same colour, same pattern, same
    intensity -- only a cadence that would strobe is slowed."""
    normalized = style.normalized()
    return replace(
        normalized,
        speed_seconds=max(MIN_INTERRUPT_CYCLE_SECONDS, normalized.speed_seconds),
    )


def interrupt_class(kind: str) -> str | None:
    """Which rung a kind sits on, or None if it never declared one."""
    return INTERRUPT_CLASS_BY_KIND.get(kind)


def grant_interrupt(
    kind: str,
    *,
    budget: InterruptBudget | None = None,
    style: SignalStyle | None = None,
) -> InterruptGrant:
    """THE enforcement point. Nothing interrupts the owner without an
    allowed grant from here."""
    permitted = (budget if isinstance(budget, InterruptBudget) else InterruptBudget()).normalized()
    signal_class = INTERRUPT_CLASS_BY_KIND.get(kind)
    cycle = (
        budgeted_style(style).speed_seconds
        if isinstance(style, SignalStyle)
        else MIN_INTERRUPT_CYCLE_SECONDS
    )

    def refuse(reason: str) -> InterruptGrant:
        # A refusal reports the COURTESY rung whatever it was asked
        # about, including an undeclared kind: the least-privileged
        # rung is what "we are not letting this through" means here.
        return InterruptGrant(
            kind=kind,
            interrupt_class=INTERRUPT_COURTESY,
            allowed=False,
            repetitions=0,
            cycle_seconds=cycle,
            hold_seconds=0.0,
            audible=False,
            reason=reason,
        )

    if signal_class is None:
        return refuse(INTERRUPT_REFUSED_UNDECLARED)

    if signal_class == INTERRUPT_CRITICAL:
        # Critical goes THROUGH a Focus. Its light language is the
        # ratified arrival-taps-then-steady-anchor (see
        # ATTENTION_ARRIVAL_TAPS); hold_seconds=None means the anchor
        # stands until dealt with. Only an explicit per-Focus "Silent"
        # reaches a critical grant, and only to hush the SOUND -- never
        # to hide the light.
        return InterruptGrant(
            kind=kind,
            interrupt_class=INTERRUPT_CRITICAL,
            allowed=True,
            repetitions=ATTENTION_ARRIVAL_TAPS,
            cycle_seconds=cycle,
            hold_seconds=None,
            audible=permitted.focus_policy != FOCUS_POLICY_SILENT,
            reason=INTERRUPT_GRANTED,
        )

    if permitted.focus_active:
        return refuse(INTERRUPT_REFUSED_FOCUS)
    if permitted.quiet_hour and kind not in QUIET_HOUR_EXEMPT_KINDS:
        return refuse(INTERRUPT_REFUSED_QUIET_HOUR)
    # A GRANTED courtesy may be audible: Focus and Quiet Hour were just
    # checked and refused outright above, so by the time a grant exists
    # there is nothing left for silence to protect. The hard-coded
    # False that used to sit here meant the timebox chime -- a sound
    # the user explicitly enabled -- could never play under any
    # conditions (audit, 2026-08-26).
    return InterruptGrant(
        kind=kind,
        interrupt_class=INTERRUPT_COURTESY,
        allowed=True,
        repetitions=permitted.burst,
        cycle_seconds=cycle,
        hold_seconds=permitted.burst * cycle + INTERRUPT_SETTLE_SECONDS,
        audible=True,
        reason=INTERRUPT_GRANTED,
    )


def signal_hold_seconds(
    style: SignalStyle,
    *,
    burst: int = DEFAULT_ALERT_BURST,
) -> float:
    """How long ONE burst of a moment signal claims the bar.

    The burst budget decides this now -- exactly `burst` repetitions at
    a cadence the 2Hz law permits, plus a settle beat -- instead of each
    pattern carrying its own private count (a blink played 3, a
    double-blink 2, and everything else held for "about two seconds",
    which is a duration, not a burst). Same arithmetic as
    grant_interrupt, for callers holding a style rather than a grant.
    """
    return normalize_alert_burst(burst) * budgeted_style(style).speed_seconds + INTERRUPT_SETTLE_SECONDS
