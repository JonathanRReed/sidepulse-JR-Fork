"""projection_for_device with worker rows present.

The regression this pins: `pin` was unbound in the method's tail for one
release, so ANY snapshot carrying a sub-agent worker row raised NameError
inside the hardware write worker -- the physical strip froze exactly when
a session fanned out workers, while the Screen Bar kept rendering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from test_sidepulse import isolate_controller

from sidepulse.attention import AttentionProjection, LifecycleMode, ProjectedAgentRow
from sidepulse.status_bar_legacy import StatusBarDevice

_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _device(device_id: str = "sidepulse:pro:serial:test") -> StatusBarDevice:
    from pathlib import Path

    return StatusBarDevice(
        device_id=device_id,
        name="Test Pro",
        root=Path("/tmp/nonexistent-test-device"),
        target=Path("/tmp/nonexistent-test-device/LEDS.LED"),
        connected=True,
        display="agent",
    )


def _row(agent_id: str, provider: str, *, worker: bool = False) -> ProjectedAgentRow:
    return ProjectedAgentRow(
        agent_id=agent_id,
        provider=provider,
        display_name=agent_id,
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable=False,
        is_subagent=worker,
        updated_at=_NOW,
        source_status=None,
    )


def test_worker_rows_survive_projection_for_an_unpinned_device(request) -> None:
    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller
    projection = AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=(_row("claude:main", "claude"),),
        worker_rows=(_row("claude:worker", "claude", worker=True),),
        transient_signals=(),
        dominant_provider="claude",
        click_target_agent_id=None,
    )
    device = _device()
    projected = controller.projection_for_device(projection, device)
    assert projected.worker_rows == projection.worker_rows
    assert projected.visible_rows == projection.visible_rows


def test_pinned_device_filters_worker_rows_to_the_pin(request) -> None:
    case = SimpleNamespace(
        addCleanup=lambda fn, *a, **k: request.addfinalizer(lambda: fn(*a, **k)),
    )
    isolate_controller(case)
    controller = case.controller
    device = _device()
    controller.settings = controller.settings.with_remembered_device(
        device_id=device.device_id,
        name=device.name,
        path=str(device.root),
    ).with_device_provider_pin(device.device_id, "codex")
    projection = AttentionProjection(
        lifecycle_mode=LifecycleMode.ACTIVE,
        actionable_attention=(),
        visible_rows=(_row("codex:main", "codex"), _row("claude:main", "claude")),
        worker_rows=(
            _row("codex:worker", "codex", worker=True),
            _row("claude:worker", "claude", worker=True),
        ),
        transient_signals=(),
        dominant_provider="codex",
        click_target_agent_id=None,
    )
    projected = controller.projection_for_device(projection, device)
    assert all(row.provider == "codex" for row in projected.worker_rows)
