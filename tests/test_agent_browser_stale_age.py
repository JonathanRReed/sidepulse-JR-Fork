"""One silence threshold, not two.

A codex turn died mid-tool (pre_tool_use with no post_tool_use). The LED
projection demoted it after 240s of silence and stopped showing it, but
this list kept calling the same work "active" for a full hour, because
it carried its own 3600s threshold. Reported as "why does it say codex
is running ... it should not be running right now" while the lights were
already correct. Two surfaces disagreeing about one work is the defect.
"""

from __future__ import annotations

from sidepulse.agent_browser import ACTIVE_AGE_STALE_SECONDS
from sidepulse.operator_state import ACTIVE_SILENCE_SECONDS


def test_the_list_ages_out_exactly_when_the_lights_do() -> None:
    assert ACTIVE_AGE_STALE_SECONDS == ACTIVE_SILENCE_SECONDS
