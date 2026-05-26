"""Category Mapper Window — edit BaseLinker → Allegro category mapping."""
from __future__ import annotations

import json
from pathlib import Path

import customtkinter as ctk

from app.transformer.category_mapper import load_category_map, suggest_category_gemini

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "allegro_categories.json"


class CategoryMapperWindow(ctk.CTkToplevel):
    """Non-blocking window for editing BaseLinker → Allegro category mapping."""

    def __init__(self, parent, products, on_save=None):
        super().__init__(parent)
        self.title("Mapa kategorii Allegro")
        self.geometry("900x600")
        self.minsize(700, 400)
        self._on_save = on_save

        # Collect unique BaseLinker categories from loaded products
        self._bl_cats = sorted({p.category_name for p in products if p.category_name})
        self._cat_map = load_category_map()
        self._entries: dict[str, ctk.CTkEntry] = {}

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Toolbar
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        ctk.CTkLabel(toolbar, text="BaseLinker kategoria → Allegro ścieżka",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkButton(toolbar, text="Sugeruj brakujące (AI)", width=180,
                      command=self._suggest_missing,
                      fg_color="#1a6f3a", hover_color="#145c2f").pack(side="right", padx=(8, 0))
        ctk.CTkButton(toolbar, text="Zapisz mapę", width=120,
                      command=self._save).pack(side="right")

        # Scrollable table
        frame = ctk.CTkScrollableFrame(self, label_text="")
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(2, minsize=80)

        # Header
        for col, text in enumerate(["BaseLinker kategoria", "Allegro ścieżka", "Status"]):
            ctk.CTkLabel(frame, text=text, font=ctk.CTkFont(weight="bold"),
                         text_color="#6B7280").grid(row=0, column=col, sticky="w", padx=8, pady=4)

        for row_idx, bl_cat in enumerate(self._bl_cats, 1):
            current = self._cat_map.get(bl_cat, "")

            ctk.CTkLabel(frame, text=bl_cat, anchor="w", wraplength=380).grid(
                row=row_idx, column=0, sticky="w", padx=8, pady=2)

            entry = ctk.CTkEntry(frame, width=340)
            entry.insert(0, current)
            entry.grid(row=row_idx, column=1, sticky="ew", padx=8, pady=2)
            self._entries[bl_cat] = entry

            status_text = "✓" if current else "?"
            status_color = "#15803D" if current else "#EA580C"
            ctk.CTkLabel(frame, text=status_text, text_color=status_color,
                         font=ctk.CTkFont(weight="bold")).grid(
                row=row_idx, column=2, padx=8, pady=2)

    def _suggest_missing(self):
        for bl_cat, entry in self._entries.items():
            if not entry.get().strip():
                try:
                    suggestion = suggest_category_gemini(bl_cat)
                    entry.delete(0, "end")
                    entry.insert(0, suggestion)
                except Exception:
                    pass

    def _save(self):
        updated = dict(self._cat_map)
        for bl_cat, entry in self._entries.items():
            val = entry.get().strip()
            if val:
                updated[bl_cat] = val
            elif bl_cat in updated:
                del updated[bl_cat]
        with open(_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(updated, f, ensure_ascii=False, indent=2)
        if self._on_save:
            self._on_save(updated)
        self.destroy()
