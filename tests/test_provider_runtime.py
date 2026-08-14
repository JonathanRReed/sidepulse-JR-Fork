from __future__ import annotations

import math
import threading
import time

import pytest

from sidepulse.capacity_types import SourceKey
from sidepulse.provider_facts import (
    EventToken,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderWatermark,
    SourceFreshness,
    SourceHealth,
    WatermarkBasis,
)
from sidepulse.provider_runtime import (
    CooldownKey,
    OpaqueScopeIdentifier,
    ProviderInvocation,
    ProviderOutcomeKind,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeDiagnostic,
    RefreshTrigger,
)


def source(
    provider: str = "codex",
    adapter: str = "quota",
    instance: str = "local",
    capability: str = "remote_quota_windows",
) -> SourceKey:
    return SourceKey(provider, adapter, instance, capability)


def invocation(
    source_key: SourceKey,
    generation: int,
    *,
    deadline: float = 1.0,
    trigger: RefreshTrigger = RefreshTrigger.AUTOMATIC,
    scope: OpaqueScopeIdentifier | None = None,
) -> ProviderInvocation:
    return ProviderInvocation(source_key, generation, deadline, trigger, scope)


def batch(source_key: SourceKey, observed_at: float = 100.0) -> ProviderFactBatch:
    return ProviderFactBatch(
        source_key=source_key,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=observed_at,
        watermark=ProviderWatermark(
            source_key=source_key,
            basis=WatermarkBasis.PROVIDER_EVENT_ID,
            occurred_at_epoch=observed_at,
            event_token=EventToken(f"event:{int(observed_at)}"),
            sequence=None,
            tie_break_rank=10,
        ),
        work_facts=(),
        request_facts=(),
        diagnostics=(),
    )


def result(
    requested: ProviderInvocation,
    outcome: ProviderOutcomeKind,
    *,
    fact_batch: ProviderFactBatch | None = None,
    cooldown_key: CooldownKey | None = None,
    retry_not_before: float | None = None,
    diagnostic: ProviderRuntimeDiagnostic | None = None,
) -> ProviderResult:
    return ProviderResult(
        invocation=requested,
        outcome=outcome,
        fact_batch=fact_batch,
        quota_windows=(),
        cooldown_key=cooldown_key,
        retry_not_before=retry_not_before,
        diagnostic=diagnostic,
    )


def poll_until(runtime: ProviderRuntime, wanted: int, *, now: float) -> tuple[ProviderResult, ...]:
    published: list[ProviderResult] = []
    limit = time.monotonic() + 1.0
    while len(published) < wanted and time.monotonic() < limit:
        published.extend(runtime.poll(monotonic_now=now))
    assert len(published) == wanted
    return tuple(published)


