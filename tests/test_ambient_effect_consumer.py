from dataclasses import replace
from types import SimpleNamespace

from sidepulse.ambient_effect_consumer import active_ambient_presentation
from sidepulse.ambient_effect_dispatch import (
    AmbientEffectSurface,
    AmbientSemanticColors,
    compile_ambient_effect_dispatch,
)
from sidepulse.semantic_effect_router import (
    SemanticEffectCandidate,
    SemanticEventKind,
    route_semantic_effects,
)


def _controller(*, started_at: float = 10.0):
    selection = route_semantic_effects(
        (SemanticEffectCandidate("ask:1", SemanticEventKind.ASK),)
    )
    dispatch = compile_ambient_effect_dispatch(
        semantic_selection=selection,
        semantic_colors=replace(AmbientSemanticColors(), ask="#123456"),
    )
    return SimpleNamespace(
        _ambient_effect_dispatch=dispatch,
        _ambient_effect_dispatch_started_at=started_at,
    )


def test_consumer_adapts_one_staged_output_for_existing_render_boundaries(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "sidepulse.ambient_effect_runtime.time.monotonic",
        lambda: 10.1,
    )

    presentation = active_ambient_presentation(
        _controller(),
        AmbientEffectSurface.SCREEN_BAR,
        reduce_motion=False,
        brightness=128,
    )

    assert presentation is not None
    assert presentation.program.startswith("brightness 128\n")
    assert presentation.static_fallback_program.startswith("brightness 128\n")
    assert presentation.led_state.value == "ask"
    assert presentation.write_policy.priority.name == "URGENT"
    assert presentation.write_policy.coalesce_identity.startswith("ambient-")
    assert presentation.motion.value == "finite"
    assert presentation.dedupe_token == ("ambient", "alert", 10.0)


def test_consumer_uses_static_fallback_under_reduce_motion(monkeypatch) -> None:
    monkeypatch.setattr(
        "sidepulse.ambient_effect_runtime.time.monotonic",
        lambda: 10.1,
    )

    presentation = active_ambient_presentation(
        _controller(),
        AmbientEffectSurface.SCREEN_BAR,
        reduce_motion=True,
        brightness=255,
    )

    assert presentation is not None
    assert presentation.program == presentation.static_fallback_program
    assert presentation.motion.value == "static"
