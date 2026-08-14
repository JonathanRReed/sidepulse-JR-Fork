"""The animation editor's model layer: an animation the FIRMWARE can express.

Everything here is a value plus a function over values -- no AppKit, no
device, no clock -- so the whole editor can be tested without hardware and
without PyObjC.

WHY A MODEL AND NOT A TEXT BOX
------------------------------
The Studio already has a text box, and a text box lets you write things the
firmware rejects. A rejected program is not a no-op: the controller "stops
the current program and blinks all LEDs red six times with 150 ms on/off
phases" -- a 3.3 Hz saturated-red strobe, which is both the ugliest thing
this hardware can do and a photosensitivity hazard. Put that in INIT.LED and
it is what the device does at every boot until someone rewrites the file.

So the editor edits a MODEL that can only hold expressible programs, and the
compiler is the only thing that produces bytes. Validation is a gate, not a
lint pass.

WHAT THE GRAMMAR ACTUALLY IS
----------------------------
Every constant and every rule below was measured against the packaged
``sdled.wasm`` (the firmware's own parser), not inferred from LEDS_FORMAT.md.
Where the document and the parser disagreed, the parser won. The surprises,
all confirmed by probe:

* ``0:off`` is ``bad-index``. There is NO per-LED off; ``0:#000000`` is the
  only way to darken one LED.
* ``#f0f`` is ``bad-color``. Three-digit hex does not exist here.
* A comment ``#`` needs a space (``# note``); ``#note`` is ``bad-color``.
  A ``;`` is a comment only as the first non-space character of a line --
  anywhere else it separates segments.
* ``repeat`` may appear AT MOST ONCE, and never before the first painting
  line. ``brightness`` and comment lines do not count as painting lines:
  ``brightness 100 / repeat / #ff0000`` is ``bad-repeat``.
* ``roll`` requires a duration (bare ``roll`` is ``bad-time``), refuses a
  delay, and cannot appear in a ``;`` segment -- it owns its line.
* The 20-line cap counts PHYSICAL lines: blanks and comments included.
* The 512-byte cap counts UTF-8 bytes, inclusive.
* Durations and delays cap at 65535 ms in every spelling (``65535ms``,
  ``65.535s``, ``65s``); ``66s`` is ``bad-time``. ``.5s`` is ``bad-time``
  too -- a decimal needs its leading digit.
* Non-ASCII is a syntax error. A no-break space pasted from a web page or a
  chat window (U+00A0) reads exactly like a space and bricks the parse.

WHAT IT CANNOT EXPRESS, AT ALL
------------------------------
No variables, no arithmetic, no conditionals, no nesting, no second loop
point, no per-LED brightness, no HSV, no gradients between two LEDs, no
sub-frame timing (a line with no timing lasts one 60 Hz frame), no runtime
input, and no way to address an LED that does not exist (indexes past the
compiled LED count parse and are then silently ignored -- which is why this
module makes that a warning rather than letting the editor lie about it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .device_writer import (
    MAX_LED_BYTES,
    MAX_LED_LINES,
    POWER_UP_FILE_NAME,
    DeviceWriteError,
)

# --- The grammar, as measured ---------------------------------------------

MAX_PROGRAM_BYTES = MAX_LED_BYTES
MAX_PROGRAM_LINES = MAX_LED_LINES
MAX_TIME_MS = 65535
MIN_REPEAT = 1
MAX_REPEAT = 65535
MAX_BRIGHTNESS = 255
MAX_COMMENT_LENGTH = 120

# An easing name with no duration runs for this long. Documented, and the
# reason "#ff00ff pulse 1s" is a 330 ms pulse delayed by a second rather
# than the one-second pulse everybody reads it as.
DEFAULT_EASING_DURATION_MS = 330
# A line with no duration, easing or delay lasts one 60 Hz frame.
FRAME_MS = 1000 // 60

EASINGS: tuple[str, ...] = (
    "linear",
    "ease",
    "ease-in",
    "ease-out",
    "ease-in-out",
    "cosine",
    "pulse",
    "none",
)

ROLL_RIGHT = "roll-right"
ROLL_LEFT = "roll-left"
ROLL_DIRECTIONS: tuple[str, ...] = (ROLL_RIGHT, ROLL_LEFT)

# The whole-bar "everything dark" colour. A sentinel rather than "#000000"
# because the firmware spells them differently and only one of them is legal
# per LED (see the module docstring).
OFF = "off"

DEFAULT_LED_COUNT = 8
# The two shipping builds. Anything else is a typo, and a typo here silently
# changes which indexes are real.
SUPPORTED_LED_COUNTS: tuple[int, ...] = (2, 8)

# "Nothing above 2Hz." Not a grammar rule -- the firmware will happily strobe
# -- so it is a warning rather than an error, and the burn refuses on it by
# default anyway.
MAX_SAFE_HZ = 2.0
MIN_SAFE_CYCLE_MS = int(1000 / MAX_SAFE_HZ)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

_HEX_RE = re.compile(r"\A#[0-9A-Fa-f]{6}\Z")
_MS_RE = re.compile(r"\A(\d+)(?:ms|MS|mS|Ms)\Z")
_SECONDS_RE = re.compile(r"\A(\d+)(?:\.(\d+))?[sS]\Z")
_INDEXED_RE = re.compile(r"\A(\d+):(.+)\Z")


# --- Problems --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnimationProblem:
    """One thing wrong with an animation, in words a person can act on.

    ``step`` is the 1-based step number the author sees, or None when the
    problem belongs to the program as a whole (bytes, lines, no steps).
    ``message`` already carries its own "step N: " prefix so a UI can print
    it unchanged -- a caller that has to reassemble the sentence will get
    the punctuation wrong somewhere, and that somewhere is a dialog.
    """

    step: int | None
    code: str
    message: str
    severity: str = SEVERITY_ERROR

    @property
    def is_error(self) -> bool:
        return self.severity == SEVERITY_ERROR


class AnimationValidationError(ValueError):
    """Raised instead of producing bytes. Carries every problem found."""

    def __init__(self, problems: tuple[AnimationProblem, ...]) -> None:
        self.problems = tuple(problems)
        first = next(
            (problem for problem in self.problems if problem.is_error),
            self.problems[0] if self.problems else None,
        )
        super().__init__(first.message if first is not None else "invalid animation")

    @property
    def errors(self) -> tuple[AnimationProblem, ...]:
        return tuple(problem for problem in self.problems if problem.is_error)

    @property
    def warnings(self) -> tuple[AnimationProblem, ...]:
        return tuple(problem for problem in self.problems if not problem.is_error)


def _error(step: int | None, code: str, message: str) -> AnimationProblem:
    return AnimationProblem(step, code, message, SEVERITY_ERROR)


def _warning(step: int | None, code: str, message: str) -> AnimationProblem:
    return AnimationProblem(step, code, message, SEVERITY_WARNING)


def _where(step: int | None) -> str:
    return "" if step is None else f"step {step}: "


# --- Values ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Timing:
    """How long one assignment takes, how it moves, and when it starts.

    A delay with neither a duration nor an easing is REFUSED rather than
    rendered: "#FF00FF 1s" is a duration to the firmware, so a model that
    quietly emitted a lone delay would produce a program that does something
    else than the author asked for. With an easing and no duration the
    firmware's documented 330 ms applies and the text is unambiguous.
    """

    duration_ms: int | None = None
    easing: str | None = None
    delay_ms: int | None = None

    @property
    def effective_duration_ms(self) -> int:
        if self.duration_ms is not None:
            return self.duration_ms
        if self.easing is not None:
            return DEFAULT_EASING_DURATION_MS
        return FRAME_MS

    @property
    def span_ms(self) -> int:
        return (self.delay_ms or 0) + self.effective_duration_ms


@dataclass(frozen=True, slots=True)
class WholeBar:
    """Every LED to one colour (or ``OFF``)."""

    color: str = OFF
    timing: Timing = field(default_factory=Timing)


@dataclass(frozen=True, slots=True)
class ColorList:
    """Colours by position. LEDs past the list turn OFF -- that is the
    firmware's rule, and it is the difference between this and WholeBar.
    Needs two or more colours: one colour is the whole-bar form and would
    compile to the same text with different meaning."""

    colors: tuple[str, ...] = ()
    timing: Timing = field(default_factory=Timing)


@dataclass(frozen=True, slots=True)
class IndexedPaint:
    """Named LEDs only. Unmentioned LEDs HOLD -- they do not go dark."""

    assignments: tuple[tuple[int, str], ...] = ()
    timing: Timing = field(default_factory=Timing)


Segment = WholeBar | ColorList | IndexedPaint


@dataclass(frozen=True, slots=True)
class PaintStep:
    """One line of colour assignments. Several segments may share a line;
    the line ends at the longest delay-plus-duration on it."""

    segments: tuple[Segment, ...] = ()


@dataclass(frozen=True, slots=True)
class BrightnessStep:
    """Scales the RGB the device drives. Every parse starts at 255."""

    level: int = MAX_BRIGHTNESS


@dataclass(frozen=True, slots=True)
class RollStep:
    """Rotate the CURRENT visible state one full wraparound loop."""

    duration_ms: int = 1000
    direction: str = ROLL_RIGHT
    easing: str | None = None


@dataclass(frozen=True, slots=True)
class RepeatStep:
    """Loop from step 1. ``count=None`` loops forever."""

    count: int | None = None


@dataclass(frozen=True, slots=True)
class CommentStep:
    """A line for the human. Costs a line and its bytes like anything else."""

    text: str = ""


Step = PaintStep | BrightnessStep | RollStep | RepeatStep | CommentStep

# The steps that make light happen. `repeat` needs one before it, and an
# animation made only of comments and brightness is not an animation.
_LIGHTING_STEPS = (PaintStep, RollStep)


@dataclass(frozen=True, slots=True)
class Animation:
    """A named program. The name never reaches the device."""

    name: str = ""
    steps: tuple[Step, ...] = ()


# --- Duration --------------------------------------------------------------


def step_duration_ms(step: Step) -> int:
    """How long one step occupies. Zero for the steps that take no time."""
    if type(step) is PaintStep:
        return max((segment.timing.span_ms for segment in step.segments), default=0)
    if type(step) is RollStep:
        return max(0, step.duration_ms)
    return 0


def animation_duration_ms(animation: Animation) -> int:
    """One pass through the animation, in milliseconds.

    The loop length, not the total runtime: a finite ``repeat N`` plays this
    N times and steps after the repeat marker play once more.
    """
    return sum(step_duration_ms(step) for step in animation.steps)


def loop_duration_ms(animation: Animation) -> int | None:
    """The length of the section that repeats, or None if nothing repeats."""
    repeat_at = next(
        (index for index, step in enumerate(animation.steps) if type(step) is RepeatStep),
        None,
    )
    if repeat_at is None:
        return None
    return sum(step_duration_ms(step) for step in animation.steps[:repeat_at])


# --- Rendering -------------------------------------------------------------


def format_time(milliseconds: int) -> str:
    """The shortest spelling the firmware accepts for this many ms.

    Bytes are the scarcest resource in a 512-byte program, and "2s" is four
    bytes cheaper than "2000ms" every time it appears.
    """
    if milliseconds >= 1000 and milliseconds % 1000 == 0:
        return f"{milliseconds // 1000}s"
    return f"{milliseconds}ms"


def normalize_color(value: str) -> str:
    """Canonical uppercase ``#RRGGBB``, or ``OFF`` unchanged."""
    if isinstance(value, str) and value.strip().lower() == OFF:
        return OFF
    return f"#{str(value).strip().lstrip('#').upper()}"


