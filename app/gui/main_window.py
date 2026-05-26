"""Marketia XML Pro — GUI.
Phases 1-5: XML parse → transforms → AI descriptions → thumbnails → export.
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.cache.sqlite_cache import open_cache
from app.parser import Product, parse_xml
from app.transformer.brand_mapper import BrandMapper
from app.transformer.model_generator import ModelNameGenerator
from app.transformer.title_transformer import TitleTransformer
from app.transformer.description_generator import (
    generate_descriptions,
    load_cached_descriptions,
)
from app.transformer.attribute_extractor import enrich_product_attributes
from app.transformer.description_cleaner import strip_jumi_descriptions
from app.transformer.category_mapper import load_category_map, map_all_products
from app.transformer.xml_diff import run_diff, STATUS_NEW, STATUS_CHANGED
from app.exporter.xml_exporter import export_xml
from app.images.thumbnail_generator import generate_thumbnails, THUMB_DIR
from app.images.imgbb_uploader import upload_thumbnails
from app.gui.preview import open_preview
from app.gui.audit_preview import open_audit_preview
from app.gui.product_detail import ProductDetailWindow
from app.gui.brand_colors import get_brand_chip_colors
from app.gui.category_mapper_window import CategoryMapperWindow
from app.gui.lifestyle_picker import LifestylePickerWindow
from app.gui.tooltip import Tooltip
from app.transformer.description_generator import generate_single_description
from app.validator import validate_ean, get_label

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

APP_NAME = "Marketia XML Pro"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

DIFF_COLORS = {
    "new":       "#1a6f3a",
    "changed":   "#b08000",
    "unchanged": None,
}


def _thumb_mode_dialog(parent, n_products: int) -> str | None:
    """Show 3-button dialog: returns 'missing', 'all', or None (cancel)."""
    result: list[str | None] = [None]

    win = ctk.CTkToplevel(parent)
    win.title("Generuj miniatury")
    win.geometry("420x190")
    win.resizable(False, False)
    win.grab_set()

    ctk.CTkLabel(
        win,
        text=f"Generować miniatury dla {n_products} produktów?",
        font=ctk.CTkFont(size=13, weight="bold"),
        wraplength=380,
    ).pack(pady=(20, 4))
    ctk.CTkLabel(
        win,
        text="rembg usuwa tło · białe 1200×1200 · zapis: output/thumbnails/",
        text_color="#6B7280",
        font=ctk.CTkFont(size=11),
        wraplength=380,
    ).pack(pady=(0, 16))

    btn_f = ctk.CTkFrame(win, fg_color="transparent")
    btn_f.pack()

    def _pick(mode: str):
        result[0] = mode
        win.destroy()

    ctk.CTkButton(btn_f, text="Tylko brakujące", width=140,
                  command=lambda: _pick("missing")).grid(row=0, column=0, padx=4)
    ctk.CTkButton(btn_f, text="Regeneruj wszystkie", width=155,
                  fg_color="#7c3aed", hover_color="#6d28d9",
                  command=lambda: _pick("all")).grid(row=0, column=1, padx=4)
    ctk.CTkButton(btn_f, text="Anuluj", width=80,
                  fg_color="#374151", hover_color="#1f2937",
                  command=win.destroy).grid(row=0, column=2, padx=4)

    parent.wait_window(win)
    return result[0]


class ProductRow(ctk.CTkFrame):
    # SKU | TYTUŁ | MARKA | KAT. | MODEL | EAN | T | AI | Q
    COL_WIDTHS = (130, 310, 110, 80, 100, 130, 40, 40, 50)

    def __init__(self, master, product: Product, on_click=None, all_brands: list[str] | None = None, on_brand_change=None, **kwargs):
        diff = getattr(product, "diff_status", None)
        diff_border = DIFF_COLORS.get(diff) if diff else None
        super().__init__(
            master,
            fg_color="#FFFFFF",
            border_width=1,
            border_color=diff_border or "#E5E7EB",
            corner_radius=6,
            **kwargs,
        )
        for i, w in enumerate(self.COL_WIDTHS):
            self.grid_columnconfigure(i, minsize=w, weight=0)

        ctk.CTkLabel(self, text=product.sku, anchor="w").grid(row=0, column=0, sticky="w", padx=4)
        ctk.CTkLabel(
            self, text=product.title or product.name, anchor="w", wraplength=330
        ).grid(row=0, column=1, sticky="w", padx=4)
        bg_c, fg_c = get_brand_chip_colors(product.brand or "")
        if all_brands and on_brand_change:
            _brand_var = ctk.StringVar(value=product.brand or "—")
            ctk.CTkOptionMenu(
                self,
                variable=_brand_var,
                values=all_brands,
                width=105, height=26,
                fg_color=bg_c, text_color=fg_c,
                button_color=bg_c, button_hover_color="#E5E7EB",
                dropdown_fg_color="white",
                font=ctk.CTkFont(size=10, weight="bold"),
                command=lambda v, p=product: on_brand_change(p, v),
            ).grid(row=0, column=2, sticky="w", padx=4, pady=2)
        else:
            ctk.CTkLabel(
                self, text=(product.brand or "—").upper()[:10],
                fg_color=bg_c, text_color=fg_c,
                corner_radius=4, font=ctk.CTkFont(size=10, weight="bold"),
            ).grid(row=0, column=2, sticky="w", padx=4, pady=4)
        # Col 3: Allegro category chip
        allegro_cat = getattr(product, "allegro_category", "")
        if allegro_cat:
            cat_short = allegro_cat.split(" > ")[-1][:12]
            ctk.CTkLabel(self, text=cat_short, fg_color="#DCFCE7", text_color="#15803D",
                         corner_radius=4, font=ctk.CTkFont(size=9)).grid(
                row=0, column=3, sticky="w", padx=4, pady=4)
        else:
            ctk.CTkLabel(self, text="?", fg_color="#FFEDD5", text_color="#C2410C",
                         corner_radius=4, font=ctk.CTkFont(size=9, weight="bold")).grid(
                row=0, column=3, sticky="w", padx=4, pady=4)

        ctk.CTkLabel(self, text=product.model_name or "—", anchor="w").grid(row=0, column=4, sticky="w", padx=4)

        ean_color = "#1f883d" if getattr(product, "ean_valid", True) else "#d1242f"
        ctk.CTkLabel(self, text=product.ean or "—", anchor="w", text_color=ean_color).grid(
            row=0, column=5, sticky="w", padx=4
        )

        title_len = len(product.title or "")
        t_ok = "✓" if 0 < title_len <= 75 else "✗"
        t_color = "#1f883d" if t_ok == "✓" else "#d1242f"
        ctk.CTkLabel(self, text=t_ok, text_color=t_color).grid(row=0, column=6, sticky="w", padx=4)

        ai_sym = "🤖" if getattr(product, "ai_done", False) else "·"
        ctk.CTkLabel(self, text=ai_sym).grid(row=0, column=7, sticky="w", padx=4)

        score = getattr(product, "quality_score", -1)
        if score >= 0:
            _, sc = get_label(score)
            ctk.CTkLabel(self, text=str(score), text_color=sc, font=ctk.CTkFont(weight="bold")).grid(
                row=0, column=8, sticky="w", padx=4
            )
        else:
            ctk.CTkLabel(self, text="—").grid(row=0, column=8, sticky="w", padx=4)

        if on_click:
            self.bind("<Button-1>", lambda e: on_click())
            for child in self.winfo_children():
                child.bind("<Button-1>", lambda e: on_click())


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1450x900")
        self.minsize(1200, 700)

        self.products: list[Product] = []
        self.q: queue.Queue = queue.Queue()
        self._xml_path: str | None = None
        self._detail_win: ProductDetailWindow | None = None
        self._filter_brand: str = "Wszystkie"
        self._filter_ai: str = "Wszystkie"
        self._session_generated: int = 0
        self._session_cached: int = 0

        self._build_layout()
        self.after(50, lambda: (self.lift(), self.focus_force()))
        self.after(100, self._poll_queue)

    def _filtered_products(self) -> list[Product]:
        result = self.products
        if self._filter_brand != "Wszystkie":
            result = [p for p in result if (p.brand or "—") == self._filter_brand]
        if self._filter_ai == "Z opisem":
            result = [p for p in result if getattr(p, "ai_done", False)]
        elif self._filter_ai == "Bez opisu":
            result = [p for p in result if not getattr(p, "ai_done", False)]
        return result

    # ── layout ────────────────────────────────────────────────────────────

    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        sidebar = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color="#F9FAFB")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        _logo_path = Path("/Users/jakubknap/Documents/_meta/logo/LOGO MARKETIA.png")
        try:
            _logo_img = ctk.CTkImage(Image.open(_logo_path), size=(155, 48))
            ctk.CTkLabel(sidebar, image=_logo_img, text="").pack(pady=(16, 2))
        except Exception:
            ctk.CTkLabel(
                sidebar, text="MARKETIA", font=ctk.CTkFont(size=18, weight="bold")
            ).pack(pady=(20, 2))
        ctk.CTkLabel(
            sidebar, text="XML PRO",
            text_color="#6B7280", font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(pady=(0, 16))

        def _sb(text, cmd, tip, pady=4, **kw) -> ctk.CTkButton:
            btn = ctk.CTkButton(sidebar, text=text, command=cmd, **kw)
            btn.pack(fill="x", padx=12, pady=pady)
            Tooltip(btn, tip)
            return btn

        _sb("1. Wczytaj XML", self._pick_xml,
            "Wczytaj plik XML eksportu BaseLinker z listą produktów.")
        _sb("2. Marka (inline)", self._no_op,
            "Marka jest edytowalna bezpośrednio w tabeli — kliknij dropdown w kolumnie MARKA.",
            pady=4)
        _sb("Mapa kategorii", self._open_category_mapper,
            "Edytuj mapowanie kategorii BaseLinker → Allegro.\n"
            "Brakujące kategorie można uzupełnić automatycznie (AI).",
            pady=(0, 4), fg_color="#374151", hover_color="#1f2937")
        _sb("3. Uruchom transformy", self._run_transforms,
            "Uruchamia pipeline transformacji:\n"
            "• Wykrywanie marki i modelu\n"
            "• Generowanie tytułu SEO (≤75 zn.)\n"
            "• Walidacja EAN\n"
            "• Mapowanie kategorii Allegro\n"
            "• Ekstrakcja atrybutów (waga, wymiary itp.)")
        self.btn_ai = _sb("4. Generuj opisy (AI)", self._run_ai,
            "Generuje opisy HTML przez Gemini AI dla wszystkich produktów bez opisu.\n"
            "Używa cache SQLite — wygenerowane opisy nie są regenerowane.\n"
            "Koszt: ~$0.005 / produkt (Batch API).",
            fg_color="#1a6f3a", hover_color="#145c2f")
        self.btn_thumb = _sb("4.5 Generuj miniatury", self._run_thumbnails,
            "Generuje miniatury 1200×1200 px:\n"
            "• rembg usuwa tło (u2net)\n"
            "• Biała kanwa, produkt na 75% pola\n"
            "• Mała kopia w prawym dolnym rogu\n"
            "• Zapis: output/thumbnails/{SKU}.jpg\n"
            "Można wybrać 'Tylko brakujące' lub 'Regeneruj wszystkie'.",
            fg_color="#7c3aed", hover_color="#6d28d9")
        self.btn_imgbb = _sb("4.6 Upload ImgBB", self._run_imgbb,
            "Wysyła miniatury na ImgBB (CDN) i zapisuje URL w produkcie.\n"
            "URL jest używany w eksporcie XML jako link do zdjęcia.\n"
            "Wymaga IMGBB_API_KEY w pliku .env.",
            fg_color="#9d174d", hover_color="#831843")
        self.btn_lifestyle = _sb("4.7 Lifestyle thumb.", self._run_lifestyle,
            "Nakłada element 'lifestyle' (pies, kwiat, dziecko itp.) na miniaturkę.\n"
            "Każda marka ma swój zestaw PNG-ek:\n"
            "• ZOOVERA → psy/koty\n"
            "• GARDENSTEIN → kwiaty\n"
            "• INTEX → dzieci w wodzie\n"
            "Efekt: wyższy CTR, styl KanzaSklep.\n"
            "Zapis: output/thumbnails/{SKU}_lifestyle.jpg",
            fg_color="#0891B2", hover_color="#0e7490")
        _sb("Podgląd opisów HTML", self._open_preview,
            "Otwiera podgląd wygenerowanych opisów HTML w przeglądarce.",
            pady=(12, 4), fg_color="#374151", hover_color="#1f2937")
        _sb("Audyt produktów", self._open_audit,
            "Otwiera raport HTML ze statusem każdego produktu:\n"
            "• Q score, EAN, długość tytułu\n"
            "• Kategoria Allegro, atrybuty\n"
            "• Fragment opisu\n"
            "Czerwona krawędź = produkt z problemem.",
            pady=(0, 4), fg_color="#374151", hover_color="#1f2937")
        self.btn_export = _sb("5. Eksport XML", self._export_xml,
            "Eksportuje przetransformowane produkty do XML BaseLinker.\n"
            "Używa kategorii Allegro zamiast BaseLinker (jeśli zmapowana).\n"
            "Zawiera atrybuty w formacie <attributes>.",
            fg_color="#0a5c99", hover_color="#074880")

        # Main area
        main = ctk.CTkFrame(self)
        main.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.summary_var = ctk.StringVar(value="Wczytaj XML aby zacząć.")
        ctk.CTkLabel(
            header, textvariable=self.summary_var, anchor="w",
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=8)

        self._build_filter_bar(main)

        self.list_frame = ctk.CTkScrollableFrame(main, label_text="", fg_color="#FAFAFA")
        self.list_frame.grid(row=2, column=0, sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 0))
        footer.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(footer)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.status_var = ctk.StringVar(value="Gotowy.")
        ctk.CTkLabel(footer, textvariable=self.status_var, anchor="e").grid(
            row=0, column=1, sticky="e"
        )

        self._build_stats_bar()

    def _build_stats_bar(self) -> None:
        self._stat_total = ctk.StringVar(value="Produkty: —")
        self._stat_ai    = ctk.StringVar(value="AI: —")
        self._stat_q     = ctk.StringVar(value="Q: —")
        self._stat_cost  = ctk.StringVar(value="~$0.00")
        self._stat_cache = ctk.StringVar(value="Cache: —")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))

        chip_specs = [
            (self._stat_total, "#DBEAFE", "#1D4ED8"),
            (self._stat_ai,    "#DCFCE7", "#15803D"),
            (self._stat_q,     "#FEF3C7", "#92400E"),
            (self._stat_cost,  "#F3F4F6", "#374151"),
            (self._stat_cache, "#EDE9FE", "#6D28D9"),
        ]
        for var, bg, fg in chip_specs:
            ctk.CTkLabel(
                bar, textvariable=var,
                fg_color=bg, text_color=fg,
                corner_radius=12,
                font=ctk.CTkFont(size=11, weight="bold"),
                padx=10, pady=4,
            ).pack(side="left", padx=4, pady=4)

    def _update_stats(self) -> None:
        total = len(self.products)
        ai_done = sum(1 for p in self.products if getattr(p, "ai_done", False))
        pct = int(ai_done / total * 100) if total else 0
        scores = [p.quality_score for p in self.products if getattr(p, "quality_score", -1) >= 0]
        q_avg = sum(scores) / len(scores) if scores else 0.0
        cost = self._session_generated * 0.005
        total_calls = self._session_generated + self._session_cached
        cache_pct = int(self._session_cached / total_calls * 100) if total_calls else 0

        self._stat_total.set(f"Produkty: {total}")
        self._stat_ai.set(f"Z opisem: {ai_done} ({pct}%)")
        self._stat_q.set(f"Q avg: {q_avg:.1f}" if scores else "Q avg: —")
        self._stat_cost.set(f"Koszt: ~${cost:.2f}")
        self._stat_cache.set(f"Cache: {cache_pct}%")

    def _build_filter_bar(self, parent: ctk.CTkFrame) -> None:
        bar = ctk.CTkFrame(parent, fg_color="#F3F4F6", corner_radius=8)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))

        ctk.CTkLabel(bar, text="Marka:", text_color="#374151").pack(side="left", padx=(12, 2))
        self._brand_menu = ctk.CTkOptionMenu(
            bar,
            values=["Wszystkie"],
            width=160,
            command=self._on_filter_brand,
        )
        self._brand_menu.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(bar, text="Status AI:", text_color="#374151").pack(side="left", padx=(8, 2))
        self._ai_seg = ctk.CTkSegmentedButton(
            bar,
            values=["Wszystkie", "Z opisem", "Bez opisu"],
            command=self._on_filter_ai,
        )
        self._ai_seg.set("Wszystkie")
        self._ai_seg.pack(side="left", padx=(0, 12))

        ctk.CTkButton(bar, text="Wyczyść", width=80, command=self._clear_filters).pack(side="left")

    def _on_filter_brand(self, value: str) -> None:
        self._filter_brand = value
        self._render_table()

    def _on_filter_ai(self, value: str) -> None:
        self._filter_ai = value
        self._render_table()

    def _clear_filters(self) -> None:
        self._filter_brand = "Wszystkie"
        self._filter_ai = "Wszystkie"
        self._brand_menu.set("Wszystkie")
        self._ai_seg.set("Wszystkie")
        self._render_table()

    def _update_brand_filter_options(self) -> None:
        brands = sorted({p.brand for p in self.products if p.brand})
        self._brand_menu.configure(values=["Wszystkie"] + brands)
        self._brand_menu.set("Wszystkie")

    # ── actions ───────────────────────────────────────────────────────────

    def _pick_xml(self):
        path = filedialog.askopenfilename(
            title="Wybierz XML BaseLinker",
            filetypes=[("XML", "*.xml"), ("Wszystkie", "*.*")],
        )
        if not path:
            return
        self._xml_path = path
        self.status_var.set("Parsuję XML…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._load_worker, args=(path,), daemon=True).start()

    def _load_worker(self, path: str):
        try:
            products = parse_xml(path)
            diff = run_diff(products)
            self.q.put(("loaded", products, path, diff))
        except Exception as e:
            self.q.put(("error", f"Parser: {e}"))

    def _run_transforms(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        self.status_var.set("Transformuję…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._transform_worker, daemon=True).start()

    def _transform_worker(self):
        try:
            bm = BrandMapper()
            bm.map_products(self.products)
            with open_cache() as conn:
                ModelNameGenerator(conn).assign_all(self.products)
            TitleTransformer().transform_all(self.products)
            for p in self.products:
                p.ean_valid = validate_ean(p.ean)
            # Try to load cached descriptions
            load_cached_descriptions(self.products)
            # Strip legacy JUMI-format descriptions so AI regenerates them
            stripped = strip_jumi_descriptions(self.products)
            if stripped:
                self.q.put(("status", f"Usunięto {stripped} opisów w formacie JUMI — zostaną wygenerowane przez AI."))
            for p in self.products:
                enrich_product_attributes(p)
            _cat_map = load_category_map()
            map_all_products(self.products, _cat_map)
            self.q.put(("transformed",))
        except Exception as e:
            self.q.put(("error", f"Transform: {e}"))

    def _run_ai(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML (krok 3).")
            return

        has_api_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
        if not has_api_key:
            messagebox.showerror(
                APP_NAME,
                "Brak GEMINI_API_KEY!\n\nDodaj do pliku .env:\nGEMINI_API_KEY=AIza...",
            )
            return

        pending = [p for p in self.products if not getattr(p, "ai_done", False)]
        if not pending:
            messagebox.showinfo(APP_NAME, "Wszystkie opisy już wygenerowane (z cache).")
            return

        if not messagebox.askyesno(
            APP_NAME,
            f"Wygenerować opisy dla {len(pending)} produktów?\n"
            f"(Koszt szacowany: ~${len(pending) * 0.005:.2f} — Batch API 50% off)",
        ):
            return

        self.btn_ai.configure(state="disabled")
        self.status_var.set(f"Generuję opisy dla {len(pending)} prod…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(
            target=self._ai_worker, args=(self.products,), daemon=True
        ).start()

    def _ai_worker(self, products: list[Product]):
        def log(msg: str):
            self.q.put(("status", msg))

        try:
            submitted, cached = generate_descriptions(
                products, progress_callback=log
            )
            self.q.put(("ai_done", submitted, cached))
        except Exception as e:
            self.q.put(("error", f"AI: {e}"))

    def _export_xml(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Brak produktów do eksportu.")
            return

        OUTPUT_DIR.mkdir(exist_ok=True)
        output_path = filedialog.asksaveasfilename(
            title="Zapisz XML",
            initialdir=str(OUTPUT_DIR),
            defaultextension=".xml",
            filetypes=[("XML", "*.xml")],
            initialfile="marketia_transformed.xml",
        )
        if not output_path:
            return

        self.status_var.set("Eksportuję XML…")
        threading.Thread(
            target=self._export_worker, args=(self.products, output_path), daemon=True
        ).start()

    def _export_worker(self, products, output_path):
        try:
            count = export_xml(products, output_path)
            self.q.put(("exported", count, output_path))
        except Exception as e:
            self.q.put(("error", f"Eksport: {e}"))

    def _open_preview(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        count = open_preview(self.products)
        if count == 0:
            messagebox.showinfo(APP_NAME, "Brak produktów z opisami AI. Uruchom krok 4.")
        else:
            self.status_var.set(f"Podgląd otwarty w przeglądarce ({count} opisów).")

    def _open_audit(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        count = open_audit_preview(self.products)
        self.status_var.set(f"Audyt otwarty w przeglądarce ({count} produktów).")

    def _run_lifestyle(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        self.btn_lifestyle.configure(state="disabled")
        LifestylePickerWindow(self, self.products, on_done=self._lifestyle_done)

    def _lifestyle_done(self, count: int) -> None:
        self.btn_lifestyle.configure(state="normal")
        self.status_var.set(f"Lifestyle: {count} miniaturek zapisanych jako *_lifestyle.jpg.")
        messagebox.showinfo(APP_NAME,
            f"Lifestyle thumbnails gotowe!\n{count} plików zapisanych w output/thumbnails/\n"
            "Format: {sku}_lifestyle.jpg\nImgBB upload będzie preferować te pliki.")

    def _open_category_mapper(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        def _on_save(updated_map):
            from app.transformer.category_mapper import map_all_products
            map_all_products(self.products, updated_map)
            self._render_table()
        CategoryMapperWindow(self, self.products, on_save=_on_save)

    def _run_imgbb(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        if not os.getenv("IMGBB_API_KEY", "").strip():
            messagebox.showerror(APP_NAME, "Brak IMGBB_API_KEY w .env!\nDodaj: IMGBB_API_KEY=twój_klucz")
            return
        with_thumb = [p for p in self.products if (THUMB_DIR / f"{p.sku}.jpg").exists()]
        if not with_thumb:
            messagebox.showinfo(APP_NAME, "Brak wygenerowanych miniaturek.\nUruchom najpierw krok 4.5.")
            return
        if not messagebox.askyesno(APP_NAME, f"Uploadować {len(with_thumb)} miniaturek do ImgBB?\nURLe zostaną wstawione jako images[0] w eksporcie XML."):
            return
        self.btn_imgbb.configure(state="disabled")
        self.status_var.set(f"Uploaduję {len(with_thumb)} miniaturek na ImgBB…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._imgbb_worker, args=(with_thumb,), daemon=True).start()

    def _imgbb_worker(self, products: list):
        def log(msg): self.q.put(("status", msg))
        try:
            uploaded = upload_thumbnails(products, THUMB_DIR, progress_callback=log)
            self.q.put(("imgbb_done", uploaded))
        except Exception as e:
            self.q.put(("error", f"ImgBB: {e}"))

    def _run_thumbnails(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML (krok 3).")
            return

        with_images = [p for p in self.products if getattr(p, "images", [])]
        if not with_images:
            messagebox.showinfo(APP_NAME, "Brak produktów z URL-ami zdjęć.")
            return

        mode = _thumb_mode_dialog(self, len(with_images))
        if mode is None:
            return

        force = (mode == "all")
        self.btn_thumb.configure(state="disabled")
        self.status_var.set(f"Generuję miniatury dla {len(with_images)} prod…")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        threading.Thread(
            target=self._thumb_worker, args=(with_images, force), daemon=True
        ).start()

    def _thumb_worker(self, products: list, force: bool = False):
        total = len(products)

        def log(msg: str):
            self.q.put(("status", msg))
            try:
                i = int(msg.split(":")[1].split("/")[0].strip())
                self.q.put(("progress", i / total))
            except Exception:
                pass

        try:
            done, skipped = generate_thumbnails(products, progress_callback=log, force=force)
            self.q.put(("thumb_done", done, skipped))
        except Exception as e:
            self.q.put(("error", f"Miniatury: {e}"))

    def _on_row_click(self, product: Product) -> None:
        brands = sorted({p.brand for p in self.products if p.brand})
        if self._detail_win is not None:
            try:
                if self._detail_win.winfo_exists():
                    self._detail_win.load_product(product)
                    self._detail_win.lift()
                    return
            except Exception:
                pass
            self._detail_win = None
        self._detail_win = ProductDetailWindow(
            self,
            product,
            all_brands=brands,
            on_brand_change=self._on_brand_change,
            on_regenerate=self._on_regenerate_product,
        )

    def _on_brand_change(self, product: Product, new_brand: str) -> None:
        product.brand = new_brand
        self._render_table()

    def _on_regenerate_product(self, product: Product) -> None:
        threading.Thread(
            target=self._single_regen_worker,
            args=(product,),
            daemon=True,
        ).start()

    def _single_regen_worker(self, product: Product) -> None:
        try:
            generate_single_description(product)
            self.q.put(("single_regen_done", product))
        except Exception as e:
            self.q.put(("error", f"Regeneracja {product.sku}: {e}"))

    def _no_op(self):
        messagebox.showinfo(
            APP_NAME,
            "Marka jest liczona automatycznie podczas transformów (krok 3).\n\n"
            "Możesz zmienić markę inline — kliknij dropdown marki przy produkcie w liście.",
        )

    # ── queue / UI updates ────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                tag = msg[0]

                if tag == "loaded":
                    _, products, path, diff = msg
                    self.products = products
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    diff_str = f"  •  Nowe: {diff.new} / Zmienione: {diff.changed} / Bez zmian: {diff.unchanged}"
                    self.summary_var.set(
                        f"📂 {Path(path).name}  •  produktów: {len(products)}{diff_str}"
                    )
                    self.status_var.set("Wczytano. Kliknij krok 3 — Uruchom transformy.")
                    self._render_table()
                    self._update_brand_filter_options()
                    self._update_stats()

                elif tag == "transformed":
                    ai_done = sum(1 for p in self.products if getattr(p, "ai_done", False))
                    self.summary_var.set(
                        f"{self.summary_var.get()}  •  transformy OK  •  opisy cache: {ai_done}"
                    )
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    self.status_var.set("Transformy OK. Krok 4 — Generuj opisy.")
                    self._render_table()
                    self._update_stats()

                elif tag == "status":
                    self.status_var.set(msg[1])

                elif tag == "progress":
                    self.progress.set(msg[1])

                elif tag == "ai_done":
                    _, submitted, cached = msg
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    ai_done = sum(1 for p in self.products if getattr(p, "ai_done", False))
                    self.summary_var.set(
                        f"{self.summary_var.get().split('•')[0]}• opisy AI: {ai_done}"
                    )
                    self.status_var.set(
                        f"Opisy gotowe. Wygenerowano: {submitted} | Cache: {cached}"
                    )
                    self.btn_ai.configure(state="normal")
                    self._render_table()
                    self._session_generated += submitted
                    self._session_cached += cached
                    self._update_stats()

                elif tag == "imgbb_done":
                    _, uploaded = msg
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    self.btn_imgbb.configure(state="normal")
                    self.status_var.set(f"ImgBB: {uploaded} miniaturek uploadowanych.")
                    messagebox.showinfo(APP_NAME, f"Upload zakończony!\n{uploaded} miniaturek na ImgBB.\nURL-e zostaną użyte w eksporcie XML.")

                elif tag == "thumb_done":
                    _, done, skipped = msg
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    self.status_var.set(f"Miniatury: {done} wygenerowanych, {skipped} z cache.")
                    self.btn_thumb.configure(state="normal")
                    messagebox.showinfo(
                        APP_NAME,
                        f"Miniatury gotowe!\n"
                        f"Wygenerowane: {done}\n"
                        f"Pominięte (cache): {skipped}\n"
                        f"Folder: output/thumbnails/",
                    )

                elif tag == "exported":
                    _, count, path = msg
                    self.status_var.set(f"Wyeksportowano {count} produktów → {path}")
                    messagebox.showinfo(APP_NAME, f"Eksport zakończony!\n{count} produktów\n{path}")

                elif tag == "error":
                    _, err = msg
                    self.progress.stop()
                    self.progress.set(0)
                    self.status_var.set("Błąd.")
                    self.btn_ai.configure(state="normal")
                    messagebox.showerror(APP_NAME, err)

                elif tag == "single_regen_done":
                    _, product = msg
                    self._render_table()
                    self._update_stats()
                    if self._detail_win:
                        try:
                            self._detail_win.refresh()
                            self._detail_win.enable_regen_btn()
                        except Exception:
                            pass

        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _render_table(self):
        for child in self.list_frame.winfo_children():
            child.destroy()

        header_row = ctk.CTkFrame(self.list_frame, fg_color="#F3F4F6", corner_radius=4)
        header_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        for i, (text, w) in enumerate(
            zip(("SKU", "TYTUŁ / NAZWA", "MARKA", "KAT.", "MODEL", "EAN", "OK", "AI", "Q"),
                ProductRow.COL_WIDTHS)
        ):
            header_row.grid_columnconfigure(i, minsize=w)
            ctk.CTkLabel(
                header_row, text=text, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#6B7280",
            ).grid(row=0, column=i, sticky="w", padx=4, pady=4)

        cap = 300
        filtered = self._filtered_products()
        brands = sorted({p.brand for p in self.products if p.brand})
        for idx, p in enumerate(filtered[:cap], 1):
            row = ProductRow(
                self.list_frame, p,
                on_click=lambda prod=p: self._on_row_click(prod),
                all_brands=brands if brands else None,
                on_brand_change=self._on_brand_change if brands else None,
            )
            row.grid(row=idx, column=0, sticky="ew", pady=1)

        if len(filtered) > cap:
            ctk.CTkLabel(
                self.list_frame,
                text=f"… (+{len(filtered) - cap} kolejnych)",
                text_color="#888",
            ).grid(row=cap + 1, column=0, pady=8)


if __name__ == "__main__":
    App().mainloop()
