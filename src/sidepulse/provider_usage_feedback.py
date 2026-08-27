"""Visible outcomes for provider connect/reconnect actions.

Extracted from the status-bar facade (which has a size ratchet for
exactly this reason). Three jobs, all AppKit-free enough to test:

  * `show_provider_usage_feedback` -- the guaranteed-visible sink every
    action message lands in. set_settings_message's only sink is empty
    until Settings has been opened once; the Usage Center banner is only
    rendered if the message is set BEFORE show(). This function owns
    that ordering so no call site can get it wrong again.
  * `alert_connection_loss` -- one attention cue when a provider that
    WAS healthy stops being healthy, through the same interrupt gates
    as every other courtesy signal.
  * `connect_claude_usage` -- the Claude connect flow: a Keychain read
    that may prompt (user-initiated only), then an honest repair that
    checks expiry and the signed-out shape before claiming success.
"""

from __future__ import annotations

import time


def show_provider_usage_feedback(controller, message: str) -> None:
    text = str(message or "")
    if not text:
        return
    try:
        controller.set_settings_message(text)
    except Exception:
        pass
    try:
        log = getattr(controller, "_provider_usage_log", None)
        if callable(log):
            log(f"provider action: {text}")
    except Exception:
        pass
    try:
        from .provider_usage_window import ProviderUsageWindowController

        window = getattr(controller, "_sidepulse_provider_usage_window", None)
        if window is None:
            window = ProviderUsageWindowController(action_target=controller)
            controller._sidepulse_provider_usage_window = window
        # Order is load-bearing: show() renders whatever message is
        # already set; a message set after show() is dropped.
        window.show_message(text)
        window.show(controller.provider_usage_state)
    except Exception:
        pass


def alert_connection_loss(
    controller,
    previous_state,
    state,
    *,
    log,
    signal_kind,
) -> None:
    """Edge-triggered attention when a provider drops. The menu row
    already carries the fix (the action label); this makes sure the
    drop is NOTICED without opening the menu."""
    from .provider_reconnect import connection_loss_transitions

    try:
        from .provider_reconnect import _LOST_STALE_REASONS

        healthy_now = {
            snapshot.provider_id
            for snapshot in state.snapshots
            if getattr(snapshot.state, "value", None) in ("ready", "stale")
            # A stale-served live failure is NOT recovered: pruning its
            # seen key here would re-announce the same loss every apply.
            and getattr(snapshot, "reason_code", None) not in _LOST_STALE_REASONS
        }
        # A recovery re-arms the announcement: losing the same provider
        # next week deserves its own cue.
        seen = tuple(
            key
            for key in getattr(controller, "_sidepulse_seen_connection_losses", ())
            if key.split(":", 1)[0] not in healthy_now
        )
        events = connection_loss_transitions(
            previous_state.snapshots,
            state.snapshots,
            seen_keys=frozenset(seen),
        )
        if not events:
            controller._sidepulse_seen_connection_losses = seen
            return
        controller._sidepulse_seen_connection_losses = (
            *seen,
            *(key for key, _p, _s in events),
        )[-256:]
        for _key, provider_id, failed_state in events:
            log(f"connection lost: {provider_id} -> {failed_state}")
        may_interrupt = getattr(controller, "may_interrupt", None)
        if callable(may_interrupt) and signal_kind is not None:
            if not may_interrupt(signal_kind):
                return
        quiet = getattr(controller, "quiet_active", None)
        if callable(quiet) and quiet():
            return
        # connection_notice_until drives the LED_DISPLAY_CONNECTION
        # claim. The first draft set quota_blink_until, whose only
        # renderer sits behind the permanently-False quota_alerts flag
        # -- a cue that could never show (audit, 2026-08-26).
        controller.connection_notice_until = max(
            float(getattr(controller, "connection_notice_until", 0.0) or 0.0),
            time.monotonic() + 4.0,
        )
        try:
            controller.schedule_event_refresh()
        except Exception:
            pass
    except Exception as exc:
        try:
            log(f"connection loss alert: {exc}")
        except Exception:
            pass


