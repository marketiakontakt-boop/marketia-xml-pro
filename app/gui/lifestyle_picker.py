"""Lifestyle Thumbnail Picker — choose lifestyle element per brand, preview, generate."""
from __future__ import annotations

import threading
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.images.lifestyle_composer import list_lifestyle_assets, compose_lifestyle, LIFESTYLE_DIR
from app.images.thumbnail_generator import THUMB_DIR
from app.gui.brand_colors import get_brand_chip_colors
from app.parser.normalizer import Product


class LifestylePickerWindow(ctk.CTkToplevel):
    """Non-blocking window to pick lifestyle element per brand and generate composited thumbnails."""

    def __init__(self, parent, products: list[Product], on_done=None):
        super().__init__(parent)
        self.title("Lifestyle Thumbnails — wybór elementów")
        self.geometry("720x540")
        self.minsize(600, 400)
        self._products = products
        self._on_done = on_done

        # Collect brands that have lifestyle assets
        self._brands = sorted({
            p.brand for p in products
            if p.brand and list_lifestyle_assets(p.brand)
        })

        self._selections: dict[str, ctk.StringVar] = {}
        self._enabled: dict[str, ctk.BooleanVar] = {}

        self._build_ui()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        ctk.CTkLabel(toolbar, text="Wybierz element lifestyle per marka",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        self._gen_btn = ctk.CTkButton(
            toolbar, text="Generuj lifestyle thumbnails",
            fg_color="#0891B2", hover_color="#0e7490",
            command=self._generate,
        )
        self._gen_btn.pack(side="right")

        scroll = ctk.CTkScrollableFrame(self, label_text="")
        scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        scroll.grid_columnconfigure(1, weight=1)

        if not self._brands:
            ctk.CTkLabel(scroll, text=(
                "Brak elementów lifestyle dla żadnej z marek.\n"
                "Upewnij się że folder data/lifestyle/ zawiera PNG-ki per marka."
            ), text_color="#6B7280").pack(pady=20)
            return

        for row_idx, brand in enumerate(self._brands):
            assets = list_lifestyle_assets(brand)
            asset_names = [a.stem for a in assets]

            bg, fg = get_brand_chip_colors(brand)
            ctk.CTkLabel(scroll, text=brand.upper(),
                         fg_color=bg, text_color=fg,
                         corner_radius=4, font=ctk.CTkFont(size=11, weight="bold"),
                         padx=8).grid(row=row_idx, column=0, sticky="w", padx=8, pady=6)

            sel_var = ctk.StringVar(value=asset_names[0] if asset_names else "")
            self._selections[brand] = sel_var
            ctk.CTkOptionMenu(scroll, variable=sel_var, values=asset_names, width=200).grid(
                row=row_idx, column=1, sticky="w", padx=8, pady=6)

            en_var = ctk.BooleanVar(value=True)
            self._enabled[brand] = en_var
            ctk.CTkCheckBox(scroll, text="aktywna", variable=en_var).grid(
                row=row_idx, column=2, padx=8, pady=6)

            ctk.CTkButton(scroll, text="Podgląd", width=70,
                          command=lambda b=brand, sv=sel_var: self._preview(b, sv.get())).grid(
                row=row_idx, column=3, padx=8, pady=6)

    def _preview(self, brand: str, asset_stem: str):
        asset_path = LIFESTYLE_DIR / brand / f"{asset_stem}.png"
        if not asset_path.exists():
            return
        sample = next(
            (p for p in self._products if p.brand == brand
             and (THUMB_DIR / f"{p.sku}.jpg").exists()), None
        )
        if not sample:
            return
        thumb = Image.open(THUMB_DIR / f"{sample.sku}.jpg")
        result = compose_lifestyle(thumb, asset_path)

        win = ctk.CTkToplevel(self)
        win.title(f"Podgląd — {brand} / {asset_stem}")
        win.geometry("640x340")
        display_size = (300, 300)

        orig_ctk = ctk.CTkImage(thumb.resize(display_size), size=display_size)
        result_ctk = ctk.CTkImage(result.resize(display_size), size=display_size)

        ctk.CTkLabel(win, text="Oryginał").grid(row=0, column=0, padx=8, pady=(8, 2))
        ctk.CTkLabel(win, text="Z lifestyle").grid(row=0, column=1, padx=8, pady=(8, 2))
        ctk.CTkLabel(win, image=orig_ctk, text="").grid(row=1, column=0, padx=8, pady=4)
        ctk.CTkLabel(win, image=result_ctk, text="").grid(row=1, column=1, padx=8, pady=4)

    def _generate(self):
        self._gen_btn.configure(state="disabled", text="Generuję…")
        threading.Thread(target=self._generate_worker, daemon=True).start()

    def _generate_worker(self):
        count = 0
        for brand in self._brands:
            if not self._enabled.get(brand, ctk.BooleanVar(value=False)).get():
                continue
            asset_stem = self._selections[brand].get()
            if not asset_stem:
                continue
            asset_path = LIFESTYLE_DIR / brand / f"{asset_stem}.png"
            if not asset_path.exists():
                continue
            for p in self._products:
                if p.brand != brand:
                    continue
                src = THUMB_DIR / f"{p.sku}.jpg"
                if not src.exists():
                    continue
                result = compose_lifestyle(Image.open(src), asset_path)
                out = THUMB_DIR / f"{p.sku}_lifestyle.jpg"
                result.save(str(out), "JPEG", quality=95)
                count += 1

        if self._on_done:
            self._on_done(count)
        self.destroy()
