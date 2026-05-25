# Marketia XML Pro — Filters, Preview, Stats, Versioning

**Date:** 2026-05-25  
**Scope:** 4 features added to existing GUI (customtkinter, Python 3.14)

---

## 1. Filter bar

**Location:** nowy wiersz między summary label a `CTkScrollableFrame` w `main_window.py`.

**Komponenty:**
- `CTkOptionMenu` — "Marka: [Wszystkie ▼]" — opcje populowane po wczytaniu XML z unikalnych `product.brand`
- `CTkSegmentedButton` — `["Wszystkie", "Z opisem", "Bez opisu"]` — filtruje po `product.ai_done`
- `CTkButton` — "Wyczyść" — reset obu do "Wszystkie"

**Implementacja:**
- `self._filter_brand: str = "Wszystkie"`
- `self._filter_ai: str = "Wszystkie"`
- Nowa metoda `_filtered_products() → list[Product]` — stosuje oba filtry
- `_render_table()` używa `_filtered_products()` zamiast `self.products`
- Po wczytaniu XML: `_update_brand_filter_options()` odświeża dropdown

**Pliki:** tylko `app/gui/main_window.py` (+~50 linii)

---

## 2. Stats bar

**Location:** drugi wiersz w istniejącym footer (current footer ma `row=0` — progressbar + status).

**Chips (CTkLabel, monospace, readonly):**
```
Produkty: 533  |  Z opisem: 245 (46%)  |  Q avg: 7.4  |  Koszt est.: ~$1.23  |  Cache: 89%
```

**Kalkulacje:**
- `total` = `len(self.products)`
- `ai_done` = count where `product.ai_done == True`
- `q_avg` = avg of `product.quality_score` where score >= 0
- `cost` = `self._session_generated * 0.005` (tracked per-session, reset on new XML load)
- `cache_pct` = `self._session_cached / (self._session_generated + self._session_cached) * 100`

**Aktualizacja:** `_update_stats()` wołana z `_poll_queue` przy eventach: `loaded`, `transformed`, `ai_done`.

**Pliki:** tylko `app/gui/main_window.py` (+~40 linii)

---

## 3. Product Detail Popup

**Nowy plik:** `app/gui/product_detail.py`  
**Klasa:** `ProductDetailWindow(ctk.CTkToplevel)`

**Konstruktor:**
```python
ProductDetailWindow(
    parent,
    product: Product,
    all_brands: list[str],
    on_brand_change: Callable[[Product, str], None],
    on_regenerate: Callable[[Product], None],
    get_history: Callable[[str], list[dict]],
)
```

**Layout (800×600):**
```
┌──────────────────────────────────────────────────────┐
│ INTEX-001 — Basen dmuchany prostokątny...     [✕]   │
├──────────────────────────────────────────────────────┤
│  [ Opis ]  [ Historia ]   ← CTkTabview               │
├──────────────────────────────────────────────────────┤
│  TAB "Opis":                                         │
│  Marka: [GARDENSTEIN ▼]   Q: 7/10   [Regeneruj opis]│
│  ────────────────────────────────────────────────    │
│  CTkTextbox (scrollable, wrap=WORD, read-only)       │
│  <surowy HTML opisu>                                 │
│                                                      │
│  TAB "Historia":                                     │
│  Ver │ Data              │ Q  │ Akcja                │
│  v3  │ 2026-05-25 14:31  │ 8  │ [aktualna]          │
│  v2  │ 2026-05-23 10:12  │ 6  │ [Przywróć]          │
│  v1  │ 2026-05-21 09:44  │ 5  │ [Przywróć]          │
└──────────────────────────────────────────────────────┘
```

**Zachowanie:**
- Popup jest `CTkToplevel` (non-blocking, niezależne okno)
- Jedno okno na raz — `main_window.py` trzyma `self._detail_win: ProductDetailWindow | None`
- Klik na nowy produkt gdy popup otwarty → `_detail_win.load_product(product)` (aktualizuje, nie tworzy nowego)
- Zmiana marki: `CTkOptionMenu` + przycisk "Zapisz" → `on_brand_change(product, new_brand)` → parent aktualizuje `product.brand`, odświeża wiersz w tabeli
- Regeneruj: `on_regenerate(product)` → parent kasuje cache dla SKU, generuje 1 opis przez Gemini, po zakończeniu woła `_detail_win.refresh()`
- "Przywróć" wersję: woła `restore_description_version(conn, sku, version_id)` → aktualizuje `product.description`, odświeża Tab "Opis"

**Trigger w main_window.py:**
- `ProductRow.__init__()` dostaje binding: `self.bind("<Button-1>", lambda e: on_click(product))`
- `App._on_row_click(product)` — tworzy lub aktualizuje `_detail_win`

**Pliki:** nowy `app/gui/product_detail.py` (~150 linii) + zmiany w `main_window.py` (~30 linii)

---

## 4. Description Versioning

**Problem:** obecna tabela `descriptions` ma `sku TEXT PRIMARY KEY` — overwrite przy każdej regeneracji.

**Rozwiązanie:** nowa tabela `description_versions` obok istniejącej `descriptions`.

**Nowy schema (dodany do `SCHEMA` w `sqlite_cache.py`):**
```sql
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
```

**Migracja:** zero-migration — nowa tabela tworzona przez `init_schema()` przy starcie. Istniejące opisy w `descriptions` nie są migrowane (brak historii przed tą zmianą — to OK).

**Zaktualizowane funkcje w `sqlite_cache.py`:**

```python
def save_description(conn, sku, html, quality_score=-1):
    # 1. istniejący UPDATE w descriptions (backward compat)
    conn.execute("INSERT INTO descriptions ... ON CONFLICT DO UPDATE ...", (sku, html))
    # 2. nowa wersja w description_versions
    next_ver = (conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM description_versions WHERE sku = ?", (sku,)
    ).fetchone()[0])
    conn.execute(
        "INSERT INTO description_versions (sku, version, description_html, quality_score) VALUES (?,?,?,?)",
        (sku, next_ver, html, quality_score)
    )

def get_description_history(conn, sku) -> list[dict]:
    rows = conn.execute(
        "SELECT id, version, quality_score, generated_at FROM description_versions "
        "WHERE sku = ? ORDER BY version DESC", (sku,)
    ).fetchall()
    return [dict(r) for r in rows]

def restore_description_version(conn, sku, version_id) -> str:
    row = conn.execute(
        "SELECT description_html FROM description_versions WHERE id = ?", (version_id,)
    ).fetchone()
    html = row["description_html"]
    conn.execute(
        "UPDATE descriptions SET description_html = ?, generated_at = CURRENT_TIMESTAMP WHERE sku = ?",
        (html, sku)
    )
    return html
```

**Wywołanie `save_description` z quality_score:** `description_generator.py` już wylicza `score_description(html)` — przekazuje do `save_description`.

**Pliki:** `app/cache/sqlite_cache.py` (+schema +3 funkcje), `app/transformer/description_generator.py` (+1 argument)

---

## Podsumowanie zmian

| Plik | Typ zmiany | Szacunek |
|------|-----------|----------|
| `app/gui/main_window.py` | filter bar + stats bar + row click | +120 linii |
| `app/gui/product_detail.py` | nowy plik — popup | ~150 linii |
| `app/cache/sqlite_cache.py` | schema + 3 nowe funkcje | +50 linii |
| `app/transformer/description_generator.py` | przekaz quality_score do save | +2 linie |

**Brak breaking changes** — istniejące dane w SQLite działają bez migracji.