def alert_new_critical_pace(
    controller, previous_state, state, *, log, signal_kind
) -> None:
    """One content-free notification per window when a lane JUST became
    projected to run dry before its reset. Extracted verbatim from the
    facade for ratchet headroom (2026-08-26); gated by the (now real)
    quota_alerts_enabled switch plus the courtesy budget."""
    from .provider_usage_platform import provider_descriptor
    from .usage_pace import critical_pace_transitions

    try:
        seen = tuple(getattr(controller, "_sidepulse_seen_pace_alerts", ()))
        alerts = critical_pace_transitions(
            previous_state.snapshots,
            state.snapshots,
            now=time.time(),
            seen_keys=frozenset(seen),
        )
        if not alerts:
            return
        controller._sidepulse_seen_pace_alerts = (
            *seen,
            *(key for key, _p, _l in alerts),
        )[-256:]
        if not getattr(controller.settings, "quota_alerts_enabled", False):
            return
        may_interrupt = getattr(controller, "may_interrupt", None)
        if callable(may_interrupt) and signal_kind is not None:
            if not may_interrupt(signal_kind):
                return
        quiet = getattr(controller, "quiet_active", None)
        if callable(quiet) and quiet():
            return
        controller.quota_blink_until = max(
            float(getattr(controller, "quota_blink_until", 0.0) or 0.0),
            time.monotonic() + 4.0,
        )
        client = controller._notification_client_for_use()
        for key, provider_id, _label in alerts[:3]:
            label = provider_descriptor(provider_id).label
            safe = "".join(
                ch for ch in label if ch.isalnum() or ch == " "
            ).strip() or provider_id
            article = "An" if safe[:1].upper() in "AEIOU" else "A"
            client.deliver(
                "quota.pace." + key.replace(":", "-"),
                "SidePulse",
                f"{article} {safe} limit is running low",
                {},
            )
    except Exception as exc:
        try:
            log(f"pace alert: {exc}")
        except Exception:
            pass


def celebrate_quota_resets(controller, events, *, log, signal_kind) -> None:
    """Confetti + one notification when a rate limit refills.

    The audit found every reset in the product's history passed in
    silence: the only renderer of the blink timer sat behind a flag
    hard-wired False. This path does not touch that flag -- a refilled
    meter is good news, gated only by the courtesy budget (a Focus or
    Quiet Hour refuses it like any other courtesy moment)."""
    try:
        may_interrupt = getattr(controller, "may_interrupt", None)
        if callable(may_interrupt) and signal_kind is not None:
            if not may_interrupt(signal_kind):
                return
        quiet = getattr(controller, "quiet_active", None)
        if callable(quiet) and quiet():
            return
        from .celebrations import RESET_CELEBRATION_SECONDS

        try:
            controller.quota_reset_celebration_provider = events[0].provider_id
        except Exception:
            controller.quota_reset_celebration_provider = None
        controller.quota_reset_celebration_until = max(
            float(
                getattr(controller, "quota_reset_celebration_until", 0.0) or 0.0
            ),
            time.monotonic() + RESET_CELEBRATION_SECONDS,
        )
        try:
            controller.schedule_event_refresh()
        except Exception:
            pass
        try:
            from .provider_usage_platform import provider_descriptor

            client = controller._notification_client_for_use()
            for event in tuple(events)[:2]:
                label = provider_descriptor(event.provider_id).label
                client.deliver(
                    "quota.reset." + event.event_id.replace(":", "-"),
                    "SidePulse",
                    f"🎉 {label} {event.label} — fresh window",
                    {},
                )
        except Exception:
            pass
    except Exception as exc:
        try:
            log(f"reset celebration: {exc}")
        except Exception:
            pass


