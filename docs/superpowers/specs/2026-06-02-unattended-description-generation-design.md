# Unattended Description Generation — Design Spec
**Date:** 2026-06-02  
**Status:** Approved

## Problem

With 7 Gemini API keys and `CONCURRENCY=5`, all keys can exhaust quota simultaneously. When that happens, failed products receive `None` and are silently dropped — no retry, no recovery. Running unattended is unreliable.

Root causes:
1. Linear key rotation: key1→key2→…→key7→fail (no return to cooled-down keys)
2. No wait/backoff when all keys are cooling
3. `CONCURRENCY=5` sends bursts that hit all keys simultaneously

## Solution: KeyScheduler + Hybrid Parallel

### Architecture

```
KeyScheduler
  _cooling_until: dict[int → float]   # key_idx → unix timestamp when available
  _lock: asyncio.Lock
  
  async acquire() → int
    # waits until first available key; if all cooling, sleeps to earliest recovery
  
  async report_failure(idx: int)
    # marks key as cooling for 62 seconds
  
  get_client(idx) → genai.Client
    # returns pre-built client from pool
```

```
ClaudeClient (internal refactor)
  _clients: list[genai.Client]     # pool of N clients, one per key
  _scheduler: KeyScheduler
  CONCURRENCY: 3                   # reduced from 5
  
  process_one(req):
    while True:
      idx = await scheduler.acquire()    # blocks until key available
      try:
        result = await _clients[idx].generate(...)
        return result
      except 429/QUOTA:
        await scheduler.report_failure(idx)
        continue                        # retry with next key
      except other_error:
        return None after MAX_ATTEMPTS
```

```
generate_descriptions (unchanged signature)
  progress_callback shows: "Klucz #3 cooling 45s | X/656 gotowych"
```

```
GUI: "⏳ Generuj automatycznie" button
  → same worker thread as existing "Generuj opisy AI"
  → generate_descriptions signature unchanged (no new flag needed)
  → GUI side: cancel_event is never set (Stop button disabled)
  → status bar prefix: "⏳ Unattended:"
```

### Key Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `CONCURRENCY` | 3 | 3 concurrent × 7 keys = up to 21 RPM theoretical; leaves headroom |
| `COOLDOWN_SECONDS` | 62 | 60s rate-limit window + 2s buffer |
| `MAX_ATTEMPTS_PER_SKU` | `len(keys) * 3` | Enough to survive multiple cooldown cycles |
| Retry on | `429`, `RESOURCE_EXHAUSTED`, `QUOTA`, `RATE_LIMIT` | Rotatable errors |
| Fail on | `JSONDecodeError`, `ValueError`, other | Non-recoverable |

### Data Flow

```
pending queue (products without SQLite cache)
       ↓
  3 async workers (semaphore)
       ↓
  scheduler.acquire() → wait if all cooling
       ↓
  genai call
  ├── success → SQLite cache → product.description
  └── 429     → scheduler.report_failure() → retry loop
  └── other   → None (logged) after MAX_ATTEMPTS
       ↓
  progress_callback("⏳ X/656 | klucz #3 cooling 45s")
```

### Files Changed

| File | Change |
|------|--------|
| `app/ai/claude_client.py` | Add `KeyScheduler`; refactor `_generate_all_async`; add `_clients` pool |
| `app/transformer/description_generator.py` | Add cooldown status to progress messages |
| `app/gui/main_window.py` | Add "Generuj automatycznie" button + unattended worker |
| `tests/test_key_scheduler.py` | New: unit tests for KeyScheduler cooldown logic |

### Testing

- `KeyScheduler.acquire()` returns available key immediately when none cooling
- `KeyScheduler.acquire()` waits correct duration when all keys cooling
- `report_failure()` sets cooldown timestamp correctly
- `report_failure()` on same key twice extends cooldown from call time (not original)
- Integration: `generate_all` with mocked 429-then-success correctly retries

### Non-Goals

- macOS notifications (not requested)
- Persistent retry across app restarts (SQLite cache already handles re-run safety)
- Per-key RPM token bucket (cooldown-on-fail is sufficient for free tier behavior)
