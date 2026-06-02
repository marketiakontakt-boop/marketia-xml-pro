"""Marketia XML Pro — GUI.
Phases 1-5: XML parse → transforms → AI descriptions → thumbnails → export.
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image
from dotenv import load_dotenv
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.cache.sqlite_cache import open_cache, clear_cache, _CLEARABLE_TABLES
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
from app.gui.settings_window import SettingsWindow
from app.gui.model_rename_window import ModelRenameWindow
from app.gui.seo_keyword_window import SeoKeywordWindow
from app.gui.variant_view import VariantViewWindow
from app.gui.tooltip import Tooltip
from app.transformer.description_generator import generate_single_description
from app.validator import validate_ean, get_label

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

APP_NAME = "Marketia XML Pro"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

_BRAND_KEYWORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"


def _all_known_brands() -> list[str]:
    """Return sorted list of all brand keys from brand_keywords.json."""
    import json
    try:
        with _BRAND_KEYWORDS_PATH.open(encoding="utf-8") as f:
            return sorted(json.load(f).keys())
    except Exception:
        return []

DIFF_COLORS = {
    "new":       "#1a6f3a",
    "changed":   "#b08000",
    "unchanged": None,
}


def _thumb_mode_dialog(parent, n_products: int) -> dict | None:
    """Show thumbnail options dialog.

    Returns dict with keys: mode ('missing'|'all'), mirror (bool).
    Returns None on cancel.
    """
    result: list[dict | None] = [None]

    win = ctk.CTkToplevel(parent)
    win.title("Generuj miniatury")
    win.geometry("460x220")
    win.resizable(False, False)
    win.grab_set()

    ctk.CTkLabel(
        win,
        text=f"Generować miniatury dla {n_products} produktów?",
        font=ctk.CTkFont(size=13, weight="bold"),
        wraplength=420,
    ).pack(pady=(20, 4))

    opts = ctk.CTkFrame(win, fg_color="#F3F4F6", corner_radius=8)
    opts.pack(fill="x", padx=20, pady=(8, 4))

    mirror_var = ctk.BooleanVar(value=False)

    ctk.CTkCheckBox(
        opts, text="Odbicie lustrzane (mirror)",
        variable=mirror_var,
        font=ctk.CTkFont(size=12),
    ).pack(anchor="w", padx=12, pady=(10, 10))

    ctk.CTkLabel(
        win,
        text="Oryginalne zdjęcie pobrane z dostawcy · opcjonalnie odbicie",
        text_color="#6B7280",
        font=ctk.CTkFont(size=10),
    ).pack(pady=(0, 8))

    btn_f = ctk.CTkFrame(win, fg_color="transparent")
    btn_f.pack(pady=(4, 16))

    def _pick(mode: str):
        result[0] = {"mode": mode, "mirror": mirror_var.get()}
        win.destroy()

    ctk.CTkButton(btn_f, text="Tylko brakujące", width=150,
                  command=lambda: _pick("missing")).grid(row=0, column=0, padx=4)
    ctk.CTkButton(btn_f, text="Regeneruj wszystkie", width=160,
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

    def __init__(self, master, product: Product, on_click=None, **kwargs):
        diff = getattr(product, "diff_status", None)
        diff_border = DIFF_COLORS.get(diff) if diff else None
        super().__init__(
            master,
            fg_color="#FFFFFF",
            border_width=1,
            border_color=diff_border or "#E5E7EB",
            corner_radius=4,
            **kwargs,
        )
        for i, w in enumerate(self.COL_WIDTHS):
            self.grid_columnconfigure(i, minsize=w, weight=0)

        # SKU
        ctk.CTkLabel(self, text=product.sku, anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, sticky="w", padx=4, pady=3)

        # Title — truncated string, no wraplength (wraplength forces full-row relayout)
        title_raw = product.title or product.name or ""
        title_text = title_raw[:55] + "…" if len(title_raw) > 55 else title_raw
        ctk.CTkLabel(self, text=title_text, anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=1, sticky="w", padx=4, pady=3)

        # Brand chip (static — no CTkOptionMenu per row)
        bg_c, fg_c = get_brand_chip_colors(product.brand or "")
        ctk.CTkLabel(
            self, text=(product.brand or "—").upper()[:10],
            fg_color=bg_c, text_color=fg_c,
            corner_radius=4, font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=2, sticky="w", padx=4, pady=3)

        # Category chip
        allegro_cat = getattr(product, "allegro_category", "")
        cat_text = allegro_cat.split(" > ")[-1][:12] if allegro_cat else "?"
        cat_bg, cat_fg = ("#DCFCE7", "#15803D") if allegro_cat else ("#FFEDD5", "#C2410C")
        ctk.CTkLabel(self, text=cat_text, fg_color=cat_bg, text_color=cat_fg,
                     corner_radius=4, font=ctk.CTkFont(size=9)).grid(
            row=0, column=3, sticky="w", padx=4, pady=3)

        # Model
        ctk.CTkLabel(self, text=product.model_name or "—", anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=4, sticky="w", padx=4, pady=3)

        # EAN
        ean_color = "#1f883d" if getattr(product, "ean_valid", True) else "#d1242f"
        ctk.CTkLabel(self, text=product.ean or "—", anchor="w",
                     text_color=ean_color, font=ctk.CTkFont(size=11)).grid(
            row=0, column=5, sticky="w", padx=4, pady=3)

        # Title length OK
        title_len = len(product.title or "")
        t_ok = "✓" if 0 < title_len <= 75 else "✗"
        t_color = "#1f883d" if t_ok == "✓" else "#d1242f"
        ctk.CTkLabel(self, text=t_ok, text_color=t_color,
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=6, sticky="w", padx=4, pady=3)

        # AI status
        ai_sym = "🤖" if getattr(product, "ai_done", False) else "·"
        ctk.CTkLabel(self, text=ai_sym, font=ctk.CTkFont(size=11)).grid(
            row=0, column=7, sticky="w", padx=4, pady=3)

        # Quality score
        score = getattr(product, "quality_score", -1)
        if score >= 0:
            _, sc = get_label(score)
            ctk.CTkLabel(self, text=str(score), text_color=sc,
                         font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=0, column=8, sticky="w", padx=4, pady=3)
        else:
            ctk.CTkLabel(self, text="—", font=ctk.CTkFont(size=11)).grid(
                row=0, column=8, sticky="w", padx=4, pady=3)

        if on_click:
            self.bind("<Button-1>", lambda e: on_click())
            for child in self.winfo_children():
                child.bind("<Button-1>", lambda e: on_click())


_BaseApp = TkinterDnD.Tk if _DND_AVAILABLE else ctk.CTk


class App(_BaseApp):
    def __init__(self):
        super().__init__()
        # customtkinter needs its appearance set on the root window
        if _DND_AVAILABLE:
            ctk.set_appearance_mode("light")
            ctk.set_default_color_theme("blue")
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
        self._cancel_event = threading.Event()
        self._page: int = 0
        self._page_size: int = 50

        self._build_layout()
        self._setup_drag_drop()
        self.after(50, lambda: (self.lift(), self.focus_force()))
        self.after(100, self._poll_queue)

    def _setup_drag_drop(self):
        if not _DND_AVAILABLE:
            return
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        """Handle a file dropped onto the window."""
        raw = event.data.strip()
        # tkinterdnd2 wraps paths with spaces in braces: {/path/to my file.xml}
        if raw.startswith("{") and raw.endswith("}"):
            path = raw[1:-1]
        else:
            # Multiple files — take only the first
            path = raw.split()[0]
        if not path.lower().endswith(".xml"):
            messagebox.showwarning(APP_NAME, "Upuść plik XML (eksport BaseLinker).")
            return
        self._load_xml(path)

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
        # row 0 = topbar, row 1 = main content (weight), row 2 = footer
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        _DARK = "#111827"
        _DARK_BTN = "#1F2937"
        _DARK_HOVER = "#374151"
        _DARK_TEXT = "#F9FAFB"
        _MUTED_TEXT = "#9CA3AF"

        # ── TOPBAR ───────────────────────────────────────────────────────────
        topbar = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color=_DARK)
        topbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        topbar.grid_propagate(False)
        topbar.grid_columnconfigure(1, weight=1)

        _logo_path = Path("/Users/jakubknap/Documents/_meta/assets/logo/LOGO MARKETIA.png")
        try:
            _logo_img = ctk.CTkImage(Image.open(_logo_path), size=(130, 40))
            ctk.CTkLabel(topbar, image=_logo_img, text="").grid(
                row=0, column=0, padx=(16, 4), pady=6)
        except Exception:
            ctk.CTkLabel(
                topbar, text="MARKETIA XML PRO",
                text_color=_DARK_TEXT, font=ctk.CTkFont(size=14, weight="bold"),
            ).grid(row=0, column=0, padx=(16, 4), pady=6)

        dnd_hint = "  ·  przeciągnij plik XML tutaj" if _DND_AVAILABLE else ""
        self.summary_var = ctk.StringVar(value=f"Wczytaj XML aby zacząć{dnd_hint}")
        ctk.CTkLabel(
            topbar, textvariable=self.summary_var,
            text_color=_MUTED_TEXT, font=ctk.CTkFont(size=11), anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=8)

        self._topbar_stats = ctk.CTkFrame(topbar, fg_color="transparent")
        self._topbar_stats.grid(row=0, column=2, sticky="e", padx=(8, 16))
        self._build_stats_bar(self._topbar_stats)

        # ── SIDEBAR ──────────────────────────────────────────────────────────
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=_DARK)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        def _section(label: str) -> None:
            ctk.CTkLabel(
                sidebar, text=label,
                text_color="#6B7280", font=ctk.CTkFont(size=9, weight="bold"),
                anchor="w",
            ).pack(fill="x", padx=16, pady=(14, 2))
            ctk.CTkFrame(sidebar, height=1, fg_color="#374151").pack(
                fill="x", padx=12, pady=(0, 4))

        def _sb(text, cmd, tip, pady=3, **kw) -> ctk.CTkButton:
            kw.setdefault("fg_color", _DARK_BTN)
            kw.setdefault("hover_color", _DARK_HOVER)
            kw.setdefault("text_color", _DARK_TEXT)
            kw.setdefault("anchor", "w")
            kw.setdefault("font", ctk.CTkFont(size=12))
            kw.setdefault("height", 34)
            kw.setdefault("corner_radius", 8)
            btn = ctk.CTkButton(sidebar, text=text, command=cmd, **kw)
            btn.pack(fill="x", padx=10, pady=pady)
            Tooltip(btn, tip)
            return btn

        # ── DANE
        _section("DANE")
        _sb("  Wczytaj XML", self._pick_xml,
            "Wczytaj plik XML eksportu BaseLinker z listą produktów.",
            fg_color="#1D4ED8", hover_color="#1E40AF")
        _sb("  Zmień markę (wszystkie)", self._open_brand_all_dialog,
            "Ustaw jedną markę dla WSZYSTKICH wczytanych produktów.\n"
            "Przydatne gdy automatyczna detekcja myli się markami.")
        _sb("  Zmień model serii", self._open_model_rename,
            "Masowa zmiana nazwy modelu dla całej serii kolorystycznej.")
        _sb("  Grupy wariantów", self._open_variant_view,
            "Podgląd i edycja grup wariantowych (kolor/rozmiar).")
        _sb("  Mapa kategorii", self._open_category_mapper,
            "Edytuj mapowanie kategorii BaseLinker → Allegro.")

        # ── PIPELINE
        _section("PIPELINE")
        _sb("  Uruchom transformy", self._run_transforms,
            "Wykrywanie marki/modelu, SEO tytuł ≤75 zn., EAN, atrybuty.",
            fg_color="#1a6f3a", hover_color="#145c2f")
        self.btn_ai = _sb("  Generuj opisy (AI)", self._run_ai,
            "Gemini AI generuje opisy HTML. Cache SQLite — idempotentne.",
            fg_color="#1a6f3a", hover_color="#145c2f")
        self.btn_ai_unattended = _sb("  ⏳ Generuj automatycznie", self._run_ai_unattended,
            "Unattended mode: retry + cooldown — odejdź od komputera.",
            fg_color="#0E7490", hover_color="#0C6177")
        self.btn_thumb = _sb("  Generuj miniatury", self._run_thumbnails,
            "Pobiera pierwsze zdjęcie produktu i zapisuje jako JPEG.",
            fg_color="#6D28D9", hover_color="#5B21B6")
        self.btn_imgbb = _sb("  Upload ImgBB", self._run_imgbb,
            "Wysyła miniatury na ImgBB (CDN). Wymaga IMGBB_API_KEY.",
            fg_color="#9D174D", hover_color="#831843")
        self.btn_lifestyle = _sb("  Lifestyle AI", self._run_lifestyle,
            "rembg + Imagen 4 — produkt na lifestyle tle.",
            fg_color="#0E7490", hover_color="#0C6177")

        # ── NARZĘDZIA
        _section("NARZĘDZIA")
        _sb("  SEO Keyword Injector", self._open_seo_injector,
            "Gemini wyodrębnia frazy SEO i wstrzykuje w opisy.")
        _sb("  Podgląd opisów HTML", self._open_preview,
            "Podgląd wygenerowanych opisów HTML w przeglądarce.")
        _sb("  Audyt produktów", self._open_audit,
            "Raport: Q score, EAN, długość tytułu, atrybuty.")

        # ── EKSPORT
        _section("EKSPORT")
        self.btn_export = _sb("  Eksport XML", self._export_xml,
            "Eksportuje XML do BaseLinker z kategorią Allegro i atrybutami.",
            fg_color="#1D4ED8", hover_color="#1E40AF")

        # ── SYSTEM (bottom)
        ctk.CTkFrame(sidebar, height=1, fg_color="#374151").pack(
            fill="x", padx=12, pady=(16, 4))
        _sb("  Wyczyść cache", self._clear_cache_dialog,
            "Usuwa dane z cache SQLite. Możesz wybrać które tabele.",
            fg_color="#7F1D1D", hover_color="#6B1919")
        _sb("  Ustawienia", self._open_settings,
            "Zarządzaj kluczami API: Gemini, ImgBB.",
            pady=(3, 16))

        # ── MAIN AREA ────────────────────────────────────────────────────────
        main = ctk.CTkFrame(self, fg_color="#F8FAFC")
        main.grid(row=1, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self._build_filter_bar(main)

        self.list_frame = ctk.CTkScrollableFrame(main, label_text="", fg_color="#FFFFFF")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self._pagination_bar = tk.Frame(main, bg="#F1F5F9", height=36)
        self._pagination_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))

        # ── FOOTER ───────────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, height=32, corner_radius=0, fg_color="#F1F5F9")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(footer, height=4, corner_radius=0)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.btn_cancel = ctk.CTkButton(
            footer, text="Zatrzymaj", width=90, height=24,
            fg_color="#DC2626", hover_color="#B91C1C",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._cancel_operation,
        )
        self.btn_cancel.grid(row=0, column=1, padx=8)
        self.status_var = ctk.StringVar(value="Gotowy.")
        ctk.CTkLabel(
            footer, textvariable=self.status_var,
            text_color="#64748B", font=ctk.CTkFont(size=11), anchor="e",
        ).grid(row=0, column=2, sticky="e", padx=(8, 12))

    def _build_stats_bar(self, parent: ctk.CTkFrame) -> None:
        self._stat_total = ctk.StringVar(value="—")
        self._stat_ai    = ctk.StringVar(value="AI —")
        self._stat_q     = ctk.StringVar(value="Q —")
        self._stat_cost  = ctk.StringVar(value="$0.00")
        self._stat_cache = ctk.StringVar(value="Cache —")

        chip_specs = [
            (self._stat_total, "#1E3A5F", "#93C5FD"),
            (self._stat_ai,    "#14532D", "#86EFAC"),
            (self._stat_q,     "#713F12", "#FDE68A"),
            (self._stat_cost,  "#374151", "#D1D5DB"),
            (self._stat_cache, "#3B0764", "#D8B4FE"),
        ]
        for var, bg, fg in chip_specs:
            ctk.CTkLabel(
                parent, textvariable=var,
                fg_color=bg, text_color=fg,
                corner_radius=10,
                font=ctk.CTkFont(size=10, weight="bold"),
                padx=8, pady=3,
            ).pack(side="left", padx=3)

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
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))

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
        self._page = 0
        self._render_table()

    def _on_filter_ai(self, value: str) -> None:
        self._filter_ai = value
        self._page = 0
        self._render_table()

    def _clear_filters(self) -> None:
        self._filter_brand = "Wszystkie"
        self._filter_ai = "Wszystkie"
        self._page = 0
        self._brand_menu.set("Wszystkie")
        self._ai_seg.set("Wszystkie")
        self._render_table()

    def _update_brand_filter_options(self) -> None:
        brands = _all_known_brands()
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
        self._load_xml(path)

    def _load_xml(self, path: str):
        self._xml_path = path
        self.status_var.set(f"Parsuję XML: {Path(path).name}…")
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
            bm.sanitize_manufacturer_names(self.products)
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

        has_api_key = bool(
            os.getenv("GEMINI_API_KEYS", "").strip()
            or os.getenv("GEMINI_API_KEY_1", "").strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
        )
        if not has_api_key:
            messagebox.showerror(
                APP_NAME,
                "Brak kluczy API Gemini!\n\n"
                "Dodaj do pliku .env:\n"
                "GEMINI_API_KEYS=klucz1,klucz2,klucz3\n"
                "lub pojedynczo:\n"
                "GEMINI_API_KEY=AIza...",
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
        self._op_start()
        threading.Thread(
            target=self._ai_worker, args=(self.products,), daemon=True
        ).start()

    def _ai_worker(self, products: list[Product]):
        def log(msg: str):
            self.q.put(("status", msg))

        try:
            submitted, cached = generate_descriptions(
                products, progress_callback=log,
                cancel_check=lambda: self._cancel_event.is_set(),
            )
            if self._cancel_event.is_set():
                self.q.put(("cancelled", f"Zatrzymano. Zapisano: {submitted} opisów."))
            else:
                self.q.put(("ai_done", submitted, cached))
        except Exception as e:
            self.q.put(("error", f"AI: {e}"))

    def _run_ai_unattended(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML (krok 3).")
            return

        has_api_key = bool(
            os.getenv("GEMINI_API_KEYS", "").strip()
            or os.getenv("GEMINI_API_KEY_1", "").strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
        )
        if not has_api_key:
            messagebox.showerror(
                APP_NAME,
                "Brak kluczy API Gemini!\n\nDodaj do pliku .env:\nGEMINI_API_KEYS=klucz1,klucz2",
            )
            return

        pending = [p for p in self.products if not getattr(p, "ai_done", False)]
        if not pending:
            messagebox.showinfo(APP_NAME, "Wszystkie opisy już wygenerowane (z cache).")
            return

        if not messagebox.askyesno(
            APP_NAME,
            f"Uruchomić unattended generation dla {len(pending)} produktów?\n\n"
            "Program będzie czekał na cooldown kluczy API.\n"
            "Możesz odejść od komputera — SQLite cache zapisuje postęp na bieżąco.\n"
            "Stop zatrzymuje po ukończeniu bieżącej paczki.",
        ):
            return

        self.btn_ai_unattended.configure(state="disabled")
        self.btn_ai.configure(state="disabled")
        self.status_var.set(f"⏳ Unattended: generuję {len(pending)} opisów…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self._op_start()
        threading.Thread(
            target=self._ai_unattended_worker, args=(self.products,), daemon=True
        ).start()

    def _ai_unattended_worker(self, products: list[Product]):
        def log(msg: str):
            self.q.put(("status", f"⏳ {msg}"))

        try:
            submitted, cached = generate_descriptions(
                products,
                progress_callback=log,
                cancel_check=lambda: self._cancel_event.is_set(),
            )
            if self._cancel_event.is_set():
                self.q.put(("cancelled", f"Zatrzymano. Zapisano: {submitted} opisów."))
            else:
                self.q.put(("ai_done", submitted, cached))
        except Exception as e:
            self.q.put(("error", f"Unattended generation: {e}"))
        finally:
            self._op_end()

    def _export_xml(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Brak produktów do eksportu.")
            return

        # Pre-export: check for local thumbnails not yet uploaded to ImgBB
        needs_upload = [
            p for p in self.products
            if not getattr(p, "thumbnail_url", "")
            and (THUMB_DIR / f"{p.sku}.jpg").exists()
        ]
        if needs_upload:
            has_key = bool(os.getenv("IMGBB_API_KEY", "").strip())
            if has_key:
                choice = messagebox.askyesnocancel(
                    "Miniatury niewgrane",
                    f"{len(needs_upload)} produktów ma miniatury lokalnie, ale nie wgrane na ImgBB.\n\n"
                    "Bez uploadu XML będzie zawierał oryginalne zdjęcia dostawcy.\n\n"
                    "Tak = Wgraj teraz na ImgBB, potem eksportuj\n"
                    "Nie = Eksportuj mimo to (oryginalne obrazy)\n"
                    "Anuluj = Wróć",
                )
                if choice is None:
                    return
                if choice:
                    self._run_imgbb_then_export(needs_upload)
                    return
            else:
                messagebox.showwarning(
                    "Miniatury niewgrane",
                    f"{len(needs_upload)} produktów ma miniatury lokalnie, ale brak IMGBB_API_KEY.\n\n"
                    "XML będzie zawierał oryginalne zdjęcia dostawcy.\n"
                    "Dodaj IMGBB_API_KEY do .env i użyj kroku 4.6 żeby wgrać zdjęcia.",
                )

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

    def _run_imgbb_then_export(self, products_to_upload: list) -> None:
        """Upload thumbnails to ImgBB, then open save dialog and export."""
        self.btn_imgbb.configure(state="disabled")
        self.btn_export.configure(state="disabled")
        self.status_var.set(f"Wgrywam {len(products_to_upload)} miniaturek na ImgBB przed eksportem…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()

        def _worker():
            def log(msg):
                self.q.put(("status", msg))
            try:
                uploaded = upload_thumbnails(products_to_upload, THUMB_DIR, progress_callback=log)
                self.q.put(("imgbb_then_export", uploaded))
            except Exception as e:
                self.q.put(("error", f"ImgBB: {e}"))

        threading.Thread(target=_worker, daemon=True).start()

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

    def _open_settings(self) -> None:
        SettingsWindow(self)

    def _clear_cache_dialog(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Wyczyść cache")
        win.geometry("420x480")
        win.resizable(False, False)
        win.grab_set()

        ctk.CTkLabel(
            win,
            text="Wyczyść cache SQLite",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            win,
            text="Zaznaczone tabele zostaną wyczyszczone.\nPo wczytaniu tego samego XML dane zostaną\nwygenerowane od nowa.",
            text_color="#6B7280",
            font=ctk.CTkFont(size=11),
            justify="center",
        ).pack(pady=(0, 12))

        checks: dict[str, ctk.BooleanVar] = {}
        frame = ctk.CTkScrollableFrame(win, fg_color="#F3F4F6", corner_radius=8, height=280)
        frame.pack(fill="x", padx=20, pady=(0, 8))

        for tbl, label in _CLEARABLE_TABLES.items():
            var = ctk.BooleanVar(value=True)
            checks[tbl] = var
            ctk.CTkCheckBox(
                frame,
                text=label,
                variable=var,
                font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=12, pady=3)

        def _select_all():
            for v in checks.values():
                v.set(True)

        def _deselect_all():
            for v in checks.values():
                v.set(False)

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkButton(row, text="Zaznacz wszystko", width=140,
                      fg_color="#374151", hover_color="#1f2937",
                      font=ctk.CTkFont(size=11),
                      command=_select_all).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="Odznacz wszystko", width=140,
                      fg_color="#374151", hover_color="#1f2937",
                      font=ctk.CTkFont(size=11),
                      command=_deselect_all).pack(side="left")

        def _do_clear():
            selected = [t for t, v in checks.items() if v.get()]
            if not selected:
                messagebox.showwarning("Brak wyboru", "Wybierz co najmniej jedną tabelę.", parent=win)
                return
            if not messagebox.askyesno(
                "Potwierdź",
                f"Wyczyścić {len(selected)} tabel(e)?\nOperacja jest nieodwracalna.",
                parent=win,
            ):
                return
            with open_cache() as conn:
                result = clear_cache(conn, selected)
            total = sum(result.values())
            win.destroy()
            messagebox.showinfo(
                "Cache wyczyszczony",
                f"Usunięto {total} rekordów z {len(result)} tabel.\n"
                "Możesz teraz wczytać XML od nowa.",
                parent=self,
            )

        ctk.CTkButton(
            win,
            text="Wyczyść zaznaczone",
            fg_color="#991b1b",
            hover_color="#7f1d1d",
            command=_do_clear,
        ).pack(pady=(4, 16))

    def _open_model_rename(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        models = [p for p in self.products if p.model_name]
        if not models:
            messagebox.showinfo(APP_NAME, "Brak przypisanych modeli. Uruchom krok 3 (Transformy).")
            return
        ModelRenameWindow(self, self.products, on_done=self._render_table)

    def _open_variant_view(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        has_models = any(p.model_name for p in self.products)
        if not has_models:
            messagebox.showinfo(APP_NAME, "Brak przypisanych modeli. Uruchom krok 3 (Transformy).")
            return
        VariantViewWindow(self, self.products, on_done=self._render_table)

    def _open_category_mapper(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        def _on_save(updated_map):
            from app.transformer.category_mapper import map_all_products
            map_all_products(self.products, updated_map)
            self._render_table()
        CategoryMapperWindow(self, self.products, on_save=_on_save)

    def _open_seo_injector(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        has_desc = any(getattr(p, "ai_done", False) for p in self.products)
        if not has_desc:
            messagebox.showinfo(
                APP_NAME,
                "Brak produktów z opisem AI.\n"
                "Uruchom krok 4 — Generuj opisy (AI) przed użyciem SEO Injector.",
            )
            return
        SeoKeywordWindow(self, self.products, on_done=self._render_table)

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
        self._op_start()
        threading.Thread(target=self._imgbb_worker, args=(with_thumb,), daemon=True).start()

    def _imgbb_worker(self, products: list):
        def log(msg): self.q.put(("status", msg))
        try:
            uploaded = upload_thumbnails(
                products, THUMB_DIR, progress_callback=log,
                cancel_check=lambda: self._cancel_event.is_set(),
            )
            if self._cancel_event.is_set():
                self.q.put(("cancelled", f"Zatrzymano. Przesłano: {uploaded} miniaturek."))
            else:
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

        opts = _thumb_mode_dialog(self, len(with_images))
        if opts is None:
            return

        force = (opts["mode"] == "all")
        mirror = opts["mirror"]
        mirror_label = " [mirror]" if mirror else ""
        self.btn_thumb.configure(state="disabled")
        self.status_var.set(f"Generuję miniatury{mirror_label} dla {len(with_images)} prod…")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self._op_start()
        threading.Thread(
            target=self._thumb_worker, args=(with_images, force, mirror), daemon=True
        ).start()

    def _thumb_worker(self, products: list, force: bool = False, mirror: bool = False):
        total = len(products)

        def log(msg: str):
            self.q.put(("status", msg))
            try:
                part = msg.split("/")[0].split()[-1]
                i = int(part)
                self.q.put(("progress", i / total))
            except Exception:
                pass

        try:
            done, skipped = generate_thumbnails(
                products, progress_callback=log, force=force, mirror=mirror,
                cancel_check=lambda: self._cancel_event.is_set(),
            )
            if self._cancel_event.is_set():
                self.q.put(("cancelled", f"Zatrzymano. Zapisano: {done} miniaturek."))
            else:
                self.q.put(("thumb_done", done, skipped))
        except Exception as e:
            self.q.put(("error", f"Miniatury: {e}"))

    def _on_row_click(self, product: Product) -> None:
        brands = _all_known_brands()
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
            on_model_change=self._on_model_change,
        )

    def _on_brand_change(self, product: Product, new_brand: str) -> None:
        import re as _re
        old_brand = product.brand
        old_model = product.model_name or ""
        product.brand = new_brand

        tt = TitleTransformer()
        product.manufacturer_name = (tt.brand_data.get(new_brand) or {}).get("name", new_brand.upper())

        # Reassign model BEFORE generating title — assign modifies product.name
        # (substitutes the series word), so title must be built from the updated name.
        with open_cache() as conn:
            conn.execute("DELETE FROM sku_model_names WHERE used_for_sku = ?", (product.sku,))
            ModelNameGenerator(conn).assign(product)
        new_model = product.model_name or ""

        tt.transform(product)

        old_disp = (tt.brand_data.get(old_brand) or {}).get("name", old_brand.upper() if old_brand else "")
        new_disp = (tt.brand_data.get(new_brand) or {}).get("name", new_brand)

        old_base = old_model.split()[0] if old_model else ""
        new_base = new_model.split()[0] if new_model else ""

        replacements: list[tuple] = []
        if old_disp and old_disp.upper() != new_disp.upper():
            replacements.append((_re.compile(_re.escape(old_disp), _re.IGNORECASE), new_disp))
        # Full model phrase first (e.g. "Horn Białe" → "Lind Białe"), then bare base word
        if old_model and new_model and old_model.upper() != new_model.upper():
            replacements.append((
                _re.compile(r'(?<![A-Za-z0-9])' + _re.escape(old_model) + r'(?![A-Za-z0-9])', _re.IGNORECASE),
                new_model,
            ))
        if old_base and new_base and old_base.upper() != new_base.upper():
            replacements.append((
                _re.compile(r'(?<![A-Za-z0-9])' + _re.escape(old_base) + r'(?![A-Za-z0-9])', _re.IGNORECASE),
                new_base,
            ))

        if replacements:
            for field in ("description", "description_extra_1", "description_extra_2"):
                val = getattr(product, field, "") or ""
                if not val:
                    continue
                for pat, rep in replacements:
                    val = pat.sub(rep, val)
                setattr(product, field, val)

        self._render_table()
        # Refresh detail popup if it's open for this product
        if self._detail_win is not None:
            try:
                if self._detail_win.winfo_exists():
                    self._detail_win.load_product(product)
            except Exception:
                pass

    def _on_model_change(self, product: Product, new_model: str) -> None:
        import re as _re
        old_model = product.model_name or ""
        product.model_name = new_model

        # Persist to SQLite cache (overrides auto-generated name)
        with open_cache() as conn:
            conn.execute("DELETE FROM sku_model_names WHERE used_for_sku = ?", (product.sku,))
            from app.cache.sqlite_cache import save_sku_model_name
            save_sku_model_name(conn, product.sku, product.brand or "", new_model)

        # Replace old model name in existing description (word-boundary aware)
        if old_model and old_model.upper() != new_model.upper():
            pat = _re.compile(
                r'(?<![A-Za-z0-9])' + _re.escape(old_model) + r'(?![A-Za-z0-9])',
                _re.IGNORECASE,
            )
            for field in ("description", "description_extra_1", "description_extra_2"):
                val = getattr(product, field, "") or ""
                if val:
                    setattr(product, field, pat.sub(new_model, val))

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

    def _open_brand_all_dialog(self) -> None:
        if not self.products:
            messagebox.showwarning(APP_NAME, "Najpierw wczytaj plik XML.")
            return

        brands = _all_known_brands()
        if not brands:
            messagebox.showwarning(APP_NAME, "Brak zdefiniowanych marek w brand_keywords.json.")
            return

        win = ctk.CTkToplevel(self)
        win.title("Zmień markę — wszystkie produkty")
        win.geometry("400x230")
        win.resizable(False, False)
        win.grab_set()
        win.after(50, win.lift)

        ctk.CTkLabel(
            win, text="Ustaw markę dla wszystkich produktów",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(pady=(24, 2))
        ctk.CTkLabel(
            win, text=f"{len(self.products)} produktów zostanie zaktualizowanych",
            text_color="#6B7280", font=ctk.CTkFont(size=11),
        ).pack(pady=(0, 14))

        brand_var = ctk.StringVar(value=brands[0])
        ctk.CTkOptionMenu(win, values=brands, variable=brand_var, width=260).pack(pady=4)

        def _apply() -> None:
            import re as _re
            new_brand = brand_var.get()
            if not new_brand:
                return
            win.destroy()

            tt = TitleTransformer()
            new_disp = (tt.brand_data.get(new_brand) or {}).get("name", new_brand)

            # Collect per-product old state BEFORE any mutations
            old_states: list[tuple] = []
            for p in self.products:
                old_states.append((
                    p,
                    p.brand or "",
                    p.model_name or "",
                    (tt.brand_data.get(p.brand) or {}).get("name", (p.brand or "").upper()),
                ))

            # Pass 1: update brand/manufacturer only — title comes AFTER model reassignment
            for p, _, _, _ in old_states:
                p.brand = new_brand
                p.manufacturer_name = (tt.brand_data.get(new_brand) or {}).get("name", new_brand.upper())

            # Pass 2: batch model reassignment (modifies product.name with new series word)
            with open_cache() as conn:
                skus = [p.sku for p in self.products]
                conn.execute(
                    f"DELETE FROM sku_model_names WHERE used_for_sku IN ({','.join('?' * len(skus))})",
                    skus,
                )
                ModelNameGenerator(conn).assign_all(self.products)

            # Pass 3: regenerate titles now that product.name has the new series names
            for p in self.products:
                tt.transform(p)

            # Pass 4: description replacements using the now-correct model names
            for p, old_brand, old_model, old_disp in old_states:
                new_model = p.model_name or ""
                old_base = old_model.split()[0] if old_model else ""
                new_base = new_model.split()[0] if new_model else ""
                replacements = []
                if old_disp and old_disp.upper() != new_disp.upper():
                    replacements.append(
                        (_re.compile(_re.escape(old_disp), _re.IGNORECASE), new_disp)
                    )
                if old_model and new_model and old_model.upper() != new_model.upper():
                    replacements.append((
                        _re.compile(
                            r'(?<![A-Za-z0-9])' + _re.escape(old_model) + r'(?![A-Za-z0-9])',
                            _re.IGNORECASE,
                        ),
                        new_model,
                    ))
                if old_base and new_base and old_base.upper() != new_base.upper():
                    replacements.append((
                        _re.compile(
                            r'(?<![A-Za-z0-9])' + _re.escape(old_base) + r'(?![A-Za-z0-9])',
                            _re.IGNORECASE,
                        ),
                        new_base,
                    ))
                if replacements:
                    for field in ("description", "description_extra_1", "description_extra_2"):
                        val = getattr(p, field, "") or ""
                        if not val:
                            continue
                        for pat, rep in replacements:
                            val = pat.sub(rep, val)
                        setattr(p, field, val)

            self._update_stats()
            self._update_brand_filter_options()
            self._render_table()

        ctk.CTkButton(
            win, text="Zastosuj do wszystkich",
            command=_apply, fg_color="#1a6f3a", hover_color="#145c2f", width=220,
        ).pack(pady=(14, 4))
        ctk.CTkButton(
            win, text="Anuluj",
            command=win.destroy, fg_color="#374151", hover_color="#1f2937", width=220,
        ).pack()

    def _no_op(self):
        messagebox.showinfo(
            APP_NAME,
            "Marka jest liczona automatycznie podczas transformów (krok 3).\n\n"
            "Możesz zmienić markę inline — kliknij dropdown marki przy produkcie w liście.",
        )

    # ── cancel / operation lifecycle ──────────────────────────────────────

    def _op_start(self) -> None:
        """Show stop button and reset cancel flag before starting an operation."""
        self._cancel_event.clear()
        self.btn_cancel.grid(row=0, column=1, padx=(8, 8))
        self.btn_cancel.configure(state="normal")

    def _op_end(self) -> None:
        """Hide stop button after operation finishes or is cancelled."""
        self._cancel_event.clear()
        self.btn_cancel.grid_remove()

    def _cancel_operation(self) -> None:
        self._cancel_event.set()
        self.btn_cancel.configure(state="disabled")
        self.status_var.set("Zatrzymywanie…")

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
                    self.btn_ai_unattended.configure(state="normal")
                    self._op_end()
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
                    self._op_end()
                    self.status_var.set(f"ImgBB: {uploaded} miniaturek uploadowanych.")
                    messagebox.showinfo(APP_NAME, f"Upload zakończony!\n{uploaded} miniaturek na ImgBB.\nURL-e zostaną użyte w eksporcie XML.")

                elif tag == "imgbb_then_export":
                    _, uploaded = msg
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    self.btn_imgbb.configure(state="normal")
                    self.btn_export.configure(state="normal")
                    self.status_var.set(f"ImgBB: {uploaded} miniaturek wgranych — teraz zapisz XML.")
                    # Now open the save dialog and export
                    OUTPUT_DIR.mkdir(exist_ok=True)
                    output_path = filedialog.asksaveasfilename(
                        title="Zapisz XML",
                        initialdir=str(OUTPUT_DIR),
                        defaultextension=".xml",
                        filetypes=[("XML", "*.xml")],
                        initialfile="marketia_transformed.xml",
                    )
                    if output_path:
                        self.status_var.set("Eksportuję XML…")
                        threading.Thread(
                            target=self._export_worker, args=(self.products, output_path), daemon=True
                        ).start()

                elif tag == "thumb_done":
                    _, done, skipped = msg
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1.0)
                    self.status_var.set(f"Miniatury: {done} wygenerowanych, {skipped} z cache.")
                    self.btn_thumb.configure(state="normal")
                    self._op_end()
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

                elif tag == "cancelled":
                    _, msg_text = msg
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(0)
                    self.status_var.set(msg_text)
                    self.btn_ai.configure(state="normal")
                    self.btn_thumb.configure(state="normal")
                    self.btn_imgbb.configure(state="normal")
                    self.btn_lifestyle.configure(state="normal")
                    self._op_end()
                    self._render_table()
                    self._update_stats()

                elif tag == "error":
                    _, err = msg
                    self.progress.stop()
                    self.progress.set(0)
                    self.status_var.set("Błąd.")
                    self.btn_ai.configure(state="normal")
                    self.btn_thumb.configure(state="normal")
                    self.btn_imgbb.configure(state="normal")
                    self.btn_lifestyle.configure(state="normal")
                    self._op_end()
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

        filtered = self._filtered_products()
        total = len(filtered)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        self._page = min(self._page, total_pages - 1)

        start = self._page * self._page_size
        page_items = filtered[start:start + self._page_size]

        for idx, p in enumerate(page_items, 1):
            row = ProductRow(
                self.list_frame, p,
                on_click=lambda prod=p: self._on_row_click(prod),
            )
            row.grid(row=idx, column=0, sticky="ew", pady=1)

        self._update_pagination(total, total_pages)

    def _update_pagination(self, total: int, total_pages: int) -> None:
        for w in self._pagination_bar.winfo_children():
            w.destroy()
        if total == 0:
            return
        start = self._page * self._page_size + 1
        end = min(start + self._page_size - 1, total)

        tk.Button(
            self._pagination_bar, text="◀",
            state="normal" if self._page > 0 else "disabled",
            command=self._prev_page,
            bg="#E5E7EB", relief="flat", padx=10, pady=4,
            font=("Helvetica", 11), cursor="hand2",
        ).pack(side="left", padx=(8, 4), pady=4)

        tk.Label(
            self._pagination_bar,
            text=f"Produkty {start}–{end} z {total}   ·   Strona {self._page + 1} / {total_pages}",
            bg="#F1F5F9", fg="#6B7280", font=("Helvetica", 10),
        ).pack(side="left", padx=8)

        tk.Button(
            self._pagination_bar, text="▶",
            state="normal" if self._page < total_pages - 1 else "disabled",
            command=self._next_page,
            bg="#E5E7EB", relief="flat", padx=10, pady=4,
            font=("Helvetica", 11), cursor="hand2",
        ).pack(side="left", padx=(4, 8), pady=4)

    def _prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._render_table()

    def _next_page(self) -> None:
        self._page += 1
        self._render_table()


if __name__ == "__main__":
    App().mainloop()
