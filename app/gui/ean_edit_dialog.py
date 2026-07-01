"""Multi-EAN editor — append extra EAN-13/EAN-8 codes for product cloning.

Original `product.ean` stays as the base offer. Each valid extra EAN becomes
an XML clone on export (suffixed SKU/product_id, same description/images/stock).
Persisted in SQLite `product_eans` table — survives XML re-load.
"""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from app.cache.sqlite_cache import open_cache, set_extra_eans
from app.parser.normalizer import Product
from app.validator.ean_validator import validate_ean

_DARK = "#1F2937"
_OK = "#1f883d"
_BAD = "#d1242f"
_DUP = "#a16207"
_MUTED = "#9CA3AF"


class EanEditDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        product: Product,
        on_done: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self._product = product
        self._on_done = on_done

        self.title(f"EAN-y dla {product.sku}")
        self.geometry("520x620")
        self.minsize(520, 560)
        self.resizable(False, True)
        self.configure(fg_color=_DARK)
        self.grab_set()
        self.after(50, self.lift)

        ctk.CTkLabel(
            self,
            text=f"Oryginalny EAN (pozostaje produktem bazowym): {product.ean or '—'}",
            text_color=_MUTED, font=ctk.CTkFont(size=11), anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            self,
            text="Dodatkowe EAN-y — po jednym w linii. Każdy = osobny klon w XML.",
            text_color="#E5E7EB", font=ctk.CTkFont(size=12), anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 4))

        ctk.CTkLabel(
            self,
            text="⚠ Klony to NIEZALEŻNE produkty w BaseLinker — każdy ma własny stock. Konfigurację synchronizacji magazynu robisz osobno w panelu BaseLinker (Multi-EAN: + więcej przy polu EAN).  Patrz INSTRUKCJA_MULTI_EAN.md.",
            text_color="#FCD34D", font=ctk.CTkFont(size=10), anchor="w",
            wraplength=480, justify="left",
        ).pack(fill="x", padx=16, pady=(0, 6))

        self._textbox = ctk.CTkTextbox(
            self, width=488, height=260,
            font=ctk.CTkFont(family="Menlo", size=12),
            fg_color="#111827", text_color="#F9FAFB",
        )
        self._textbox.pack(padx=16, pady=(0, 6))
        self._textbox.insert("1.0", "\n".join(product.extra_eans))
        self._textbox.bind("<KeyRelease>", lambda _e: self._refresh_status())

        self._status_box = ctk.CTkTextbox(
            self, width=488, height=120,
            font=ctk.CTkFont(family="Menlo", size=11),
            fg_color="#0B1220", text_color="#D1D5DB",
        )
        self._status_box.pack(padx=16, pady=(0, 6))
        self._status_box.configure(state="disabled")

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(2, 12))
        ctk.CTkButton(btn_row, text="Zapisz", width=110, command=self._save).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="Wyczyść", width=90,
            fg_color="transparent", border_width=1, border_color="#6B7280",
            command=self._clear_all,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="Anuluj", width=80,
            fg_color="transparent", border_width=1, border_color="#6B7280",
            command=self.destroy,
        ).pack(side="left", padx=6)

        self.bind("<Escape>", lambda _e: self.destroy())
        self._refresh_status()

    # ── internals ───────────────────────────────────────────────────────────

    def _parse_lines(self) -> list[str]:
        raw = self._textbox.get("1.0", "end")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _classify(self, lines: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
        """Return (valid_unique_eans, [(line, reason) for rejected])."""
        valid: list[str] = []
        seen: set[str] = set()
        rejected: list[tuple[str, str]] = []
        original = (self._product.ean or "").strip()
        for line in lines:
            if line == original:
                rejected.append((line, "duplikat oryginalnego EAN"))
                continue
            if line in seen:
                rejected.append((line, "duplikat w tej liście"))
                continue
            if not validate_ean(line):
                rejected.append((line, "nieprawidłowy checksum / format"))
                continue
            valid.append(line)
            seen.add(line)
        return valid, rejected

    def _refresh_status(self) -> None:
        lines = self._parse_lines()
        valid, rejected = self._classify(lines)

        self._status_box.configure(state="normal")
        self._status_box.delete("1.0", "end")
        self._status_box.insert("end", f"Klonów do utworzenia: {len(valid)}\n")
        if valid:
            self._status_box.insert("end", "✓ " + ", ".join(valid) + "\n")
        for line, reason in rejected:
            self._status_box.insert("end", f"✗ {line} — {reason}\n")
        self._status_box.configure(state="disabled")

    def _clear_all(self) -> None:
        self._textbox.delete("1.0", "end")
        self._refresh_status()

    def _save(self) -> None:
        lines = self._parse_lines()
        valid, _ = self._classify(lines)
        with open_cache() as conn:
            set_extra_eans(conn, self._product.sku, valid)
        self._product.extra_eans = list(valid)
        if self._on_done:
            self._on_done()
        self.destroy()
