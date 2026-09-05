from types import SimpleNamespace

from sidepulse.provider_reset_events import ResetChannel, ResetChannelOutcome
from sidepulse.provider_usage_feedback import deliver_reset_channels
from sidepulse.provider_usage_qol import ResetEvent


def test_quiet_visuals_do_not_block_notification_fallback() -> None:
    delivered = []
    controller = SimpleNamespace(
        quiet_active=lambda: True,
        settings=SimpleNamespace(virtual_status_device_enabled=True),
        has_connected_physical_device=lambda: True,
        _notification_client_for_use=lambda: SimpleNamespace(
            deliver=lambda *args: delivered.append(args)
        ),
    )
    event = ResetEvent("codex:weekly:event", "codex", "weekly", "Weekly reset", 1000, "acct", 900)

    receipts = deliver_reset_channels(
        controller,
        event,
        tuple(ResetChannel),
        now=1001,
        monotonic_now=50,
        log=lambda _message: None,
        sound_player=lambda: True,
    )

    by_channel = {receipt.channel: receipt for receipt in receipts}
    assert by_channel[ResetChannel.OVERLAY].outcome is ResetChannelOutcome.SUPPRESSED
    assert by_channel[ResetChannel.HARDWARE].outcome is ResetChannelOutcome.SUPPRESSED
    assert by_channel[ResetChannel.SOUND].outcome is ResetChannelOutcome.SUPPRESSED
    assert by_channel[ResetChannel.NOTIFICATION].outcome is ResetChannelOutcome.DELIVERED
    assert len(delivered) == 1


def test_common_effect_only_receipts_surfaces_that_exist() -> None:
    controller = SimpleNamespace(
        quiet_active=lambda: False,
        settings=SimpleNamespace(virtual_status_device_enabled=False),
        has_connected_physical_device=lambda: True,
        schedule_event_refresh=lambda: None,
    )
    event = ResetEvent("codex:weekly:event", "codex", "weekly", "Weekly reset", 1000, "acct", 900)

    receipts = deliver_reset_channels(
        controller,
        event,
        (ResetChannel.OVERLAY, ResetChannel.HARDWARE),
        now=1001,
        monotonic_now=50,
        log=lambda _message: None,
    )

    assert [(item.channel, item.outcome, item.reason) for item in receipts] == [
        (ResetChannel.OVERLAY, ResetChannelOutcome.SUPPRESSED, "surface_unavailable"),
        (ResetChannel.HARDWARE, ResetChannelOutcome.DELIVERED, "effect_scheduled"),
    ]
