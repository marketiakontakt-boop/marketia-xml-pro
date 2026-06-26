"""Tests for ClaudeClient API key rotation logic."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.claude_client import _is_rotatable, _load_keys


_CLEAN_ENV = {k: "" for k in (
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GEMINI_PAID_KEYS",
    *[f"GEMINI_API_KEY_{i}" for i in range(1, 5)],
)}


class TestLoadKeys(unittest.TestCase):
    # _load_keys returns (paid: list, free: list)

    def test_comma_separated(self):
        env = {**_CLEAN_ENV, "GEMINI_API_KEYS": "k1,k2,k3"}
        with patch.dict(os.environ, env, clear=False):
            paid, free = _load_keys()
            assert paid == []
            assert free == ["k1", "k2", "k3"]

    def test_numbered_keys(self):
        env = {**_CLEAN_ENV, "GEMINI_API_KEY_1": "a", "GEMINI_API_KEY_2": "b"}
        with patch.dict(os.environ, env, clear=False):
            paid, free = _load_keys()
            assert free == ["a", "b"]

    def test_single_fallback(self):
        env = {**_CLEAN_ENV, "GEMINI_API_KEY": "solo"}
        with patch.dict(os.environ, env, clear=False):
            paid, free = _load_keys()
            assert free == ["solo"]

    def test_comma_has_priority_over_numbered(self):
        env = {**_CLEAN_ENV, "GEMINI_API_KEYS": "x,y", "GEMINI_API_KEY_1": "z"}
        with patch.dict(os.environ, env, clear=False):
            paid, free = _load_keys()
            assert free == ["x", "y"]

    def test_paid_keys_returned_separately(self):
        env = {**_CLEAN_ENV, "GEMINI_PAID_KEYS": "p1,p2", "GEMINI_API_KEYS": "f1,f2"}
        with patch.dict(os.environ, env, clear=False):
            paid, free = _load_keys()
            assert paid == ["p1", "p2"]
            assert free == ["f1", "f2"]


class TestIsRotatable(unittest.TestCase):
    def _e(self, msg):
        return Exception(msg)

    def test_429_detected(self):
        assert _is_rotatable(self._e("HTTP 429 Too Many Requests"))

    def test_resource_exhausted(self):
        assert _is_rotatable(self._e("RESOURCE_EXHAUSTED quota exceeded"))

    def test_unauthenticated(self):
        assert _is_rotatable(self._e("UNAUTHENTICATED: API key invalid"))

    def test_generic_error_not_rotatable(self):
        assert not _is_rotatable(self._e("JSONDecodeError invalid response"))

    def test_value_error_not_rotatable(self):
        assert not _is_rotatable(ValueError("bad argument"))


class TestKeyRotation(unittest.TestCase):
    """Smoke test: ClaudeClient initializes and exposes key_count."""

    def test_key_count(self):
        with patch("app.ai.claude_client.genai.Client"):
            with patch("app.ai.claude_client._load_keys", return_value=([], ["k1", "k2"])):
                from app.ai.claude_client import ClaudeClient
                c = ClaudeClient()
        assert c.key_count == 2

    def test_paid_key_count(self):
        with patch("app.ai.claude_client.genai.Client"):
            with patch("app.ai.claude_client._load_keys", return_value=(["p1"], ["f1", "f2"])):
                from app.ai.claude_client import ClaudeClient
                c = ClaudeClient()
        assert c.key_count == 3
        assert c.paid_count == 1


if __name__ == "__main__":
    unittest.main()
