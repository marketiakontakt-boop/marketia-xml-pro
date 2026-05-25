# Filters, Preview, Stats, Versioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodaj do Marketia XML Pro: filtrowanie listy (marka + status AI), popup podglądu opisu z edycją marki i regeneracją, stats bar na dole okna oraz wersjonowanie opisów w SQLite.

**Architecture:** Cztery niezależne zmiany. Versioning najpierw (SQLite) bo popup go używa. Potem filter bar i stats bar (zmiany w main_window.py). Na końcu popup jako nowy moduł i wpięcie go w main_window.

**Tech Stack:** Python 3.14, customtkinter, sqlite3, Google Gemini (genai), venv: `./venv/bin/python`

---

## File Map

| Plik | Zmiana |
|------|--------|
| `app/cache/sqlite_cache.py` | +schema `description_versions`, update `save_description`, +2 nowe funkcje |
| `app/transformer/description_generator.py` | `save_description` + `quality_score`, nowa `generate_single_description()` |
| `app/gui/preview.py` | nowa `open_single_preview(product)` |
| `app/gui/main_window.py` | filter bar + stats bar + row click + popup callbacks |
| `app/gui/product_detail.py` | NOWY — `ProductDetailWindow(CTkToplevel)` |
| `tests/test_sqlite_cache.py` | NOWY — testy cache functions |

---

## Task 1: Description versioning — SQLite

**Files:**
- Modify: `app/cache/sqlite_cache.py`
- Create: `tests/test_sqlite_cache.py`

- [ ] **Krok 1: Utwórz plik testowy i napisz testy dla nowych funkcji**

```python
# tests/test_sqlite_cache.py
import pytest
from app.cache.sqlite_cache import (
    open_cache,
    save_description,
    get_cached_description,
    get_description_history,
    restore_description_version,
)

@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test.db"
    with open_cache(db) as c:
        yield c

def test_save_description_creates_version(conn):
    save_description(conn, "SKU-001", "<p>v1</p>", quality_score=7)
    history = get_description_history(conn, "SKU-001")
    assert len(history) == 1
    assert history[0]["version"] == 1
    assert history[0]["quality_score"] == 7

def test_second_save_increments_version(conn):
    save_description(conn, "SKU-001", "<p>v1</p>", quality_score=6)
    save_description(conn, "SKU-001", "<p>v2</p>", quality_score=8)
    history = get_description_history(conn, "SKU-001")
    assert len(history) == 2
    assert history[0]["version"] == 2  # DESC order — newest first
    assert history[1]["version"] == 1

def test_restore_version_updates_current(conn):
    save_description(conn, "SKU-001", "<p>v1</p>", quality_score=5)
    save_description(conn, "SKU-001", "<p>v2</p>", quality_score=9)
    history = get_description_history(conn, "SKU-001")
    old_version_id = history[1]["id"]  # v1
    html = restore_description_version(conn, "SKU-001", old_version_id)
    assert html == "<p>v1</p>"
    assert get_cached_description(conn, "SKU-001") == "<p>v1</p>"

def test_get_description_history_empty(conn):
    assert get_description_history(conn, "UNKNOWN") == []
```

