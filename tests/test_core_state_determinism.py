from __future__ import annotations

from sidepulse.core_state import CoreDomain, CoreStateStore, stable_digest


def test_stable_digest_is_independent_of_mapping_and_set_order() -> None:
    first = {
        "mapping": {"z": 3, "a": 1, "m": 2},
        "set": {"codex", "claude", "cursor"},
    }
    second = {
        "set": {"cursor", "codex", "claude"},
        "mapping": {"m": 2, "a": 1, "z": 3},
    }

    assert stable_digest(first) == stable_digest(second)


def test_equivalent_reordered_observation_does_not_advance_generation() -> None:
    store = CoreStateStore()
    first = store.observe(
        {
            CoreDomain.AGENTS: {
                "providers": {"codex", "claude"},
                "counts": {"active": 2, "waiting": 1},
            }
        }
    )
    second = store.observe(
        {
            CoreDomain.AGENTS: {
                "counts": {"waiting": 1, "active": 2},
                "providers": {"claude", "codex"},
            }
        }
    )

    assert first.changed_domains == frozenset({CoreDomain.AGENTS})
    assert second.changed_domains == frozenset()
    assert second.from_generation == first.to_generation
    assert second.to_generation == first.to_generation
