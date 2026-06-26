"""Audyt modeli — wykrywanie i scalanie błędnych grup modelowych."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from tkinter import messagebox

from app.parser.normalizer import Product
from app.transformer.model_generator import _VARIANT_WORDS, _FURNITURE_WORDS, _strip_noise

CACHE_DB = Path(__file__).resolve().parents[2] / "cache" / "marketia.db"

_DARK = "#1F2937"
_DARK_BTN = "#374151"
_DARK_HOVER = "#4B5563"
_DARK_TEXT = "#F9FAFB"
_GREEN = "#1a6f3a"
_GREEN_HOVER = "#145c2f"


def _descriptor_words(name: str) -> set[str]:
    """Słowa z nazwy produktu po usunięciu kolorów, mebli i noise."""
    cleaned = _strip_noise(name).lower()
    words = cleaned.split()
    return {
        w for w in words
        if w not in _VARIANT_WORDS
        and w not in _FURNITURE_WORDS
        and len(w) >= 3
        and w.isalpha()
    }


def _detect_misgroups(products: list[Product]) -> list[dict]:
    """Zwraca listę potencjalnych mis-grup (max 30, posortowane wg. liczby produktów)."""
    series_products: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if not p.model_name:
            continue
        series = p.model_name.split()[0]
        series_products[series].append(p)

    word_to_series: dict[str, set[str]] = defaultdict(set)
    word_to_products: dict[str, dict[str, list[Product]]] = defaultdict(lambda: defaultdict(list))

    for series, prods in series_products.items():
        for p in prods:
            for word in _descriptor_words(p.name or ""):
                word_to_series[word].add(series)
                word_to_products[word][series].append(p)

    misgroups = []
    seen: set[frozenset] = set()
    for word, series_set in word_to_series.items():
        if len(series_set) < 2:
            continue
        key = frozenset(series_set)
        if key in seen:
            continue
        seen.add(key)
        groups = [
            {"series": s, "products": word_to_products[word][s]}
            for s in sorted(series_set)
        ]
        misgroups.append({"word": word.upper(), "groups": groups})

    misgroups.sort(key=lambda x: sum(len(g["products"]) for g in x["groups"]), reverse=True)
    return misgroups[:30]


def _merge_groups(target_series: str, groups: list[dict]) -> int:
    """Scal wszystkie grupy do target_series. Zwraca liczbę zmienionych produktów."""
    count = 0
    try:
        conn = sqlite3.connect(CACHE_DB)
        for g in groups:
            if g["series"] == target_series:
                continue
            for p in g["products"]:
                parts = p.model_name.split() if p.model_name else []
                suffix = " ".join(parts[1:]) if len(parts) > 1 else ""
                new_model = f"{target_series} {suffix}".strip() if suffix else target_series
                p.model_name = new_model
                conn.execute(
                    "INSERT OR REPLACE INTO sku_model_names "
                    "(used_for_sku, brand, model_name) VALUES "
                    "(?, (SELECT brand FROM sku_model_names WHERE used_for_sku=?), ?)",
                    (p.sku, p.sku, new_model),
                )
                count += 1
        conn.commit()
        conn.close()
    except Exception:
        pass
    return count


class _MergeDialog(ctk.CTkToplevel):
    """Wybór serii docelowej przed scaleniem."""

    def __init__(self, parent, misgroup: dict):
        super().__init__(parent)
        self.title("Wybierz serię docelową")
        self.geometry("420x300")
        self.resizable(False, False)
        self.grab_set()
        self.focus_force()

        self.result: str | None = None
        self._mg = misgroup
        self._var = ctk.StringVar(value=misgroup["groups"][0]["series"])

        self._build()

    def _build(self):
        mg = self._mg
        total = sum(len(g["products"]) for g in mg["groups"])
        header = ctk.CTkFrame(self, fg_color=_DARK, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text=f"Scal grupy [{mg['word']}]  •  {total} produktów",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_DARK_TEXT,
        ).pack(padx=16, pady=10)

        ctk.CTkLabel(
            self,
            text="Wybierz serię, która zostanie DOCELOWĄ (inne zostaną do niej scalone):",
            font=ctk.CTkFont(size=11),
            text_color=_DARK,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(12, 4))

        radio_frame = ctk.CTkScrollableFrame(self, fg_color="#F9FAFB", height=120)
        radio_frame.pack(fill="x", padx=16, pady=(0, 12))

        for g in mg["groups"]:
            s = g["series"]
            n = len(g["products"])
            ctk.CTkRadioButton(
                radio_frame,
                text=f"{s}  ({n} prod.)",
                variable=self._var,
                value=s,
                text_color=_DARK,
                fg_color=_GREEN,
            ).pack(anchor="w", padx=8, pady=3)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))

        ctk.CTkButton(
            btn_row, text="Scal",
            fg_color=_GREEN, hover_color=_GREEN_HOVER,
            command=self._confirm,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Anuluj",
            fg_color=_DARK_BTN, hover_color=_DARK_HOVER,
            command=self.destroy,
        ).pack(side="left")

    def _confirm(self):
        self.result = self._var.get()
        self.destroy()


class ModelAuditWindow(ctk.CTkToplevel):
    """Okno audytu serii modelowych — wykrywa i scala błędne grupy."""

    def __init__(self, parent, products: list[Product], on_done: Callable | None = None):
        super().__init__(parent)
        self.title("Audyt modeli")
        self.geometry("920x640")
        self.resizable(True, True)
        self.grab_set()
        self.focus_force()

        self._products = products
        self._on_done = on_done
        self._misgroups = _detect_misgroups(products)
        self._selected_series: str | None = None

        self._build()

    def _build(self):
        # Header
        header = ctk.CTkFrame(self, fg_color=_DARK, corner_radius=0)
        header.pack(fill="x")

        n_with_model = sum(1 for p in self._products if p.model_name)
        self._header_label = ctk.CTkLabel(
            header,
            text=self._header_text(),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_DARK_TEXT,
        )
        self._header_label.pack(side="left", padx=16, pady=10)

        panes = ctk.CTkFrame(self, fg_color="transparent")
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=3)
        panes.columnconfigure(1, weight=2)
        panes.rowconfigure(0, weight=1)

        left_frame = ctk.CTkFrame(panes, fg_color="#1A2332", corner_radius=0)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 1))
        ctk.CTkLabel(left_frame, text="Podejrzane grupy",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#94A3B8").pack(anchor="w", padx=12, pady=(10, 4))
        self._misgroup_scroll = ctk.CTkScrollableFrame(left_frame, fg_color="transparent")
        self._misgroup_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        right_frame = ctk.CTkFrame(panes, fg_color="#161D29", corner_radius=0)
        right_frame.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(right_frame, text="Wszystkie serie",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#94A3B8").pack(anchor="w", padx=12, pady=(10, 4))
        self._series_scroll = ctk.CTkScrollableFrame(right_frame, fg_color="transparent")
        self._series_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        preview_frame = ctk.CTkFrame(self, fg_color="#111827", corner_radius=0)
        preview_frame.pack(fill="x", side="bottom")
        ctk.CTkLabel(preview_frame, text="Podgląd serii",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#94A3B8").pack(anchor="w", padx=12, pady=(8, 2))
        self._preview_scroll = ctk.CTkScrollableFrame(
            preview_frame, fg_color="transparent", height=120)
        self._preview_scroll.pack(fill="x", padx=8, pady=(0, 4))

        btn_bar = ctk.CTkFrame(self, fg_color=_DARK, corner_radius=0)
        btn_bar.pack(fill="x", side="bottom")

        ctk.CTkButton(
            btn_bar, text="Odśwież",
            fg_color=_DARK_BTN, hover_color=_DARK_HOVER,
            command=self._refresh,
        ).pack(side="right", padx=8, pady=8)
        ctk.CTkButton(
            btn_bar, text="Zamknij",
            fg_color=_DARK_BTN, hover_color=_DARK_HOVER,
            command=self.destroy,
        ).pack(side="right", padx=(0, 4), pady=8)

        self._populate_misgroups()
        self._populate_series()

    def _header_text(self) -> str:
        n_mg = len(self._misgroups)
        n_model = sum(1 for p in self._products if p.model_name)
        return f"{n_mg} podejrzanych grup  |  Produkty z modelem: {n_model}"

    def _series_map(self) -> dict[str, list[Product]]:
        groups: dict[str, list[Product]] = defaultdict(list)
        for p in self._products:
            if not p.model_name:
                continue
            series = p.model_name.split()[0]
            groups[series].append(p)
        return dict(groups)

    def _populate_misgroups(self):
        for w in self._misgroup_scroll.winfo_children():
            w.destroy()

        if not self._misgroups:
            ctk.CTkLabel(
                self._misgroup_scroll,
                text="Brak podejrzanych grup. Wszystko wygląda OK.",
                text_color="#6B7280",
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=8, pady=16)
            return

        for mg in self._misgroups:
            self._add_misgroup_row(mg)

    def _add_misgroup_row(self, mg: dict):
        total = sum(len(g["products"]) for g in mg["groups"])
        row = ctk.CTkFrame(
            self._misgroup_scroll,
            fg_color="#1E293B",
            corner_radius=6,
        )
        row.pack(fill="x", padx=2, pady=3)

        # Słowo kluczowe
        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(6, 2))

        ctk.CTkLabel(
            top,
            text=f"[{mg['word']}]",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#FBBF24",
            width=80,
            anchor="w",
        ).pack(side="left")

        # Serie
        series_parts = []
        for g in mg["groups"]:
            series_parts.append(f"{g['series']} ({len(g['products'])})")
        series_txt = "  +  ".join(series_parts)
        ctk.CTkLabel(
            top,
            text=series_txt,
            font=ctk.CTkFont(size=11),
            text_color=_DARK_TEXT,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            top,
            text="Scal ▾",
            fg_color=_GREEN, hover_color=_GREEN_HOVER,
            width=70, height=24,
            font=ctk.CTkFont(size=11),
            command=lambda m=mg: self._merge_dialog(m),
        ).pack(side="right", padx=(4, 0))

    def _populate_series(self):
        for w in self._series_scroll.winfo_children():
            w.destroy()

        smap = self._series_map()
        if not smap:
            ctk.CTkLabel(
                self._series_scroll,
                text="Brak produktów z modelem.",
                text_color="#6B7280",
                font=ctk.CTkFont(size=11),
            ).pack(anchor="w", padx=8, pady=8)
            return

        brand_cache: dict[str, str] = {}
        for p in self._products:
            if p.model_name and p.brand:
                s = p.model_name.split()[0]
                brand_cache.setdefault(s, p.brand)

        # Header row
        hrow = ctk.CTkFrame(self._series_scroll, fg_color="transparent")
        hrow.pack(fill="x", padx=4, pady=(2, 4))
        for txt, w in (("Seria", 110), ("Brand", 80), ("Prod.", 40)):
            ctk.CTkLabel(
                hrow, text=txt, width=w, anchor="w",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="#6B7280",
            ).pack(side="left", padx=2)

        for series in sorted(smap.keys()):
            prods = smap[series]
            brand = brand_cache.get(series, "")
            srow = ctk.CTkFrame(
                self._series_scroll,
                fg_color="transparent",
                cursor="hand2",
            )
            srow.pack(fill="x", padx=2, pady=1)

            ctk.CTkLabel(
                srow, text=series, width=110, anchor="w",
                font=ctk.CTkFont(size=11),
                text_color=_DARK_TEXT,
            ).pack(side="left", padx=2)
            ctk.CTkLabel(
                srow, text=brand, width=80, anchor="w",
                font=ctk.CTkFont(size=10),
                text_color="#9CA3AF",
            ).pack(side="left", padx=2)
            ctk.CTkLabel(
                srow, text=str(len(prods)), width=40, anchor="w",
                font=ctk.CTkFont(size=11),
                text_color="#60A5FA",
            ).pack(side="left", padx=2)

            srow.bind("<Button-1>", lambda e, s=series: self._select_series(s))
            for child in srow.winfo_children():
                child.bind("<Button-1>", lambda e, s=series: self._select_series(s))

    def _select_series(self, series: str):
        self._selected_series = series
        self._show_preview(series)

    def _show_preview(self, series: str):
        for w in self._preview_scroll.winfo_children():
            w.destroy()

        smap = self._series_map()
        prods = smap.get(series, [])
        if not prods:
            return

        # Column header
        hrow = ctk.CTkFrame(self._preview_scroll, fg_color="transparent")
        hrow.pack(fill="x", pady=(0, 2))
        for txt, width in (("SKU", 120), ("Nazwa", 300), ("Model", 160)):
            ctk.CTkLabel(
                hrow, text=txt, width=width, anchor="w",
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="#6B7280",
            ).pack(side="left", padx=2)

        for p in prods[:50]:
            short_name = (p.name or "")[:55] + ("…" if len(p.name or "") > 55 else "")
            prow = ctk.CTkFrame(self._preview_scroll, fg_color="transparent")
            prow.pack(fill="x", pady=1)
            ctk.CTkLabel(
                prow, text=p.sku, width=120, anchor="w",
                font=ctk.CTkFont(size=10),
                text_color="#9CA3AF",
            ).pack(side="left", padx=2)
            ctk.CTkLabel(
                prow, text=short_name, width=300, anchor="w",
                font=ctk.CTkFont(size=10),
                text_color=_DARK_TEXT,
            ).pack(side="left", padx=2)
            ctk.CTkLabel(
                prow, text=p.model_name or "—", width=160, anchor="w",
                font=ctk.CTkFont(size=10),
                text_color="#60A5FA",
            ).pack(side="left", padx=2)

        if len(prods) > 50:
            ctk.CTkLabel(
                self._preview_scroll,
                text=f"… i {len(prods) - 50} więcej",
                text_color="#6B7280",
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w", padx=4)

    def _merge_dialog(self, misgroup: dict):
        dlg = _MergeDialog(self, misgroup)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        target = dlg.result
        count = _merge_groups(target, misgroup["groups"])
        messagebox.showinfo(
            "Scalanie zakończone",
            f"Zmieniono {count} produktów → seria {target}.",
            parent=self,
        )
        self._refresh()
        if self._on_done:
            self._on_done()

    def _refresh(self):
        self._misgroups = _detect_misgroups(self._products)
        self._header_label.configure(text=self._header_text())
        self._populate_misgroups()
        self._populate_series()
        if self._selected_series:
            self._show_preview(self._selected_series)