- [ ] **Krok 2: Uruchom testy — powinny FAIL (funkcje jeszcze nie istnieją)**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro
./venv/bin/python -m pytest tests/test_sqlite_cache.py -v
```

Oczekiwane: `ImportError` lub `FAILED` dla `get_description_history`, `restore_description_version`.

- [ ] **Krok 3: Dodaj tabelę `description_versions` do SCHEMA w `sqlite_cache.py`**

W pliku `app/cache/sqlite_cache.py`, do stałej `SCHEMA` (przed ostatnim `"""`) dodaj:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    ...existing...
);

CREATE TABLE IF NOT EXISTS used_model_names (
    ...existing...
);

CREATE INDEX IF NOT EXISTS idx_used_models_brand ON used_model_names(brand);

CREATE TABLE IF NOT EXISTS descriptions (
    ...existing...
);

CREATE TABLE IF NOT EXISTS batch_state (
    ...existing...
);

CREATE TABLE IF NOT EXISTS description_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sku              TEXT NOT NULL,
    version          INTEGER NOT NULL,
    description_html TEXT NOT NULL,
    quality_score    INTEGER DEFAULT -1,
    generated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_desc_ver_sku_ver
    ON description_versions(sku, version);
"""
```

Uwaga: zachowaj całą resztę SCHEMA bez zmian, dodaj tylko blok `description_versions` i index.

- [ ] **Krok 4: Zaktualizuj `save_description` — dodaj `quality_score` i zapis do `description_versions`**

Zastąp obecną funkcję `save_description`:

```python
def save_description(conn: sqlite3.Connection, sku: str, html: str, quality_score: int = -1) -> None:
    # Keep descriptions table as current/latest (backward compat)
    conn.execute(
        """
        INSERT INTO descriptions (sku, description_html)
        VALUES (?, ?)
        ON CONFLICT(sku) DO UPDATE SET
            description_html = excluded.description_html,
            generated_at = CURRENT_TIMESTAMP
        """,
        (sku, html),
    )
    # Write new version to description_versions
    next_ver = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM description_versions WHERE sku = ?",
        (sku,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO description_versions (sku, version, description_html, quality_score) "
        "VALUES (?, ?, ?, ?)",
        (sku, next_ver, html, quality_score),
    )
```

- [ ] **Krok 5: Dodaj `get_description_history` i `restore_description_version`**

Po funkcji `save_description` dodaj:

```python
def get_description_history(conn: sqlite3.Connection, sku: str) -> list[dict]:
    """Return all saved versions for sku, newest first."""
    rows = conn.execute(
        "SELECT id, version, quality_score, generated_at "
        "FROM description_versions WHERE sku = ? ORDER BY version DESC",
        (sku,),
    ).fetchall()
    return [dict(r) for r in rows]


def restore_description_version(conn: sqlite3.Connection, sku: str, version_id: int) -> str:
    """Set descriptions[sku] to the HTML from description_versions[version_id]. Returns HTML."""
    row = conn.execute(
        "SELECT description_html FROM description_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Version id={version_id} not found for sku={sku}")
    html = row["description_html"]
    conn.execute(
        "UPDATE descriptions SET description_html = ?, generated_at = CURRENT_TIMESTAMP "
        "WHERE sku = ?",
        (html, sku),
    )
    return html
```

- [ ] **Krok 6: Uruchom testy — powinny PASS**

```bash
./venv/bin/python -m pytest tests/test_sqlite_cache.py -v
```

Oczekiwane: `4 passed`.

- [ ] **Krok 7: Commit**

```bash
git add app/cache/sqlite_cache.py tests/test_sqlite_cache.py
git commit -m "feat: description versioning — description_versions table + history/restore"
```

---

## Task 2: Przekaż `quality_score` do `save_description` + `generate_single_description`

**Files:**
- Modify: `app/transformer/description_generator.py`

- [ ] **Krok 1: Zaktualizuj wywołanie `save_description` w `generate_descriptions`**

W `app/transformer/description_generator.py`, w bloku `with open_cache() as conn:` (okolica linii 98-107):

Zastąp:
```python
    with open_cache() as conn:
        for sku, html in results.items():
            if html is None:
                continue
            if sku in sku_map:
                p = sku_map[sku]
                p.description = html
                p.ai_done = True
                p.quality_score = score_description(html)
            save_description(conn, sku, html)
```

Na:
```python
    with open_cache() as conn:
        for sku, html in results.items():
            if html is None:
                continue
            score = score_description(html)
            if sku in sku_map:
                p = sku_map[sku]
                p.description = html
                p.ai_done = True
                p.quality_score = score
            save_description(conn, sku, html, quality_score=score)