def report_reconnect_outcome(controller, state, *, log) -> None:
    """One-shot truth about what a reconnect click actually achieved.

    The click's message could only describe the ATTEMPT; the outcome
    arrives with the forced refresh a moment later. Clicked live
    (grok, three times, 2026-08-26): every click said "signed in --
    refreshing now" while the server kept rejecting the token and the
    card never changed. This closes the loop."""
    try:
        watch = getattr(controller, "_sidepulse_reconnect_watch", None)
        if not watch:
            return
        provider_id, clicked_at = watch
        snapshot = next(
            (
                item
                for item in getattr(state, "snapshots", ())
                if item.provider_id == provider_id
            ),
            None,
        )
        if snapshot is None or float(
            getattr(snapshot, "observed_at", 0.0) or 0.0
        ) < float(clicked_at):
            return  # the forced refresh has not landed yet; keep waiting
        controller._sidepulse_reconnect_watch = None
        from .provider_usage_platform import provider_descriptor

        label = provider_descriptor(provider_id).label
        value = getattr(snapshot.state, "value", str(snapshot.state))
        if value == "ready":
            message = f"{label} reconnected — live numbers are in."
        elif getattr(snapshot, "reason_code", None) == "authentication_required":
            message = (
                f"{label} is still being rejected by the server — its own "
                f"CLI has to mint a fresh sign-in (for Grok: `grok login`). "
                "SidePulse retries the moment that happens."
            )
        else:
            action = getattr(snapshot, "action_label", None)
            message = f"{label} is still {value.replace('_', ' ')}" + (
                f" — {action}." if action else "."
            )
        log(f"reconnect outcome: {provider_id} -> {value}")
        window = getattr(controller, "_sidepulse_provider_usage_window", None)
        if window is not None:
            try:
                if window.window.isVisible():
                    window.show_message(message)
            except Exception:
                pass
        try:
            controller.set_settings_message(message)
        except Exception:
            pass
    except Exception as exc:
        try:
            log(f"reconnect outcome: {exc}")
        except Exception:
            pass


def connect_claude_usage(controller, *, log) -> None:
    """The Claude connect flow behind the Connect/Reconnect click.

    The original sin here was optimism: whatever token the Keychain
    held was re-stored -- expired, empty-but-refreshable, it did not
    matter -- and the message said "Claude usage connected." while the
    usage endpoint kept rejecting it."""
    message = "Claude usage connected."
    try:
        from .credentials import (
            CLAUDE_CODE_KEYCHAIN,
            CredentialOutcome,
            KeychainConsentLedger,
            read_keychain_secret,
        )
        from .provider_credential_store import ProviderCredentialStore
        from .provider_reconnect import repair_claude_credential
        from .providers import default_state_dir

        result = read_keychain_secret(
            CLAUDE_CODE_KEYCHAIN,
            allow_prompt=True,
            ledger=KeychainConsentLedger(
                default_state_dir() / "keychain-consent.json"
            ),
        )
        if not result.ok:
            message = {
                CredentialOutcome.DENIED: (
                    "Keychain access was declined — click Connect again "
                    "and choose Allow."
                ),
                CredentialOutcome.COOLING_DOWN: (
                    "Keychain access was declined recently — try again "
                    "in a few minutes."
                ),
            }.get(
                result.outcome,
                "Claude Code's sign-in was not found in the Keychain.",
            )
        else:
            repair = repair_claude_credential(
                ProviderCredentialStore(),
                now=time.time(),
                keychain_payload_reader=lambda: result.secret,
            )
            message = repair.message
            if repair.changed or repair.outcome.value == "already_healthy":
                controller._request_provider_usage(
                    force=True, providers=("claude",)
                )
    except Exception as exc:
        message = f"Could not read the Claude Code sign-in: {exc}"
    try:
        log(f"claude usage connect: {message}")
    except Exception:
        pass
    show_provider_usage_feedback(controller, message)


__all__ = [
    "alert_connection_loss",
    "connect_claude_usage",
    "show_provider_usage_feedback",
]
