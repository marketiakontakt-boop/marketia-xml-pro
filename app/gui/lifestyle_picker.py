"""Lifestyle AI Thumbnail — brand selector dialog."""
from __future__ import annotations

import threading

import customtkinter as ctk
from tkinter import messagebox

from app.images.lifestyle_composer import generate_lifestyle_thumbnails, _BRAND_SCENES
from app.gui.brand_colors import get_brand_chip_colors
from app.parser.normalizer import Product


class LifestylePickerWindow(ctk.CTkToplevel):
    """Dialog: pick which brands to generate AI lifestyle thumbnails for."""

    def __init__(self, parent, products: list[Product], on_done=None):
        super().__init__(parent)
        self.title("Lifestyle AI Thumbnails")
        self.geometry("480x460")
        self.resizable(False, False)
        self.grab_set()
        self.focus_force()

        self._products = products
        self._on_done = on_done

        # Brands that have scene prompts defined and have products with images
        all_brands = sorted({
            p.brand for p in products
            if p.brand and getattr(p, "images", [])
        })
        self._brands = all_brands

        self._enabled: dict[str, ctk.BooleanVar] = {
            b: ctk.BooleanVar(value=b.lower() in _BRAND_SCENES)
            for b in all_brands
        }
        self._force_var = ctk.BooleanVar(value=False)

        self._build()

    def _build(self):
        ctk.CTkLabel(
            self,
            text="Lifestyle AI Thumbnails",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            self,
            text="Imagen 4 generuje tło z kontekstem (ludzie, ogród, wnętrze).\n"
                 "rembg wycina produkt z oryginału i nakłada go na scenę.",
            text_color="#6B7280",
            font=ctk.CTkFont(size=11),
            justify="center",
        ).pack(pady=(0, 12))

        # Brand list
        frame = ctk.CTkScrollableFrame(self, fg_color="#F3F4F6", corner_radius=8, height=240)
        frame.pack(fill="x", padx=20, pady=(0, 8))

        if not self._brands:
            ctk.CTkLabel(
                frame,
                text="Brak produktów ze zdjęciami.",
                text_color="#9CA3AF",
            ).pack(pady=20)
        else:
            for brand in self._brands:
                count = sum(1 for p in self._products if p.brand == brand and getattr(p, "images", []))
                has_scene = brand.lower() in _BRAND_SCENES
                row = ctk.CTkFrame(frame, fg_color="transparent")
                row.pack(fill="x", padx=8, pady=4)

                bg, fg = get_brand_chip_colors(brand)
                ctk.CTkLabel(
                    row,
                    text=brand.upper(),
                    fg_color=bg,
                    text_color=fg,
                    corner_radius=4,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    padx=8,
                ).pack(side="left")

                ctk.CTkLabel(
                    row,
                    text=f"  {count} prod.",
                    text_color="#374151",
                    font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=4)

                if not has_scene:
                    ctk.CTkLabel(
                        row,
                        text="(domyślna scena)",
                        text_color="#9CA3AF",
                        font=ctk.CTkFont(size=10),
                    ).pack(side="left", padx=4)

                ctk.CTkCheckBox(
                    row,
                    text="",
                    variable=self._enabled[brand],
                    width=24,
                ).pack(side="right")

        # Force checkbox
        ctk.CTkCheckBox(
            self,
            text="Regeneruj nawet jeśli już istnieje",
            variable=self._force_var,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(4, 0))

        ctk.CTkLabel(
            self,
            text="Uwaga: każde zdjęcie = 1 call Imagen 4 (zużywa kredyty).",
            text_color="#B45309",
            font=ctk.CTkFont(size=10),
        ).pack(pady=(4, 8))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(0, 16))

        self._gen_btn = ctk.CTkButton(
            btn_row,
            text="Generuj lifestyle AI",
            fg_color="#0891B2",
            hover_color="#0e7490",
            command=self._start,
        )
        self._gen_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Anuluj",
            fg_color="#374151",
            hover_color="#1f2937",
            command=self.destroy,
        ).pack(side="left")

    def _start(self):
        selected = [b for b, v in self._enabled.items() if v.get()]
        if not selected:
            messagebox.showwarning("Brak wyboru", "Zaznacz co najmniej jedną markę.", parent=self)
            return
        self._gen_btn.configure(state="disabled", text="Generuję…")
        threading.Thread(
            target=self._worker,
            args=(selected, self._force_var.get()),
            daemon=True,
        ).start()

    def _worker(self, brands: list[str], force: bool):
        try:
            done, skipped = generate_lifestyle_thumbnails(
                self._products,
                brands=brands,
                force=force,
            )
        except Exception as e:
            done, skipped = 0, 0
            self.after(0, lambda: messagebox.showerror(
                "Błąd", str(e), parent=self
            ))

        if self._on_done:
            self._on_done(done)
        self.after(0, self.destroy)
