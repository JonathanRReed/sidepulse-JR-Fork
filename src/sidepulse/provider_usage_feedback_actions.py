"""Provider feedback dispatch shared by the retained controller."""

from __future__ import annotations


def alert_new_critical_pace(controller, previous_state, state, *, legacy) -> None:
    from .provider_usage_feedback import alert_new_critical_pace as deliver

    deliver(
        controller,
        previous_state,
        state,
        log=legacy.log_status_bar,
        signal_kind=getattr(legacy.signals_module, "SIGNAL_QUOTA", None),
    )
    runtime = getattr(controller, "_sidepulse_optional_integration_runtime", None)
    if runtime is not None:
        runtime.publish_creator_output(
            legacy.AgentMode.IDLE_READY,
            signal="quota_warning",
        )


def report_reconnect_outcome(controller, state, *, legacy) -> None:
    from .provider_usage_feedback import report_reconnect_outcome as deliver

    deliver(controller, state, log=legacy.log_status_bar)


def celebrate_quota_resets(controller, events, *, legacy) -> None:
    from .provider_usage_feedback import celebrate_quota_resets as deliver

    deliver(
        controller,
        events,
        log=legacy.log_status_bar,
        signal_kind=getattr(legacy.signals_module, "SIGNAL_QUOTA", None),
    )
    if events:
        runtime = getattr(controller, "_sidepulse_optional_integration_runtime", None)
        if runtime is not None:
            runtime.publish_creator_output(
                legacy.AgentMode.IDLE_READY,
                signal="reset",
            )


def alert_connection_loss(controller, previous_state, state, *, legacy) -> None:
    from .provider_usage_feedback import alert_connection_loss as deliver

    deliver(
        controller,
        previous_state,
        state,
        log=legacy.log_status_bar,
        signal_kind=getattr(legacy.signals_module, "SIGNAL_NOTIFICATION", None),
    )