def _render_timing(timing: Timing) -> str:
    parts: list[str] = []
    if timing.duration_ms is not None:
        parts.append(format_time(timing.duration_ms))
    if timing.easing is not None:
        parts.append(timing.easing)
    if timing.delay_ms is not None:
        parts.append(format_time(timing.delay_ms))
    return (" " + " ".join(parts)) if parts else ""


def _render_segment(segment: Segment) -> str:
    if type(segment) is WholeBar:
        head = OFF if segment.color == OFF else normalize_color(segment.color)
    elif type(segment) is ColorList:
        head = " ".join(normalize_color(color) for color in segment.colors)
    else:
        head = " ".join(
            f"{index}:{normalize_color(color)}" for index, color in segment.assignments
        )
    return head + _render_timing(segment.timing)


def render_step(step: Step) -> str:
    """One step to one physical line of the device DSL."""
    if type(step) is PaintStep:
        return "; ".join(_render_segment(segment) for segment in step.segments)
    if type(step) is BrightnessStep:
        return f"brightness {step.level}"
    if type(step) is RollStep:
        easing = f" {step.easing}" if step.easing else ""
        return f"{step.direction} {format_time(step.duration_ms)}{easing}"
    if type(step) is RepeatStep:
        return "repeat" if step.count is None else f"repeat {step.count}"
    if type(step) is CommentStep:
        return f"// {step.text}".rstrip()
    raise AnimationValidationError(
        (_error(None, "unknown-step", f"unknown step type: {type(step).__name__}"),)
    )


