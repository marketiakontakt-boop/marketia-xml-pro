"""AI client — Google Gemini (gemini-2.5-flash).

Supports both sequential and parallel async processing.
SQLite cache prevents re-generating already done products.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import google.genai as genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MODEL = "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 8192
CONCURRENCY = 5   # parallel requests — stay within 15 RPM free tier


def _get_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Brak GEMINI_API_KEY. Sprawdź plik .env:\nGEMINI_API_KEY=AIza..."
        )
    return genai.Client(api_key=key)


def _make_config(system: str) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.7,
        # Disable thinking tokens — saves cost, avoids hitting output limit
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that Gemini sometimes adds."""
    text = re.sub(r"^```[\w]*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()


class ClaudeClient:
    """Named ClaudeClient for backward-compat; uses Gemini under the hood."""

    def __init__(self):
        self.client = _get_client()

    def call(self, system: str, content: str) -> str:
        """Single synchronous call. Returns cleaned HTML."""
        resp = self.client.models.generate_content(
            model=MODEL,
            config=_make_config(system),
            contents=content,
        )
        return _strip_fences(resp.text)

    def generate_all(
        self,
        requests: list[dict],
        progress_callback=None,
    ) -> dict[str, str | None]:
        """Process requests in parallel (up to CONCURRENCY simultaneous).

        Calls progress_callback(done, total, custom_id, error?) after each.
        Returns {custom_id: html_or_None}.
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
        lock = asyncio.Lock()

        async def process_one(req: dict):
            nonlocal done_count
            custom_id = req["custom_id"]
            async with sem:
                try:
                    resp = await self.client.aio.models.generate_content(
                        model=MODEL,
                        config=_make_config(req["system"]),
                        contents=req["content"],
                    )
                    html = _strip_fences(resp.text)
                    async with lock:
                        results[custom_id] = html
                        done_count += 1
                        if progress_callback:
                            progress_callback(done_count, total, custom_id)
                except Exception as e:
                    async with lock:
                        results[custom_id] = None
                        done_count += 1
                        if progress_callback:
                            progress_callback(done_count, total, custom_id, error=str(e))

        await asyncio.gather(*[process_one(r) for r in requests])
        return results