def test_hung_source_times_out_while_sibling_publishes() -> None:
    hung_source = source()
    sibling_source = source("claude", "transcripts", "local", "transcript_usage")
    hung_entered = threading.Event()
    release_hung = threading.Event()
    sibling_returned = threading.Event()

    def hung(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        hung_entered.set()
        release_hung.wait()
        return result(call, ProviderOutcomeKind.SUCCESS, fact_batch=batch(call.source_key))

    def sibling(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        sibling_returned.set()
        return result(call, ProviderOutcomeKind.SUCCESS, fact_batch=batch(call.source_key))

    runtime = ProviderRuntime({hung_source: hung, sibling_source: sibling})
    first = invocation(hung_source, 1, deadline=0.01)
    second = invocation(sibling_source, 1, deadline=1.0)
    started_at = time.monotonic()

    assert runtime.request((first, second)) == (first, second)
    assert hung_entered.wait(1.0)
    assert sibling_returned.wait(1.0)

    outcomes = poll_until(runtime, 2, now=started_at + 0.02)
    assert {(item.invocation.source_key, item.outcome) for item in outcomes} == {
        (hung_source, ProviderOutcomeKind.TIMED_OUT),
        (sibling_source, ProviderOutcomeKind.SUCCESS),
    }
    release_hung.set()
    runtime.stop(deadline_seconds=1.0)


def test_timeout_keeps_worker_owned_and_discards_late_generation() -> None:
    source_key = source()
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        calls.append(call.generation)
        entered.set()
        release.wait()
        return result(call, ProviderOutcomeKind.SUCCESS, fact_batch=batch(source_key, 101.0))

    runtime = ProviderRuntime({source_key: adapter})
    first = invocation(source_key, 1, deadline=0.01)
    started_at = time.monotonic()
    runtime.request((first,))
    assert entered.wait(1.0)
    assert runtime.poll(monotonic_now=started_at + 0.02)[0].outcome is ProviderOutcomeKind.TIMED_OUT

    assert runtime.request((invocation(source_key, 2),)) == ()
    assert calls == [1]
    assert runtime.state_for(source_key).in_flight is True

    release.set()
    poll_until(runtime, 0, now=started_at + 0.03)
    limit = time.monotonic() + 1.0
    late: tuple[ProviderResult, ...] = ()
    while runtime.state_for(source_key).in_flight and time.monotonic() < limit:
        late += runtime.poll(monotonic_now=started_at + 0.03)
    assert late == ()
    assert runtime.last_known_fact_batch(source_key) is None
    runtime.stop(deadline_seconds=1.0)


def test_invalidation_rejects_late_result_without_replacing_owned_worker() -> None:
    source_key = source()
    entered = threading.Event()
    release = threading.Event()

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        entered.set()
        release.wait()
        return result(call, ProviderOutcomeKind.SUCCESS, fact_batch=batch(source_key))

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1),))
    assert entered.wait(1.0)

    assert runtime.invalidate(source_key, generation=2) is True
    assert runtime.state_for(source_key).generation == 2
    assert runtime.request((invocation(source_key, 3),)) == ()

    release.set()
    limit = time.monotonic() + 1.0
    published: tuple[ProviderResult, ...] = ()
    while runtime.state_for(source_key).in_flight and time.monotonic() < limit:
        published += runtime.poll(monotonic_now=time.monotonic())
    assert published == ()
    assert runtime.last_known_fact_batch(source_key) is None
    runtime.stop(deadline_seconds=1.0)


def test_empty_clears_last_known_good_while_failure_retains_it() -> None:
    source_key = source()
    outcomes = iter(
        (
            ProviderOutcomeKind.SUCCESS,
            ProviderOutcomeKind.FAILED,
            ProviderOutcomeKind.EMPTY,
        )
    )

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        outcome = next(outcomes)
        return result(
            call,
            outcome,
            fact_batch=batch(source_key) if outcome is ProviderOutcomeKind.SUCCESS else None,
            diagnostic=(
                ProviderRuntimeDiagnostic("source_unavailable") if outcome is ProviderOutcomeKind.FAILED else None
            ),
        )

    runtime = ProviderRuntime({source_key: adapter})
    for generation in (1, 2):
        runtime.request((invocation(source_key, generation),))
        poll_until(runtime, 1, now=time.monotonic())
    assert runtime.last_known_fact_batch(source_key) is not None
    assert runtime.state_for(source_key).consecutive_failures == 1

    runtime.request((invocation(source_key, 3),))
    published = poll_until(runtime, 1, now=time.monotonic())
    assert published[0].outcome is ProviderOutcomeKind.EMPTY
    assert runtime.last_known_fact_batch(source_key) is None
    assert runtime.state_for(source_key).consecutive_failures == 0
    runtime.stop(deadline_seconds=1.0)


