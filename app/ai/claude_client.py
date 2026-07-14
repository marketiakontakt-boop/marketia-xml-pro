"""AI client — Google Gemini (gemini-2.5-flash).

Paid-only setup od 2026-07-04. Multi-key rotacja jako fallback przy transient errors.
SQLite cache prevents re-generating already done products.
Token usage tracked for cost reporting ($0.075/1M input, $0.30/1M output).
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from pathlib import Path

import google.genai as genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MODEL = "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 8192
CONCURRENCY = 5
API_TIMEOUT = 120.0  # seconds — hangs without this block the semaphore indefinitely

# Per-request retry przy transient errors (500/503/network glitch/RPM burst).
# 3 attempts × backoff 1s/3s/8s = up to 12s wait per failing request.
# Bez tego user tracił 90%+ na wielkich batchach — 18/500 zamiast 480/500.
MAX_RETRIES = 3
RETRY_BACKOFF = (1.0, 3.0, 8.0)

# Gemini 2.5 Flash pricing (USD per token)
_PRICE_INPUT  = 0.075 / 1_000_000   # $0.075 / 1M input tokens
_PRICE_OUTPUT = 0.30  / 1_000_000   # $0.30  / 1M output tokens


def _load_keys() -> list[str]:
    """Load paid Gemini API keys from GEMINI_API_KEYS (comma-sep).

    Free-tier keys / cooldown scheduling zostały wyeliminowane 2026-07-04 —
    używamy TYLKO Google Cloud paid API. Multi-key comma-sep zachowane
    na wypadek gdyby user miał kilka projektów.
    """
    raw = os.getenv("GEMINI_API_KEYS", "").strip()
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


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


class ClaudeClient:
    """Named ClaudeClient for backward-compat; uses Gemini under the hood.

    Multi-key rotacja tylko jako fallback przy transient errors — paid key
    prawie nigdy nie fail (bez daily quota jak w free tier).
    """

    def __init__(self, on_key_rotated=None, on_key_cooling=None):
        # `on_key_cooling` param zachowany dla backward-compat — no-op od 2026-07-04.
        # `on_key_rotated` używany w sync `.call()` gdy przełączamy na kolejny klucz przy 429.
        keys = _load_keys()
        if not keys:
            raise RuntimeError(
                "Brak kluczy API Gemini.\n"
                "Ustaw GEMINI_API_KEYS=paid_key w pliku .env"
            )
        self._keys = keys
        self._clients = [genai.Client(api_key=k) for k in keys]
        self._on_key_rotated = on_key_rotated

        # Token usage counters (thread-safe)
        self._tok_lock = threading.Lock()
        self._input_tokens: int = 0
        self._output_tokens: int = 0

    # ------------------------------------------------------------------
    # Public info

    @property
    def key_count(self) -> int:
        return len(self._keys)

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
        """Single synchronous call — iterates keys on transient errors."""
        last_err: Exception | None = None
        for idx in range(len(self._clients)):
            client = self._clients[idx]
            try:
                resp = client.models.generate_content(
                    model=MODEL,
                    config=_make_config(system, json_mode=json_mode),
                    contents=content,
                )
                self._track_usage(resp)
                return _strip_fences(resp.text)
            except Exception as e:
                last_err = e
                if self._on_key_rotated and idx + 1 < len(self._clients):
                    self._on_key_rotated(idx + 2, len(self._clients))
                continue
        raise RuntimeError(f"Wszystkie klucze API Gemini nieudane: {last_err}")

    # ------------------------------------------------------------------
    # Async / parallel API

    def generate_all(
        self,
        requests: list[dict],
        progress_callback=None,
        wait_on_cooldown: bool = False,
        cancel_check=None,
        on_result=None,
    ) -> dict[str, str | None]:
        """Process requests in parallel via ThreadPoolExecutor + SYNC Gemini client.

        `wait_on_cooldown` — no-op od 2026-07-04.
        `on_result(sku, raw_text)` — SYNC callback per success (streaming save).
        `progress_callback(done, total, custom_id, error=None)`.
        Returns {custom_id: text_or_None}.

        2026-07-13 refactor: async → threads. Python 3.14 asyncio + google-genai async
        client zostawiał "dangling" thread pool który wisi cleanup po `[N/N]` success
        (main thread stuck na __psynch_cvwait). Threading + sync client = deterministic
        exit, brak asyncio.run() overhead. CONCURRENCY workers, retry loop bez zmian.
        """
        from concurrent.futures import ThreadPoolExecutor
        results: dict[str, str | None] = {}
        total = len(requests)
        done_count = 0
        state_lock = threading.Lock()

        def _process_one(req: dict) -> None:
            nonlocal done_count
            custom_id = req["custom_id"]
            if cancel_check and cancel_check():
                with state_lock:
                    results[custom_id] = None
                    done_count += 1
                return

            last_err: Exception | None = None
            for attempt in range(MAX_RETRIES):
                if cancel_check and cancel_check():
                    break
                for idx in range(len(self._clients)):
                    try:
                        resp = self._clients[idx].models.generate_content(
                            model=MODEL,
                            config=_make_config(req["system"], json_mode=req.get("json_mode", False)),
                            contents=req["content"],
                        )
                        self._track_usage(resp)
                        html = _strip_fences(resp.text)
                        with state_lock:
                            results[custom_id] = html
                            done_count += 1
                            if progress_callback:
                                progress_callback(done_count, total, custom_id)
                        if on_result:
                            try:
                                on_result(custom_id, html)
                            except Exception as save_err:
                                print(f"[on_result save failed] {custom_id}: {save_err}", flush=True)
                        return
                    except Exception as e:
                        last_err = e
                        continue
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF[attempt])

            with state_lock:
                results[custom_id] = None
                done_count += 1
                if progress_callback:
                    progress_callback(
                        done_count, total, custom_id,
                        error=f"{MAX_RETRIES} prób nieudanych: {last_err}",
                    )

        # Manual polling zamiast `f.result()` — Python 3.14 + google-genai SDK zostawia
        # thread-locking hooks które wiszą na future.result() nawet gdy future.done()==True
        # (2026-07-13, samples potwierdziły main thread stuck na __psynch_cvwait).
        # Polling loop wraca gdy tylko wszystkie futures są done, bez triggerowania
        # blocking cleanup hooks.
        ex = ThreadPoolExecutor(max_workers=CONCURRENCY)
        futures = [ex.submit(_process_one, r) for r in requests]
        while any(not f.done() for f in futures):
            time.sleep(0.1)
        ex.shutdown(wait=False)
        return results

    async def _generate_all_async(
        self,
        requests: list[dict],
        progress_callback=None,
        cancel_check=None,
        on_result=None,
    ) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        total = len(requests)
        done_count = 0
        sem = asyncio.Semaphore(CONCURRENCY)
        result_lock = asyncio.Lock()

        async def _call(client, req: dict):
            """Single API call with hard timeout."""
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
                if cancel_check and cancel_check():
                    async with result_lock:
                        results[custom_id] = None
                        done_count += 1
                    return

                last_err: Exception | None = None

                # RETRY loop: N attempts × M keys, backoff między attempts.
                # Transient errors (500/503/network/RPM burst) recovery bez utraty request.
                for attempt in range(MAX_RETRIES):
                    if cancel_check and cancel_check():
                        break
                    for idx in range(len(self._clients)):
                        try:
                            resp = await _call(self._clients[idx], req)
                            self._track_usage(resp)
                            html = _strip_fences(resp.text)
                            async with result_lock:
                                results[custom_id] = html
                                done_count += 1
                                if progress_callback:
                                    progress_callback(done_count, total, custom_id)
                            # Streaming save — SYNC call (nie to_thread!).
                            # Python 3.14 bug 2026-07-13: asyncio.to_thread + wait_for zostawia
                            # ThreadPoolExecutor który nie chce się zamknąć przy asyncio.run()
                            # cleanup (main thread wisi na __psynch_cvwait). Fix: sync call, blokuje
                            # event loop ~50ms/save = akceptowalne (5 concurrent = ~250ms max, znikome
                            # vs latencja Gemini). Jeśli save wisi (SQLite lock), całość wisi — ale
                            # to problem uniqueny per session, nie systemowy.
                            if on_result:
                                try:
                                    on_result(custom_id, html)
                                except Exception as save_err:
                                    print(f"[on_result save failed] {custom_id}: {save_err}", flush=True)
                            return
                        except Exception as e:
                            last_err = e
                            continue
                    # All keys failed this attempt → backoff before next attempt
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_BACKOFF[attempt])

                # Wszystkie retries wyczerpane
                async with result_lock:
                    results[custom_id] = None
                    done_count += 1
                    if progress_callback:
                        progress_callback(
                            done_count, total, custom_id,
                            error=f"{MAX_RETRIES} prób nieudanych: {last_err}",
                        )

        await asyncio.gather(*[process_one(r) for r in requests])
        return results
