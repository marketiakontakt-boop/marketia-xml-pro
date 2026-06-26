"""Product detail popup — HTML preview, brand edit, description history."""
from __future__ import annotations

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
        on_model_change: Callable[[Product, str], None] | None = None,
    ):
        super().__init__(parent)
        self.resizable(True, True)
        self.geometry("820x620")
        self.minsize(600, 400)

        self._on_brand_change = on_brand_change
        self._on_regenerate = on_regenerate
        self._on_model_change = on_model_change
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
        parent.grid_rowconfigure(2, weight=1)

        # ── row 0: brand + actions ─────────────────────────────────────────
        toolbar = ctk.CTkFrame(parent, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 2))

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

        # ── row 1: model name ──────────────────────────────────────────────
        model_bar = ctk.CTkFrame(parent, fg_color="transparent")
        model_bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        ctk.CTkLabel(model_bar, text="Model:", text_color="#6B7280").pack(side="left", padx=(4, 4))
        self._model_var = ctk.StringVar()
        self._model_entry = ctk.CTkEntry(
            model_bar, textvariable=self._model_var, width=220,
            placeholder_text="np. Bergen, Falcon, Dator…",
        )
        self._model_entry.pack(side="left", padx=(0, 6))
        self._model_entry.bind("<Return>", lambda e: self._save_model())
        ctk.CTkButton(
            model_bar, text="Zapisz model", width=110,
            fg_color="#374151", hover_color="#1F2937",
            command=self._save_model,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            model_bar, text="Usuń model", width=90,
            fg_color="#7f1d1d", hover_color="#991b1b",
            command=self._clear_model,
        ).pack(side="left", padx=(0, 12))
        self._model_hint = ctk.CTkLabel(
            model_bar, text="", text_color="#6B7280", font=ctk.CTkFont(size=10)
        )
        self._model_hint.pack(side="left")

        # ── row 2: HTML textbox ────────────────────────────────────────────
        self._html_box = ctk.CTkTextbox(parent, wrap="word", font=ctk.CTkFont(family="Courier", size=11))
        self._html_box.grid(row=2, column=0, sticky="nsew")

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

        # model name field
        self._model_var.set(p.model_name or "")
        self._model_hint.configure(text="")

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

    def _save_model(self) -> None:
        new_model = self._model_var.get().strip()
        if not new_model:
            return
        if self._on_model_change:
            self._on_model_change(self._product, new_model)
            self._model_hint.configure(text=f"✓ zapisano: {new_model}", text_color="#1f883d")

    def _clear_model(self) -> None:
        if self._on_model_change:
            self._on_model_change(self._product, "")
            self._model_var.set("")
            self._model_hint.configure(text="✓ model usunięty", text_color="#6B7280")

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