def test_manual_refresh_queues_once_and_never_bypasses_cooldown() -> None:
    source_key = source()
    calls: list[int] = []
    cooldown_until = time.monotonic() + 60.0
    exact_scope = CooldownKey(source_key, OpaqueScopeIdentifier("scope:primary"))

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        calls.append(call.generation)
        if len(calls) == 1:
            return result(
                call,
                ProviderOutcomeKind.COOLDOWN,
                cooldown_key=exact_scope,
                retry_not_before=cooldown_until,
                diagnostic=ProviderRuntimeDiagnostic("rate_limited"),
            )
        return result(call, ProviderOutcomeKind.EMPTY)

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1, scope=exact_scope.opaque_scope),))
    poll_until(runtime, 1, now=time.monotonic())

    manual_two = invocation(
        source_key,
        2,
        trigger=RefreshTrigger.MANUAL,
        scope=exact_scope.opaque_scope,
    )
    manual_three = invocation(
        source_key,
        3,
        trigger=RefreshTrigger.MANUAL,
        scope=exact_scope.opaque_scope,
    )
    assert runtime.request((manual_two, manual_three)) == ()
    assert calls == [1]
    assert runtime.state_for(source_key).queued_after_cooldown is True

    assert runtime.poll(monotonic_now=cooldown_until - 0.001) == ()
    assert calls == [1]
    runtime.poll(monotonic_now=cooldown_until)
    poll_until(runtime, 1, now=cooldown_until)
    assert calls == [1, 2]
    assert runtime.state_for(source_key).queued_after_cooldown is False
    runtime.stop(deadline_seconds=1.0)


def test_retry_after_applies_only_to_exact_source_key() -> None:
    limited = source("codex", "quota", "local", "remote_quota_windows")
    sibling = source("codex", "transcripts", "local", "transcript_usage")
    cooldown_until = time.monotonic() + 60.0

    def limited_adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        return result(
            call,
            ProviderOutcomeKind.COOLDOWN,
            cooldown_key=CooldownKey(call.source_key, OpaqueScopeIdentifier("scope:one")),
            retry_not_before=cooldown_until,
            diagnostic=ProviderRuntimeDiagnostic("rate_limited"),
        )

    def sibling_adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        return result(call, ProviderOutcomeKind.EMPTY)

    runtime = ProviderRuntime({limited: limited_adapter, sibling: sibling_adapter})
    runtime.request((invocation(limited, 1, scope=OpaqueScopeIdentifier("scope:one")),))
    poll_until(runtime, 1, now=time.monotonic())

    sibling_call = invocation(sibling, 1, trigger=RefreshTrigger.MANUAL)
    assert runtime.request((sibling_call,)) == (sibling_call,)
    assert poll_until(runtime, 1, now=time.monotonic())[0].outcome is ProviderOutcomeKind.EMPTY
    runtime.stop(deadline_seconds=1.0)


def test_retry_after_applies_only_to_exact_opaque_scope_on_same_source() -> None:
    source_key = source()
    limited_scope = OpaqueScopeIdentifier("scope:limited")
    sibling_scope = OpaqueScopeIdentifier("scope:sibling")
    cooldown_until = time.monotonic() + 60.0

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        if call.cooldown_scope == limited_scope:
            return result(
                call,
                ProviderOutcomeKind.COOLDOWN,
                cooldown_key=CooldownKey(source_key, limited_scope),
                retry_not_before=cooldown_until,
                diagnostic=ProviderRuntimeDiagnostic("rate_limited"),
            )
        return result(call, ProviderOutcomeKind.EMPTY)

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1, scope=limited_scope),))
    poll_until(runtime, 1, now=time.monotonic())

    sibling = invocation(source_key, 2, scope=sibling_scope)
    assert runtime.request((sibling,)) == (sibling,)
    assert poll_until(runtime, 1, now=time.monotonic())[0].outcome is ProviderOutcomeKind.EMPTY
    runtime.stop(deadline_seconds=1.0)


def test_extreme_retry_not_before_is_clamped_to_bounded_horizon() -> None:
    source_key = source()
    scope = OpaqueScopeIdentifier("scope:primary")
    poll_now = time.monotonic()

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        return result(
            call,
            ProviderOutcomeKind.COOLDOWN,
            cooldown_key=CooldownKey(source_key, scope),
            retry_not_before=10_000_000_000.0,
            diagnostic=ProviderRuntimeDiagnostic("rate_limited"),
        )

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1, scope=scope),))
    poll_until(runtime, 1, now=poll_now)

    assert runtime.state_for(source_key).cooldown_until == poll_now + 3_600.0
    runtime.stop(deadline_seconds=1.0)


