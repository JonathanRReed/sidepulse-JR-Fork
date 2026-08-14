"""Why the light is doing that -- the decision, never the data.

Every debugging session in this project started the same way: read a log
the user cannot see. The user can see the light and nothing behind it, so
"why is it red" has only ever been answerable by the person with a
terminal. This module renders the answer.

It explains the DECISION and refuses to show its inputs' contents. There
are no payloads here, no prompts, no tool names, no paths, no session ids
-- one agent display name (already on screen in the mailbox and already
spoken by the Screen Bar), one rule, one timestamp.

The rule ladder is not re-derived here, which is the whole point: it is
read back off ``presentation_policy._automatic_glance``'s result. That
ladder takes the FIRST rule that applies, so the resolved semantic alone
proves that every rung above the winner was tested and failed, and that
every rung below it was never reached. Saying "not reached" instead of
"no" is the difference between explaining the code and guessing at it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from .intake_health import (
    IntakeReport,
    format_age_ago,
    format_duration,
)
from .presentation_policy import (
    GlanceOverrideReason,
    GlanceSemantic,
    ResolvedGlance,
)

PANEL_TITLE: Final = "Why Is It Doing That?"
MENU_ITEM_TITLE: Final = "Why Is It Doing That?"
_CONTENT_PLEDGE: Final = (
    "This panel explains the decision. It never shows what an agent said."
)

# The exact ladder in presentation_policy._automatic_glance, in its exact
# order. Changing that ladder without changing this list makes the panel
# lie, which is why the tests pin them against each other.
RULE_LADDER: Final = (
    (GlanceSemantic.ATTENTION, "Someone is waiting on you"),
    (GlanceSemantic.FRESH_FAILURE, "A failure just happened"),
    (GlanceSemantic.FRESH_COMPLETION, "An agent just finished"),
    (GlanceSemantic.ACTIVE, "An agent is working"),
    (GlanceSemantic.UNRESOLVED_FAILURE, "A failure is still unresolved"),
    (GlanceSemantic.CAPACITY, "Capacity is worth showing"),
    (GlanceSemantic.REST, "Nothing needs you"),
)

_OVERRIDE_REASONS: Final = {
    GlanceOverrideReason.NONE: None,
    GlanceOverrideReason.EXPLICIT_DEVICE_MODE: "this device is set to show something else",
    GlanceOverrideReason.PROVIDER_PIN: "this device is pinned to one provider",
    GlanceOverrideReason.SAFETY_SIGNAL: "a safety signal took the light",
    GlanceOverrideReason.FOCUS: "a Focus is active",
    GlanceOverrideReason.SHARED_SPACE_PRIVACY: "the shared-space setting is on",
    GlanceOverrideReason.UNAVAILABLE: "the light could not be resolved",
}

_LIGHT_NAMES: Final = {
    GlanceSemantic.ATTENTION: "Ask",
    GlanceSemantic.FRESH_FAILURE: "Failed",
    GlanceSemantic.FRESH_COMPLETION: "Done",
    GlanceSemantic.ACTIVE: "Working",
    GlanceSemantic.UNRESOLVED_FAILURE: "Failed",
    GlanceSemantic.CAPACITY: "Working",
    GlanceSemantic.REST: "Rest",
}

_DOT_WIDTH: Final = 38


class RungOutcome(str, Enum):
    NOT_MET = "not_met"
    MET = "met"
    NOT_REACHED = "not_reached"


@dataclass(frozen=True, slots=True)
class DecisionRung:
    name: str
    outcome: RungOutcome

    def __post_init__(self) -> None:
        if type(self.name) is not str or type(self.outcome) is not RungOutcome:
            raise ValueError("invalid decision rung")


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    light: str
    light_state: str
    color: str | None
    rungs: tuple[DecisionRung, ...]
    override: str | None
    motion: str
    driver: str | None
    changed_at_epoch: float | None
    changed_age_seconds: float | None
    intake: IntakeReport | None

    def __post_init__(self) -> None:
        if not (
            type(self.light) is str
            and type(self.rungs) is tuple
            and all(type(rung) is DecisionRung for rung in self.rungs)
            and (self.intake is None or type(self.intake) is IntakeReport)
        ):
            raise ValueError("invalid decision trace")


def rungs_for_semantic(
    semantic: GlanceSemantic,
    *,
    overridden: bool,
) -> tuple[DecisionRung, ...]:
    """Read the first-match ladder back off the semantic it produced."""
    if overridden:
        return tuple(
            DecisionRung(name, RungOutcome.NOT_REACHED) for _key, name in RULE_LADDER
        )
    winner = next(
        (index for index, (key, _name) in enumerate(RULE_LADDER) if key is semantic),
        None,
    )
    rungs: list[DecisionRung] = []
    for index, (_key, name) in enumerate(RULE_LADDER):
        if winner is None:
            outcome = RungOutcome.NOT_REACHED
        elif index < winner:
            outcome = RungOutcome.NOT_MET
        elif index == winner:
            outcome = RungOutcome.MET
        else:
            outcome = RungOutcome.NOT_REACHED
        rungs.append(DecisionRung(name, outcome))
    return tuple(rungs)


def build_decision_trace(
    glance: ResolvedGlance | None,
    *,
    light_state: str,
    color: str | None,
    driver: str | None,
    changed_at_epoch: float | None,
    changed_age_seconds: float | None,
    intake: IntakeReport | None,
) -> DecisionTrace:
    if type(glance) is not ResolvedGlance:
        return DecisionTrace(
            light="Not decided yet",
            light_state=str(light_state or "unknown"),
            color=color,
            rungs=(),
            override=None,
            motion="nothing is playing",
            driver=None,
            changed_at_epoch=changed_at_epoch,
            changed_age_seconds=changed_age_seconds,
            intake=intake,
        )
    overridden = glance.override_reason is not GlanceOverrideReason.NONE
    cue = glance.cue
    if cue is None:
        motion = "steady — no burst"
    else:
        times = "once" if cue.repetitions == 1 else f"{cue.repetitions} times"
        motion = f"a burst, {times}"
    return DecisionTrace(
        light=_LIGHT_NAMES.get(glance.semantic, glance.semantic.value),
        light_state=str(light_state or glance.semantic.value),
        color=color,
        rungs=rungs_for_semantic(glance.semantic, overridden=overridden),
        override=_OVERRIDE_REASONS.get(glance.override_reason),
        motion=motion,
        driver=driver,
        changed_at_epoch=changed_at_epoch,
        changed_age_seconds=changed_age_seconds,
        intake=intake,
    )


def _dotted(label: str, value: str) -> str:
    filler = max(1, _DOT_WIDTH - len(label))
    return f"  {label} {'.' * filler} {value}"


def _changed_line(trace: DecisionTrace) -> str:
    if trace.changed_age_seconds is None:
        return "  Unchanged since SidePulse started."
    held = format_duration(trace.changed_age_seconds)
    if trace.changed_at_epoch is None:
        return f"  Unchanged for {held}."
    try:
        stamp = datetime.fromtimestamp(float(trace.changed_at_epoch)).strftime("%H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return f"  Unchanged for {held}."
    return f"  Unchanged for {held} (since {stamp})."


def decision_trace_text(trace: DecisionTrace) -> str:
    if type(trace) is not DecisionTrace:
        raise ValueError("invalid decision trace")
    lines: list[str] = ["THE LIGHT RIGHT NOW", ""]
    identity = [trace.light]
    # "Ask · ask" is not two facts. The LED state token earns its place
    # only where it says something the light's name does not.
    if trace.light_state.lower() != trace.light.lower():
        identity.append(trace.light_state)
    if trace.color:
        identity.append(trace.color)
    lines.append(f"  {' · '.join(identity)}")
    lines.append(f"  Motion: {trace.motion}")
    lines.append(_changed_line(trace))
    lines.append("")

    lines.append("THE RULE THAT PRODUCED IT")
    lines.append("")
    if not trace.rungs:
        lines.append("  No presentation has been resolved yet.")
    else:
        lines.append("  The light takes the FIRST rule that applies.")
        for index, rung in enumerate(trace.rungs, start=1):
            verdict = {
                RungOutcome.NOT_MET: "no",
                RungOutcome.MET: "THIS ONE",
                RungOutcome.NOT_REACHED: "not reached",
            }[rung.outcome]
            lines.append(_dotted(f"{index}. {rung.name}", verdict))
    if trace.override:
        lines.append(f"  Overridden: {trace.override}.")
    elif trace.rungs:
        lines.append("  Nothing overrode it.")
    lines.append("")

    lines.append("THE AGENT BEHIND IT")
    lines.append("")
    lines.append(f"  {trace.driver}" if trace.driver else "  No agent is driving the light.")
    lines.append("")

    lines.append("WHAT SIDEPULSE CAN HEAR")
    lines.append("")
    report = trace.intake
    if report is None:
        lines.append("  Intake has not been checked yet.")
    else:
        known = report.known
        if not known:
            lines.append("  No provider is connected, so nothing can arrive.")
        for item in known:
            lines.append(_dotted(item.label, _heard_value(item)))
        lines.append("")
        lines.append(
            _dotted(
                report.hook_state.check.value,
                f"{report.hook_state.code.value} "
                f"({report.hook_state.count} of {report.hook_state.limit})",
            )
        )
        lines.append(
            _dotted(
                report.source_health.check.value,
                f"{report.source_health.code.value} "
                f"({report.source_health.count} of {report.source_health.limit})",
            )
        )
    lines.append("")
    lines.append(_CONTENT_PLEDGE)
    return "\n".join(lines)


def _heard_value(item) -> str:
    if not item.installed:
        return f"not connected · last heard {format_age_ago(item.heard_age_seconds)}"
    if item.stuck:
        return "writing to the log, nothing arriving"
    if item.event_accepted_at is None:
        return "connected, nothing yet"
    return format_age_ago(item.heard_age_seconds)
