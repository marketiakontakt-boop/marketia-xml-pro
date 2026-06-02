"""AI client — Google Gemini (gemini-2.5-flash).

Supports both sequential and parallel async processing.
SQLite cache prevents re-generating already done products.
Multiple API keys: set GEMINI_API_KEYS=key1,key2,key3 or GEMINI_API_KEY_1/2/3 in .env.
KeyScheduler tracks per-key cooldown after 429/quota errors and waits automatically.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import threading
from pathlib import Path

import google.genai as genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MODEL = "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 8192
CONCURRENCY = 3   # reduced from 5 — leaves headroom within 15 RPM per key

_QUOTA_KEYWORDS = frozenset([
    "RESOURCE_EXHAUSTED", "QUOTA", "429", "RATE_LIMIT",
    "API_KEY_INVALID", "UNAUTHENTICATED", "PERMISSION_DENIED",
])


def _is_rotatable(e: Exception) -> bool:
    msg = str(e).upper()
    return any(kw in msg for kw in _QUOTA_KEYWORDS)


def _load_keys() -> list[str]:
    """Load all configured Gemini API keys.

    Priority: GEMINI_API_KEYS (comma-separated) → GEMINI_API_KEY_1/2/… → GEMINI_API_KEY.
    """
    multi = os.getenv("GEMINI_API_KEYS", "").strip()
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]

    numbered: list[str] = []
    for i in range(1, 20):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            numbered.append(k)
        else:
            break
    if numbered:
        return numbered

    single = os.getenv("GEMINI_API_KEY", "").strip()
    if single:
        return [single]
    return []


def _make_config(system: str, json_mode: bool = False) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.7,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        response_mime_type="application/json" if json_mode else None,
    )


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that Gemini sometimes adds."""
    text = re.sub(r"^```[\w]*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()


class KeyScheduler:
    """Per-key cooldown tracker for Gemini API quota management.

    When a key returns 429/QUOTA, it's marked as cooling for cooldown_seconds.
    acquire() blocks (via asyncio.sleep) until at least one key is available,
    then returns the index of the key with the earliest recovery time.
    """

    def __init__(
        self,
        keys: list[str],
        cooldown_seconds: float = 62.0,
        on_key_cooling=None,
    ):
        self._keys = keys
        self._clients = [genai.Client(api_key=k) for k in keys]
        self._cooldown = cooldown_seconds
        self._cooling_until: dict[int, float] = {}   # idx → monotonic timestamp
        self._lock = asyncio.Lock()
        self._on_key_cooling = on_key_cooling         # callback(idx: int, seconds: float)

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def get_client(self, idx: int) -> genai.Client:
        return self._clients[idx]

    async def acquire(self) -> int:
        """Wait until a key is available; returns the index of the least-recently-used key."""
        while True:
            async with self._lock:
                now = time.monotonic()
                available = [
                    i for i in range(len(self._keys))
                    if self._cooling_until.get(i, 0.0) <= now
                ]
                if available:
                    # Pick key with lowest cooling_until (least recently troubled)
                    return min(available, key=lambda i: self._cooling_until.get(i, 0.0))
                # All keys cooling — sleep until the earliest one recovers
                earliest = min(self._cooling_until.values())
                wait = max(0.1, earliest - now + 0.1)
            await asyncio.sleep(wait)

    async def report_failure(self, idx: int) -> None:
        """Mark key idx as cooling for cooldown_seconds from now."""
        async with self._lock:
            self._cooling_until[idx] = time.monotonic() + self._cooldown
        if self._on_key_cooling:
            self._on_key_cooling(idx, self._cooldown)

    def cooling_status(self) -> str:
        """Human-readable status string e.g. 'klucz #2 cooling 45s | klucz #3 cooling 30s'."""
        now = time.monotonic()
        cooling = [
            (i, self._cooling_until[i] - now)
            for i in range(len(self._keys))
            if self._cooling_until.get(i, 0.0) > now
        ]
        if not cooling:
            return ""
        return " | ".join(f"klucz #{i + 1} cooling {int(s)}s" for i, s in cooling)


class ClaudeClient:
    """Named ClaudeClient for backward-compat; uses Gemini under the hood.

    on_key_rotated(new_index, total) — called in sync call() path on key switch.
    on_key_cooling(key_idx, seconds) — called in async path when key enters cooldown.
    """

    def __init__(self, on_key_rotated=None, on_key_cooling=None):
        self._keys = _load_keys()
        if not self._keys:
            raise RuntimeError(
                "Brak kluczy API Gemini.\n"
                "Ustaw GEMINI_API_KEYS=klucz1,klucz2 lub GEMINI_API_KEY w pliku .env"
            )
        self._on_key_rotated = on_key_rotated
        self._scheduler = KeyScheduler(
            self._keys,
            cooldown_seconds=62.0,
            on_key_cooling=on_key_cooling,
        )

    # ------------------------------------------------------------------
    # Public info

    @property
    def key_count(self) -> int:
        return len(self._keys)

    # ------------------------------------------------------------------
    # Sync API (single product, sequential)

    def call(self, system: str, content: str, json_mode: bool = False) -> str:
        """Single synchronous call — iterates keys on quota errors."""
        last_err: Exception | None = None
        for idx in range(len(self._keys)):
            client = self._scheduler.get_client(idx)
            try:
                resp = client.models.generate_content(
                    model=MODEL,
                    config=_make_config(system, json_mode=json_mode),
                    contents=content,
                )
                return _strip_fences(resp.text)
            except Exception as e:
                if _is_rotatable(e):
                    last_err = e
                    if self._on_key_rotated and idx + 1 < len(self._keys):
                        self._on_key_rotated(idx + 2, len(self._keys))
                    continue
                raise
        raise RuntimeError(f"Wszystkie klucze API Gemini wyczerpane: {last_err}")

    # ------------------------------------------------------------------
    # Async / parallel API

    def generate_all(
        self,
        requests: list[dict],
        progress_callback=None,
    ) -> dict[str, str | None]:
        """Process requests in parallel (up to CONCURRENCY simultaneous).

        Uses KeyScheduler: on 429, key enters cooldown and request retries
        automatically with the next available key. Never drops a request
        due to rate limits — waits until a key recovers.

        progress_callback(done, total, custom_id, error=None, cooling_status="")
        Returns {custom_id: text_or_None}.
        """
        return asyncio.run(
            self._generate_all_async(requests, progress_callback)
        )

    async def _generate_all_async(
        self,
        requests: list[dict],
        progress_callback=None,
    ) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        total = len(requests)
        done_count = 0
        sem = asyncio.Semaphore(CONCURRENCY)
        result_lock = asyncio.Lock()
        max_attempts = self._scheduler.key_count * 3

        async def process_one(req: dict):
            nonlocal done_count
            custom_id = req["custom_id"]
            async with sem:
                last_err: Exception | None = None
                for _ in range(max_attempts):
                    key_idx = await self._scheduler.acquire()
                    try:
                        resp = await self._scheduler.get_client(key_idx).aio.models.generate_content(
                            model=MODEL,
                            config=_make_config(req["system"], json_mode=req.get("json_mode", False)),
                            contents=req["content"],
                        )
                        html = _strip_fences(resp.text)
                        async with result_lock:
                            results[custom_id] = html
                            done_count += 1
                            if progress_callback:
                                progress_callback(
                                    done_count, total, custom_id,
                                    cooling_status=self._scheduler.cooling_status(),
                                )
                        return
                    except Exception as e:
                        last_err = e
                        if _is_rotatable(e):
                            await self._scheduler.report_failure(key_idx)
                            continue
                        # Non-recoverable error — fail immediately
                        async with result_lock:
                            results[custom_id] = None
                            done_count += 1
                            if progress_callback:
                                progress_callback(done_count, total, custom_id, error=str(e))
                        return

                # Exhausted max_attempts (all keys kept cooling)
                async with result_lock:
                    if custom_id not in results:
                        results[custom_id] = None
                        done_count += 1
                        if progress_callback:
                            progress_callback(
                                done_count, total, custom_id,
                                error=f"Wyczerpano {max_attempts} prób: {last_err}",
                            )

        await asyncio.gather(*[process_one(r) for r in requests])
        return results
