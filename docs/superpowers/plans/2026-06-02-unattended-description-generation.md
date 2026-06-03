# Unattended Description Generation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragile linear key rotation with a `KeyScheduler` that tracks per-key cooldowns and waits transparently — enabling unattended generation that never drops products on rate-limit.

**Architecture:** New `KeyScheduler` class manages a pool of `genai.Client` objects (one per API key) with per-key cooldown tracking. `ClaudeClient._generate_all_async` acquires a key via `scheduler.acquire()` (async wait) instead of rotating linearly. A new GUI button "Generuj automatycznie" disables the Stop button and runs to completion.

**Tech Stack:** Python asyncio, google-genai, customtkinter, SQLite (existing)

---

## File Map

| File | Change |
|------|--------|
| `app/ai/claude_client.py` | Add `KeyScheduler`; refactor `_generate_all_async`; simplify `call()`; CONCURRENCY 5→3 |
| `app/transformer/description_generator.py` | Replace `on_key_rotated` with `on_key_cooling` callback |
| `app/gui/main_window.py` | Add `btn_ai_unattended`, `_run_ai_unattended()`, `_ai_unattended_worker()`; update `ai_done` handler |
| `tests/test_key_scheduler.py` | New: unit tests for `KeyScheduler` |

---

## Task 1: KeyScheduler — tests first

**Files:**
- Create: `tests/test_key_scheduler.py`

- [ ] **Step 1.1: Write failing tests for KeyScheduler**

Create `tests/test_key_scheduler.py`:

```python
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
```

- [ ] **Step 1.2: Run tests to verify they fail (KeyScheduler not yet defined)**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro
./venv/bin/python -m pytest tests/test_key_scheduler.py -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError: module has no attribute 'KeyScheduler'`

---

## Task 2: Implement KeyScheduler and refactor ClaudeClient

**Files:**
- Modify: `app/ai/claude_client.py` (full rewrite of class)

- [ ] **Step 2.1: Replace claude_client.py content**

Replace the entire file with:

```python
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
```

- [ ] **Step 2.2: Run KeyScheduler tests**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro
./venv/bin/python -m pytest tests/test_key_scheduler.py -v
```

Expected: all 9 tests PASS

- [ ] **Step 2.3: Run existing key_rotator tests (backward compat)**

```bash
./venv/bin/python -m pytest tests/test_key_rotator.py -v
```

Expected: all tests PASS (note: `current_key_index` property removed — if tests fail on it, remove those assertions)

- [ ] **Step 2.4: Run full suite**

```bash
./venv/bin/python -m pytest --tb=short -q
```

Expected: all existing tests PASS

- [ ] **Step 2.5: Commit**

```bash
git add app/ai/claude_client.py tests/test_key_scheduler.py
git commit -m "feat: add KeyScheduler with per-key cooldown, replace linear rotation"
```

---

## Task 3: Update description_generator.py

**Files:**
- Modify: `app/transformer/description_generator.py:77-80`

The `on_progress` callback in `description_generator.py` currently has signature `(done, total, sku, error=None)`. The new `process_one` passes an extra `cooling_status=""` kwarg. We need to accept it.

- [ ] **Step 3.1: Update `generate_descriptions` to use `on_key_cooling` and accept new callback kwargs**

Replace lines 77–108 in `app/transformer/description_generator.py`:

```python
    def on_key_cooling(key_idx: int, seconds: float):
        log(f"⏳ Klucz #{key_idx + 1} wchodzi w cooldown {int(seconds)}s — czekam na wolny klucz…")

    client = ClaudeClient(on_key_cooling=on_key_cooling)

    requests = []
    for p in pending:
        brand_key = p.brand or "unknown"
        brand_info = brand_data.get(brand_key, {"name": brand_key.upper(), "tagline": ""})
        user_msg = build_description_prompt_v2(p, brand_info, brand_key)
        requests.append({
            "custom_id": p.sku,
            "system": SYSTEM_PROMPT_JSON,
            "content": user_msg,
            "json_mode": True,
        })

    sku_map = {p.sku: p for p in pending}
    total = len(requests)
    generated = 0

    def on_progress(done: int, total_: int, sku: str, error: str | None = None, cooling_status: str = ""):
        nonlocal generated
        suffix = f" | {cooling_status}" if cooling_status else ""
        if error:
            log(f"[{done}/{total_}] BŁĄD {sku}: {error}{suffix}")
        else:
            generated += 1
            log(f"[{done}/{total_}] ✓ {sku}{suffix}")
```

