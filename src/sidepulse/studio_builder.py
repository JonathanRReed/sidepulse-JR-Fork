"""The Studio's no-typing builder: steps you can touch.

The Studio was a text editor for the LEDS.LED DSL -- powerful, and a
wall for anyone who did not want to type. The builder is the same
program as a row of controls (2026-08-26): each step is a color well, a
duration dial, and a feel; Loop is a checkbox. Every change compiles to
the DSL and lands in the editor below, so the text view becomes the
advanced view of the same program -- through the same validation, the
same safety compiler, the same preview. The DSL is an output format
now, not the interface.

View construction and the pure compiler live here; the actions live on
the controller like every other Studio control.
"""

from __future__ import annotations

from AppKit import NSColor, NSColorWell

from . import native_ui

#: (dsl_ease, human label) -- the feel popup's vocabulary.
EASE_CHOICES: tuple[tuple[str, str], ...] = (
    ("pulse", "Breathe"),
    ("cosine", "Fade"),
    ("ease", "Ease"),
    ("none", "Hold"),
)
MIN_STEP_MS = 100
MAX_STEP_MS = 4_000
MAX_BUILDER_STEPS = 12

DEFAULT_STEPS: tuple[dict, ...] = (
    {"color": "#00E5FF", "ms": 800, "ease": "pulse"},
    {"color": "#000000", "ms": 300, "ease": "cosine"},
)


def compile_builder_program(steps, loop: bool) -> str:
    """Steps to DSL. Pure; the editor pipeline validates the result."""
    lines = []
    for step in steps:
        color = str(step.get("color") or "#000000")
        if color.lower() in {"#000000", "off"}:
            color = "off"
        ms = max(MIN_STEP_MS, min(MAX_STEP_MS, int(step.get("ms") or MIN_STEP_MS)))
        ease = str(step.get("ease") or "none")
        if ease not in {key for key, _label in EASE_CHOICES}:
            ease = "none"
        lines.append(f"{color} {ms}ms {ease}")
    if loop and lines:
        lines.append("repeat")
    return "\n".join(lines)


def _hex_to_nscolor(hex_value: str):
    text = str(hex_value or "#000000").lstrip("#")
    try:
        red = int(text[0:2], 16) / 255.0
        green = int(text[2:4], 16) / 255.0
        blue = int(text[4:6], 16) / 255.0
    except (ValueError, IndexError):
        red = green = blue = 0.0
    return NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1.0)


def rebuild_builder_rows(target) -> None:
    """Repaint the step rows from the controller's step list."""
    stack = getattr(target, "_studio_builder_rows_stack", None)
    if stack is None:
        return
    for view in list(stack.arrangedSubviews()):
        stack.removeArrangedSubview_(view)
        view.removeFromSuperview()
    steps = getattr(target, "studio_builder_steps", list(DEFAULT_STEPS))
    for index, step in enumerate(steps):
        row = native_ui.make_stack(orientation="horizontal", spacing=8.0)
        well = NSColorWell.alloc().init()
        well.setColor_(_hex_to_nscolor(step.get("color")))
        well.setTarget_(target)
        well.setAction_("studioBuilderColorChanged:")
        well.setTag_(index)
        native_ui.constrain_width(well, 44.0)
        row.addArrangedSubview_(well)
        slider = native_ui.make_slider(
            min_value=float(MIN_STEP_MS),
            max_value=float(MAX_STEP_MS),
            value=float(step.get("ms") or MIN_STEP_MS),
            target=target,
            action="studioBuilderDurationChanged:",
            continuous=True,
        )
        slider.setTag_(index)
        native_ui.constrain_width(slider, 130.0)
        row.addArrangedSubview_(slider)
        duration_label = native_ui.make_label(
            f"{int(step.get('ms') or MIN_STEP_MS)}ms", secondary=True, size=11.0
        )
        native_ui.constrain_width(duration_label, 52.0)
        row.addArrangedSubview_(duration_label)
        ease_popup = native_ui.make_popup_button(target, "studioBuilderEaseChanged:")
        for ease_key, label in EASE_CHOICES:
            ease_popup.addItemWithTitle_(label)
            ease_popup.lastItem().setRepresentedObject_(ease_key)
        selected = next(
            (
                position
                for position, (ease_key, _label) in enumerate(EASE_CHOICES)
                if ease_key == step.get("ease")
            ),
            0,
        )
        ease_popup.selectItemAtIndex_(selected)
        ease_popup.setTag_(index)
        row.addArrangedSubview_(ease_popup)
        remove = native_ui.make_button("−", target, "studioBuilderRemoveStep:")
        remove.setTag_(index)
        row.addArrangedSubview_(remove)
        stack.addArrangedSubview_(row)
    labels = getattr(target, "_studio_builder_duration_labels", None)
    if labels is not None:
        labels.clear()
        for row in stack.arrangedSubviews():
            views = list(row.arrangedSubviews())
            labels.append(views[2] if len(views) > 2 else None)


def build_studio_builder(target):
    """The builder card content; mounted above the editor."""
    outer = native_ui.make_stack(orientation="vertical", spacing=8.0)
    outer.addArrangedSubview_(
        native_ui.make_wrapping_label(
            "Or build it: each row is one step — a color, how long, and "
            "its feel. Loop plays it forever. Everything you build lands "
            "in the editor below as real program text.",
            secondary=True,
            size=11.0,
            max_width=520.0,
        )
    )
    rows = native_ui.make_stack(orientation="vertical", spacing=6.0)
    target._studio_builder_rows_stack = rows
    target._studio_builder_duration_labels = []
    outer.addArrangedSubview_(rows)
    controls = native_ui.make_stack(orientation="horizontal", spacing=8.0)
    controls.addArrangedSubview_(
        native_ui.make_button("+ Add Step", target, "studioBuilderAddStep:")
    )
    loop_checkbox = native_ui.make_checkbox(
        "Loop", target, "studioBuilderLoopToggled:"
    )
    loop_checkbox.setState_(
        1 if getattr(target, "studio_builder_loop", True) else 0
    )
    controls.addArrangedSubview_(loop_checkbox)
    outer.addArrangedSubview_(controls)
    rebuild_builder_rows(target)
    return outer
