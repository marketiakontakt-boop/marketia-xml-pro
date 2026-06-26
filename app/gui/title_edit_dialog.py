"""Inline title editor — single-product title manual override."""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from app.parser.normalizer import Product

_MAX = 75
_WARN = 70
_DARK = "#1F2937"


class TitleEditDialog(ctk.CTkToplevel):
    def __init__(self, master, product: Product, on_done: Callable[[], None] | None = None):
        super().__init__(master)
        self._product = product
        self._on_done = on_done

        self.title("Edytuj tytuł")
        self.geometry("560x200")
        self.resizable(False, False)
        self.configure(fg_color=_DARK)
        self.grab_set()
        self.after(50, self.lift)

        ctk.CTkLabel(
            self, text=f"SKU: {product.sku}", anchor="w",
            text_color="#9CA3AF", font=ctk.CTkFont(size=11),
        ).pack(fill="x", padx=16, pady=(12, 2))

        self._var = ctk.StringVar(value=product.title or "")
        entry = ctk.CTkEntry(
            self, textvariable=self._var, width=528,
            font=ctk.CTkFont(size=13),
        )
        entry.pack(padx=16, pady=(0, 4))
        entry.focus()
        entry.icursor("end")
        entry.bind("<Return>", lambda _e: self._save())
        entry.bind("<Escape>", lambda _e: self.destroy())

        self._counter = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=11),
        )
        self._counter.pack(fill="x", padx=16)
        self._var.trace_add("write", lambda *_: self._update_counter())
        self._update_counter()

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(8, 12))
        ctk.CTkButton(btn_row, text="Zapisz", width=100, command=self._save).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="Anuluj", width=80,
            fg_color="transparent", border_width=1, border_color="#6B7280",
            command=self.destroy,
        ).pack(side="left", padx=6)

    def _update_counter(self) -> None:
        n = len(self._var.get())
        color = "#EF4444" if n > _MAX else ("#F59E0B" if n > _WARN else "#6EE7B7")
        self._counter.configure(text=f"{n} / {_MAX} znaków", text_color=color)

    def _save(self) -> None:
        self._product.title = self._var.get().strip()
        if self._on_done:
            self._on_done()
        self.destroy()