(Remove the old `on_key_rotated` function and old `ClaudeClient(on_key_rotated=on_key_rotated)` line.)

- [ ] **Step 3.2: Run full suite**

```bash
./venv/bin/python -m pytest --tb=short -q
```

Expected: all tests PASS

- [ ] **Step 3.3: Commit**

```bash
git add app/transformer/description_generator.py
git commit -m "feat: description_generator uses on_key_cooling callback + cooling_status in progress"
```

---

## Task 4: GUI — "Generuj automatycznie" button

**Files:**
- Modify: `app/gui/main_window.py`

Four changes needed:
1. Add `btn_ai_unattended` button after `btn_ai` (line ~376)
2. Add `_run_ai_unattended()` method
3. Add `_ai_unattended_worker()` method
4. Re-enable `btn_ai_unattended` in `ai_done` handler (line ~1281)

- [ ] **Step 4.1: Add button in sidebar** (after line 376 `self.btn_ai = ...`)

Find this block:
```python
        self.btn_ai = _sb("  Generuj opisy (AI)", self._run_ai,
            "Gemini AI generuje opisy HTML. Cache SQLite — idempotentne.",
            fg_color="#1a6f3a", hover_color="#145c2f")
        self.btn_thumb = _sb("  Generuj miniatury", self._run_thumbnails,
```

Replace with:
```python
        self.btn_ai = _sb("  Generuj opisy (AI)", self._run_ai,
            "Gemini AI generuje opisy HTML. Cache SQLite — idempotentne.",
            fg_color="#1a6f3a", hover_color="#145c2f")
        self.btn_ai_unattended = _sb("  ⏳ Generuj automatycznie", self._run_ai_unattended,
            "Unattended mode: retry + cooldown — odejdź od komputera.",
            fg_color="#0E7490", hover_color="#0C6177")
        self.btn_thumb = _sb("  Generuj miniatury", self._run_thumbnails,
```

- [ ] **Step 4.2: Add `_run_ai_unattended()` method** (place after `_run_ai` ~line 634)

```python
    def _run_ai_unattended(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML (krok 3).")
            return

        has_api_key = bool(
            os.getenv("GEMINI_API_KEYS", "").strip()
            or os.getenv("GEMINI_API_KEY_1", "").strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
        )
        if not has_api_key:
            messagebox.showerror(
                APP_NAME,
                "Brak kluczy API Gemini!\n\nDodaj do pliku .env:\nGEMINI_API_KEYS=klucz1,klucz2",
            )
            return

        pending = [p for p in self.products if not getattr(p, "ai_done", False)]
        if not pending:
            messagebox.showinfo(APP_NAME, "Wszystkie opisy już wygenerowane (z cache).")
            return

        if not messagebox.askyesno(
            APP_NAME,
            f"Uruchomić unattended generation dla {len(pending)} produktów?\n\n"
            "Program będzie czekał na cooldown kluczy API i nie można go zatrzymać.\n"
            "Możesz odejść od komputera — SQLite cache zapisuje postęp na bieżąco.",
        ):
            return

        self.btn_ai_unattended.configure(state="disabled")
        self.btn_ai.configure(state="disabled")
        self.status_var.set(f"⏳ Unattended: generuję {len(pending)} opisów…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self._op_start()
        self.btn_cancel.configure(state="disabled")   # unattended: no cancel
        threading.Thread(
            target=self._ai_unattended_worker, args=(self.products,), daemon=True
        ).start()
```

- [ ] **Step 4.3: Add `_ai_unattended_worker()` method** (place after `_run_ai_unattended`)

```python
    def _ai_unattended_worker(self, products: list[Product]):
        def log(msg: str):
            self.q.put(("status", f"⏳ {msg}"))

        try:
            submitted, cached = generate_descriptions(
                products,
                progress_callback=log,
                cancel_check=None,   # unattended: never cancel
            )
            self.q.put(("ai_done", submitted, cached))
        except Exception as e:
            self.q.put(("error", f"Unattended generation: {e}"))
        finally:
            self._op_end()
```

- [ ] **Step 4.4: Re-enable `btn_ai_unattended` in `ai_done` handler**

Find (around line 1281):
```python
                    self.btn_ai.configure(state="normal")
                    self._op_end()
```