```

- [ ] **Krok 2: Dodaj `generate_single_description` na końcu pliku**

```python
def generate_single_description(product: Product) -> str:
    """Generate description for one product synchronously (for popup regeneration).

    Updates product in-place and saves to cache. Returns HTML.
    """
    brand_data = _load_brand_data()
    brand_key = product.brand or "unknown"
    brand_info = brand_data.get(brand_key, {"name": brand_key.upper(), "tagline": ""})
    user_msg = build_description_prompt(product, brand_info, brand_key)

    client = ClaudeClient()
    html = client.call(SYSTEM_PROMPT, user_msg)
    score = score_description(html)

    with open_cache() as conn:
        save_description(conn, product.sku, html, quality_score=score)

    product.description = html
    product.ai_done = True
    product.quality_score = score
    return html
```

- [ ] **Krok 3: Sprawdź że istniejące testy przechodzą**

```bash
./venv/bin/python -m pytest tests/ -v
```

Oczekiwane: `4 passed`.

- [ ] **Krok 4: Commit**

```bash
git add app/transformer/description_generator.py
git commit -m "feat: pass quality_score to save_description, add generate_single_description"
```

---

## Task 3: `open_single_preview` w preview.py

**Files:**
- Modify: `app/gui/preview.py`

- [ ] **Krok 1: Dodaj `open_single_preview` na końcu pliku `app/gui/preview.py`**

```python
def open_single_preview(product: Product) -> None:
    """Open a single product's description rendered in system browser."""
    if not product.description:
        return
    score = product.quality_score if product.quality_score >= 0 else score_description(product.description)
    label, color = get_label(score)
    badge = f'<span class="score-badge" style="background:{color}">{score}/10 {label}</span>'
    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>{product.sku} — Podgląd opisu</title>
<style>{JUMI_CSS}</style>
</head>
<body>
<div class="product-card">
  <div class="product-header">
    <h2>{product.title or product.name}{badge}</h2>
    <div class="meta">SKU: {product.sku} | Marka: {product.brand or '—'} | EAN: {product.ean or '—'}</div>
  </div>
  {product.description}
</div>
</body>
</html>"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html", encoding="utf-8", delete=False)
    tmp.write(html)
    tmp.close()
    webbrowser.open(f"file://{tmp.name}")
```

- [ ] **Krok 2: Commit**

```bash
git add app/gui/preview.py
git commit -m "feat: add open_single_preview for single-product HTML preview"
```

---

## Task 4: Filter bar w `main_window.py`

**Files:**
- Modify: `app/gui/main_window.py`

- [ ] **Krok 1: Dodaj zmienne stanu filtrów do `App.__init__`**

W metodzie `__init__` klasy `App`, po linii `self._xml_path: str | None = None` dodaj:

```python
        self._filter_brand: str = "Wszystkie"
        self._filter_ai: str = "Wszystkie"
```

- [ ] **Krok 2: Dodaj metodę `_filtered_products`**

Przed metodą `_build_layout` dodaj:

```python
    def _filtered_products(self) -> list[Product]:
        result = self.products
        if self._filter_brand != "Wszystkie":
            result = [p for p in result if (p.brand or "—") == self._filter_brand]
        if self._filter_ai == "Z opisem":
            result = [p for p in result if getattr(p, "ai_done", False)]
        elif self._filter_ai == "Bez opisu":
            result = [p for p in result if not getattr(p, "ai_done", False)]
        return result
