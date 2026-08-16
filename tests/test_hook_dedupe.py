from __future__ import annotations

import multiprocessing
from pathlib import Path

from sidepulse.hook_dedupe import HookEventDeduplicator


def test_same_token_is_accepted_once_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "grok.dedupe.json"
    first = HookEventDeduplicator(path, max_tokens=4)
    second = HookEventDeduplicator(path, max_tokens=4)

    assert first.accept("event-a") is True
    assert second.accept("event-a") is False
    assert second.accept("event-b") is True


def test_history_is_bounded_and_old_tokens_are_evicted(tmp_path: Path) -> None:
    dedupe = HookEventDeduplicator(tmp_path / "state.json", max_tokens=3)

    for token in ("a", "b", "c", "d"):
        assert dedupe.accept(token) is True

    assert dedupe.tokens() == ("b", "c", "d")
    assert dedupe.accept("a") is True


def test_invalid_token_is_rejected_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    dedupe = HookEventDeduplicator(path)

    assert dedupe.accept("") is False
    assert dedupe.accept("x" * 1025) is False
    assert not path.exists()


def _accept_once(path: str, queue) -> None:
    queue.put(HookEventDeduplicator(Path(path)).accept("race-token"))


def test_concurrent_processes_accept_only_one_copy(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(target=_accept_once, args=(str(path), queue))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0

    results = [queue.get(timeout=2) for _ in processes]
    assert results.count(True) == 1
    assert results.count(False) == 3


def test_run_once_records_only_after_callback_succeeds(tmp_path: Path) -> None:
    dedupe = HookEventDeduplicator(tmp_path / "state.json")
    calls: list[str] = []

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("write failed")

    try:
        dedupe.run_once("event", fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("callback failure must propagate")

    assert dedupe.tokens() == ()
    assert dedupe.run_once("event", lambda: calls.append("ok")) is True
    assert dedupe.run_once("event", lambda: calls.append("duplicate")) is False
    assert calls == ["fail", "ok"]