def render_animation(animation: Animation) -> str:
    """Model to text with NO validation. Use compile_animation to get bytes."""
    return "\n".join(render_step(step) for step in animation.steps)


# --- Validation ------------------------------------------------------------


def normalize_led_count(value: object) -> int:
    """The device's real LED count, or the 8-LED Pro if it is not a build we
    ship. Guessing here would move which indexes are real, so it is narrow
    on purpose."""
    return int(value) if value in SUPPORTED_LED_COUNTS else DEFAULT_LED_COUNT


def _check_time(
    problems: list[AnimationProblem],
    step: int,
    label: str,
    value: object,
) -> None:
    if type(value) is not int or isinstance(value, bool):
        problems.append(
            _error(
                step,
                "bad-time",
                f"{_where(step)}{label} must be a whole number of milliseconds, "
                f"not {value!r}",
            )
        )
        return
    if value < 0 or value > MAX_TIME_MS:
        problems.append(
            _error(
                step,
                "bad-time",
                f"{_where(step)}{label} must be 0-{MAX_TIME_MS} ms, not {value}",
            )
        )


def _check_easing(
    problems: list[AnimationProblem],
    step: int,
    easing: object,
) -> None:
    if easing is None or easing in EASINGS:
        return
    problems.append(
        _error(
            step,
            "bad-easing",
            f"{_where(step)}easing must be one of {', '.join(EASINGS)} -- not {easing!r}",
        )
    )


def _check_timing(
    problems: list[AnimationProblem],
    step: int,
    timing: Timing,
) -> None:
    if type(timing) is not Timing:
        problems.append(
            _error(step, "bad-timing", f"{_where(step)}timing must be a Timing value")
        )
        return
    if timing.duration_ms is not None:
        _check_time(problems, step, "duration", timing.duration_ms)
    _check_easing(problems, step, timing.easing)
    if timing.delay_ms is not None:
        _check_time(problems, step, "delay", timing.delay_ms)
        if timing.duration_ms is None and timing.easing is None:
            problems.append(
                _error(
                    step,
                    "delay-without-duration",
                    f"{_where(step)}a delay needs a duration or an easing -- the "
                    "firmware reads a lone time as the duration, so this would "
                    "play immediately instead of waiting",
                )
            )


def _check_color(
    problems: list[AnimationProblem],
    step: int,
    color: object,
    *,
    allow_off: bool,
    context: str,
) -> None:
    if allow_off and color == OFF:
        return
    if isinstance(color, str) and color.strip().lower() == OFF:
        problems.append(
            _error(
                step,
                "indexed-off",
                f"{_where(step)}{context} cannot be \"off\" -- the firmware has no "
                "per-LED off; paint #000000 instead",
            )
        )
        return
    if not isinstance(color, str) or not _HEX_RE.match(color.strip()):
        problems.append(
            _error(
                step,
                "bad-color",
                f"{_where(step)}{context} must be a six-digit hex colour like "
                f"#FF00FF, not {color!r}",
            )
        )


def _check_index(
    problems: list[AnimationProblem],
    step: int,
    index: object,
    led_count: int,
) -> None:
    if type(index) is not int or isinstance(index, bool) or index < 0:
        problems.append(
            _error(
                step,
                "bad-index",
                f"{_where(step)}LED index must be 0 or more, not {index!r}",
            )
        )
        return
    if index >= led_count:
        problems.append(
            _warning(
                step,
                "index-out-of-range",
                f"{_where(step)}LED {index} does not exist on this "
                f"{led_count}-LED device -- the firmware parses it and then "
                "ignores it, so this paints nothing",
            )
        )