```

- [ ] **Krok 3: Dodaj metodę `_build_filter_bar`**

```python
    def _build_filter_bar(self, parent: ctk.CTkFrame) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 4))

        ctk.CTkLabel(bar, text="Marka:", anchor="w").pack(side="left", padx=(8, 2))
        self._brand_menu = ctk.CTkOptionMenu(
            bar,
            values=["Wszystkie"],
            width=160,
            command=self._on_filter_brand,
        )
        self._brand_menu.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(bar, text="Status AI:", anchor="w").pack(side="left", padx=(0, 2))
        self._ai_seg = ctk.CTkSegmentedButton(
            bar,
            values=["Wszystkie", "Z opisem", "Bez opisu"],
            command=self._on_filter_ai,
        )
        self._ai_seg.set("Wszystkie")
        self._ai_seg.pack(side="left", padx=(0, 12))

        ctk.CTkButton(bar, text="Wyczyść", width=80, command=self._clear_filters).pack(side="left")

    def _on_filter_brand(self, value: str) -> None:
        self._filter_brand = value
        self._render_table()

    def _on_filter_ai(self, value: str) -> None:
        self._filter_ai = value
        self._render_table()

    def _clear_filters(self) -> None:
        self._filter_brand = "Wszystkie"
        self._filter_ai = "Wszystkie"
        self._brand_menu.set("Wszystkie")
        self._ai_seg.set("Wszystkie")
        self._render_table()

    def _update_brand_filter_options(self) -> None:
        brands = sorted({p.brand or "—" for p in self.products if p.brand})
        self._brand_menu.configure(values=["Wszystkie"] + brands)
        self._brand_menu.set("Wszystkie")
```

- [ ] **Krok 4: Zmodyfikuj `_build_layout` — wstaw filter bar i przesuń listy**

W `_build_layout`, w bloku `# Main area`, znajdź:

```python
        main.grid_rowconfigure(1, weight=1)
```

Zmień na:
```python
        main.grid_rowconfigure(2, weight=1)
```

Następnie po bloku `header` (który ma `row=0`), przed `self.list_frame`, dodaj wywołanie:
```python
        self._build_filter_bar(main)
```

I zmień `self.list_frame` z `row=1` na `row=2`:
```python
        self.list_frame = ctk.CTkScrollableFrame(main, label_text="Produkty")
        self.list_frame.grid(row=2, column=0, sticky="nsew")
```

- [ ] **Krok 5: Zaktualizuj `_render_table` — używaj `_filtered_products()`**

W `_render_table`, znajdź linię:
```python
        for idx, p in enumerate(self.products[:cap], 1):
```

Zmień na:
```python
        filtered = self._filtered_products()
        for idx, p in enumerate(filtered[:cap], 1):
```

I dalej:
```python
        if len(self.products) > cap:
```
Zmień na:
```python
        if len(filtered) > cap:
            ctk.CTkLabel(
                self.list_frame,
                text=f"… (+{len(filtered) - cap} kolejnych)",
                text_color="#888",
            ).grid(row=cap + 1, column=0, pady=8)
```

- [ ] **Krok 6: Wire `_update_brand_filter_options` po wczytaniu XML**

W `_poll_queue`, w bloku `if tag == "loaded":`, po linii `self._render_table()` dodaj:
```python
                    self._update_brand_filter_options()
```

- [ ] **Krok 7: Uruchom appkę i sprawdź filtry ręcznie**

```bash
cd /Users/jakubknap/Projects/marketia-xml-pro
./venv/bin/python -m app.main
```

Wczytaj XML → sprawdź że dropdown marki jest wypełniony → filtruj → wyczyść.

- [ ] **Krok 8: Commit**

```bash
git add app/gui/main_window.py
git commit -m "feat: filter bar — brand dropdown + AI status filter"
```

---

## Task 5: Stats bar w `main_window.py`

**Files:**
- Modify: `app/gui/main_window.py`

- [ ] **Krok 1: Dodaj zmienne sesji do `App.__init__`**

Po `self._filter_ai` dodaj:

```python
        self._session_generated: int = 0
        self._session_cached: int = 0
```

- [ ] **Krok 2: Dodaj metodę `_build_stats_bar`**

