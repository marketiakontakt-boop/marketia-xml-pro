"""AI client — Google Gemini (gemini-2.5-flash).

Supports both sequential and parallel async processing.
SQLite cache prevents re-generating already done products.
Multiple API keys: set GEMINI_API_KEYS=key1,key2,key3 or GEMINI_API_KEY_1/2/3 in .env.
Paid keys (GEMINI_PAID_KEYS): always tried first, never enter cooldown.
KeyScheduler tracks per-key cooldown after 429/quota errors and waits automatically.
Token usage is tracked for cost reporting ($0.075/1M input, $0.30/1M output).
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
CONCURRENCY = 5
API_TIMEOUT = 120.0  # seconds — hangs without this block the semaphore indefinitely

# Gemini 2.5 Flash pricing (USD per token)
_PRICE_INPUT  = 0.075 / 1_000_000   # $0.075 / 1M input tokens
_PRICE_OUTPUT = 0.30  / 1_000_000   # $0.30  / 1M output tokens

_QUOTA_KEYWORDS = frozenset([
    "RESOURCE_EXHAUSTED", "QUOTA", "429", "RATE_LIMIT",
    "API_KEY_INVALID", "UNAUTHENTICATED", "PERMISSION_DENIED",
])


def _is_rotatable(e: Exception) -> bool:
    if isinstance(e, asyncio.TimeoutError):
        return True  # network hang — try next key
    msg = str(e).upper()
    return any(kw in msg for kw in _QUOTA_KEYWORDS)


def _load_keys() -> tuple[list[str], list[str]]:
    """Load Gemini API keys. Returns (paid_keys, free_keys).

    Paid keys (GEMINI_PAID_KEYS) are placed first, never enter cooldown.
    Free keys: GEMINI_API_KEYS (comma-sep) → GEMINI_API_KEY_1/2/… → GEMINI_API_KEY.
    """
    paid: list[str] = []
    paid_raw = os.getenv("GEMINI_PAID_KEYS", "").strip()
    if paid_raw:
        paid = [k.strip() for k in paid_raw.split(",") if k.strip()]

    multi = os.getenv("GEMINI_API_KEYS", "").strip()
    if multi:
        free = [k.strip() for k in multi.split(",") if k.strip()]
        return paid, free

    numbered: list[str] = []
    for i in range(1, 20):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            numbered.append(k)
        else:
            break
    if numbered:
        return paid, numbered

    single = os.getenv("GEMINI_API_KEY", "").strip()
    return paid, [single] if single else []


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
    Keys with index < no_cooldown_up_to are never placed in cooldown (paid keys).
    acquire() blocks (via asyncio.sleep) until at least one key is available,
    then returns the index of the key with the earliest recovery time.
    """

    def __init__(
        self,
        keys: list[str],
        cooldown_seconds: float = 62.0,
        no_cooldown_up_to: int = 0,
        on_key_cooling=None,
    ):
        self._keys = keys
        self._clients = [genai.Client(api_key=k) for k in keys]
        self._cooldown = cooldown_seconds
        self._no_cooldown_up_to = no_cooldown_up_to   # first N keys are never cooled
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
                    # Prefer paid keys (lower indices) first
                    paid_avail = [i for i in available if i < self._no_cooldown_up_to]
                    if paid_avail:
                        return paid_avail[0]
                    return min(available, key=lambda i: self._cooling_until.get(i, 0.0))
                earliest = min(self._cooling_until.values())
                wait = max(0.1, earliest - now + 0.1)
            await asyncio.sleep(wait)

    async def report_failure(self, idx: int) -> None:
        """Mark key idx as cooling. Paid keys (idx < no_cooldown_up_to) are never cooled."""
        if idx < self._no_cooldown_up_to:
            return  # paid key — no cooldown
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
        paid, free = _load_keys()
        all_keys = paid + free
        if not all_keys:
            raise RuntimeError(
                "Brak kluczy API Gemini.\n"
                "Ustaw GEMINI_API_KEYS=klucz1,klucz2 lub GEMINI_API_KEY w pliku .env"
            )
        self._keys = all_keys
        self._paid_count = len(paid)
        self._on_key_rotated = on_key_rotated

        # Token usage counters (thread-safe)
        self._tok_lock = threading.Lock()
        self._input_tokens: int = 0
        self._output_tokens: int = 0

        self._scheduler = KeyScheduler(
            self._keys,
            cooldown_seconds=62.0,
            no_cooldown_up_to=self._paid_count,
            on_key_cooling=on_key_cooling,
        )

    # ------------------------------------------------------------------
    # Public info

    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def paid_count(self) -> int:
        return self._paid_count

    def _track_usage(self, resp) -> None:
        """Extract and accumulate token counts from a Gemini response."""
        try:
            meta = resp.usage_metadata
            if meta:
                with self._tok_lock:
                    self._input_tokens  += getattr(meta, "prompt_token_count", 0) or 0
                    self._output_tokens += getattr(meta, "candidates_token_count", 0) or 0
        except Exception:
            pass

    @property
    def cost_usd(self) -> float:
        with self._tok_lock:
            return self._input_tokens * _PRICE_INPUT + self._output_tokens * _PRICE_OUTPUT

    def usage_summary(self) -> str:
        with self._tok_lock:
            return (
                f"${self.cost_usd:.4f}  "
                f"({self._input_tokens:,} in / {self._output_tokens:,} out tok)"
            )

    # ------------------------------------------------------------------
    # Sync API (single product, sequential)

    def call(self, system: str, content: str, json_mode: bool = False) -> str:
        """Single synchronous call — iterates keys on quota errors.
        Paid keys (first paid_count indices) are never skipped over indefinitely.
        """
        last_err: Exception | None = None
        # Build iteration order: paid keys first, then free keys
        order = list(range(len(self._keys)))
        for idx in order:
            client = self._scheduler.get_client(idx)
            try:
                resp = client.models.generate_content(
                    model=MODEL,
                    config=_make_config(system, json_mode=json_mode),
                    contents=content,
                )
                self._track_usage(resp)
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
        wait_on_cooldown: bool = False,
        cancel_check=None,
    ) -> dict[str, str | None]:
        """Process requests in parallel (up to CONCURRENCY simultaneous).

        wait_on_cooldown=False (default): try all keys once, return None on quota — fast mode.
        wait_on_cooldown=True: wait for cooldown recovery and retry — unattended mode.

        progress_callback(done, total, custom_id, error=None, cooling_status="")
        Returns {custom_id: text_or_None}.
        """
        return asyncio.run(
            self._generate_all_async(requests, progress_callback, wait_on_cooldown, cancel_check)
        )

    async def _generate_all_async(
        self,
        requests: list[dict],
        progress_callback=None,
        wait_on_cooldown: bool = False,
        cancel_check=None,
    ) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        total = len(requests)
        done_count = 0
        sem = asyncio.Semaphore(CONCURRENCY)
        result_lock = asyncio.Lock()

        async def _call(client, req: dict):
            """Single API call with hard timeout — prevents semaphore slots from being locked forever."""
            return await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL,
                    config=_make_config(req["system"], json_mode=req.get("json_mode", False)),
                    contents=req["content"],
                ),
                timeout=API_TIMEOUT,
            )

        async def process_one(req: dict):
            nonlocal done_count
            custom_id = req["custom_id"]
            async with sem:
                # Honour cancel before starting a new request
                if cancel_check and cancel_check():
                    async with result_lock:
                        results[custom_id] = None
                        done_count += 1
                    return

                last_err: Exception | None = None

                if wait_on_cooldown:
                    # Unattended mode: wait for available key, retry up to key_count*3 times
                    max_attempts = self._scheduler.key_count * 3
                    for _ in range(max_attempts):
                        if cancel_check and cancel_check():
                            break
                        key_idx = await self._scheduler.acquire()
                        try:
                            resp = await _call(self._scheduler.get_client(key_idx), req)
                            self._track_usage(resp)
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
                            async with result_lock:
                                results[custom_id] = None
                                done_count += 1
                                if progress_callback:
                                    progress_callback(done_count, total, custom_id, error=str(e))
                            return
                    # Exhausted max_attempts (or cancelled)
                    async with result_lock:
                        if custom_id not in results:
                            results[custom_id] = None
                            done_count += 1
                            if progress_callback:
                                err_msg = "Anulowano" if (cancel_check and cancel_check()) else f"Wyczerpano próby: {last_err}"
                                progress_callback(done_count, total, custom_id, error=err_msg)
                else:
                    # Fast mode: try each key once, give up when all exhausted
                    for idx in range(len(self._keys)):
                        try:
                            resp = await _call(self._scheduler.get_client(idx), req)
                            self._track_usage(resp)
                            html = _strip_fences(resp.text)
                            async with result_lock:
                                results[custom_id] = html
                                done_count += 1
                                if progress_callback:
                                    progress_callback(done_count, total, custom_id)
                            return
                        except Exception as e:
                            last_err = e
                            if _is_rotatable(e):
                                continue
                            async with result_lock:
                                results[custom_id] = None
                                done_count += 1
                                if progress_callback:
                                    progress_callback(done_count, total, custom_id, error=str(e))
                            return
                    # All keys exhausted (or all timed out)
                    async with result_lock:
                        if custom_id not in results:
                            results[custom_id] = None
                            done_count += 1
                            if progress_callback:
                                progress_callback(
                                    done_count, total, custom_id,
                                    error=f"Wszystkie klucze wyczerpane: {last_err}",
                                )

        await asyncio.gather(*[process_one(r) for r in requests])
        return results
