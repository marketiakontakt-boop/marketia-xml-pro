"""Variant groups view — shows products grouped by model series.

Allows editing variant names and exporting a BaseLinker-ready XML
where products in the same series share a variant_group_id.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from tkinter import filedialog, messagebox

from app.parser.normalizer import Product
from app.transformer.variant_grouper import assign_variant_groups, detect_variant_groups, _base_and_variant
from app.gui.brand_colors import get_brand_chip_colors

_GROUP_COLORS = [
    "#DBEAFE", "#DCFCE7", "#FEF9C3", "#FCE7F3",
    "#FFEDD5", "#EDE9FE", "#E0F2FE", "#FEE2E2",
]


class VariantViewWindow(ctk.CTkToplevel):
    """Read/edit variant groups, then export or close."""

    def __init__(self, parent, products: list[Product], on_done: Callable | None = None):
        super().__init__(parent)
        self.title("Warianty produktów")
        self.geometry("900x660")
        self.minsize(700, 500)
        self.grab_set()
        self.focus_force()

        self._products = products
        self._on_done = on_done

        n_groups = assign_variant_groups(products)
        self._groups = detect_variant_groups(products)

        self._build(n_groups)

    # ------------------------------------------------------------------

    def _build(self, n_groups: int):
        # Header
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            top,
            text=f"Grupy wariantowe ({n_groups})",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")

        standalone = sum(1 for p in self._products if p.variant_group_id == 0 and p.model_name)
        ctk.CTkLabel(
            top,
            text=f"  •  Samodzielne: {standalone}  •  Bez modelu: {sum(1 for p in self._products if not p.model_name)}",
            text_color="#6B7280",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            top, text="Eksportuj XML z wariantami", width=200,
            fg_color="#0a5c99", hover_color="#074880",
            command=self._export,
        ).pack(side="right")

        ctk.CTkLabel(
            self,
            text="Edytuj nazwy wariantów (kolor/rozmiar). Produkty z tą samą nazwą bazową modelu są łączone w grupę.",
            text_color="#6B7280", font=ctk.CTkFont(size=11),
            wraplength=860,
        ).pack(anchor="w", padx=20, pady=(0, 8))

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(self, fg_color="#F9FAFB")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        scroll.grid_columnconfigure(0, weight=1)

        # Render each group
        for gidx, (key, members) in enumerate(sorted(self._groups.items())):
            brand, base = key.split("::", 1)
            bg = _GROUP_COLORS[gidx % len(_GROUP_COLORS)]
            self._render_group(scroll, gidx, brand, base, members, bg)

        # Standalone products section (collapsed)
        standalone_list = [p for p in self._products if p.variant_group_id == 0 and p.model_name]
        if standalone_list:
            sep = ctk.CTkFrame(scroll, fg_color="#E5E7EB", height=1)
            sep.pack(fill="x", pady=(12, 4))
            ctk.CTkLabel(
                scroll,
                text=f"Samodzielne produkty ({len(standalone_list)}) — brak wariantów",
                text_color="#9CA3AF", font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(anchor="w", padx=4, pady=(0, 4))
            for p in standalone_list[:20]:
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", padx=4, pady=1)
                ctk.CTkLabel(row, text=p.sku, width=110, text_color="#9CA3AF",
                             font=ctk.CTkFont(size=10), anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=p.model_name, text_color="#374151",
                             font=ctk.CTkFont(size=10), anchor="w").pack(side="left")
            if len(standalone_list) > 20:
                ctk.CTkLabel(scroll, text=f"… i {len(standalone_list)-20} więcej",
                             text_color="#9CA3AF", font=ctk.CTkFont(size=10)).pack(anchor="w", padx=4)

        # Close button
        ctk.CTkButton(
            self, text="Zamknij", fg_color="#374151", hover_color="#1f2937",
            command=self.destroy,
        ).pack(pady=(0, 16))

    def _render_group(self, parent, gidx: int, brand: str, base: str, members: list[Product], bg: str):
        chip_bg, chip_fg = get_brand_chip_colors(brand)

        frame = ctk.CTkFrame(parent, fg_color=bg, corner_radius=8)
        frame.pack(fill="x", pady=4)

        # Group header
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(8, 4))

        ctk.CTkLabel(
            header,
            text=f"#{gidx + 1}",
            width=28, text_color="#6B7280",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text=brand.upper(),
            fg_color=chip_bg, text_color=chip_fg,
            corner_radius=4, font=ctk.CTkFont(size=10, weight="bold"),
            padx=6, pady=2,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkLabel(
            header,
            text=f"Seria: {base}  •  {len(members)} wariantów",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")

        # Product rows
        for p in members:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)

            ctk.CTkLabel(row, text=p.sku, width=110,
                         text_color="#6B7280", font=ctk.CTkFont(size=10), anchor="w").pack(side="left")

            ctk.CTkLabel(row, text=p.title or p.name, width=320,
                         text_color="#1F2937", font=ctk.CTkFont(size=10), anchor="w").pack(side="left")

            ctk.CTkLabel(row, text="Wariant:", text_color="#6B7280",
                         font=ctk.CTkFont(size=10)).pack(side="left", padx=(8, 2))

            var_var = ctk.StringVar(value=p.variant_name)

            def _on_change(val, product=p, sv=var_var):
                product.variant_name = sv.get()

            entry = ctk.CTkEntry(row, textvariable=var_var, width=150,
                                 font=ctk.CTkFont(size=10))
            entry.pack(side="left")
            var_var.trace_add("write", lambda *a, sv=var_var, pp=p: setattr(pp, "variant_name", sv.get()))

        ctk.CTkLabel(frame, text="", height=4).pack()  # bottom padding

    # ------------------------------------------------------------------

    def _export(self):
        from app.exporter.xml_exporter import export_xml

        output_dir = Path(__file__).resolve().parents[2] / "output"
        path = filedialog.asksaveasfilename(
            title="Zapisz XML z wariantami",
            initialdir=str(output_dir),
            defaultextension=".xml",
            filetypes=[("XML", "*.xml")],
            initialfile="marketia_variants.xml",
        )
        if not path:
            return

        try:
            count = export_xml(self._products, path, include_variants=True)
            messagebox.showinfo("Eksport", f"Zapisano {count} produktów z wariantami.\n{path}", parent=self)
            if self._on_done:
                self._on_done()
        except Exception as e:
            messagebox.showerror("Błąd eksportu", str(e), parent=self)