```python
    def _build_stats_bar(self, parent: ctk.CTkFrame) -> None:
        self._stat_total    = ctk.StringVar(value="Produkty: —")
        self._stat_ai       = ctk.StringVar(value="Z opisem: —")
        self._stat_q        = ctk.StringVar(value="Q avg: —")
        self._stat_cost     = ctk.StringVar(value="Koszt: —")
        self._stat_cache    = ctk.StringVar(value="Cache: —")

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(2, 6))

        for var in (self._stat_total, self._stat_ai, self._stat_q, self._stat_cost, self._stat_cache):
            ctk.CTkLabel(
                bar, textvariable=var, anchor="w",
                font=ctk.CTkFont(size=11),
                text_color="#aaa",
            ).pack(side="left", padx=10)

    def _update_stats(self) -> None:
        total = len(self.products)
        ai_done = sum(1 for p in self.products if getattr(p, "ai_done", False))
        pct = int(ai_done / total * 100) if total else 0
        scores = [p.quality_score for p in self.products if getattr(p, "quality_score", -1) >= 0]
        q_avg = sum(scores) / len(scores) if scores else 0.0
        cost = self._session_generated * 0.005
        total_calls = self._session_generated + self._session_cached
        cache_pct = int(self._session_cached / total_calls * 100) if total_calls else 0

        self._stat_total.set(f"Produkty: {total}")
        self._stat_ai.set(f"Z opisem: {ai_done} ({pct}%)")
        self._stat_q.set(f"Q avg: {q_avg:.1f}" if scores else "Q avg: —")
        self._stat_cost.set(f"Koszt: ~${cost:.2f}")
        self._stat_cache.set(f"Cache: {cache_pct}%")
```

- [ ] **Krok 3: Wstaw stats bar do `_build_layout`**

W `_build_layout`, footer jest teraz tak skonfigurowany:
```python
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        footer.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(footer)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.status_var = ctk.StringVar(value="Gotowy.")
        ctk.CTkLabel(footer, textvariable=self.status_var, anchor="e").grid(
            row=0, column=1, sticky="e"
        )
```

Zmień layout root grida: `self.grid_rowconfigure(0, weight=1)` zostaje, dodaj:
- footer na `row=1` (istnieje)
- stats bar na `row=2`

Po bloku footer dodaj:

```python
        self._build_stats_bar(self)
```

Ale `_build_stats_bar` przyjmuje `parent` i griduje się sam jako `row=1`. Zmień więc footer na `row=1`, stats na `row=2`:

Zmień footer.grid na:
```python
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 0))
```

I w `_build_stats_bar` zmień `bar.grid(row=1, ...)` na `bar.grid(row=2, ...)`:
```python
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
```

- [ ] **Krok 4: Wire `_update_stats` w `_poll_queue`**

W `_poll_queue`, w bloku `if tag == "loaded":`, po `self._render_table()` dodaj:
```python
                    self._update_stats()
```

W bloku `elif tag == "transformed":`, po istniejącym kodzie dodaj:
```python
                    self._update_stats()
```

W bloku `elif tag == "ai_done":`, po `self._render_table()` dodaj:
```python
                    self._session_generated += submitted
                    self._session_cached += cached
                    self._update_stats()
```

- [ ] **Krok 5: Uruchom appkę i sprawdź stats bar**

```bash
./venv/bin/python -m app.main
```

Wczytaj XML → sprawdź że chips pojawiają się na dole → wygeneruj opisy → sprawdź że koszt/cache się aktualizują.

- [ ] **Krok 6: Commit**

```bash
git add app/gui/main_window.py
git commit -m "feat: stats bar — produkty, AI%, Q avg, koszt sesji, cache%"
```

---

## Task 6: Product Detail Popup — nowy plik

**Files:**
- Create: `app/gui/product_detail.py`

- [ ] **Krok 1: Utwórz `app/gui/product_detail.py`**