def test_cooldown_scope_cap_evicts_earliest_with_deterministic_tie() -> None:
    source_key = source()
    scopes = tuple(OpaqueScopeIdentifier(f"scope:{index:02d}") for index in range(9))
    now = time.monotonic()
    establishment_complete = threading.Event()
    invoked_after_cap: list[OpaqueScopeIdentifier | None] = []

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        scope = call.cooldown_scope
        assert scope is not None
        if establishment_complete.is_set():
            invoked_after_cap.append(scope)
            return result(call, ProviderOutcomeKind.EMPTY)
        deadline = now + (10.0 if scope in scopes[:2] else 20.0 + scopes.index(scope))
        return result(
            call,
            ProviderOutcomeKind.COOLDOWN,
            cooldown_key=CooldownKey(source_key, scope),
            retry_not_before=deadline,
            diagnostic=ProviderRuntimeDiagnostic("rate_limited"),
        )

    runtime = ProviderRuntime({source_key: adapter})
    for generation, scope in enumerate(scopes, start=1):
        runtime.request((invocation(source_key, generation, scope=scope),))
        poll_until(runtime, 1, now=now)
    establishment_complete.set()

    evicted = invocation(source_key, 10, scope=scopes[0])
    assert runtime.request((evicted,)) == (evicted,)
    poll_until(runtime, 1, now=now)

    still_blocked = invocation(
        source_key,
        11,
        trigger=RefreshTrigger.MANUAL,
        scope=scopes[1],
    )
    assert runtime.request((still_blocked,)) == ()
    assert invoked_after_cap == [scopes[0]]
    assert runtime.state_for(source_key).queued_after_cooldown is True
    runtime.stop(deadline_seconds=1.0)


@pytest.mark.parametrize("invalid_retry", (-1.0, float("nan"), float("inf")))
def test_invalid_retry_not_before_is_rejected(invalid_retry: float) -> None:
    requested = invocation(source(), 1, scope=OpaqueScopeIdentifier("scope:primary"))

    with pytest.raises(ValueError):
        result(
            requested,
            ProviderOutcomeKind.COOLDOWN,
            cooldown_key=CooldownKey(requested.source_key, requested.cooldown_scope),
            retry_not_before=invalid_retry,
            diagnostic=ProviderRuntimeDiagnostic("rate_limited"),
        )


@pytest.mark.parametrize(
    "outcome, diagnostic_code",
    (
        (ProviderOutcomeKind.AUTH_REQUIRED, "sign_in_required"),
        (ProviderOutcomeKind.ACCESS_DENIED, "access_denied"),
    ),
)
def test_auth_required_and_access_denied_remain_distinct(
    outcome: ProviderOutcomeKind,
    diagnostic_code: str,
) -> None:
    source_key = source()

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        return result(call, outcome, diagnostic=ProviderRuntimeDiagnostic(diagnostic_code))

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1),))
    published = poll_until(runtime, 1, now=time.monotonic())
    assert published[0].outcome is outcome
    assert published[0].diagnostic == ProviderRuntimeDiagnostic(diagnostic_code)
    runtime.stop(deadline_seconds=1.0)


def test_auth_retry_stays_blocked_until_credential_generation_changes() -> None:
    source_key = source()
    calls: list[int] = []

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        calls.append(call.generation)
        if len(calls) == 1:
            return result(
                call,
                ProviderOutcomeKind.AUTH_REQUIRED,
                diagnostic=ProviderRuntimeDiagnostic("sign_in_required"),
            )
        return result(call, ProviderOutcomeKind.EMPTY)

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1),))
    poll_until(runtime, 1, now=time.monotonic())

    assert runtime.request((invocation(source_key, 2),)) == ()
    assert calls == [1]
    runtime.set_credential_generation(source_key, 1)
    third = invocation(source_key, 3)
    assert runtime.request((third,)) == (third,)
    assert poll_until(runtime, 1, now=time.monotonic())[0].outcome is ProviderOutcomeKind.EMPTY
    assert calls == [1, 3]
    runtime.stop(deadline_seconds=1.0)


