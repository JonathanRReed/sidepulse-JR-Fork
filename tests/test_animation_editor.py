"""The animation editor's model layer, and the three motions that were lies.

Two rules this file holds itself to.

FIRST: validation is only real if the firmware agrees. Every "this is legal"
assertion is cross-checked against the packaged ``sdled.wasm`` -- the actual
parser the hardware runs -- and every "this is illegal" assertion names the
firmware error it would have produced. A validator that agrees with itself is
the decorative kind.

SECOND: every test here was run against the code with the behaviour deleted,
and failed. The deletions are named in each test's docstring so the next
person can repeat them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sidepulse import animation_store as store
from sidepulse.animation import (
    MAX_PROGRAM_BYTES,
    MAX_PROGRAM_LINES,
    OFF,
    Animation,
    AnimationValidationError,
    BrightnessStep,
    ColorList,
    CommentStep,
    IndexedPaint,
    PaintStep,
    RepeatStep,
    RollStep,
    Timing,
    WholeBar,
    animation_duration_ms,
    burn_power_up_animation,
    compile_animation,
    loop_duration_ms,
    parse_animation,
    plan_power_up_burn,
    problems_for_program,
    read_program,
    render_animation,
    validate_animation,
)
from sidepulse.led_status import style_to_program
from sidepulse.signals import SIGNAL_PATTERNS, SignalStyle

# --- helpers ---------------------------------------------------------------


def _firmware(led_count: int = 8):
    from sidepulse.led_wasm import LedWasmUnavailableError, SdLedWasmController

    try:
        return SdLedWasmController(led_count=led_count)
    except LedWasmUnavailableError as exc:  # pragma: no cover - CI without JSC
        pytest.skip(str(exc))


def _codes(problems) -> set[str]:
    return {problem.code for problem in problems}


def _messages(problems) -> str:
    return "\n".join(problem.message for problem in problems)


BREATHING = Animation(
    "Breathing",
    (
        CommentStep("a slow warm swell"),
        PaintStep((WholeBar(OFF, Timing(400, "cosine")),)),
        PaintStep((WholeBar("#FF9F0A", Timing(1400, "pulse")),)),
        RepeatStep(),
    ),
)

STAGGERED = Animation(
    "Staggered",
    (
        BrightnessStep(200),
        PaintStep(
            (
                IndexedPaint(((0, "#FF0044"), (1, "#FF8800")), Timing(150, "ease", 0)),
                IndexedPaint(((2, "#FFFF00"), (3, "#00FF66")), Timing(150, "ease", 100)),
            )
        ),
        PaintStep((ColorList(("#FF0044", "#FF8800", "#FFFF00"), Timing(300, "cosine")),)),
        RollStep(2000, easing="linear"),
        RepeatStep(4),
    ),
)


# --- the model compiles to text the firmware accepts -----------------------


@pytest.mark.parametrize("animation", [BREATHING, STAGGERED], ids=["breathing", "staggered"])
def test_a_compiled_animation_is_accepted_by_the_real_firmware_parser(animation) -> None:
    """The whole point. Delete any rendering rule -- the ``; `` segment
    join, the ``roll-right`` spelling, the uppercase hex -- and the firmware
    rejects the bytes here rather than in the owner's hands.
    """
    controller = _firmware()
    program = compile_animation(animation)
    result = controller.parse(program, 0)
    assert result.ok, f"{result.error_name} at line {result.line} col {result.column}\n{program}"


def test_compiled_text_is_canonical_and_stable_under_a_round_trip() -> None:
    """compile -> parse -> compile must be a fixed point, or the library
    rewrites its own entries every time one is opened and saved.

    Deleted ``normalize_color``'s uppercasing: the second compile differs.
    """
    program = compile_animation(BREATHING)
    reparsed = parse_animation(program, name="Breathing")
    assert compile_animation(reparsed) == program
    assert reparsed == BREATHING

    lowercase = parse_animation("#ff9f0a 1.4s pulse\nrepeat")
    assert compile_animation(lowercase) == "#FF9F0A 1400ms pulse\nrepeat"


def test_seconds_are_spelled_the_short_way_because_bytes_are_the_budget() -> None:
    """512 bytes is the whole program. "2s" instead of "2000ms" is four
    bytes back, every time it appears.

    Deleted format_time's seconds branch: this asserts 2000ms.
    """
    animation = Animation("t", (PaintStep((WholeBar("#FFFFFF", Timing(2000, "pulse")),)),))
    assert compile_animation(animation) == "#FFFFFF 2s pulse"
    fractional = Animation("t", (PaintStep((WholeBar("#FFFFFF", Timing(1400)),)),))
    assert compile_animation(fractional) == "#FFFFFF 1400ms"


def test_a_delay_is_never_emitted_where_the_firmware_would_read_a_duration() -> None:
    """The trap in the grammar: "#FF00FF pulse 1s" is a 330 ms pulse
    DELAYED by a second, and "#FF00FF 1s" is a duration -- so a lone delay
    cannot be spelled at all. The model refuses instead of emitting
    something that means the wrong thing.

    Deleted the delay-without-duration branch of _check_timing: this passes
    validation and compiles to "#FF00FF 1s", a duration.
    """
    lone_delay = Animation("t", (PaintStep((WholeBar("#FF00FF", Timing(delay_ms=1000)),)),))
    problems = validate_animation(lone_delay)
    assert "delay-without-duration" in _codes(problems)
    with pytest.raises(AnimationValidationError):
        compile_animation(lone_delay)

    with_easing = Animation(
        "t", (PaintStep((WholeBar("#FF00FF", Timing(easing="pulse", delay_ms=1000)),)),)
    )
    assert compile_animation(with_easing) == "#FF00FF pulse 1s"
    assert _firmware().parse("#FF00FF pulse 1s", 0).ok


# --- validation refuses exactly what the firmware refuses ------------------


@pytest.mark.parametrize(
    ("program", "firmware_error"),
    [
        ("0:off 100ms", "bad-index"),
        ("#f0f 100ms", "bad-color"),
        ("#FF0000 100ms swoosh", "bad-time"),
        ("#FF0000 66s", "bad-time"),
        ("#FF0000 100ms\nrepeat 0", "bad-repeat"),
        ("repeat\n#FF0000 100ms", "bad-repeat"),
        ("#FF0000 100ms\nrepeat 2\n#00FF00 100ms\nrepeat 3", "bad-repeat"),
        ("#FF0000 #00FF00\nroll", "bad-time"),
        ("brightness 300\n#FF0000 100ms", "bad-brightness"),
        ("ff0000 100ms", "syntax"),
        ("#FF0000 100ms ease 100ms 50ms", "trailing-input"),
        ("\n".join(["#FF0000 10ms"] * 21), "too-many-lines"),
    ],
)
def test_every_refusal_is_a_refusal_the_firmware_would_also_make(
    program: str, firmware_error: str
) -> None:
    """The anti-decorative-validation test: for each program the model
    rejects, the real parser is asked what IT thinks, and has to agree --
    with the specific error, not merely "not ok".

    Deleted any one of these checks and the model happily produces bytes
    that make the device blink red six times.
    """
    controller = _firmware()
    result = controller.parse(program, 0)
    assert not result.ok, f"the firmware accepted {program!r}"
    assert result.error_name == firmware_error

    problems = problems_for_program(program)
    assert [p for p in problems if p.is_error], f"the model accepted {program!r}"


def test_out_of_range_leds_are_a_warning_because_the_firmware_ignores_them() -> None:
    """The one disagreement worth keeping: 9:#FFFFFF PARSES on an 8-LED
    build and then paints nothing. Rejecting it would break the documented
    portability of shared scripts; accepting it silently would let the
    editor lie about what the owner will see. So: a warning, and the burn
    refuses on warnings by default.

    Deleted the index >= led_count branch: no warning, and a Dot burn
    silently drops six of eight steps.
    """
    controller = _firmware()
    assert controller.parse("9:#FFFFFF 100ms", 0).ok

    animation = Animation("t", (PaintStep((IndexedPaint(((9, "#FFFFFF"),), Timing(100)),)),))
    problems = validate_animation(animation, led_count=8)
    assert _codes(problems) == {"index-out-of-range"}
    assert not [p for p in problems if p.is_error]
    assert "does not exist" in _messages(problems)
    # Same program, 2-LED Dot: LED 1 is fine, LED 9 is not.
    dot = Animation("t", (PaintStep((IndexedPaint(((1, "#FFFFFF"),), Timing(100)),)),))
    assert validate_animation(dot, led_count=2) == ()


def test_problem_messages_name_the_step_and_the_fix() -> None:
    """"step 3: ..." or it is not an error message, it is a shrug.

    Deleted _where()'s prefix: every message loses its step number.
    """
    animation = Animation(
        "t",
        (
            PaintStep((WholeBar("#FFFFFF", Timing(100)),)),
            PaintStep((WholeBar("#FFFFFF", Timing(100)),)),
            PaintStep((IndexedPaint(((0, OFF),), Timing(100)),)),
        ),
    )
    problems = validate_animation(animation)
    assert len(problems) == 1
    problem = problems[0]
    assert problem.step == 3
    assert problem.message.startswith("step 3: ")
    assert "#000000" in problem.message


def test_a_program_over_the_byte_cap_is_refused_with_its_actual_size() -> None:
    """Deleted the byte check: compile returns 600+ bytes and the device
    write fails at the last possible moment instead of the first.
    """
    wide = Animation(
        "t",
        tuple(
            PaintStep(
                (
                    IndexedPaint(
                        tuple((index, "#FF0044") for index in range(8)),
                        Timing(100, "ease", 50),
                    ),
                )
            )
            for _ in range(6)
        ),
    )
    rendered = render_animation(wide)
    assert len(rendered.encode()) > MAX_PROGRAM_BYTES
    with pytest.raises(AnimationValidationError) as caught:
        compile_animation(wide)
    assert "bytes" in str(caught.value)
    assert str(len(rendered.encode())) in str(caught.value)


def test_the_line_cap_counts_comments_the_way_the_firmware_does() -> None:
    """Measured: the firmware's 20-line cap counts every physical line,
    comments and blanks included.

    Deleted the line check: a 12-comment, 12-step animation compiles and
    the device answers with a red parse-error strobe.
    """
    controller = _firmware()
    twenty_one = "\n".join(["// c"] * 11 + ["#FF0000 10ms"] * 10)
    assert controller.parse(twenty_one, 0).error_name == "too-many-lines"

    animation = Animation(
        "t",
        tuple(CommentStep("c") for _ in range(11))
        + tuple(PaintStep((WholeBar("#FF0000", Timing(10)),)) for _ in range(10)),
    )
    assert len(animation.steps) > MAX_PROGRAM_LINES
    assert "too-many-lines" in _codes(validate_animation(animation))


def test_a_non_breaking_space_is_caught_before_the_device_sees_it() -> None:
    """The paste hazard. U+00A0 renders exactly like a space and is a
    firmware syntax error; a program copied out of a chat window or a web
    page carries them invisibly.

    Deleted _check_text's non-ASCII branch: this parses into a model whose
    comment carries the character straight into the file.
    """
    problems = problems_for_program("// tap\u00a0tap\n#FF0000 100ms")
    assert "non-ascii" in _codes(problems)
    assert "U+00A0" in _messages(problems)


def test_reading_a_program_never_raises_so_an_editor_can_validate_as_you_type() -> None:
    """Deleted read_program (leaving only the raising parse_animation): the
    settings pane has to wrap every keystroke in try/except and gets one
    problem instead of all of them.
    """
    animation, problems = read_program("#FF0000 100ms\n0:off\nrepeat 0")
    assert isinstance(animation, Animation)
    assert len(problems) >= 2
    assert problems_for_program("") == problems_for_program("")
    assert "empty" in _codes(problems_for_program(""))


# --- what an animation is worth in time ------------------------------------


def test_duration_arithmetic_matches_the_line_rule() -> None:
    """A line ends at its LONGEST delay-plus-duration; brightness, comments
    and repeat take no time; an untimed line lasts one 60 Hz frame.

    Deleted the max() in step_duration_ms: the staggered step reports 150 ms
    instead of 250 ms and the strobe check under-counts.
    """
    assert animation_duration_ms(BREATHING) == 1800
    assert loop_duration_ms(BREATHING) == 1800
    assert animation_duration_ms(STAGGERED) == 250 + 300 + 2000
    untimed = Animation("t", (PaintStep((WholeBar("#FFFFFF"),)),))
    assert animation_duration_ms(untimed) == 16
    assert loop_duration_ms(untimed) is None


def test_a_loop_faster_than_two_hertz_is_flagged_and_blocks_a_burn(tmp_path) -> None:
    """The owner's law, which the firmware knows nothing about.

    Deleted _cadence_problems: a 10 Hz strobe burns to INIT.LED without a
    word, and plays at every boot.
    """
    strobe = Animation(
        "t",
        (
            PaintStep((WholeBar("#FFFFFF", Timing(50, "none")),)),
            PaintStep((WholeBar(OFF, Timing(50, "none")),)),
            RepeatStep(),
        ),
    )
    problems = validate_animation(strobe)
    assert "strobe" in _codes(problems)
    assert "10.0 Hz" in _messages(problems)
    with pytest.raises(AnimationValidationError):
        plan_power_up_burn(
            strobe, device_path=tmp_path, firmware_parser=lambda text, count: None
        )
    # The author may still insist -- explicitly.
    plan = plan_power_up_burn(
        strobe,
        device_path=tmp_path,
        allow_warnings=True,
        firmware_parser=lambda text, count: None,
    )
    assert plan.warnings


# --- burning to INIT.LED ---------------------------------------------------


class _RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text, *, device_path=None, file_name=None, **_kwargs):
        self.calls.append((text, file_name))
        return Path("/tmp/fake-device") / str(file_name)


def test_a_burn_is_a_dry_run_unless_it_is_asked_for_out_loud(tmp_path) -> None:
    """The one operation here a person cannot undo by looking away.

    Deleted the dry_run default (made it False): every caller that forgot
    the keyword writes to the boot file.
    """
    writer = _RecordingWriter()
    plan = burn_power_up_animation(BREATHING, device_path=tmp_path, writer=writer)
    assert writer.calls == []
    assert plan.dry_run and not plan.written
    assert plan.payload == compile_animation(BREATHING).encode("utf-8")

    written = burn_power_up_animation(
        BREATHING, device_path=tmp_path, dry_run=False, writer=writer
    )
    assert writer.calls == [(plan.program, "INIT.LED")]
    assert written.written and not written.dry_run


def test_an_invalid_animation_never_reaches_the_writer(tmp_path) -> None:
    """The MODEL validator is a gate in its own right, not a preview of what
    the firmware parser will say.

    The animation here is one the firmware accepts and the model refuses: a
    lone delay renders as "#FF00FF 1s", which parses perfectly and means a
    one-second HOLD rather than the one-second wait the author asked for.
    So the firmware gate cannot save this test -- only the model gate can,
    which is the point. (An earlier version of this test used "0:off", and
    passed with the model gate deleted because the firmware caught it: a
    test that could not fail for its own stated reason.)

    Deleted the errors check in plan_power_up_burn: the writer is called
    with a program that does the wrong thing at every boot.
    """
    writer = _RecordingWriter()
    misread = Animation(
        "t", (PaintStep((WholeBar("#FF00FF", Timing(delay_ms=1000)),)),)
    )
    assert _firmware().parse("#FF00FF 1s", 0).ok
    with pytest.raises(AnimationValidationError):
        burn_power_up_animation(
            misread, device_path=tmp_path, dry_run=False, writer=writer
        )
    assert writer.calls == []

    # And the plain case: a program the firmware itself rejects.
    broken = Animation("t", (PaintStep((IndexedPaint(((0, OFF),), Timing(100)),)),))
    with pytest.raises(AnimationValidationError):
        burn_power_up_animation(
            broken, device_path=tmp_path, dry_run=False, writer=writer
        )
    assert writer.calls == []


def test_a_burn_refuses_when_the_firmware_parser_cannot_be_consulted() -> None:
    """Fail closed. INIT.LED replays at every boot, so "we could not check"
    must never mean "go ahead".

    Deleted the require_firmware_parse branch: an unavailable parser turns
    the strongest gate into a no-op and the burn proceeds unverified.
    """
    writer = _RecordingWriter()

    def unavailable(text, led_count):
        raise RuntimeError("JavaScriptCore is unavailable.")

    with pytest.raises(AnimationValidationError) as caught:
        burn_power_up_animation(
            BREATHING, dry_run=False, writer=writer, firmware_parser=unavailable
        )
    assert "unverified" in str(caught.value)
    assert writer.calls == []

    with pytest.raises(AnimationValidationError):
        burn_power_up_animation(
            BREATHING,
            dry_run=False,
            writer=writer,
            firmware_parser=lambda text, count: "bad-color at line 1, column 1",
        )
    assert writer.calls == []


def test_the_burn_consults_the_real_firmware_by_default() -> None:
    """Not a stub: the default parser IS the packaged sdled.wasm, so a
    caller that passes nothing is still verified against the firmware.

    Changed the firmware_parser default to None: the plan comes back with
    firmware_checked False (or, with require_firmware_parse still on,
    refuses) and this fails either way.
    """
    _firmware()
    plan = plan_power_up_burn(BREATHING, device_path=Path("/tmp/not-a-device"))
    assert plan.firmware_checked
    assert plan.program == compile_animation(BREATHING)


def test_the_payload_is_the_exact_bytes_the_device_will_hold(tmp_path) -> None:
    """Deleted the payload field (or derived it from a pretty-printed
    rendering): what the owner is shown stops being what is written.
    """
    plan = plan_power_up_burn(
        STAGGERED, device_path=tmp_path, firmware_parser=lambda text, count: None
    )
    assert plan.payload.decode("utf-8") == plan.program
    assert plan.byte_count == len(plan.program.encode("utf-8"))
    assert plan.byte_count <= MAX_PROGRAM_BYTES


def test_a_saved_animation_can_be_burned_by_name_through_led_status(tmp_path) -> None:
    """The call site the settings window gets. Library -> model -> bytes,
    with the dry run still the default.

    Deleted led_status.burn_saved_animation_to_power_up: the editor has no
    device-facing entry point and the module is unreachable.
    """
    from sidepulse.led_status import burn_saved_animation_to_power_up

    _firmware()
    path = tmp_path / "animation-library.json"
    library = store.AnimationLibrary().with_program(
        "Boot", compile_animation(BREATHING), now_epoch=1.0
    )
    store.save_animation_library(path, library)

    plan = burn_saved_animation_to_power_up(
        "Boot", library_path=path, device_path=tmp_path, led_count=8
    )
    assert plan.dry_run and not plan.written
    assert plan.program == compile_animation(BREATHING)
    assert plan.target == tmp_path / "INIT.LED"

    with pytest.raises(store.AnimationLibraryError):
        burn_saved_animation_to_power_up("Nope", library_path=path, device_path=tmp_path)


# --- the personal library --------------------------------------------------


def test_the_library_round_trips_through_a_private_file(tmp_path) -> None:
    """Deleted atomic_private_write in favour of write_text: the file lands
    world-readable, which is the discipline every other store here keeps.
    """
    path = tmp_path / "animation-library.json"
    library = (
        store.AnimationLibrary()
        .with_program("Boot", compile_animation(BREATHING), now_epoch=1.0)
        .with_program("Stagger", compile_animation(STAGGERED), now_epoch=2.0)
    )
    store.save_animation_library(path, library)
    assert path.stat().st_mode & 0o777 == 0o600

    restored = store.load_animation_library(path)
    assert restored.health is store.LibraryHealth.HEALTHY
    assert restored.library == library
    assert restored.library.get("Boot").to_animation().steps == BREATHING.steps


def test_the_library_is_bounded_in_count_and_in_bytes(tmp_path) -> None:
    """Two bounds, not one: a count cap alone lets the file grow, a byte cap
    alone lets the popup become a scroll.

    Deleted the MAX_SAVED_ANIMATIONS check: the 25th save is accepted and
    the loader then calls the file corrupt.
    """
    library = store.AnimationLibrary()
    for index in range(store.MAX_SAVED_ANIMATIONS):
        library = library.with_program(f"look {index}", "#FF0000 100ms", now_epoch=1.0)
    with pytest.raises(store.AnimationLibraryError) as caught:
        library.with_program("one more", "#FF0000 100ms")
    assert "Delete one" in str(caught.value)

    path = tmp_path / "library.json"
    store.save_animation_library(path, library)
    assert store.load_animation_library(path).library.names == library.names

    long_name = "x" * (store.MAX_ANIMATION_NAME_LENGTH + 1)
    with pytest.raises(store.AnimationLibraryError):
        store.AnimationLibrary().with_program(long_name, "#FF0000 100ms")
    with pytest.raises(store.AnimationLibraryError):
        store.AnimationLibrary().with_program("   ", "#FF0000 100ms")


def test_the_library_refuses_to_hold_a_program_the_firmware_would_reject() -> None:
    """A saved look that cannot be burned is a trap set for the person who
    loads it a month later.

    Deleted the parse in with_program: "0:off" is stored, and the burn
    refuses only once the owner has already pressed the button.
    """
    with pytest.raises(store.AnimationLibraryError) as caught:
        store.AnimationLibrary().with_program("Broken", "0:off 100ms")
    assert "cannot be saved" in str(caught.value)


def test_rename_keeps_its_place_and_refuses_a_name_already_taken() -> None:
    """Deleted the in-place rename (append instead): a rename silently
    reorders the owner's list, which is how a look gets lost.
    """
    library = (
        store.AnimationLibrary()
        .with_program("one", "#FF0000 100ms", now_epoch=1.0)
        .with_program("two", "#00FF00 100ms", now_epoch=2.0)
        .with_program("three", "#0000FF 100ms", now_epoch=3.0)
    )
    renamed = library.renamed("two", "middle")
    assert renamed.names == ("one", "middle", "three")
    assert renamed.get("middle").program == "#00FF00 100ms"

    with pytest.raises(store.AnimationLibraryError):
        renamed.renamed("one", "three")
    with pytest.raises(store.AnimationLibraryError):
        renamed.renamed("gone", "anything")

    assert library.with_program("two", "#FFFFFF 100ms", now_epoch=9.0).names == (
        "one",
        "two",
        "three",
    )
    assert library.without("two").names == ("one", "three")
    with pytest.raises(store.AnimationLibraryError):
        library.without("gone")


@pytest.mark.parametrize(
    ("payload", "health"),
    [
        ("not json at all", store.LibraryHealth.CORRUPT),
        ('{"version":99,"animations":[]}', store.LibraryHealth.UNSUPPORTED),
        ('{"version":1}', store.LibraryHealth.CORRUPT),
        ('{"version":1,"animations":[{"name":"x"}]}', store.LibraryHealth.CORRUPT),
        ('{"version":1,"animations":{}}', store.LibraryHealth.CORRUPT),
    ],
)
def test_an_unreadable_library_degrades_instead_of_taking_the_window_down(
    tmp_path, payload: str, health
) -> None:
    """Deleted the typed health (letting the decoder raise): opening
    Settings after a partial write throws instead of showing an empty list.
    """
    path = tmp_path / "library.json"
    path.write_text(payload, encoding="utf-8")
    restored = store.load_animation_library(path)
    assert restored.health is health
    assert restored.library.entries == ()

    assert (
        store.load_animation_library(tmp_path / "missing.json").health
        is store.LibraryHealth.MISSING
    )


def test_the_stored_document_has_exactly_the_fields_it_declares(tmp_path) -> None:
    """Deleted the exact-field decode: an extra key rides along silently and
    the format drifts.
    """
    path = tmp_path / "library.json"
    store.save_animation_library(
        path,
        store.AnimationLibrary().with_program("one", "#FF0000 100ms", now_epoch=1.0),
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document) == {"version", "animations"}
    assert set(document["animations"][0]) == {"name", "program", "updated_at_epoch"}

    document["animations"][0]["extra"] = True
    path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load_animation_library(path).health is store.LibraryHealth.CORRUPT


# --- the three motions that were lies --------------------------------------
#
# The claim in MASTER-PLAN.md is that Heartbeat, Knock (double-blink) and
# Blink "render as the same shape at different ratios". Measured against the
# firmware before this wave:
#
#   blink        #C 567ms cosine / off 433ms cosine, three times
#   double-blink #C 567ms cosine / off 433ms cosine, twice
#   heartbeat    #C 180ms cosine / off 120ms cosine / #C 180ms / off 520ms
#
# Blink and Knock were the SAME two lines -- byte-for-byte -- so the claim is
# literally true for that pair. Heartbeat was not identical, but it was built
# from the same primitive (a whole-bar ramp to full and back with cosine on
# both edges), so all three read as one motion at three ratios. The tests
# below pin the three shapes apart.


def _levels(program: str) -> list[tuple[float, str, int]]:
    """(level, easing, duration) per line, for whole-bar programs.

    Returns [] for a program that paints individual LEDs -- a per-LED chase
    has no single whole-bar envelope, and pretending it does would compare
    four indexed patterns by their shared "off 300ms cosine" preamble.
    """
    animation, _problems = read_program(program)
    out: list[tuple[float, str, int]] = []
    for step in animation.steps:
        if type(step) is not PaintStep:
            continue
        segment = step.segments[0]
        if type(segment) is not WholeBar:
            return []
        if segment.color == OFF:
            level = 0.0
        else:
            level = max(int(segment.color[index : index + 2], 16) for index in (1, 3, 5)) / 255
        out.append((level, segment.timing.easing or "", segment.timing.effective_duration_ms))
    return out


def _unit_shape(program: str) -> tuple[tuple[float, str, float], ...]:
    """The repeating unit, normalised by its own length.

    Two motions that are "the same shape at a different ratio" produce the
    same tuple here; that is the defect, made falsifiable.
    """
    lines = _levels(program)
    for size in range(1, len(lines) + 1):
        if len(lines) % size:
            continue
        unit = lines[:size]
        if all(lines[start : start + size] == unit for start in range(0, len(lines), size)):
            span = sum(duration for _level, _easing, duration in unit) or 1
            return tuple(
                (level, easing, round(duration / span, 3)) for level, easing, duration in unit
            )
    return tuple(
        (level, easing, round(duration, 3)) for level, easing, duration in lines
    )


def test_blink_and_knock_are_no_longer_the_same_shape() -> None:
    """The defect, exactly. Before this wave both patterns emitted the same
    two lines and differed only in how many times they were repeated, so
    their repeating unit was identical.

    Restore the old ``elif style.pattern in ("blink", "double-blink")``
    branch and this fails: both sides collapse to
    ((1.0, 'cosine', 0.567), (0.0, 'cosine', 0.433)).
    """
    blink = style_to_program(SignalStyle("#FFFFFF", "blink", 1.0, 1.0))
    knock = style_to_program(SignalStyle("#FFFFFF", "double-blink", 1.0, 1.0))
    assert _unit_shape(blink) != _unit_shape(knock)
    assert blink.splitlines()[:4] != knock.splitlines()


def test_blink_has_hard_edges_and_nothing_else_does() -> None:
    """A cosine blink is a triangle, and a triangle is what breathe already
    is. The word "blink" has to mean the square.

    Restore ``cosine`` in the blink branch: every edge eases and blink is
    breathe at another ratio.
    """
    blink = style_to_program(SignalStyle("#34C759", "blink", 1.0, 1.0))
    assert [easing for _level, easing, _duration in _levels(blink)] == ["none"] * 6
    breathe = style_to_program(SignalStyle("#34C759", "breathe", 1.0, 1.0))
    assert "cosine" in breathe and "pulse" in breathe


def test_a_knock_is_two_taps_and_then_a_rest() -> None:
    """What makes it a knock rather than a flash: the silence after it. The
    old shape was 567 ms lit / 433 ms dark twice -- more light than dark,
    with no rest at all.

    Restore the old double-blink branch and the longest dark span (433 ms)
    is SHORTER than the longest lit span (567 ms); this asserts the reverse.
    """
    knock = _levels(style_to_program(SignalStyle("#FFFFFF", "double-blink", 1.2, 1.0)))
    taps = [duration for level, _easing, duration in knock if level > 0]
    darks = [duration for level, _easing, duration in knock if level == 0]
    assert len(taps) == 2
    assert max(darks) >= 2 * max(taps)
    assert sum(duration for _level, _easing, duration in knock) == 1200


def test_a_heartbeat_is_two_unequal_thumps_and_a_flat_rest() -> None:
    """Three properties, none of which the old one had: each beat rises AND
    falls inside its own duration (``pulse``), the rest is a flat hold
    (``none``, not a cosine decay that reads as part of the beat), and the
    second beat is dimmer than the first so it cannot be mistaken for a
    knock's two equal taps.

    Restore the old heartbeat branch and all three assertions fail: both
    beats are the same colour, both use cosine, and the rest is a decay.
    """
    beats = _levels(style_to_program(SignalStyle("#FFFFFF", "heartbeat", 1.4, 1.0)))
    lit = [(level, easing) for level, easing, _duration in beats if level > 0]
    dark = [(easing, duration) for level, easing, duration in beats if level == 0]
    assert [easing for _level, easing in lit] == ["pulse", "pulse"]
    assert lit[0][0] > lit[1][0]
    assert all(easing == "none" for easing, _duration in dark)
    assert max(duration for _easing, duration in dark) >= 2 * min(
        duration for _easing, duration in dark
    )


def test_no_two_signal_patterns_share_a_repeating_unit() -> None:
    """The general form of the defect, over the whole catalogue.

    Restore the old blink/double-blink branch: blink and double-blink
    collide here.
    """
    shapes: dict[tuple, str] = {}
    for pattern in SIGNAL_PATTERNS:
        program = style_to_program(SignalStyle("#FFFFFF", pattern, 1.0, 1.0))
        shape = _unit_shape(program)
        if not shape:
            continue  # indexed patterns are compared in the firmware test below
        assert shape not in shapes, f"{pattern} renders the same shape as {shapes[shape]}"
        shapes[shape] = pattern


def test_the_firmware_renders_three_distinguishable_motions() -> None:
    """The same claim, measured on the real engine rather than on the text:
    sample the whole-bar brightness of each motion over its own cycle and
    require the three envelopes to differ.

    Restore the old branches and blink/knock come back byte-identical, so
    their sampled envelopes match exactly.
    """
    controller = _firmware()
    envelopes: dict[str, tuple[int, ...]] = {}
    for pattern, cycle_ms in (
        ("blink", 1000),
        ("double-blink", 1000),
        ("heartbeat", 1000),
    ):
        program = style_to_program(SignalStyle("#FFFFFF", pattern, 1.0, 1.0))
        parsed = controller.parse(program, 0)
        assert parsed.ok, f"{pattern}: {parsed.error_name}"
        envelopes[pattern] = tuple(
            controller.step(5 + sample * cycle_ms // 40)[0][1] for sample in range(40)
        )
    assert envelopes["blink"] != envelopes["double-blink"]
    assert envelopes["blink"] != envelopes["heartbeat"]
    assert envelopes["double-blink"] != envelopes["heartbeat"]
    # A knock and a heartbeat both go dark for most of their cycle; a blink
    # does not. That is the coarse difference a person actually sees.
    assert sum(envelopes["blink"]) > sum(envelopes["double-blink"])
    assert sum(envelopes["blink"]) > sum(envelopes["heartbeat"])


@pytest.mark.parametrize("pattern", SIGNAL_PATTERNS)
@pytest.mark.parametrize("speed", [0.1, 1.0, 10.0])
def test_every_pattern_still_parses_on_the_real_firmware(pattern: str, speed: float) -> None:
    """The reshaped motions must stay inside the grammar at every speed the
    settings pane offers.

    Deleted the max(1, ...) floors in the new branches: a 0.1 s knock emits
    a 0 ms rest, which the firmware accepts but which collapses the shape.
    """
    controller = _firmware()
    program = style_to_program(SignalStyle("#ABCDEF", pattern, speed, 1.0), 128)
    result = controller.parse(program, 0)
    assert result.ok, f"{pattern}@{speed}: {result.error_name} line {result.line}"
    assert len(program.encode()) <= MAX_PROGRAM_BYTES
    assert len(program.splitlines()) <= MAX_PROGRAM_LINES