```python
"""Product detail popup — HTML preview, brand edit, description history."""
from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

from app.cache.sqlite_cache import get_description_history, restore_description_version, open_cache
from app.gui.preview import open_single_preview
from app.parser.normalizer import Product
from app.validator.quality_scorer import get_label


class ProductDetailWindow(ctk.CTkToplevel):
    """Non-blocking popup showing description, brand editor, and version history."""

    def __init__(
        self,
        parent,
        product: Product,
        all_brands: list[str],
        on_brand_change: Callable[[Product, str], None],
        on_regenerate: Callable[[Product], None],
    ):
        super().__init__(parent)
        self.resizable(True, True)
        self.geometry("820x620")
        self.minsize(600, 400)

        self._on_brand_change = on_brand_change
        self._on_regenerate = on_regenerate
        self._all_brands = all_brands

        self._tabs = ctk.CTkTabview(self)
        self._tabs.pack(fill="both", expand=True, padx=10, pady=10)
        self._tabs.add("Opis")
        self._tabs.add("Historia")

        self._build_opis_tab(self._tabs.tab("Opis"))
        self._build_historia_tab(self._tabs.tab("Historia"))

        self.load_product(product)

    # ── public ────────────────────────────────────────────────────────────

    def load_product(self, product: Product) -> None:
        """Switch popup to show a different product."""
        self._product = product
        self.title(f"{product.sku} — {(product.title or product.name)[:55]}")
        self._refresh_opis()
        self._refresh_historia()

    def refresh(self) -> None:
        """Call after external regeneration completes."""
        self._refresh_opis()
        self._refresh_historia()

    # ── build ──────────────────────────────────────────────────────────────

    def _build_opis_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # toolbar
        toolbar = ctk.CTkFrame(parent, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        ctk.CTkLabel(toolbar, text="Marka:").pack(side="left", padx=(4, 4))
        self._brand_var = ctk.StringVar()
        self._brand_menu = ctk.CTkOptionMenu(
            toolbar, variable=self._brand_var, values=["—"], width=160
        )
        self._brand_menu.pack(side="left", padx=(0, 6))
        ctk.CTkButton(toolbar, text="Zapisz markę", width=110, command=self._save_brand).pack(
            side="left", padx=(0, 16)
        )

        self._q_label = ctk.CTkLabel(toolbar, text="Q: —", font=ctk.CTkFont(weight="bold"))
        self._q_label.pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            toolbar, text="Otwórz w przeglądarce", width=160, command=self._open_browser
        ).pack(side="left", padx=(0, 8))
        self._regen_btn = ctk.CTkButton(
            toolbar, text="Regeneruj opis", width=120,
            fg_color="#1a6f3a", hover_color="#145c2f",
            command=self._regenerate,
        )
        self._regen_btn.pack(side="left")

        # HTML textbox
        self._html_box = ctk.CTkTextbox(parent, wrap="word", font=ctk.CTkFont(family="Courier", size=11))
        self._html_box.grid(row=1, column=0, sticky="nsew")

    def _build_historia_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        self._hist_frame = ctk.CTkScrollableFrame(parent, label_text="Wersje opisu")
        self._hist_frame.grid(row=0, column=0, sticky="nsew")
        self._hist_frame.grid_columnconfigure(0, weight=1)

    # ── refresh ────────────────────────────────────────────────────────────

    def _refresh_opis(self) -> None:
        p = self._product
        # brand menu
        brands = self._all_brands or ["—"]
        self._brand_menu.configure(values=brands)
        self._brand_var.set(p.brand or "—")

        # quality score
        score = getattr(p, "quality_score", -1)
        if score >= 0:
            label, color = get_label(score)
            self._q_label.configure(text=f"Q: {score}/10 {label}", text_color=color)
        else:
            self._q_label.configure(text="Q: —", text_color="gray")

        # HTML content
        self._html_box.configure(state="normal")
        self._html_box.delete("1.0", "end")
        desc = getattr(p, "description", None) or ""
        self._html_box.insert("1.0", desc if desc else "(brak opisu — uruchom krok 4)")
        self._html_box.configure(state="disabled")

    def _refresh_historia(self) -> None:
        for child in self._hist_frame.winfo_children():
            child.destroy()

        with open_cache() as conn:
            history = get_description_history(conn, self._product.sku)

        if not history:
            ctk.CTkLabel(self._hist_frame, text="Brak historii wersji.", text_color="#888").grid(
                row=0, column=0, padx=8, pady=8
            )
            return

        # header
        header = ctk.CTkFrame(self._hist_frame, fg_color="#1f1f1f")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        for col, (text, w) in enumerate([("Ver", 50), ("Data", 160), ("Q", 50), ("Akcja", 100)]):
            header.grid_columnconfigure(col, minsize=w)
            ctk.CTkLabel(header, text=text, font=ctk.CTkFont(weight="bold"), text_color="#ddd").grid(
                row=0, column=col, padx=6, pady=4, sticky="w"
            )

        for row_idx, rec in enumerate(history, 1):
            is_current = row_idx == 1
            self._add_history_row(row_idx, rec, is_current)

    def _add_history_row(self, row_idx: int, rec: dict, is_current: bool) -> None:
        score = rec["quality_score"]
        _, color = get_label(score) if score >= 0 else ("—", "#888")
        fg = "#1a6f3a" if is_current else "transparent"

        row_frame = ctk.CTkFrame(self._hist_frame, fg_color=fg)
        row_frame.grid(row=row_idx, column=0, sticky="ew", pady=1)
        for col, w in enumerate([50, 160, 50, 100]):
            row_frame.grid_columnconfigure(col, minsize=w)

        ctk.CTkLabel(row_frame, text=f"v{rec['version']}").grid(row=0, column=0, padx=6, sticky="w")
        ts = str(rec["generated_at"])[:16]
        ctk.CTkLabel(row_frame, text=ts).grid(row=0, column=1, padx=6, sticky="w")
        ctk.CTkLabel(row_frame, text=str(score) if score >= 0 else "—", text_color=color).grid(
            row=0, column=2, padx=6, sticky="w"
        )
        if is_current:
            ctk.CTkLabel(row_frame, text="aktualna", text_color="#1f883d").grid(
                row=0, column=3, padx=6, sticky="w"
            )
        else:
            ctk.CTkButton(
                row_frame, text="Przywróć", width=80,
                command=lambda vid=rec["id"]: self._restore_version(vid),
            ).grid(row=0, column=3, padx=4, pady=2, sticky="w")

    # ── actions ────────────────────────────────────────────────────────────

    def _save_brand(self) -> None:
        new_brand = self._brand_var.get()
        if new_brand and new_brand != "—":
            self._on_brand_change(self._product, new_brand)

    def _open_browser(self) -> None:
        if getattr(self._product, "description", None):
            open_single_preview(self._product)

    def _regenerate(self) -> None:
        self._regen_btn.configure(state="disabled", text="Generuję…")
        self._on_regenerate(self._product)

    def enable_regen_btn(self) -> None:
        self._regen_btn.configure(state="normal", text="Regeneruj opis")

    def _restore_version(self, version_id: int) -> None:
        with open_cache() as conn:
            html = restore_description_version(conn, self._product.sku, version_id)
        self._product.description = html
        self._refresh_opis()
        self._refresh_historia()
```

