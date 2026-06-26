"""Model series rename window.

Allows renaming a model series consistently across all color variants.
Example: "EVA" → "MILAN"
  Eva Białe   →  Milan Białe
  Eva Żółte   →  Milan Żółte
  EVA BEIGE   →  MILAN BEIGE

Also updates the SQLite cache (used_model_names) so future runs stay consistent.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Callable



import customtkinter as ctk
from tkinter import messagebox

from app.parser.normalizer import Product

CACHE_DB = Path(__file__).resolve().parents[2] / "cache" / "marketia.db"


def _extract_series(model_name: str) -> tuple[str, str]:
    """Split 'EVA BIAŁE' → ('EVA', 'BIAŁE').  Strips pool-exhaustion numeric suffix (-2, -10…)."""
    parts = model_name.strip().split(None, 1)
    base = re.sub(r"-\d+$", "", parts[0])
    return base, parts[1] if len(parts) > 1 else ""


def _build_series_map(products: list[Product]) -> dict[str, list[Product]]:
    """Group products by the first word of their model_name (the 'series' key)."""
    groups: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if not p.model_name:
            continue
        base, _ = _extract_series(p.model_name)
        groups[base.upper()].append(p)
    return dict(groups)


def _apply_rename(
    products: list[Product],
    old_base: str,
    new_base: str,
) -> list[Product]:
    """Rename model_name and replace the old base name in descriptions. Returns affected list."""
    old_up = old_base.upper()
    pattern = re.compile(
        r'(?<![A-Za-z0-9_])' + re.escape(old_base) + r'(?![A-Za-z0-9_])',
        re.IGNORECASE,
    )
    affected = []
    for p in products:
        if not p.model_name:
            continue
        base, suffix = _extract_series(p.model_name)
        if base.upper() != old_up:
            continue
        # Update model_name — match case style from original
        if base.isupper():
            p.model_name = new_base.upper() + (" " + suffix if suffix else "")
        elif base.istitle():
            p.model_name = new_base.capitalize() + (" " + suffix if suffix else "")
        else:
            p.model_name = new_base + (" " + suffix if suffix else "")
        # Replace old name in title and all description fields
        for field in ("title", "description", "description_extra_1", "description_extra_2"):
            val = getattr(p, field, "") or ""
            if val:
                setattr(p, field, pattern.sub(new_base, val))
        affected.append(p)
    return affected


def _update_cache(old_base: str, new_base: str, affected: list[Product]):
    """Update sku_model_names in SQLite so future transform runs stay consistent."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        for p in affected:
            conn.execute(
                "INSERT OR REPLACE INTO sku_model_names (used_for_sku, brand, model_name) "
                "VALUES (?, (SELECT brand FROM sku_model_names WHERE used_for_sku = ?), ?)",
                (p.sku, p.sku, p.model_name),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass  # non-fatal — user can re-transform


class ModelRenameWindow(ctk.CTkToplevel):
    """Dialog for renaming a model series consistently."""

    def __init__(self, parent, products: list[Product], on_done: Callable | None = None):
        super().__init__(parent)
        self.title("Zmień model serii")
        self.geometry("680x560")
        self.resizable(False, True)
        self.grab_set()
        self.focus_force()

        self._products = products
        self._on_done = on_done
        self._series_map = _build_series_map(products)
        self._selected_base: str | None = None

        self._build()
        # Auto-select when there's only one series (e.g. user pre-selected products)
        if len(self._series_map) == 1:
            self._select_series(next(iter(self._series_map)))

    # ------------------------------------------------------------------

    def _build(self):
        # Left: series list
        left = ctk.CTkFrame(self, width=200, fg_color="#F3F4F6", corner_radius=0)
        left.pack(side="left", fill="y", padx=0, pady=0)
        left.pack_propagate(False)

        ctk.CTkLabel(
            left, text="Serie modeli",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(pady=(12, 4), padx=8)

        self._series_scroll = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._series_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        self._series_btns: dict[str, ctk.CTkButton] = {}
        for base in sorted(self._series_map.keys()):
            count = len(self._series_map[base])
            btn = ctk.CTkButton(
                self._series_scroll,
                text=f"{base}  ({count})",
                anchor="w",
                fg_color="transparent",
                text_color="#1F2937",
                hover_color="#E5E7EB",
                command=lambda b=base: self._select_series(b),
            )
            btn.pack(fill="x", padx=2, pady=1)
            self._series_btns[base] = btn

        # Right: rename panel
        right = ctk.CTkFrame(self, fg_color="white")
        right.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            right, text="Zmiana nazwy serii",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(20, 4))

        self._selected_label = ctk.CTkLabel(
            right, text="← Wybierz serię z listy",
            text_color="#9CA3AF",
            font=ctk.CTkFont(size=12),
        )
        self._selected_label.pack(anchor="w", padx=20, pady=(0, 12))

        # Rename form
        form = ctk.CTkFrame(right, fg_color="#F9FAFB", corner_radius=8)
        form.pack(fill="x", padx=20, pady=(0, 8))

        row1 = ctk.CTkFrame(form, fg_color="transparent")
        row1.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(row1, text="Stara nazwa serii:", width=160, anchor="w").pack(side="left")
        self._old_name_label = ctk.CTkLabel(row1, text="—", font=ctk.CTkFont(weight="bold"), anchor="w")
        self._old_name_label.pack(side="left")

        row2 = ctk.CTkFrame(form, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkLabel(row2, text="Nowa nazwa serii:", width=160, anchor="w").pack(side="left")
        self._new_name_var = ctk.StringVar()
        ctk.CTkEntry(row2, textvariable=self._new_name_var, width=220,
                     placeholder_text="np. Milan").pack(side="left")

        # Preview
        ctk.CTkLabel(
            right, text="Podgląd zmian:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(8, 2))

        self._preview_frame = ctk.CTkScrollableFrame(right, fg_color="#FAFAFA", height=200)
        self._preview_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        self._new_name_var.trace_add("write", lambda *_: self._refresh_preview())

        # Buttons
        btn_row = ctk.CTkFrame(right, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(4, 16))

        ctk.CTkButton(
            btn_row, text="Zastosuj zmianę",
            fg_color="#1a6f3a", hover_color="#145c2f",
            command=self._apply,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row, text="Zamknij",
            fg_color="#374151", hover_color="#1f2937",
            command=self.destroy,
        ).pack(side="left")

    # ------------------------------------------------------------------

    def _select_series(self, base: str):
        self._selected_base = base
        self._selected_label.configure(
            text=f"Wybrana seria: {base}  •  {len(self._series_map[base])} produktów",
            text_color="#1F2937",
        )
        self._old_name_label.configure(text=base)
        # Highlight button
        for b, btn in self._series_btns.items():
            btn.configure(
                fg_color="#DBEAFE" if b == base else "transparent",
                text_color="#1D4ED8" if b == base else "#1F2937",
            )
        self._refresh_preview()

    def _refresh_preview(self):
        for w in self._preview_frame.winfo_children():
            w.destroy()

        if not self._selected_base:
            return

        new_base = self._new_name_var.get().strip()
        products = self._series_map[self._selected_base]

        for p in products[:40]:
            base, suffix = _extract_series(p.model_name)
            if new_base:
                if base.isupper():
                    new_model = new_base.upper() + (" " + suffix if suffix else "")
                elif base.istitle():
                    new_model = new_base.capitalize() + (" " + suffix if suffix else "")
                else:
                    new_model = new_base + (" " + suffix if suffix else "")
                arrow = f"{p.model_name}  →  {new_model}"
                color = "#15803D"
            else:
                arrow = p.model_name
                color = "#6B7280"

            row = ctk.CTkFrame(self._preview_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(
                row, text=p.sku, width=110,
                text_color="#9CA3AF", font=ctk.CTkFont(size=10), anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=arrow,
                text_color=color, font=ctk.CTkFont(size=11), anchor="w",
            ).pack(side="left")

        if len(products) > 40:
            ctk.CTkLabel(
                self._preview_frame,
                text=f"… i {len(products) - 40} więcej",
                text_color="#9CA3AF",
                font=ctk.CTkFont(size=10),
            ).pack(anchor="w")

    # ------------------------------------------------------------------

    def _apply(self):
        if not self._selected_base:
            messagebox.showwarning("Brak wyboru", "Wybierz serię z listy.", parent=self)
            return

        new_base = self._new_name_var.get().strip()
        if not new_base:
            messagebox.showwarning("Brak nazwy", "Wpisz nową nazwę serii.", parent=self)
            return

        if new_base.upper() == self._selected_base.upper():
            messagebox.showinfo("Bez zmian", "Nowa nazwa jest taka sama jak stara.", parent=self)
            return

        affected = _apply_rename(self._products, self._selected_base, new_base)
        _update_cache(self._selected_base, new_base, affected)

        # Rebuild series map after rename
        self._series_map = _build_series_map(self._products)

        # Refresh sidebar list
        for w in self._series_scroll.winfo_children():
            w.destroy()
        self._series_btns.clear()
        for base in sorted(self._series_map.keys()):
            count = len(self._series_map[base])
            btn = ctk.CTkButton(
                self._series_scroll,
                text=f"{base}  ({count})",
                anchor="w",
                fg_color="transparent",
                text_color="#1F2937",
                hover_color="#E5E7EB",
                command=lambda b=base: self._select_series(b),
            )
            btn.pack(fill="x", padx=2, pady=1)
            self._series_btns[base] = btn

        self._selected_base = None
        self._selected_label.configure(text="Zmiana zastosowana ✓", text_color="#15803D")
        self._old_name_label.configure(text="—")
        self._new_name_var.set("")
        self._refresh_preview()

        self._selected_label.configure(
            text=f"Zmieniono {len(affected)} produktów ✓",
            text_color="#15803D",
        )

        if self._on_done:
            self._on_done()