def test_stop_is_bounded_rejects_new_work_and_signals_cancellation() -> None:
    source_key = source()
    entered = threading.Event()
    cancelled = threading.Event()
    release = threading.Event()

    def adapter(call: ProviderInvocation, cancel: threading.Event) -> ProviderResult:
        entered.set()
        cancel.wait()
        cancelled.set()
        release.wait()
        return result(call, ProviderOutcomeKind.EMPTY)

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1),))
    assert entered.wait(1.0)

    before = time.monotonic()
    runtime.stop(deadline_seconds=0.01)
    elapsed = time.monotonic() - before
    assert elapsed < 0.25
    assert cancelled.wait(1.0)
    with pytest.raises(RuntimeError, match="stopped"):
        runtime.request((invocation(source_key, 2),))
    release.set()


def test_unsupported_source_invokes_nothing_and_publishes_typed_result() -> None:
    supported = source()
    unsupported = source("devin", "quota", "local", "remote_quota_windows")
    called = threading.Event()

    def adapter(call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        called.set()
        return result(call, ProviderOutcomeKind.EMPTY)

    runtime = ProviderRuntime({supported: adapter})
    requested = invocation(unsupported, 1)
    assert runtime.request((requested,)) == ()
    published = runtime.poll(monotonic_now=time.monotonic())
    assert called.is_set() is False
    assert len(published) == 1
    assert published[0].outcome is ProviderOutcomeKind.UNSUPPORTED
    assert published[0].diagnostic == ProviderRuntimeDiagnostic("unsupported_source")
    runtime.stop(deadline_seconds=1.0)


def test_unexpected_exception_becomes_content_free_product_diagnostic() -> None:
    source_key = source()
    private_values = (
        "https://provider.example/account",
        "/Users/private/transcript.jsonl",
        "Bearer secret-token",
        "account@example.com",
        '{"raw":"response body"}',
    )

    def adapter(_call: ProviderInvocation, _cancel: threading.Event) -> ProviderResult:
        raise RuntimeError(" ".join(private_values))

    runtime = ProviderRuntime({source_key: adapter})
    runtime.request((invocation(source_key, 1),))
    published = poll_until(runtime, 1, now=time.monotonic())
    assert published[0].outcome is ProviderOutcomeKind.FAILED
    assert published[0].diagnostic == ProviderRuntimeDiagnostic("adapter_failed")
    serialized = repr(published[0])
    assert all(value not in serialized for value in private_values)
    runtime.stop(deadline_seconds=1.0)


def test_invalid_runtime_inputs_fail_closed() -> None:
    source_key = source()
    with pytest.raises(ValueError):
        ProviderRuntime({}, max_sources=33)
    with pytest.raises(ValueError):
        invocation(source_key, 0)
    with pytest.raises(ValueError):
        invocation(source_key, 1, deadline=math.nan)
    with pytest.raises(ValueError):
        ProviderRuntimeDiagnostic("https://private.example")
    with pytest.raises(ValueError):
        ProviderRuntimeDiagnostic("raw_response")
    with pytest.raises(ValueError):
        OpaqueScopeIdentifier("api-key:secret")


def test_result_outcome_shape_requires_explicit_empty_and_content_free_failures() -> None:
    requested = invocation(source(), 1)

    with pytest.raises(ValueError):
        result(requested, ProviderOutcomeKind.SUCCESS)
    with pytest.raises(ValueError):
        result(
            requested,
            ProviderOutcomeKind.FAILED,
            fact_batch=batch(requested.source_key),
            diagnostic=ProviderRuntimeDiagnostic("source_unavailable"),
        )