- [ ] **Krok 2: Commit**

```bash
git add app/gui/product_detail.py
git commit -m "feat: ProductDetailWindow — description preview, brand edit, version history"
```

---

## Task 7: Wire popup w `main_window.py`

**Files:**
- Modify: `app/gui/main_window.py`

- [ ] **Krok 1: Dodaj import**

Na górze `main_window.py`, po imporcie `open_preview` dodaj:
```python
from app.gui.product_detail import ProductDetailWindow
from app.transformer.description_generator import generate_single_description
```

- [ ] **Krok 2: Dodaj `_detail_win` do `__init__`**

Po `self._xml_path` dodaj:
```python
        self._detail_win: ProductDetailWindow | None = None
```

- [ ] **Krok 3: Dodaj metodę `_on_row_click`**

```python
    def _on_row_click(self, product: Product) -> None:
        brands = sorted({p.brand for p in self.products if p.brand})
        if self._detail_win is not None:
            try:
                self._detail_win.winfo_exists()
                self._detail_win.load_product(product)
                self._detail_win.lift()
                return
            except Exception:
                self._detail_win = None
        self._detail_win = ProductDetailWindow(
            self,
            product,
            all_brands=brands,
            on_brand_change=self._on_brand_change,
            on_regenerate=self._on_regenerate_product,
        )

    def _on_brand_change(self, product: Product, new_brand: str) -> None:
        product.brand = new_brand
        self._render_table()

    def _on_regenerate_product(self, product: Product) -> None:
        threading.Thread(
            target=self._single_regen_worker,
            args=(product,),
            daemon=True,
        ).start()

    def _single_regen_worker(self, product: Product) -> None:
        try:
            generate_single_description(product)
            self.q.put(("single_regen_done", product))
        except Exception as e:
            self.q.put(("error", f"Regeneracja {product.sku}: {e}"))
```