def _check_segment(
    problems: list[AnimationProblem],
    step: int,
    segment: object,
    led_count: int,
) -> None:
    kind = type(segment)
    if kind is WholeBar:
        _check_color(
            problems, step, segment.color, allow_off=True, context="the colour"
        )
        _check_timing(problems, step, segment.timing)
        return
    if kind is ColorList:
        if type(segment.colors) is not tuple:
            problems.append(
                _error(
                    step,
                    "bad-step",
                    f"{_where(step)}a colour list must be a tuple of colours",
                )
            )
            return
        if len(segment.colors) < 2:
            problems.append(
                _error(
                    step,
                    "short-color-list",
                    f"{_where(step)}a colour list needs at least two colours -- "
                    "one colour paints the whole bar, which is a different step",
                )
            )
        for position, color in enumerate(segment.colors):
            _check_color(
                problems,
                step,
                color,
                allow_off=False,
                context=f"colour {position + 1}",
            )
        if len(segment.colors) > led_count:
            problems.append(
                _warning(
                    step,
                    "color-list-too-long",
                    f"{_where(step)}this list has {len(segment.colors)} colours but "
                    f"the device has {led_count} LEDs -- the extra colours are "
                    "parsed and then ignored",
                )
            )
        _check_timing(problems, step, segment.timing)
        return
    if kind is IndexedPaint:
        if type(segment.assignments) is not tuple or any(
            type(item) is not tuple or len(item) != 2 for item in segment.assignments
        ):
            problems.append(
                _error(
                    step,
                    "bad-step",
                    f"{_where(step)}LED assignments must be (index, colour) pairs",
                )
            )
            return
        if not segment.assignments:
            problems.append(
                _error(
                    step,
                    "empty-assignment",
                    f"{_where(step)}this step assigns no LED at all",
                )
            )
        seen: set[int] = set()
        for index, color in segment.assignments:
            _check_index(problems, step, index, led_count)
            _check_color(
                problems,
                step,
                color,
                allow_off=False,
                context=f"the colour for LED {index}",
            )
            if index in seen:
                problems.append(
                    _warning(
                        step,
                        "duplicate-index",
                        f"{_where(step)}LED {index} is assigned twice here -- the "
                        "last assignment on a line wins, so the first one never "
                        "shows",
                    )
                )
            if type(index) is int and not isinstance(index, bool):
                seen.add(index)
        _check_timing(problems, step, segment.timing)
        return
    problems.append(
        _error(
            step,
            "unknown-segment",
            f"{_where(step)}unknown segment type: {kind.__name__}",
        )
    )


def _check_text(
    problems: list[AnimationProblem],
    step: int | None,
    text: str,
    *,
    context: str,
) -> None:
    for character in text:
        if ord(character) > 127:
            problems.append(
                _error(
                    step,
                    "non-ascii",
                    f"{_where(step)}{context} contains {character!r} "
                    f"(U+{ord(character):04X}); the firmware only accepts ASCII. A "
                    "no-break space pasted from a web page looks exactly like a "
                    "space and is the usual cause",
                )
            )
            return
        if character in "\n\r\t\\" or (ord(character) < 32):
            problems.append(
                _error(
                    step,
                    "bad-character",
                    f"{_where(step)}{context} contains {character!r}, which cannot "
                    "appear in a program line",
                )
            )
            return


