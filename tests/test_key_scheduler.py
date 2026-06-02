"""Tests for KeyScheduler cooldown logic."""
import asyncio
import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock


def _make_scheduler(n_keys=3, cooldown=62.0, on_key_cooling=None):
    with patch("app.ai.claude_client.genai.Client") as MockClient:
        MockClient.return_value = MagicMock()
        from app.ai.claude_client import KeyScheduler
        return KeyScheduler(
            [f"key{i}" for i in range(n_keys)],
            cooldown_seconds=cooldown,
            on_key_cooling=on_key_cooling,
        )


def test_acquire_returns_available_key():
    sched = _make_scheduler(n_keys=3)
    idx = asyncio.run(sched.acquire())
    assert 0 <= idx < 3


def test_acquire_prefers_least_recently_used():
    """Key with lowest cooling_until (0.0 = never used) is picked first."""
    sched = _make_scheduler(n_keys=3)
    # Mark key 0 as used recently (cooling_until = small positive value)
    sched._cooling_until[0] = time.monotonic() - 1  # already expired, but > 0
    idx = asyncio.run(sched.acquire())
    # key 1 and 2 have cooling_until=0, so one of them should be picked
    assert idx in (1, 2)


def test_report_failure_sets_cooldown():
    sched = _make_scheduler(n_keys=2, cooldown=62.0)
    before = time.monotonic()
    asyncio.run(sched.report_failure(0))
    assert sched._cooling_until[0] >= before + 61.9


def test_report_failure_twice_extends_from_second_call():
    """Second report_failure resets cooldown from call time, not from original."""
    sched = _make_scheduler(n_keys=1, cooldown=62.0)
    asyncio.run(sched.report_failure(0))
    time.sleep(0.05)
    t_before_second = time.monotonic()
    asyncio.run(sched.report_failure(0))
    assert sched._cooling_until[0] >= t_before_second + 61.9


def test_acquire_waits_when_all_cooling():
    """All keys cooling → acquire sleeps until earliest key recovers."""
    sched = _make_scheduler(n_keys=2, cooldown=62.0)
    # Simulate both keys cooling for 0.15s
    soon = time.monotonic() + 0.15
    sched._cooling_until[0] = soon
    sched._cooling_until[1] = soon + 1.0

    t0 = time.monotonic()
    idx = asyncio.run(sched.acquire())
    elapsed = time.monotonic() - t0

    assert idx == 0           # earliest-recovering key
    assert elapsed >= 0.1     # actually waited


def test_on_key_cooling_callback_fired():
    fired = []
    sched = _make_scheduler(n_keys=2, on_key_cooling=lambda idx, secs: fired.append((idx, secs)))
    asyncio.run(sched.report_failure(1))
    assert len(fired) == 1
    assert fired[0][0] == 1
    assert fired[0][1] == pytest.approx(62.0, abs=0.1)


def test_cooling_status_empty_when_none_cooling():
    sched = _make_scheduler(n_keys=2)
    assert sched.cooling_status() == ""


def test_cooling_status_shows_cooling_keys():
    sched = _make_scheduler(n_keys=3, cooldown=62.0)
    asyncio.run(sched.report_failure(1))
    status = sched.cooling_status()
    assert "klucz #2" in status
    assert "cooling" in status


def test_get_client_returns_correct_client():
    sched = _make_scheduler(n_keys=3)
    for i in range(3):
        client = sched.get_client(i)
        assert client is not None