Replace with:
```python
                    self.btn_ai.configure(state="normal")
                    self.btn_ai_unattended.configure(state="normal")
                    self._op_end()
```

- [ ] **Step 4.5: Verify import compiles**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro
./venv/bin/python -c "import app.gui.main_window; print('OK')"
```

Expected: `OK`

- [ ] **Step 4.6: Run full test suite**

```bash
./venv/bin/python -m pytest --tb=short -q
```

Expected: 123+ tests PASS (114 existing + 9 new KeyScheduler tests)

- [ ] **Step 4.7: Commit**

```bash
git add app/gui/main_window.py
git commit -m "feat: add 'Generuj automatycznie' unattended mode button with disabled cancel"
```

---

## Task 5: Smoke test — verify rate limiting works end-to-end

- [ ] **Step 5.1: Verify KeyScheduler cooldown integration with mocked 429**

Create a one-off test script (delete after):

```python
# /tmp/test_cooldown_integration.py
import asyncio, sys, os, time
sys.path.insert(0, "/Users/jakubknap/Projects/marketia-xml-pro")

from unittest.mock import AsyncMock, patch, MagicMock

async def main():
    with patch("app.ai.claude_client.genai.Client") as MockClient:
        call_counts = {0: 0, 1: 0}
        
        def make_mock_client(key_idx):
            client = MagicMock()
            async def generate_content(**kwargs):
                call_counts[key_idx] += 1
                if key_idx == 0 and call_counts[0] <= 2:
                    raise Exception("RESOURCE_EXHAUSTED: quota exceeded")
                mock_resp = MagicMock()
                mock_resp.text = f'{{"section_1": "ok from key {key_idx}"}}'
                return mock_resp
            client.aio.models.generate_content = generate_content
            return client
        
        cooling_events = []
        MockClient.side_effect = [make_mock_client(0), make_mock_client(1)]
        
        from app.ai.claude_client import ClaudeClient
        with patch("app.ai.claude_client._load_keys", return_value=["k0", "k1"]):
            c = ClaudeClient(on_key_cooling=lambda idx, secs: cooling_events.append(idx))
        
        # Override cooldown to 0.2s so test is fast
        c._scheduler._cooldown = 0.2
        
        reqs = [{"custom_id": "SKU1", "system": "sys", "content": "content", "json_mode": False}]
        results = c.generate_all(reqs)
        
        print(f"Results: {results}")
        print(f"Cooling events (key indices): {cooling_events}")
        assert results.get("SKU1") is not None, "SKU1 should succeed after key rotation"
        assert 0 in cooling_events, "Key 0 should have entered cooldown"
        print("✅ Integration test PASSED")

asyncio.run(main())
```

Run:
```bash
./venv/bin/python /tmp/test_cooldown_integration.py
```

Expected: `✅ Integration test PASSED`

Delete the temp script:
```bash
rm /tmp/test_cooldown_integration.py
```

- [ ] **Step 5.2: Final full test run**

```bash
./venv/bin/python -m pytest --tb=short -q
```

Expected: 123 passed (114 original + 9 KeyScheduler), 0 failed

- [ ] **Step 5.3: Final commit**

```bash
git add -A
git commit -m "feat: unattended description generation — KeyScheduler + retry-on-cooldown complete"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ KeyScheduler with per-key cooldown — Task 2
- ✅ `acquire()` waits when all keys cooling — Task 2
- ✅ `report_failure()` sets 62s cooldown — Task 2
- ✅ CONCURRENCY reduced 5→3 — Task 2
- ✅ `on_key_cooling` callback to progress — Task 3
- ✅ `cooling_status` in progress messages — Task 3
- ✅ "Generuj automatycznie" button — Task 4
- ✅ Stop button disabled in unattended mode — Task 4
- ✅ Unit tests for KeyScheduler — Task 1

**Type consistency:**
- `KeyScheduler.get_client(idx: int) -> genai.Client` — used in Task 2 `process_one` ✓
- `KeyScheduler.acquire() -> int` — used in Task 2 `process_one` ✓
- `KeyScheduler.report_failure(idx: int)` — used in Task 2 `process_one` ✓
- `on_key_cooling(idx: int, seconds: float)` — defined in Task 2, used in Task 3 ✓
- `cooling_status: str = ""` kwarg in `on_progress` — matches Task 2 callback call ✓