def validate_animation(
    animation: Animation,
    *,
    led_count: int = DEFAULT_LED_COUNT,
) -> tuple[AnimationProblem, ...]:
    """Every problem with this animation, errors and warnings alike.

    An ERROR means the firmware would refuse the bytes (or that they would
    mean something other than what the model says). A WARNING means the
    firmware accepts them and the result is not what the author meant: an
    LED that does not exist, a colour that is overwritten on the same line,
    a loop faster than the 2 Hz the product promises. Burning refuses on
    both by default -- INIT.LED replays at every boot, and "it parsed" is
    not the bar for something the owner cannot see before it runs.
    """
    if type(animation) is not Animation:
        return (_error(None, "not-an-animation", "that is not an animation"),)
    led_count = normalize_led_count(led_count)
    problems: list[AnimationProblem] = []

    if not animation.steps:
        problems.append(
            _error(None, "empty", "an animation needs at least one step")
        )

    repeat_at: int | None = None
    lit_before_repeat = False
    for position, step in enumerate(animation.steps, start=1):
        kind = type(step)
        if kind is PaintStep:
            if type(step.segments) is not tuple:
                problems.append(
                    _error(
                        position,
                        "bad-step",
                        f"{_where(position)}a step's segments must be a tuple",
                    )
                )
            elif not step.segments:
                problems.append(
                    _error(
                        position,
                        "empty-step",
                        f"{_where(position)}this step paints nothing",
                    )
                )
            else:
                for segment in step.segments:
                    _check_segment(problems, position, segment, led_count)
        elif kind is BrightnessStep:
            level = step.level
            if (
                type(level) is not int
                or isinstance(level, bool)
                or not (0 <= level <= MAX_BRIGHTNESS)
            ):
                problems.append(
                    _error(
                        position,
                        "bad-brightness",
                        f"{_where(position)}brightness must be 0-{MAX_BRIGHTNESS}, "
                        f"not {level!r}",
                    )
                )
        elif kind is RollStep:
            if step.direction not in ROLL_DIRECTIONS:
                problems.append(
                    _error(
                        position,
                        "bad-roll-direction",
                        f"{_where(position)}roll direction must be "
                        f"{' or '.join(ROLL_DIRECTIONS)}, not {step.direction!r}",
                    )
                )
            _check_time(problems, position, "roll duration", step.duration_ms)
            if type(step.duration_ms) is int and step.duration_ms <= 0:
                problems.append(
                    _error(
                        position,
                        "roll-needs-duration",
                        f"{_where(position)}roll needs a duration -- the firmware "
                        "rejects a roll with no time to travel in",
                    )
                )
            _check_easing(problems, position, step.easing)
        elif kind is RepeatStep:
            if repeat_at is not None:
                problems.append(
                    _error(
                        position,
                        "second-repeat",
                        f"{_where(position)}only one repeat is allowed, and there "
                        f"is already one at step {repeat_at}",
                    )
                )
            elif not lit_before_repeat:
                problems.append(
                    _error(
                        position,
                        "repeat-too-early",
                        f"{_where(position)}repeat needs a step that lights "
                        "something before it -- brightness and comments do not "
                        "count",
                    )
                )
            count = step.count
            if count is not None and (
                type(count) is not int
                or isinstance(count, bool)
                or not (MIN_REPEAT <= count <= MAX_REPEAT)
            ):
                problems.append(
                    _error(
                        position,
                        "bad-repeat",
                        f"{_where(position)}repeat count must be "
                        f"{MIN_REPEAT}-{MAX_REPEAT}, not {count!r}",
                    )
                )
            if repeat_at is None:
                repeat_at = position
        elif kind is CommentStep:
            if len(step.text) > MAX_COMMENT_LENGTH:
                problems.append(
                    _error(
                        position,
                        "long-comment",
                        f"{_where(position)}a comment may be at most "
                        f"{MAX_COMMENT_LENGTH} characters",
                    )
                )
            _check_text(problems, position, step.text, context="this comment")
        else:
            problems.append(
                _error(
                    position,
                    "unknown-step",
                    f"{_where(position)}unknown step type: {kind.__name__}",
                )
            )
        if kind in _LIGHTING_STEPS and repeat_at is None:
            lit_before_repeat = True

    if animation.steps and not any(
        type(step) in _LIGHTING_STEPS for step in animation.steps
    ):
        problems.append(
            _error(
                None,
                "nothing-lit",
                "an animation needs at least one step that lights something",
            )
        )

    problems.extend(_program_problems(animation))
    try:
        problems.extend(_cadence_problems(animation))
    except (AttributeError, TypeError):
        # A model malformed enough to break arithmetic has already produced
        # its own error above; a crash in the advisory pass would replace a
        # readable list of problems with a traceback.
        pass
    return tuple(problems)


def _program_problems(animation: Animation) -> list[AnimationProblem]:
    """The two hard caps, measured on the text this animation compiles to."""
    problems: list[AnimationProblem] = []
    try:
        text = render_animation(animation)
    except (AnimationValidationError, AttributeError, TypeError):
        return problems
    byte_count = len(text.encode("utf-8", errors="replace"))
    if byte_count > MAX_PROGRAM_BYTES:
        problems.append(
            _error(
                None,
                "too-long",
                f"this animation is {byte_count} bytes; the device accepts "
                f"{MAX_PROGRAM_BYTES}. Shorten a comment, or merge steps onto one "
                "line with ';'",
            )
        )
    line_count = len(animation.steps)
    if line_count > MAX_PROGRAM_LINES:
        problems.append(
            _error(
                None,
                "too-many-lines",
                f"this animation is {line_count} steps; the device accepts "
                f"{MAX_PROGRAM_LINES} lines, and comments count",
            )
        )
    return problems


def _cadence_problems(animation: Animation) -> list[AnimationProblem]:
    """The product's own law, which the firmware does not know about."""
    loop_ms = loop_duration_ms(animation)
    if loop_ms is None or loop_ms <= 0 or loop_ms >= MIN_SAFE_CYCLE_MS:
        return []
    hertz = 1000.0 / loop_ms
    return [
        _warning(
            None,
            "strobe",
            f"this loop repeats every {loop_ms} ms ({hertz:.1f} Hz). Nothing on "
            f"this hardware may repeat faster than {MAX_SAFE_HZ:g} Hz",
        )
    ]


def errors_only(
    problems: tuple[AnimationProblem, ...],
) -> tuple[AnimationProblem, ...]:
    return tuple(problem for problem in problems if problem.is_error)


def warnings_only(
    problems: tuple[AnimationProblem, ...],
) -> tuple[AnimationProblem, ...]:
    return tuple(problem for problem in problems if not problem.is_error)


def compile_animation(
    animation: Animation,
    *,
    led_count: int = DEFAULT_LED_COUNT,
) -> str:
    """The exact device text, or an exception. Never a best effort."""
    problems = validate_animation(animation, led_count=led_count)
    failures = errors_only(problems)
    if failures:
        raise AnimationValidationError(problems)
    return render_animation(animation)


# --- Parsing ---------------------------------------------------------------


def _parse_time(token: str) -> int | None:
    match = _MS_RE.match(token)
    if match:
        return int(match.group(1))
    match = _SECONDS_RE.match(token)
    if match:
        whole, fraction = match.group(1), match.group(2) or ""
        if len(fraction) > 3:
            return None
        milliseconds = int(whole) * 1000
        if fraction:
            milliseconds += int(fraction.ljust(3, "0"))
        return milliseconds
    return None


