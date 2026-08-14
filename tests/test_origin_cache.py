"""Origin is derived once per session, not once per hook.

Measured in the field (46 sessions / 1168 events, pinsonlawrimore's
hook-latency study): origin detection was ~24% of every hook's wall
time, forking /bin/ps once per process ancestor -- to recompute an
answer that cannot change while the session lives. No session in that
sample ever produced a second origin.
"""

from __future__ import annotations

from unittest.mock import patch

from sidepulse.origin import clear_origin_cache, detect_agent_origin


def setup_function() -> None:
    clear_origin_cache()


def test_one_ancestry_walk_per_session() -> None:
    with patch("sidepulse.origin.process_ancestry", return_value=()) as walk:
        for _ in range(50):
            detect_agent_origin("claude", env={}, session_id="s1")
        assert walk.call_count == 1


def test_each_session_is_derived_independently() -> None:
    with patch("sidepulse.origin.process_ancestry", return_value=()) as walk:
        for session in ("s1", "s2", "s3"):
            for _ in range(20):
                detect_agent_origin("claude", env={}, session_id=session)
        assert walk.call_count == 3


def test_without_a_session_nothing_is_cached() -> None:
    with patch("sidepulse.origin.process_ancestry", return_value=()) as walk:
        detect_agent_origin("claude", env={})
        detect_agent_origin("claude", env={})
        assert walk.call_count == 2


def test_the_cache_is_bounded() -> None:
    from sidepulse.origin import _ORIGIN_CACHE_MAX_SESSIONS, _origin_cache

    with patch("sidepulse.origin.process_ancestry", return_value=()):
        for index in range(_ORIGIN_CACHE_MAX_SESSIONS + 40):
            detect_agent_origin("claude", env={}, session_id=f"s{index}")
    assert len(_origin_cache) <= _ORIGIN_CACHE_MAX_SESSIONS


def test_clearing_one_session_forces_a_fresh_walk() -> None:
    with patch("sidepulse.origin.process_ancestry", return_value=()) as walk:
        detect_agent_origin("claude", env={}, session_id="s1")
        clear_origin_cache("s1")
        detect_agent_origin("claude", env={}, session_id="s1")
        assert walk.call_count == 2
