from dataclasses import dataclass

from sidepulse.core_state import CoreDomain, CoreStateStore, stable_digest


@dataclass(frozen=True)
class Fact:
    identity: str
    value: int


def test_stable_digest_is_order_independent_for_mappings() -> None:
    assert stable_digest({"b": 2, "a": 1}) == stable_digest({"a": 1, "b": 2})


def test_store_reports_only_changed_domains_and_keeps_generation_on_noop() -> None:
    store = CoreStateStore()
    first = store.observe(
        {
            CoreDomain.AGENTS: (Fact("a", 1),),
            CoreDomain.BATTERY: {"percent": 50},
        }
    )
    noop = store.observe(
        {
            CoreDomain.AGENTS: (Fact("a", 1),),
            CoreDomain.BATTERY: {"percent": 50},
        }
    )
    changed = store.observe(
        {
            CoreDomain.AGENTS: (Fact("a", 2),),
            CoreDomain.BATTERY: {"percent": 50},
        }
    )

    assert first.changed_domains == {CoreDomain.AGENTS, CoreDomain.BATTERY}
    assert first.to_generation == 1
    assert noop.changed is False
    assert noop.from_generation == noop.to_generation == 1
    assert changed.changed_domains == {CoreDomain.AGENTS}
    assert changed.from_generation == 1
    assert changed.to_generation == 2


def test_removed_domain_and_urgent_change_are_reported() -> None:
    store = CoreStateStore()
    store.observe(
        {
            CoreDomain.AGENTS: (),
            CoreDomain.ATTENTION: "idle",
        },
        urgent_domains=frozenset({CoreDomain.ATTENTION}),
    )

    delta = store.observe(
        {CoreDomain.ATTENTION: "needs-you"},
        urgent_domains=frozenset({CoreDomain.ATTENTION}),
    )

    assert delta.changed_domains == {CoreDomain.AGENTS, CoreDomain.ATTENTION}
    assert delta.urgent is True


def test_long_private_text_is_hashed_not_retained() -> None:
    private = "PRIVATE-SENTINEL-" * 1000
    digest = stable_digest({"message": private})

    assert len(digest) == 64
    assert "PRIVATE" not in digest