def _parse_timing(
    tokens: list[str],
    problems: list[AnimationProblem],
    step: int,
) -> Timing:
    if not tokens:
        return Timing()
    if len(tokens) > 3:
        problems.append(
            _error(
                step,
                "trailing-input",
                f"{_where(step)}there is extra text after the timing: "
                f"{' '.join(tokens[3:])!r}",
            )
        )
        tokens = tokens[:3]

    kinds: list[tuple[str, object]] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in EASINGS:
            kinds.append(("easing", lowered))
            continue
        milliseconds = _parse_time(token)
        if milliseconds is None:
            problems.append(
                _error(
                    step,
                    "bad-time",
                    f"{_where(step)}{token!r} is not a time or an easing. Times "
                    "look like 330ms, 1s or 0.33s (a decimal needs its leading "
                    f"zero); easings are {', '.join(EASINGS)}",
                )
            )
            kinds.append(("bad", token))
            continue
        kinds.append(("time", milliseconds))

    shape = tuple(kind for kind, _value in kinds)
    values = [value for _kind, value in kinds]
    if shape == ("time",):
        return Timing(duration_ms=values[0])
    if shape == ("easing",):
        return Timing(easing=values[0])
    if shape == ("time", "easing"):
        return Timing(duration_ms=values[0], easing=values[1])
    if shape == ("time", "time"):
        return Timing(duration_ms=values[0], delay_ms=values[1])
    if shape == ("easing", "time"):
        return Timing(easing=values[0], delay_ms=values[1])
    if shape == ("time", "easing", "time"):
        return Timing(duration_ms=values[0], easing=values[1], delay_ms=values[2])
    if "bad" not in shape:
        problems.append(
            _error(
                step,
                "bad-timing",
                f"{_where(step)}timing must be a duration, an easing, or "
                "duration-easing-delay in that order -- not "
                f"{' '.join(tokens)!r}",
            )
        )
    return Timing()


def _parse_segment(
    text: str,
    problems: list[AnimationProblem],
    step: int,
) -> Segment | None:
    tokens = text.split()
    if not tokens:
        problems.append(
            _error(step, "empty-step", f"{_where(step)}this step paints nothing")
        )
        return None

    indexed: list[tuple[int, str]] = []
    colors: list[str] = []
    consumed = 0
    for token in tokens:
        match = _INDEXED_RE.match(token)
        if match is not None:
            if colors:
                problems.append(
                    _error(
                        step,
                        "mixed-forms",
                        f"{_where(step)}a step is either a list of colours or a "
                        "set of LED:colour assignments, never both",
                    )
                )
                return None
            index_text, color = match.group(1), match.group(2)
            _check_color(
                problems,
                step,
                color,
                allow_off=False,
                context=f"the colour for LED {index_text}",
            )
            indexed.append((int(index_text), normalize_color(color)))
            consumed += 1
            continue
        lowered = token.lower()
        if lowered == OFF and not colors and not indexed:
            colors.append(OFF)
            consumed += 1
            continue
        if token.startswith("#"):
            if indexed:
                problems.append(
                    _error(
                        step,
                        "mixed-forms",
                        f"{_where(step)}a step is either a list of colours or a "
                        "set of LED:colour assignments, never both",
                    )
                )
                return None
            if colors and colors[0] == OFF:
                problems.append(
                    _error(
                        step,
                        "off-in-list",
                        f"{_where(step)}\"off\" paints the whole bar and cannot be "
                        "part of a colour list",
                    )
                )
                return None
            _check_color(
                problems,
                step,
                token,
                allow_off=False,
                context=f"colour {len(colors) + 1}",
            )
            colors.append(normalize_color(token))
            consumed += 1
            continue
        break

    timing = _parse_timing(tokens[consumed:], problems, step)
    if indexed:
        return IndexedPaint(tuple(indexed), timing)
    if not colors:
        problems.append(
            _error(
                step,
                "syntax",
                f"{_where(step)}{tokens[0]!r} is not a colour, an LED assignment, "
                "or a keyword. Colours look like #FF00FF; assignments look like "
                "0:#FF00FF",
            )
        )
        return None
    if len(colors) == 1:
        return WholeBar(colors[0], timing)
    return ColorList(tuple(colors), timing)


def _parse_line(
    line: str,
    problems: list[AnimationProblem],
    step: int,
) -> Step | None:
    stripped = line.strip()
    if stripped.startswith("//") or stripped.startswith(";"):
        return CommentStep(stripped.lstrip("/;").strip())
    if stripped == "#" or stripped.startswith("# ") or stripped.startswith("#\t"):
        return CommentStep(stripped[1:].strip())

    head = stripped.split(maxsplit=1)
    keyword = head[0].lower()
    rest = head[1].strip() if len(head) > 1 else ""

    if keyword == "brightness":
        if not rest.isdigit():
            problems.append(
                _error(
                    step,
                    "bad-brightness",
                    f"{_where(step)}brightness needs a whole number 0-"
                    f"{MAX_BRIGHTNESS}, not {rest!r}",
                )
            )
            return None
        return BrightnessStep(int(rest))

    if keyword == "repeat":
        if not rest:
            return RepeatStep(None)
        if not rest.isdigit():
            problems.append(
                _error(
                    step,
                    "bad-repeat",
                    f"{_where(step)}repeat takes a count or nothing at all, not "
                    f"{rest!r}",
                )
            )
            return None
        return RepeatStep(int(rest))

    if keyword in ("roll", ROLL_LEFT, ROLL_RIGHT):
        direction = ROLL_RIGHT if keyword == "roll" else keyword
        tokens = rest.split()
        if not tokens:
            problems.append(
                _error(
                    step,
                    "roll-needs-duration",
                    f"{_where(step)}roll needs a duration, like \"roll 2s\" -- the "
                    "firmware rejects a bare roll",
                )
            )
            return None
        timing = _parse_timing(tokens, problems, step)
        if timing.delay_ms is not None:
            problems.append(
                _error(
                    step,
                    "roll-delay",
                    f"{_where(step)}roll takes a duration and an easing; it has no "
                    "delay",
                )
            )
            return None
        if timing.duration_ms is None:
            problems.append(
                _error(
                    step,
                    "roll-needs-duration",
                    f"{_where(step)}roll needs a duration, like \"roll 2s\"",
                )
            )
            return None
        return RollStep(timing.duration_ms, direction, timing.easing)

    if ";" in stripped:
        segments = []
        for piece in stripped.split(";"):
            if not piece.strip():
                continue
            segment = _parse_segment(piece, problems, step)
            if segment is not None:
                segments.append(segment)
        return PaintStep(tuple(segments)) if segments else None

    segment = _parse_segment(stripped, problems, step)
    return PaintStep((segment,)) if segment is not None else None


