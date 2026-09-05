"""Translate JR-Bar's shared state colours into explicit vendor light fields."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .colors import ColorSettings


@dataclass(frozen=True, slots=True)
class CreatorMicroBrightnessProfile:
    device_id: str = "creator-micro"
    brightness: int = 102
    auto_brightness_enabled: bool = False


@dataclass(frozen=True, slots=True)
class CreatorMicroLightFrame:
    color: int
    brightness: float
    effect: int

    def params(self) -> list[dict[str, int | float]]:
        return [
            {"id": index, "c": self.color, "b": self.brightness,
             "e": self.effect, "s": 0.5, "sk": 0, "sa": 0}
            for index in range(20)
        ]


def creator_micro_light_frame(
    state: str, *, colors: ColorSettings | None = None, brightness: float = 0.4,
    idle_off: bool = True,
) -> CreatorMicroLightFrame:
    colors = colors if colors is not None else ColorSettings()
    modes = {
        "input_required": "ask", "failure": "ask", "quota_exhausted": "ask",
        "quota_warning": "ask", "reset": "done", "completed": "done",
        "active": "working", "idle": "idle",
    }
    if state not in modes or not math.isfinite(brightness) or not 0 <= brightness <= 1:
        raise ValueError("invalid Creator Micro light state")
    dark = (state == "idle" and idle_off) or brightness == 0
    color = int(colors.mode_color(modes[state]).lstrip("#"), 16)
    return CreatorMicroLightFrame(color, 0 if dark else brightness, 0 if dark else 1)


def creator_micro_light_params(state: str) -> list[dict[str, int | float]]:
    return creator_micro_light_frame(state).params()