- [ ] **Krok 4: Handle `single_regen_done` w `_poll_queue`**

W `_poll_queue`, po bloku `elif tag == "error":` dodaj:

```python
                elif tag == "single_regen_done":
                    _, product = msg
                    self._render_table()
                    self._update_stats()
                    if self._detail_win:
                        try:
                            self._detail_win.refresh()
                            self._detail_win.enable_regen_btn()
                        except Exception:
                            pass
```

- [ ] **Krok 5: Dodaj click binding do `ProductRow`**

Zmień sygnaturę `ProductRow.__init__`:
```python
    def __init__(self, master, product: Product, on_click=None, **kwargs):
```

Na końcu `__init__`, przed ostatnią linią (po wszystkich `.grid()` call-ach), dodaj:

```python
        if on_click:
            self.bind("<Button-1>", lambda e: on_click())
            for child in self.winfo_children():
                child.bind("<Button-1>", lambda e: on_click())
```

- [ ] **Krok 6: Przekaż `on_click` przy tworzeniu `ProductRow` w `_render_table`**

Znajdź:
```python
            row = ProductRow(self.list_frame, p)
```

Zmień na:
```python
            row = ProductRow(self.list_frame, p, on_click=lambda prod=p: self._on_row_click(prod))
```

- [ ] **Krok 7: E2E test ręczny**

```bash
./venv/bin/python -m app.main
```

1. Wczytaj XML → uruchom transformy
2. Kliknij na produkt w liście → popup powinien się otworzyć
3. Sprawdź że Tab "Opis" pokazuje HTML (lub "(brak opisu)" gdy nie ma)
4. Zmień markę i kliknij "Zapisz markę" → sprawdź że wiersz w tabeli się aktualizuje
5. Kliknij inny produkt → popup się aktualizuje (nie otwiera nowego okna)
6. (jeśli masz GEMINI_API_KEY) Kliknij "Regeneruj opis" na produkcie bez opisu → po chwili pojawia się w popup
7. Po wygenerowaniu kilku opisów: Tab "Historia" pokazuje wersje

- [ ] **Krok 8: Commit**

```bash
git add app/gui/main_window.py
git commit -m "feat: wire ProductDetailWindow — row click, brand change, single regeneration"
```

---

## Self-Review

**Spec coverage:**
- ✅ Filter bar: marka + status AI — Task 4
- ✅ Stats bar: totalnej, AI%, Q avg, koszt, cache% — Task 5
- ✅ Popup z opisem + zmiana marki + regeneracja — Task 6+7
- ✅ Tab Historia z wersjami + Przywróć — Task 6
- ✅ Description versioning w SQLite — Task 1+2

**Placeholder scan:** brak TBD, brak "podobnie jak wyżej", wszystkie kroki mają kod.

**Type consistency:**
- `save_description(conn, sku, html, quality_score=-1)` — używane tak w Task 1 (implementacja) i Task 2 (wywołanie)
- `get_description_history(conn, sku) -> list[dict]` — implementowane w Task 1, używane w `product_detail.py` Task 6
- `restore_description_version(conn, sku, version_id) -> str` — implementowane w Task 1, używane w Task 6
- `generate_single_description(product)` — implementowane w Task 2, używane w Task 7
- `open_single_preview(product)` — implementowane w Task 3, używane w Task 6
- `ProductDetailWindow(parent, product, all_brands, on_brand_change, on_regenerate)` — definiowane w Task 6, używane w Task 7
- `load_product(product)`, `refresh()`, `enable_regen_btn()` — definiowane w Task 6, używane w Task 7

Wszystkie typy spójne.