def read_program(
    text: str,
    *,
    name: str = "",
    led_count: int = DEFAULT_LED_COUNT,
) -> tuple[Animation, tuple[AnimationProblem, ...]]:
    """Device text into (model, problems) WITHOUT raising -- what an editor
    calls on every keystroke. The model is whatever could be understood; the
    problems are everything wrong with the text and with that model.
    """
    if not isinstance(text, str):
        return (
            Animation(str(name)),
            (_error(None, "not-text", "an animation program must be text"),),
        )
    problems: list[AnimationProblem] = []
    raw_lines = text.splitlines()
    if len(raw_lines) > MAX_PROGRAM_LINES:
        problems.append(
            _error(
                None,
                "too-many-lines",
                f"this program is {len(raw_lines)} lines; the device accepts "
                f"{MAX_PROGRAM_LINES}, and blank and comment lines count",
            )
        )
    byte_count = len(text.encode("utf-8", errors="replace"))
    if byte_count > MAX_PROGRAM_BYTES:
        problems.append(
            _error(
                None,
                "too-long",
                f"this program is {byte_count} bytes; the device accepts "
                f"{MAX_PROGRAM_BYTES}",
            )
        )

    steps: list[Step] = []
    for number, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        _check_text(problems, number, line, context="this line")
        step = _parse_line(line, problems, number)
        if step is not None:
            steps.append(step)

    animation = Animation(str(name), tuple(steps))
    problems.extend(validate_animation(animation, led_count=led_count))
    return animation, tuple(problems)


def problems_for_program(
    text: str,
    *,
    led_count: int = DEFAULT_LED_COUNT,
) -> tuple[AnimationProblem, ...]:
    """Everything wrong with a program the owner is typing. Never raises."""
    return read_program(text, led_count=led_count)[1]


def parse_animation(
    text: str,
    *,
    name: str = "",
    led_count: int = DEFAULT_LED_COUNT,
) -> Animation:
    """Device text back into the model, or an exception.

    Blank lines are dropped: they cost the firmware a line but carry no
    meaning, so the model does not keep them. The raw text is still checked
    against the line and byte caps first, because the author's text is what
    they are looking at when the error appears. Colours come back
    canonicalised (uppercase), so compile(parse(text)) is stable.
    """
    animation, problems = read_program(text, name=name, led_count=led_count)
    if errors_only(problems):
        raise AnimationValidationError(problems)
    return animation


def describe_problems(problems: tuple[AnimationProblem, ...]) -> str:
    """Every problem as one printable block, errors first."""
    ordered = list(errors_only(problems)) + list(warnings_only(problems))
    return "\n".join(problem.message for problem in ordered)


# --- Burning to INIT.LED ---------------------------------------------------


class AnimationBurnError(RuntimeError):
    """The bytes were fine and the write still could not happen."""


@dataclass(frozen=True, slots=True)
class BurnPlan:
    """Exactly what would be written, and whether it was actually written.

    A plan is produced BEFORE any device is touched, and a dry run stops
    here. ``payload`` is the byte-for-byte content of INIT.LED, not a
    rendering of it: what the caller shows the owner is what the device
    gets.
    """

    program: str
    payload: bytes
    led_count: int
    target: Path | None
    problems: tuple[AnimationProblem, ...]
    firmware_checked: bool
    dry_run: bool
    written: bool

    @property
    def warnings(self) -> tuple[AnimationProblem, ...]:
        return warnings_only(self.problems)

    @property
    def byte_count(self) -> int:
        return len(self.payload)


def firmware_parse_error(text: str, led_count: int) -> str | None:
    """The real firmware parser's verdict, or None when it accepts.

    Raises when the parser itself cannot run, which callers must treat as a
    refusal rather than a pass -- see plan_power_up_burn.
    """
    from .led_wasm import SdLedWasmController

    controller = SdLedWasmController(led_count=normalize_led_count(led_count))
    # Parse from a known-clean engine state: the controller keeps state
    # across parses, and a burn must not inherit a verdict from whatever the
    # preview was doing a moment ago.
    controller.reset(0)
    result = controller.parse(text, 0)
    if result.ok:
        return None
    return (
        f"the firmware rejected this program: {result.error_name} at line "
        f"{result.line}, column {result.column}"
    )


def plan_power_up_burn(
    animation: Animation,
    *,
    device_path: Path | None = None,
    led_count: int = DEFAULT_LED_COUNT,
    allow_warnings: bool = False,
    firmware_parser=firmware_parse_error,
    require_firmware_parse: bool = True,
) -> BurnPlan:
    """Everything up to the write, with no write.

    Four gates, in this order, because each one can only be trusted once the
    one before it passed:

    1. the model validates (errors, and warnings unless allowed);
    2. it compiles to text;
    3. the text passes the device's own size checks;
    4. the real firmware parser accepts it.

    Gate 4 refuses when the parser is UNAVAILABLE rather than passing.
    INIT.LED replays at every boot, and a program nobody verified is exactly
    the thing that turns a boot into a red parse-error strobe.
    """
    led_count = normalize_led_count(led_count)
    problems = list(validate_animation(animation, led_count=led_count))
    blocking = list(errors_only(tuple(problems)))
    if not allow_warnings:
        blocking.extend(warnings_only(tuple(problems)))
    if blocking:
        raise AnimationValidationError(tuple(problems))

    program = render_animation(animation)
    from .device_writer import normalize_led_text, validate_led_text

    if normalize_led_text(program) != program:
        raise AnimationValidationError(
            (
                _error(
                    None,
                    "escape-sequence",
                    "this program contains a backslash escape, which the device "
                    "writer would rewrite before the device saw it",
                ),
            )
        )
    try:
        validate_led_text(program)
    except DeviceWriteError as error:
        raise AnimationValidationError(
            (_error(None, "device-limits", str(error)),)
        ) from error

    firmware_checked = False
    if firmware_parser is not None:
        try:
            verdict = firmware_parser(program, led_count)
        except Exception as error:
            if require_firmware_parse:
                raise AnimationValidationError(
                    (
                        _error(
                            None,
                            "firmware-parser-unavailable",
                            "the firmware parser is unavailable, so this program is "
                            f"unverified and will not be burned ({error})",
                        ),
                    )
                ) from error
        else:
            firmware_checked = True
            if verdict:
                raise AnimationValidationError(
                    (_error(None, "firmware-rejected", str(verdict)),)
                )
    elif require_firmware_parse:
        raise AnimationValidationError(
            (
                _error(
                    None,
                    "firmware-parser-unavailable",
                    "no firmware parser was supplied, so this program is unverified "
                    "and will not be burned",
                ),
            )
        )

    target: Path | None = None
    try:
        from .device_writer import resolve_target_path

        target = resolve_target_path(
            device_path=device_path, file_name=POWER_UP_FILE_NAME
        )
    except DeviceWriteError:
        # A dry run on a desk with no device still has to show the bytes.
        target = None

    return BurnPlan(
        program=program,
        payload=program.encode("utf-8"),
        led_count=led_count,
        target=target,
        problems=tuple(problems),
        firmware_checked=firmware_checked,
        dry_run=True,
        written=False,
    )


def burn_power_up_animation(
    animation: Animation,
    *,
    device_path: Path | None = None,
    led_count: int = DEFAULT_LED_COUNT,
    dry_run: bool = True,
    allow_warnings: bool = False,
    firmware_parser=firmware_parse_error,
    require_firmware_parse: bool = True,
    writer=None,
) -> BurnPlan:
    """Write one animation into INIT.LED so the hardware boots wearing it.

    ``dry_run`` defaults to True: burning is the one operation here that a
    person cannot undo by looking away, so a caller has to ask for it in so
    many words. The firmware plays INIT.LED immediately on write, which is
    the confirmation, and also the reason a bad program is felt instantly on
    every boot afterwards.
    """
    plan = plan_power_up_burn(
        animation,
        device_path=device_path,
        led_count=led_count,
        allow_warnings=allow_warnings,
        firmware_parser=firmware_parser,
        require_firmware_parse=require_firmware_parse,
    )
    if dry_run:
        return plan

    if writer is None:
        from .device_writer import write_led_program

        writer = write_led_program

    try:
        target = writer(
            plan.program,
            device_path=device_path,
            file_name=POWER_UP_FILE_NAME,
        )
    except DeviceWriteError as error:
        raise AnimationBurnError(str(error)) from error

    return BurnPlan(
        program=plan.program,
        payload=plan.payload,
        led_count=plan.led_count,
        target=Path(target),
        problems=plan.problems,
        firmware_checked=plan.firmware_checked,
        dry_run=False,
        written=True,
    )


__all__ = [
    "DEFAULT_LED_COUNT",
    "EASINGS",
    "MAX_PROGRAM_BYTES",
    "MAX_PROGRAM_LINES",
    "MAX_TIME_MS",
    "OFF",
    "ROLL_LEFT",
    "ROLL_RIGHT",
    "Animation",
    "AnimationBurnError",
    "AnimationProblem",
    "AnimationValidationError",
    "BrightnessStep",
    "BurnPlan",
    "ColorList",
    "CommentStep",
    "IndexedPaint",
    "PaintStep",
    "RepeatStep",
    "RollStep",
    "Timing",
    "WholeBar",
    "animation_duration_ms",
    "burn_power_up_animation",
    "compile_animation",
    "describe_problems",
    "errors_only",
    "firmware_parse_error",
    "format_time",
    "loop_duration_ms",
    "normalize_color",
    "normalize_led_count",
    "parse_animation",
    "plan_power_up_burn",
    "problems_for_program",
    "read_program",
    "render_animation",
    "render_step",
    "step_duration_ms",
    "validate_animation",
    "warnings_only",
]
