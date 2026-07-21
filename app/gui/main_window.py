"""Marketia Produktyzator — GUI.
Phases 1-5: XML parse → transforms → AI descriptions → thumbnails → export.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
import traceback
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
from app.cache.sqlite_cache import save_description
from app.transformer.attribute_extractor import enrich_product_attributes
from app.transformer.description_cleaner import strip_jumi_descriptions
from app.transformer.category_mapper import load_category_map, map_all_products
from app.transformer.xml_diff import run_diff, STATUS_NEW, STATUS_CHANGED
from app.exporter.xml_exporter import export_xml
from app.images.thumbnail_generator import generate_thumbnails, THUMB_DIR
from app.images.imgbb_uploader import upload_thumbnails, upload_infographics
from app.images.infographic_generator import generate_all_infographics
from app.gui.preview import open_preview
from app.gui.audit_preview import open_audit_preview
from app.gui.product_detail import ProductDetailWindow
from app.gui.brand_colors import get_brand_chip_colors
from app.gui.category_mapper_window import CategoryMapperWindow
from app.gui.settings_window import SettingsWindow
from app.gui.model_rename_window import ModelRenameWindow
from app.gui.seo_keyword_window import SeoKeywordWindow
from app.gui.variant_view import VariantViewWindow
from app.gui.model_audit_window import ModelAuditWindow
from app.gui.title_edit_dialog import TitleEditDialog
from app.gui.ean_edit_dialog import EanEditDialog
from app.gui.sync_report_dialog import SyncReportDialog
from app.gui.olx_dialog import OLXPublishDialog
from app.olx.auth import OLXAuth, OLXAuthError
from app.olx.api import OLXClient, OLXAPIError
from app.olx.categories import refresh_categories
from app.gui.tooltip import Tooltip
from app.validator import validate_ean, get_label

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

APP_NAME = "Marketia Produktyzator"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

_BRAND_KEYWORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "brand_keywords.json"


def _all_known_brands() -> list[str]:
    """Return sorted list of all brand keys from brand_keywords.json (excludes 'unknown')."""
    import json
    try:
        with _BRAND_KEYWORDS_PATH.open(encoding="utf-8") as f:
            return sorted(k for k in json.load(f).keys() if k != "unknown")
    except Exception:
        return []

DIFF_COLORS = {
    "new":       "#1a6f3a",
    "changed":   "#b08000",
    "unchanged": None,
}

# ── Row thumbnail helpers ──────────────────────────────────────────────────
_THUMB_ROW_SIZE = (44, 44)
_thumb_cache: dict[str, ctk.CTkImage | None] = {}
# Multi-subscriber pending: multiple labels can await the same URL.
# Previously `set[str]` guard = new labels after re-render lost callback.
_thumb_pending: dict[str, list[ctk.CTkLabel]] = {}
_thumb_pending_lock = threading.Lock()
# TTL na failed loads — scroll/re-render pozwala na retry po tym czasie.
_THUMB_FAILED_TTL = 60.0
_thumb_failed_at: dict[str, float] = {}
_thumb_url_sem = threading.Semaphore(8)  # cap concurrent URL downloads


def _apply_thumb(label: ctk.CTkLabel, img: ctk.CTkImage) -> None:
    try:
        label.after(0, lambda: label.configure(image=img, text="") if label.winfo_exists() else None)
    except Exception:
        pass


def _schedule_thumb(product: Product, label: ctk.CTkLabel) -> None:
    """Load a small product image into *label* (local file sync, URL async).

    Multi-subscriber pending: gdy ten sam URL już się ładuje z powodu innego
    ProductRow (paginacja/filtr re-render), nowa labelka dopisuje się do listy
    subskrybentów. Worker po skończeniu iteruje wszystkie labelki i updatuje
    te, które wciąż istnieją. Fix 2026-07-12 dla losowo pustych slotów.
    """
    sku = product.sku
    local = THUMB_DIR / f"{sku}.jpg"
    if not local.exists():
        local = THUMB_DIR / f"{sku}.png"
    key = str(local) if local.exists() else (product.images[0] if product.images else "")
    if not key:
        return

    if key in _thumb_cache:
        img = _thumb_cache[key]
        if img:
            _apply_thumb(label, img)
        elif key in _thumb_failed_at and (time.monotonic() - _thumb_failed_at[key]) > _THUMB_FAILED_TTL:
            # TTL wygasł — pozwól spróbować ponownie
            _thumb_cache.pop(key, None)
            _thumb_failed_at.pop(key, None)
            _schedule_thumb(product, label)
        return

    with _thumb_pending_lock:
        if key in _thumb_pending:
            _thumb_pending[key].append(label)
            return
        _thumb_pending[key] = [label]

    def _worker():
        img = None
        try:
            if key.startswith("http"):
                with _thumb_url_sem:
                    from io import BytesIO
                    from urllib.request import urlopen
                    pil = Image.open(BytesIO(urlopen(key, timeout=8).read())).convert("RGB")
            else:
                pil = Image.open(key).convert("RGB")
            pil = pil.resize(_THUMB_ROW_SIZE, Image.LANCZOS)
            img = ctk.CTkImage(pil, size=_THUMB_ROW_SIZE)
        except Exception as e:
            print(f"[thumb load failed] {key[:80]}: {type(e).__name__}: {e}", flush=True)

        _thumb_cache[key] = img
        if img is None:
            _thumb_failed_at[key] = time.monotonic()

        with _thumb_pending_lock:
            subscribers = _thumb_pending.pop(key, [])
        if img:
            for lbl in subscribers:
                _apply_thumb(lbl, img)

    threading.Thread(target=_worker, daemon=True).start()


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


def _ai_titles_dialog(parent, n_products: int, last_custom: str = "") -> dict | None:
    """Show AI titles options dialog with optional custom instructions.

    Returns dict {force: bool, custom_instruction: str} or None on cancel.
    Custom instruction is session-scoped — `last_custom` preselects the field.
    """
    result: list[dict | None] = [None]

    win = ctk.CTkToplevel(parent)
    win.title("Generuj tytuły AI")
    win.geometry("560x460")
    win.resizable(False, False)
    win.grab_set()

    ctk.CTkLabel(
        win,
        text=f"Wygenerować tytuły AI (Gemini) dla {n_products} produktów?",
        font=ctk.CTkFont(size=13, weight="bold"),
        wraplength=500,
    ).pack(pady=(20, 8))

    force_var = ctk.BooleanVar(value=False)
    ctk.CTkCheckBox(
        win,
        text="Wymuś regenerację (nadpisz cache)",
        variable=force_var,
        font=ctk.CTkFont(size=12),
    ).pack(anchor="w", padx=32, pady=(0, 8))

    ctk.CTkLabel(
        win,
        text="Dodatkowe instrukcje AI (opcjonalne):",
        font=ctk.CTkFont(size=12, weight="bold"),
    ).pack(anchor="w", padx=24, pady=(8, 2))

    ctk.CTkLabel(
        win,
        text="Np. „Nie wpisuj wymiarów w tytułach — nie mają sensu dla drapaczek dla kotów"
        "\"    lub    „Zawsze dodawaj słowo PREMIUM na końcu."
        "\n\nPole jednorazowe — instrukcje NIE zapisują się do cache tytułów.",
        text_color="#6B7280",
        font=ctk.CTkFont(size=10),
        wraplength=500,
        justify="left",
    ).pack(anchor="w", padx=24, pady=(0, 6))

    text_frame = ctk.CTkFrame(win, fg_color="#F3F4F6", corner_radius=8)
    text_frame.pack(fill="x", padx=24, pady=(0, 8))
    text_box = ctk.CTkTextbox(
        text_frame,
        height=110,
        font=ctk.CTkFont(size=12),
        wrap="word",
    )
    text_box.pack(fill="x", padx=8, pady=8)
    if last_custom:
        text_box.insert("1.0", last_custom)

    btn_f = ctk.CTkFrame(win, fg_color="transparent")
    btn_f.pack(pady=(4, 16))

    def _submit():
        custom = text_box.get("1.0", "end").strip()
        result[0] = {"force": force_var.get(), "custom_instruction": custom}
        win.destroy()

    def _clear_text():
        text_box.delete("1.0", "end")

    ctk.CTkButton(btn_f, text="Generuj", width=120,
                  command=_submit).grid(row=0, column=0, padx=4)
    ctk.CTkButton(btn_f, text="Wyczyść pole", width=120,
                  fg_color="#9CA3AF", hover_color="#6B7280",
                  command=_clear_text).grid(row=0, column=1, padx=4)
    ctk.CTkButton(btn_f, text="Anuluj", width=80,
                  fg_color="#374151", hover_color="#1f2937",
                  command=win.destroy).grid(row=0, column=2, padx=4)

    parent.wait_window(win)
    return result[0]


class ProductRow(ctk.CTkFrame):
    # CB | IMG | SKU | TYTUŁ | MARKA | KAT. | MODEL | EAN+btn | T | AI | Q
    COL_WIDTHS = (28, 50, 130, 280, 110, 80, 100, 200, 40, 40, 50)

    def __init__(self, master, product: Product, on_click=None, on_select=None, on_title_edit=None, on_ean_edit=None, is_selected: bool = False, **kwargs):
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

        # Checkbox (col 0)
        self._cb_var = ctk.BooleanVar(value=is_selected)
        self._cb = ctk.CTkCheckBox(
            self, variable=self._cb_var, text="",
            width=24, checkbox_width=16, checkbox_height=16,
        )
        self._cb.grid(row=0, column=0, padx=(4, 0), pady=3)
        if on_select:
            self._cb.configure(command=lambda sku=product.sku: on_select(sku, self._cb_var.get()))

        # Image thumbnail (col 1) — lazy-loaded from local file or product URL
        self._img_lbl = ctk.CTkLabel(self, text="·", width=44, fg_color="transparent",
                                     font=ctk.CTkFont(size=16), text_color="#D1D5DB")
        self._img_lbl.grid(row=0, column=1, padx=3, pady=3)
        _schedule_thumb(product, self._img_lbl)

        # SKU (col 2)
        ctk.CTkLabel(self, text=product.sku, anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=2, sticky="w", padx=4, pady=3)

        # Title (col 3) — truncated; click opens edit dialog
        title_raw = product.title or product.name or ""
        title_text = title_raw[:52] + "…" if len(title_raw) > 52 else title_raw
        self._title_lbl = ctk.CTkLabel(self, text=title_text, anchor="w",
                                       font=ctk.CTkFont(size=11),
                                       cursor="hand2" if on_title_edit else "")
        self._title_lbl.grid(row=0, column=3, sticky="w", padx=4, pady=3)

        # Brand chip (col 4)
        _brand_key = product.brand if product.brand and product.brand != "unknown" else ""
        bg_c, fg_c = get_brand_chip_colors(_brand_key)
        ctk.CTkLabel(
            self, text=_brand_key.upper()[:10] if _brand_key else "—",
            fg_color=bg_c, text_color=fg_c,
            corner_radius=4, font=ctk.CTkFont(size=10, weight="bold"),
        ).grid(row=0, column=4, sticky="w", padx=4, pady=3)

        # Category chip (col 5)
        allegro_cat = getattr(product, "allegro_category", "")
        cat_text = allegro_cat.split(" > ")[-1][:12] if allegro_cat else "?"
        cat_bg, cat_fg = ("#DCFCE7", "#15803D") if allegro_cat else ("#FFEDD5", "#C2410C")
        ctk.CTkLabel(self, text=cat_text, fg_color=cat_bg, text_color=cat_fg,
                     corner_radius=4, font=ctk.CTkFont(size=9)).grid(
            row=0, column=5, sticky="w", padx=4, pady=3)

        # Model (col 6)
        ctk.CTkLabel(self, text=product.model_name or "—", anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=0, column=6, sticky="w", padx=4, pady=3)

        # EAN (col 7) — value + dedicated "+ EAN" button (clones counter when extras present)
        ean_color = "#1f883d" if getattr(product, "ean_valid", True) else "#d1242f"
        extra_n = len(getattr(product, "extra_eans", []) or [])
        self._ean_cell = ctk.CTkFrame(self, fg_color="transparent")
        self._ean_cell.grid(row=0, column=7, sticky="w", padx=2, pady=2)
        self._ean_lbl = ctk.CTkLabel(
            self._ean_cell, text=product.ean or "—", anchor="w",
            text_color=ean_color, font=ctk.CTkFont(size=11),
        )
        self._ean_lbl.pack(side="left", padx=(2, 4))
        if on_ean_edit:
            btn_text = f"+ EAN ({extra_n})" if extra_n else "+ EAN"
            btn_fg = "#15803D" if extra_n else "#2563EB"
            btn_hover = "#166534" if extra_n else "#1D4ED8"
            self._ean_btn = ctk.CTkButton(
                self._ean_cell, text=btn_text, width=70, height=22,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color=btn_fg, hover_color=btn_hover, text_color="#FFFFFF",
                corner_radius=4,
                command=lambda: on_ean_edit(),
            )
            self._ean_btn.pack(side="left")
            Tooltip(
                self._ean_btn,
                "Dodaj dodatkowe EAN-y — każdy stworzy klona produktu\n"
                "(SKU-1, SKU-2, …) z innym kodem dla różnych kart Allegro."
            )

        # Title length OK (col 8)
        title_len = len(product.title or "")
        t_ok = "✓" if 0 < title_len <= 75 else "✗"
        t_color = "#1f883d" if t_ok == "✓" else "#d1242f"
        t_lbl = ctk.CTkLabel(self, text=t_ok, text_color=t_color,
                              font=ctk.CTkFont(size=11))
        t_lbl.grid(row=0, column=8, sticky="w", padx=4, pady=3)
        Tooltip(
            t_lbl,
            f"Tytuł {title_len} zn.\n✓ = OK (1-75 zn.) — limit Allegro\n✗ = za długi lub pusty"
        )

        # AI status (col 9)
        ai_sym = "🤖" if getattr(product, "ai_done", False) else "·"
        ai_lbl = ctk.CTkLabel(self, text=ai_sym, font=ctk.CTkFont(size=11))
        ai_lbl.grid(row=0, column=9, sticky="w", padx=4, pady=3)
        Tooltip(
            ai_lbl,
            "🤖 = opis AI wygenerowany\n· = jeszcze nie ma — uruchom 'Generuj opisy (AI)' w sidebar"
        )

        # Quality score (col 10)
        score = getattr(product, "quality_score", -1)
        if score >= 0:
            _, sc = get_label(score)
            q_lbl = ctk.CTkLabel(self, text=str(score), text_color=sc,
                                  font=ctk.CTkFont(size=11, weight="bold"))
            q_lbl.grid(row=0, column=10, sticky="w", padx=4, pady=3)
        else:
            q_lbl = ctk.CTkLabel(self, text="—", font=ctk.CTkFont(size=11))
            q_lbl.grid(row=0, column=10, sticky="w", padx=4, pady=3)
        Tooltip(
            q_lbl,
            "Q score = jakość opisu (0-10).\nWyliczane przez AI:\n- czy ma <b>, liczby, jednostki, sekcje\n— = brak opisu"
        )

        if on_click:
            self.bind("<Button-1>", lambda e: on_click())
            for child in self.winfo_children():
                if isinstance(child, ctk.CTkCheckBox):
                    continue
                if child is self._title_lbl:
                    continue  # title label gets its own binding below
                if child is self._ean_cell:
                    continue  # EAN cell has its own button — don't swallow clicks
                child.bind("<Button-1>", lambda e: on_click())
        if on_title_edit:
            self._title_lbl.bind("<Button-1>", lambda e: on_title_edit())


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
        self._session_cost_usd: float = 0.0
        self._cancel_event = threading.Event()
        self._page: int = 0
        self._page_size: int = 50
        self._selected_skus: set[str] = set()
        self._sel_label: ctk.CTkLabel | None = None
        self._sel_clear_btn: ctk.CTkButton | None = None
        # Session-scoped custom instruction for AI titles (persist while app alive).
        self._last_ai_custom_instruction: str = ""

        # Session state persistence — save/restore filtry/selekcja per XML file
        self._session_xml_hash: str | None = None
        self._last_session_save_ts: float = 0.0

        self._build_layout()
        self._setup_drag_drop()
        self.after(50, lambda: (self.lift(), self.focus_force()))
        self.after(100, self._poll_queue)
        # Zapisz sesję przed zamknięciem okna — filtry/selekcja przetrwają restart.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_drag_drop(self):
        if not _DND_AVAILABLE:
            return
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

    # ── Session state persistence ─────────────────────────────────────────
    def _capture_state(self) -> dict:
        """Return current UI session state as dict for persistence."""
        return {
            "filter_brand": self._filter_brand,
            "filter_ai": self._filter_ai,
            "selected_skus": sorted(self._selected_skus),
            "page": self._page,
            "custom_instruction": self._last_ai_custom_instruction,
        }

    def _restore_state(self, state: dict) -> None:
        """Restore UI session state from dict."""
        self._filter_brand = state.get("filter_brand", "Wszystkie")
        self._filter_ai = state.get("filter_ai", "Wszystkie")
        self._selected_skus = set(state.get("selected_skus", []))
        self._page = int(state.get("page", 0))
        self._last_ai_custom_instruction = state.get("custom_instruction", "")
        # Sync widgets, jeśli już istnieją.
        try:
            if hasattr(self, "_brand_menu"):
                self._brand_menu.set(self._filter_brand)
            if hasattr(self, "_ai_seg"):
                self._ai_seg.set(self._filter_ai)
        except Exception:
            pass
        self._update_sel_indicator()

    def _maybe_save_session(self, force: bool = False) -> None:
        """Throttled session save (max 1x/2s) unless force=True."""
        if not self._session_xml_hash or not getattr(self, "_xml_path", None):
            return
        now = time.monotonic()
        if not force and (now - self._last_session_save_ts) < 2.0:
            return
        self._last_session_save_ts = now
        state = self._capture_state()
        try:
            from app.cache.sqlite_cache import save_session_state, open_cache
            with open_cache() as conn:
                save_session_state(conn, self._session_xml_hash, self._xml_path, state)
        except Exception as e:
            print(f"[SESSION SAVE] failed: {e}", flush=True)

    def _on_close(self) -> None:
        """Persist session before window destroys."""
        try:
            self._maybe_save_session(force=True)
        except Exception:
            pass
        self.destroy()

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

        _logo_path = Path(__file__).resolve().parents[2] / "assets" / "logo.png"
        _logo_container = ctk.CTkFrame(topbar, fg_color="transparent")
        _logo_container.grid(row=0, column=0, padx=(16, 0), pady=4)
        try:
            _logo_img = ctk.CTkImage(Image.open(_logo_path), size=(174, 52))
            ctk.CTkLabel(_logo_container, image=_logo_img, text="").pack(side="left")
        except Exception:
            ctk.CTkLabel(
                _logo_container, text=APP_NAME.upper(),
                text_color=_DARK_TEXT, font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(side="left")
        ctk.CTkFrame(_logo_container, width=1, height=36, fg_color="#444444").pack(
            side="left", padx=(12, 0)
        )

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
        sidebar = ctk.CTkFrame(self, width=230, corner_radius=0, fg_color=_DARK)
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        _sb_scroll = ctk.CTkScrollableFrame(
            sidebar, fg_color=_DARK, corner_radius=0,
            scrollbar_button_color="#374151",
            scrollbar_button_hover_color="#4B5563",
        )
        _sb_scroll.pack(fill="both", expand=True)

        def _section(label: str) -> None:
            ctk.CTkLabel(
                _sb_scroll, text=label,
                text_color="#6B7280", font=ctk.CTkFont(size=9, weight="bold"),
                anchor="w",
            ).pack(fill="x", padx=16, pady=(14, 2))
            ctk.CTkFrame(_sb_scroll, height=1, fg_color="#374151").pack(
                fill="x", padx=12, pady=(0, 4))

        def _sb(text, cmd, tip, pady=3, **kw) -> ctk.CTkButton:
            kw.setdefault("fg_color", _DARK_BTN)
            kw.setdefault("hover_color", _DARK_HOVER)
            kw.setdefault("text_color", _DARK_TEXT)
            kw.setdefault("anchor", "w")
            kw.setdefault("font", ctk.CTkFont(size=12))
            kw.setdefault("height", 34)
            kw.setdefault("corner_radius", 8)
            btn = ctk.CTkButton(_sb_scroll, text=text, command=cmd, **kw)
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
        _sb("  Audyt modeli", self._open_model_audit,
            "Wykrywa produkty w tej samej serii (np. NOTO) przypisane do różnych grup — pozwala je scalić.")
        _sb("  Usuń modele", self._remove_models,
            "Usuwa model z zaznaczonych produktów (lub wszystkich). Produkt zostaje bez modelu.",
            fg_color="#7f1d1d", hover_color="#991b1b")
        _sb("  Odśwież modele", self._reset_models,
            "Czyści cache nazw modeli i przelicza je ponownie (nowe grupowanie bez usuwania opisów).",
            fg_color="#92400e", hover_color="#78350f")
        _sb("  Grupy wariantów", self._open_variant_view,
            "Podgląd i edycja grup wariantowych (kolor/rozmiar).")
        _sb("  Mapa kategorii", self._open_category_mapper,
            "Edytuj mapowanie kategorii BaseLinker → Allegro.")

        # ── PIPELINE
        _section("PIPELINE")
        _sb("  Uruchom transformy", self._run_transforms,
            "Wykrywanie marki/modelu, SEO tytuł ≤75 zn., EAN, atrybuty.",
            fg_color="#1a6f3a", hover_color="#145c2f")
        _sb("  Regeneruj tytuły", self._regen_titles,
            "Przelicza tytuły SEO dla zaznaczonych produktów (lub wszystkich).")
        self.btn_ai_titles = _sb("  🤖 Tytuły AI (Gemini)", self._run_ai_titles,
            "Gemini generuje tytuły SEO Allegro (max 75 zn., promp v1 z audytu TOP ofert).\n"
            "Cache SQLite — idempotentne.",
            fg_color="#0E7490", hover_color="#0C6177")
        self.btn_ai = _sb("  Generuj opisy (AI)", self._run_ai,
            "Gemini AI generuje opisy HTML. Cache SQLite — idempotentne.",
            fg_color="#1a6f3a", hover_color="#145c2f")
        _sb("  Zaktualizuj model w opisach", self._sync_model_in_descriptions,
            "Zastępuje starą nazwę modelu aktualną we wszystkich istniejących opisach.")
        self.btn_thumb = _sb("  Generuj miniatury", self._run_thumbnails,
            "Pobiera pierwsze zdjęcie produktu i zapisuje jako JPEG.",
            fg_color="#6D28D9", hover_color="#5B21B6")
        self.btn_infographics = _sb("  Infografiki AI", self._run_infographics,
            "Generuje infografiki parametrów (WYMIARY/WAGA/KOLOR/MATERIAŁ) — packshot + zielony pasek. "
            "Bez marki (regulamin Allegro). Wchodzą jako image_extra_N w XML.",
            fg_color="#4D7021", hover_color="#3A5518")
        self.btn_imgbb = _sb("  Upload ImgBB", self._run_imgbb,
            "Wysyła miniatury na ImgBB (CDN). Wymaga IMGBB_API_KEY.",
            fg_color="#9D174D", hover_color="#831843")
        self.btn_lifestyle = _sb("  Miniatury AI", self._run_lifestyle,
            "rembg + Flux Pro — produkt na lifestyle tle. Otwiera zakładkę Miniatury AI.",
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
        self.btn_bl_sync = _sb("  Sync stany magazynowe (BL)", self._run_bl_sync,
            "Kopiuje stany z hurtowni (MultiStore, Kathay, ...) do Marketia Katalog.\n"
            "Dla klonów `SKU-N` bierze stan rodzica `SKU`. Wymaga w .env:\n"
            "BASELINKER_SOURCE_INVENTORY_IDS + BASELINKER_TARGET_INVENTORY_ID.",
            fg_color="#0E7490", hover_color="#0C6177")
        self.btn_olx = _sb("  Wystaw na OLX", self._run_olx_publish,
            "Wystaw wybrane produkty jako oferty OLX (wymaga OLX_CLIENT_ID w .env).",
            fg_color="#4D7021", hover_color="#3A5518")
        self.btn_olx_refresh = _sb("  Odśwież kategorie OLX", self._run_olx_refresh_categories,
            "Pobiera drzewo kategorii OLX (jednorazowo, cache 7 dni).\n"
            "Przy pierwszym uruchomieniu otwiera przeglądarkę do autoryzacji.",
            fg_color="#4D7021", hover_color="#3A5518")

        # ── SYSTEM (bottom)
        ctk.CTkFrame(_sb_scroll, height=1, fg_color="#374151").pack(
            fill="x", padx=12, pady=(16, 4))
        _sb("  Wyczyść cache", self._clear_cache_dialog,
            "Usuwa dane z cache SQLite. Możesz wybrać które tabele.",
            fg_color="#7F1D1D", hover_color="#6B1919")
        _sb("  Ustawienia", self._open_settings,
            "Zarządzaj kluczami API: Gemini, ImgBB.",
            pady=(3, 16))

        # ── MAIN AREA (tabbed) ────────────────────────────────────────────────
        self._main_tabs = ctk.CTkTabview(
            self, fg_color="#F8FAFC", corner_radius=0,
            anchor="nw", border_width=0,
            segmented_button_fg_color="#E5E7EB",
            segmented_button_selected_color="#1D4ED8",
            segmented_button_unselected_color="#E5E7EB",
            segmented_button_selected_hover_color="#1E40AF",
            segmented_button_unselected_hover_color="#D1D5DB",
        )
        self._main_tabs.grid(row=1, column=1, sticky="nsew")

        tab_prod = self._main_tabs.add("  Produkty  ")
        tab_thumb = self._main_tabs.add("  Miniatury AI  ")

        # ── PRODUKTY TAB
        tab_prod.grid_columnconfigure(0, weight=1)
        tab_prod.grid_rowconfigure(1, weight=1)
        main = tab_prod

        self._build_filter_bar(main)

        self.list_frame = ctk.CTkScrollableFrame(main, label_text="", fg_color="#FFFFFF")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self._pagination_bar = tk.Frame(main, bg="#F1F5F9", height=36)
        self._pagination_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))

        # ── MINIATURY AI TAB
        self._build_thumbnail_tab(tab_thumb)

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
        total_calls = self._session_generated + self._session_cached
        cache_pct = int(self._session_cached / total_calls * 100) if total_calls else 0
        cost = self._session_cost_usd
        cost_str = f"${cost:.4f}" if cost > 0 else "$0.0000"

        self._stat_total.set(f"Produkty: {total}")
        self._stat_ai.set(f"Z opisem: {ai_done} ({pct}%)")
        self._stat_q.set(f"Q avg: {q_avg:.1f}" if scores else "Q avg: —")
        self._stat_cost.set(f"Koszt: {cost_str}")
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

        # Selection indicator (prawa strona paska)
        self._sel_label = ctk.CTkLabel(
            bar, text="", text_color="#2563EB",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        self._sel_label.pack(side="right", padx=(0, 4))
        self._sel_clear_btn = ctk.CTkButton(
            bar, text="× Odznacz", width=80, height=26,
            fg_color="transparent", border_width=1, border_color="#93C5FD",
            text_color="#2563EB", hover_color="#EFF6FF",
            command=self._clear_selection,
        )
        self._sel_clear_btn.pack(side="right", padx=(0, 8))
        self._sel_clear_btn.pack_forget()  # ukryty na start

    def _on_filter_brand(self, value: str) -> None:
        self._filter_brand = value
        self._page = 0
        self._render_table()
        self._maybe_save_session()

    def _on_filter_ai(self, value: str) -> None:
        self._filter_ai = value
        self._page = 0
        self._render_table()
        self._maybe_save_session()

    def _clear_filters(self) -> None:
        self._filter_brand = "Wszystkie"
        self._filter_ai = "Wszystkie"
        self._page = 0
        self._brand_menu.set("Wszystkie")
        self._ai_seg.set("Wszystkie")
        self._render_table()

    def _effective_products(self, base: list[Product]) -> list[Product]:
        """Return selected subset if any selection, else return base."""
        if self._selected_skus:
            return [p for p in base if p.sku in self._selected_skus]
        return base

    def _toggle_product_selection(self, sku: str, selected: bool) -> None:
        if selected:
            self._selected_skus.add(sku)
        else:
            self._selected_skus.discard(sku)
        self._update_sel_indicator()
        self._maybe_save_session()

    def _clear_selection(self) -> None:
        self._selected_skus.clear()
        self._update_sel_indicator()
        self._render_table()
        self._maybe_save_session()

    def _update_sel_indicator(self) -> None:
        if self._sel_label is None:
            return
        n = len(self._selected_skus)
        if n > 0:
            self._sel_label.configure(text=f"{n} zaznaczonych")
            self._sel_clear_btn.pack(side="right", padx=(0, 8))
        else:
            self._sel_label.configure(text="")
            self._sel_clear_btn.pack_forget()

    def _on_header_checkbox(self) -> None:
        select = self._header_sel_var.get()
        filtered = self._filtered_products()
        start = self._page * self._page_size
        page_items = filtered[start:start + self._page_size]
        for p in page_items:
            if select:
                self._selected_skus.add(p.sku)
            else:
                self._selected_skus.discard(p.sku)
        self._update_sel_indicator()
        self._render_table()

    def _update_brand_filter_options(self) -> None:
        brands = _all_known_brands()
        self._brand_menu.configure(values=["Wszystkie"] + brands)
        self._brand_menu.set("Wszystkie")
        # Fix 2026-07-01: bez resetowania state filter zostaje stara wartość ("homestein")
        # która po transform może już nie istnieć → _filtered_products zwraca [] → user
        # widzi pustą tabelę i musi klikać filtry żeby "odświeżyć".
        self._filter_brand = "Wszystkie"
        self._filter_ai = "Wszystkie"
        if hasattr(self, "_ai_seg"):
            self._ai_seg.set("Wszystkie")
        self._page = 0

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
        import time
        try:
            t0 = time.time()
            self.q.put(("status", "Parsuję XML…"))
            products = parse_xml(path)
            t_parse = time.time() - t0

            # Compute XML hash dla session identification (tani, ~100KB read).
            from app.cache.sqlite_cache import hash_xml_file
            try:
                xml_hash = hash_xml_file(path)
            except Exception:
                xml_hash = None

            t = time.time()
            self.q.put(("status", f"Parsuję XML: {len(products)} produktów ({t_parse:.1f}s). Ładuję cache EAN…"))
            self._hydrate_extra_eans(products)
            t_ean = time.time() - t

            t = time.time()
            self.q.put(("status", f"Ładuję cache packshotów (ImgBB)…"))
            n_thumbs = self._hydrate_thumbnail_urls(products)
            t_thumb = time.time() - t

            t = time.time()
            self.q.put(("status", f"Porównuję zmiany (diff)…"))
            diff = run_diff(products)
            t_diff = time.time() - t

            self.q.put(("status", f"Wczytano {len(products)} produktów w {time.time()-t0:.1f}s (parse={t_parse:.1f}s, ean={t_ean:.1f}s, thumbs={n_thumbs}, diff={t_diff:.1f}s)."))
            self.q.put(("loaded", products, path, diff, xml_hash))
        except Exception as e:
            self.q.put(("error", f"Parser: {e}"))

    @staticmethod
    def _hydrate_extra_eans(products: list[Product]) -> None:
        """Populate Product.extra_eans from SQLite cache after parsing."""
        from app.cache.sqlite_cache import get_extra_eans, open_cache
        with open_cache() as conn:
            for p in products:
                p.extra_eans = get_extra_eans(conn, p.sku)

    @staticmethod
    def _hydrate_thumbnail_urls(products: list[Product]) -> int:
        """Populate Product.thumbnail_url from imgbb_uploads cache after parsing.

        Bez tego user po zamknięciu apki traci przypisanie packshotu → eksport XML
        wraca do oryginałów dostawcy zamiast używać wgranego packshotu (2026-07-12d).
        Returns liczba produktów które dostały URL z cache.
        """
        from app.cache.sqlite_cache import open_cache
        from app.images.imgbb_uploader import get_cached_url
        n = 0
        with open_cache() as conn:
            for p in products:
                url = get_cached_url(conn, p.sku)
                if url:
                    p.thumbnail_url = url
                    n += 1
        return n

    def _run_transforms(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        target = self._effective_products(self.products)
        self.status_var.set("Transformuję…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._transform_worker, args=(target,), daemon=True).start()

    def _transform_worker(self, products: list[Product]):
        import time
        try:
            n = len(products)
            t0 = time.time()

            self.q.put(("status", f"Transformuję ({n} prod.): 1/7 marki…"))
            t = time.time()
            bm = BrandMapper()
            bm.map_products(products)
            bm.sanitize_manufacturer_names(products)
            t_brand = time.time() - t
            known = sum(1 for p in products if p.brand and p.brand != "unknown")

            self.q.put(("status", f"Transformuję ({n} prod.): 2/7 modele… (marki: {known}/{n} rozpoznanych w {t_brand:.1f}s)"))
            t = time.time()
            with open_cache() as conn:
                ModelNameGenerator(conn).assign_all(products)
            t_model = time.time() - t

            self.q.put(("status", f"Transformuję: 3/7 tytuły SEO…"))
            t = time.time()
            TitleTransformer().transform_all(products)
            # Load AI titles v4 z cache — nadpisz deterministic gdy cache trafi.
            # User request 2026-07-12e: cache = single source of truth, po restarcie
            # widać AI wersję bez klikania "Tytuły AI".
            from app.ai.title_generator import load_cached_ai_titles
            n_ai_titles = load_cached_ai_titles(products)
            t_title = time.time() - t

            self.q.put(("status", f"Transformuję: 4/7 walidacja EAN + cache opisów… (AI titles: {n_ai_titles}/{n})"))
            t = time.time()
            for p in products:
                p.ean_valid = validate_ean(p.ean)
            load_cached_descriptions(products)
            t_ean = time.time() - t

            self.q.put(("status", f"Transformuję: 5/7 czyszczenie opisów JUMI…"))
            t = time.time()
            stripped = strip_jumi_descriptions(products)
            t_strip = time.time() - t

            self.q.put(("status", f"Transformuję: 6/7 atrybuty…"))
            t = time.time()
            for p in products:
                enrich_product_attributes(p)
            t_attr = time.time() - t

            self.q.put(("status", f"Transformuję: 7/7 mapowanie kategorii Allegro…"))
            t = time.time()
            _cat_map = load_category_map()
            map_all_products(products, _cat_map)
            t_cat = time.time() - t

            total = time.time() - t0
            self.q.put(("status",
                f"Transformy OK — {total:.1f}s "
                f"(marki={t_brand:.1f}s modele={t_model:.1f}s tytuły={t_title:.1f}s "
                f"ean/cache={t_ean:.1f}s jumi={t_strip:.1f}s atrs={t_attr:.1f}s kat={t_cat:.1f}s)"
            ))
            if stripped:
                self.q.put(("status", f"Usunięto {stripped} opisów JUMI — zostaną wygenerowane przez AI."))
            self.q.put(("transformed",))
        except Exception as e:
            self.q.put(("error", f"Transform: {e}"))

    def _run_ai(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML (krok 3).")
            return

        if not os.getenv("GEMINI_API_KEYS", "").strip():
            messagebox.showerror(
                APP_NAME,
                "❌ Brak klucza Gemini API\n\n"
                "Ustaw GEMINI_API_KEYS w pliku .env (paid Google Cloud key).",
            )
            return

        base = self._effective_products(self.products)
        pending = [p for p in base if not getattr(p, "ai_done", False)]
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
            target=self._ai_worker, args=(base,), daemon=True
        ).start()

    def _ai_worker(self, products: list[Product]):
        # Heartbeat state — czytany przez _poll_queue na main thread (bez self.after z workera!).
        # UWAGA: żadnych Tk ops (self.after / widget.configure) w tym threadzie — tylko self.q.put i primitive attrs.
        self._ai_worker_alive = True
        self._ai_worker_op = "Generuję opisy"
        self._ai_worker_start = time.monotonic()
        self._ai_worker_total = len(products)
        self._ai_worker_done = 0

        _prog_re = re.compile(r"\[(\d+)/(\d+)\]")

        def log_and_count(msg: str):
            m = _prog_re.search(msg)
            if m:
                self._ai_worker_done = int(m.group(1))
            self.q.put(("status", msg))

        # Watchdog: Python 3.14 + google-genai SDK czasem wisi w cleanup PO wszystkich
        # SUCCESS (`[N/N]` osiągnięte, opisy zapisane w cache, ale `generate_descriptions`
        # nie return'uje). Rozwiązanie: uruchom w wewnętrznym daemon threadzie i monitoruj
        # postęp. Gdy done==total i minie 8s bez zmian, uznaj że opisy są gotowe (są w
        # cache), emit "ai_done" i wraca — daemon thread umrze z procesem (2026-07-13).
        result = [None]
        exc = [None]
        def _run():
            try:
                result[0] = generate_descriptions(
                    products, progress_callback=log_and_count,
                    cancel_check=lambda: self._cancel_event.is_set(),
                )
            except Exception as e:
                exc[0] = e

        inner = threading.Thread(target=_run, daemon=True)
        inner.start()

        last_done = -1
        last_change_ts = time.monotonic()
        while inner.is_alive():
            inner.join(timeout=1.0)
            if not inner.is_alive():
                break
            if self._cancel_event.is_set():
                break
            # Watchdog: batch reported wszystkie N/N i od 8s bez updateów → assume done
            if self._ai_worker_done >= self._ai_worker_total > 0:
                if self._ai_worker_done != last_done:
                    last_done = self._ai_worker_done
                    last_change_ts = time.monotonic()
                elif time.monotonic() - last_change_ts > 8.0:
                    # Assume finished — opisy są w cache; policz z cache
                    print(f"[AI_WORKER WATCHDOG] {self._ai_worker_done}/{self._ai_worker_total} zaraportowane, brak zmian 8s — kończę (opisy w cache)", flush=True)
                    break

        if exc[0]:
            tb = "".join(traceback.format_exception(type(exc[0]), exc[0], exc[0].__traceback__))
            print(f"[AI_WORKER] EXCEPTION:\n{tb}", flush=True)
            self.q.put(("error", f"AI: {exc[0]}"))
        elif self._cancel_event.is_set():
            # Count what's actually in cache
            from app.cache.sqlite_cache import get_cached_description
            with open_cache() as conn:
                saved = sum(1 for p in products if get_cached_description(conn, p.sku))
            self.q.put(("cancelled", f"Zatrzymano. Zapisano: {saved} opisów."))
        elif result[0] is not None:
            # Normal return (unlikely bo SDK wisi)
            submitted, cached, cost = result[0]
            self.q.put(("ai_done", submitted, cached, cost))
        else:
            # Watchdog fired: count from cache
            from app.cache.sqlite_cache import get_cached_description
            with open_cache() as conn:
                saved = sum(1 for p in products if get_cached_description(conn, p.sku))
            self.q.put(("ai_done", saved, 0, 0.0))
        self._ai_worker_alive = False

    # Unattended mode usunięty 2026-07-04 — paid Gemini key nie ma cooldown,
    # więc regular `_run_ai` wystarcza. Był potrzebny tylko dla free tier daily quota.

    def _export_xml(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Brak produktów do eksportu.")
            return

        target = self._effective_products(self.products)
        total = len(self.products)
        n = len(target)
        if not n:
            messagebox.showinfo(APP_NAME, "Wybrana selekcja jest pusta.")
            return
        extra_clones = sum(len(getattr(p, "extra_eans", []) or []) for p in target)
        final_entries = n + extra_clones
        clones_note = (
            f"\n+ {extra_clones} klonów multi-EAN = {final_entries} wpisów <product> w XML"
            if extra_clones else ""
        )
        if n < total:
            if not messagebox.askyesno(
                APP_NAME,
                f"Masz zaznaczone {n} z {total} wczytanych produktów.\n\n"
                f"Eksport obejmie TYLKO te {n} zaznaczone.{clones_note}\n"
                f"Pozostałe {total - n} produktów NIE znajdzie się w XML.\n\n"
                "Kontynuować?",
            ):
                return
        elif extra_clones:
            if not messagebox.askyesno(
                APP_NAME,
                f"Eksport obejmie {n} produktów{clones_note}.\n\n"
                f"Każdy klon to osobny wpis z innym EAN-em (te same dane, "
                f"suffix SKU `-1`, `-2`, …).\n\n"
                "Kontynuować?",
            ):
                return

        # Pre-export: check for local thumbnails not yet uploaded to ImgBB (within target only)
        needs_upload = [
            p for p in target
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
                    self._run_imgbb_then_export(needs_upload, target)
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

        status_msg = (
            f"Eksportuję XML ({n} prod. + {extra_clones} klonów = {final_entries} wpisów)…"
            if extra_clones else f"Eksportuję XML ({n} prod.)…"
        )
        self.status_var.set(status_msg)
        threading.Thread(
            target=self._export_worker, args=(target, output_path), daemon=True
        ).start()

    def _run_imgbb_then_export(self, products_to_upload: list, target_products: list | None = None) -> None:
        """Upload thumbnails to ImgBB, then open save dialog and export."""
        # Stash target so post-upload export keeps the same selection.
        self._pending_export_target = target_products if target_products is not None else self.products
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
                # Also upload infographics for the same target — bez URL nie pójdą jako image_extra_N.
                infographics_dir = OUTPUT_DIR / "infographics"
                if infographics_dir.exists():
                    target = getattr(self, "_pending_export_target", None) or self.products
                    upload_infographics(target, infographics_dir, progress_callback=log)
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

    # ── MINIATURY AI TAB ─────────────────────────────────────────────────────

    def _build_thumbnail_tab(self, parent: ctk.CTkFrame) -> None:
        """Build the Miniatury AI tab content."""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        # Header
        hdr = ctk.CTkFrame(parent, fg_color="#F0F9FF", corner_radius=8)
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        ctk.CTkLabel(
            hdr, text="Generator miniatur AI — Flux Pro",
            font=ctk.CTkFont(size=14, weight="bold"), text_color="#0E7490",
        ).pack(side="left", padx=12, pady=8)
        ctk.CTkLabel(
            hdr,
            text="rembg wycina tło  ·  Flux Pro generuje scenę lifestyle  ·  Pillow nakłada produkt",
            font=ctk.CTkFont(size=11), text_color="#6B7280",
        ).pack(side="left", padx=(0, 12))

        # Controls
        ctrl = ctk.CTkFrame(parent, fg_color="#F3F4F6", corner_radius=8)
        ctrl.grid(row=1, column=0, sticky="ew", padx=12, pady=4)

        self._thumb_brand_vars: dict[str, ctk.BooleanVar] = {}
        self._thumb_brands_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self._thumb_brands_frame.pack(side="left", fill="x", expand=True, padx=12, pady=8)

        self._thumb_force_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            ctrl, text="Regeneruj istniejące",
            variable=self._thumb_force_var,
            font=ctk.CTkFont(size=11),
        ).pack(side="right", padx=(0, 12), pady=8)

        self._thumb_status_var = ctk.StringVar(value="Wczytaj XML i kliknij Generuj.")
        ctk.CTkLabel(
            ctrl, textvariable=self._thumb_status_var,
            font=ctk.CTkFont(size=11), text_color="#B45309",
        ).pack(side="right", padx=(0, 16), pady=8)

        self._thumb_gen_btn = ctk.CTkButton(
            ctrl, text="  Generuj lifestyle AI",
            fg_color="#0E7490", hover_color="#0C6177",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._thumb_start,
        )
        self._thumb_gen_btn.pack(side="right", padx=(0, 8), pady=8)

        # Preview area
        self._thumb_preview_frame = ctk.CTkScrollableFrame(
            parent, fg_color="#FFFFFF", label_text=""
        )
        self._thumb_preview_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 8))
        self._thumb_preview_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._thumb_preview_frame,
            text="Wczytaj XML i kliknij 'Generuj lifestyle AI'.\n"
                 "Wygenerowane miniatury pojawią się tutaj.",
            text_color="#9CA3AF", font=ctk.CTkFont(size=12),
        ).pack(pady=40)

    def _thumb_tab_refresh(self) -> None:
        """Rebuild brand checkboxes based on currently loaded products."""
        from app.images.lifestyle_composer import _BRAND_SCENES
        frame = self._thumb_brands_frame
        for w in frame.winfo_children():
            w.destroy()
        self._thumb_brand_vars.clear()

        if not self.products:
            ctk.CTkLabel(
                frame, text="Brak produktów — wczytaj XML.",
                text_color="#9CA3AF", font=ctk.CTkFont(size=11),
            ).pack(side="left")
            return

        brands = sorted({
            p.brand for p in self.products
            if p.brand and p.brand != "unknown" and getattr(p, "images", [])
        })
        from app.gui.brand_colors import get_brand_chip_colors
        for brand in brands:
            var = ctk.BooleanVar(value=brand.lower() in _BRAND_SCENES)
            self._thumb_brand_vars[brand] = var
            count = sum(1 for p in self.products if p.brand == brand and getattr(p, "images", []))
            bg, fg = get_brand_chip_colors(brand)
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(side="left", padx=4)
            ctk.CTkCheckBox(
                row, text=f"{brand.upper()} ({count})",
                variable=var,
                font=ctk.CTkFont(size=11),
                fg_color=bg, hover_color=bg,
                text_color="#374151",
                checkmark_color=fg,
            ).pack()

        total = sum(1 for p in self.products if p.brand in brands and getattr(p, "images", []))
        self._thumb_status_var.set(
            f"~{total} produktów  ·  ~${total * 0.04:.2f} koszt  ·  FAL_KEY wymagany"
        )

    def _thumb_start(self) -> None:
        selected = [b for b, v in self._thumb_brand_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("Brak wyboru", "Zaznacz co najmniej jedną markę.", parent=self)
            return
        fal_key = os.getenv("FAL_KEY", "").strip() or os.getenv("FAL_API_KEY", "").strip()
        if not fal_key:
            messagebox.showerror(
                "Brak FAL_KEY",
                "Dodaj klucz fal.ai do pliku .env jako FAL_KEY=twój_klucz\n"
                "Konto: fal.ai → Dashboard → API Keys",
                parent=self,
            )
            return
        self._thumb_gen_btn.configure(state="disabled", text="Generuję…")
        self._thumb_status_var.set("Generuję… może potrwać kilka minut.")
        threading.Thread(
            target=self._thumb_worker,
            args=(selected, self._thumb_force_var.get()),
            daemon=True,
        ).start()

    def _thumb_worker(self, brands: list[str], force: bool) -> None:
        from app.images.lifestyle_composer import generate_lifestyle_thumbnails

        def _progress(msg: str) -> None:
            self.after(0, lambda m=msg: self._thumb_status_var.set(m))

        try:
            done, skipped = generate_lifestyle_thumbnails(
                self._effective_products(self.products),
                brands=brands,
                force=force,
                progress_callback=_progress,
            )
            self.after(0, lambda: self._thumb_finish(done, skipped))
        except Exception as e:
            self.after(0, lambda err=str(e): (
                messagebox.showerror("Błąd Lifestyle AI", err, parent=self),
                self._thumb_gen_btn.configure(state="normal", text="  Generuj lifestyle AI"),
                self._thumb_status_var.set("Błąd — sprawdź FAL_KEY."),
            ))

    def _thumb_finish(self, done: int, skipped: int) -> None:
        self._thumb_gen_btn.configure(state="normal", text="  Generuj lifestyle AI")
        self._thumb_status_var.set(f"Gotowe: {done} wygenerowanych, {skipped} pominiętych.")
        self._lifestyle_done(done)
        self._thumb_show_results()

    def _thumb_show_results(self) -> None:
        """Display generated thumbnails in the preview area."""
        from pathlib import Path
        thumb_dir = Path(__file__).resolve().parents[2] / "output" / "thumbnails"
        files = sorted(thumb_dir.glob("*_lifestyle.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)

        frame = self._thumb_preview_frame
        for w in frame.winfo_children():
            w.destroy()

        if not files:
            ctk.CTkLabel(
                frame, text="Brak wygenerowanych miniatur.",
                text_color="#9CA3AF", font=ctk.CTkFont(size=12),
            ).pack(pady=20)
            return

        # Grid: 4 columns
        cols = 4
        grid = ctk.CTkFrame(frame, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=8, pady=8)
        for i in range(cols):
            grid.grid_columnconfigure(i, weight=1)

        for idx, fpath in enumerate(files[:40]):
            col = idx % cols
            row = idx // cols
            try:
                img = Image.open(fpath).resize((200, 200))
                ctk_img = ctk.CTkImage(img, size=(200, 200))
                cell = ctk.CTkFrame(grid, fg_color="#F8FAFC", corner_radius=8)
                cell.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
                ctk.CTkLabel(cell, image=ctk_img, text="").pack(pady=(8, 2))
                ctk.CTkLabel(
                    cell, text=fpath.stem[:20],
                    font=ctk.CTkFont(size=9), text_color="#6B7280",
                ).pack(pady=(0, 6))
            except Exception:
                pass

    def _run_lifestyle(self) -> None:
        self._main_tabs.set("  Miniatury AI  ")
        self._thumb_tab_refresh()

    def _lifestyle_done(self, count: int) -> None:
        self.status_var.set(f"Lifestyle AI: {count} miniaturek zapisanych jako *_lifestyle.jpg.")

    def _open_settings(self) -> None:
        SettingsWindow(self)

    def _run_bl_sync(self) -> None:
        """Sync stany hurtowni → target katalog (Marketia Katalog, rodzice + klony)."""
        token = os.getenv("BASELINKER_TOKEN", "").strip()
        src_raw = os.getenv("BASELINKER_SOURCE_INVENTORY_IDS", "").strip()
        tgt_raw = os.getenv("BASELINKER_TARGET_INVENTORY_ID", "").strip()

        if not token or not src_raw or not tgt_raw:
            messagebox.showwarning(
                APP_NAME,
                "Brak konfiguracji BaseLinker.\n\n"
                "Uzupełnij w .env:\n"
                "  BASELINKER_TOKEN=<twój token>\n"
                "  BASELINKER_SOURCE_INVENTORY_IDS=52173,45513  (hurtownie)\n"
                "  BASELINKER_TARGET_INVENTORY_ID=36715          (Marketia Katalog — klony PARENT-N)",
            )
            return

        try:
            source_ids = [int(x.strip()) for x in src_raw.split(",") if x.strip()]
        except ValueError:
            messagebox.showerror(
                APP_NAME,
                "BASELINKER_SOURCE_INVENTORY_IDS musi być comma-sep int-y (np. 52173,45513).",
            )
            return
        try:
            target_id = int(tgt_raw)
        except ValueError:
            messagebox.showerror(
                APP_NAME,
                "BASELINKER_TARGET_INVENTORY_ID musi być liczbą (np. 36715).",
            )
            return

        self.btn_bl_sync.configure(state="disabled")
        self.status_var.set("BaseLinker: sync stanów magazynowych…")
        self._op_start()
        self.progress.configure(mode="indeterminate")
        self.progress.start()

        def _worker():
            try:
                from app.sync import BaseLinkerError, sync_from_wholesale_to_target
                result = sync_from_wholesale_to_target(
                    token=token,
                    source_inventory_ids=source_ids,
                    target_inventory_id=target_id,
                    log=lambda m: self.q.put(("status", f"BL: {m}")),
                )
                self.q.put(("bl_sync_done", result))
            except BaseLinkerError as e:
                self.q.put(("bl_sync_error", str(e)))
            except Exception as e:
                from urllib.error import URLError
                import socket
                if isinstance(e, (URLError, socket.gaierror, socket.timeout)):
                    msg = (
                        "Brak połączenia z BaseLinker.\n\n"
                        "Sprawdź:\n"
                        "• Czy masz internet?\n"
                        "• Czy api.baselinker.com jest dostępne (firewall/VPN)?\n\n"
                        f"Szczegóły techniczne: {e}"
                    )
                else:
                    msg = f"Nieoczekiwany błąd: {e}"
                self.q.put(("bl_sync_error", msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _run_olx_publish(self) -> None:
        """Otwiera OLXPublishDialog dla zaznaczonych produktów.

        Wymaga OLX_CLIENT_ID + OLX_CLIENT_SECRET w .env. Jeśli brak — pokazuje
        instrukcję rejestracji aplikacji w developer.olx.pl.
        """
        if not os.getenv("OLX_CLIENT_ID", "").strip():
            messagebox.showwarning(
                APP_NAME,
                "Brak konfiguracji OLX API.\n\n"
                "Aby wystawiać oferty na OLX:\n"
                "1. Zarejestruj aplikację w https://developer.olx.pl/\n"
                "2. Ustaw redirect_uri: http://127.0.0.1:8765/callback\n"
                "3. Uzupełnij w .env:\n"
                "   OLX_CLIENT_ID=<twój client_id>\n"
                "   OLX_CLIENT_SECRET=<twój client_secret>\n"
                "   OLX_CONTACT_NAME=<imię do kontaktu>\n"
                "   OLX_CONTACT_PHONE=<+48123456789>\n"
                "   OLX_LOCATION_CITY_ID=<ID miasta z GET /cities>",
            )
            return

        # Weź zaznaczone albo wszystkie widoczne po filtrach.
        selected = getattr(self, "_selected_skus", None) or set()
        if selected:
            products = [p for p in self.products if p.sku in selected]
        else:
            products = list(getattr(self, "_filtered_products", self.products))

        if not products:
            messagebox.showinfo(APP_NAME, "Brak produktów do wystawienia.")
            return

        OLXPublishDialog(self, products=products)

    def _run_olx_refresh_categories(self) -> None:
        if not os.getenv("OLX_CLIENT_ID", "").strip() or not os.getenv("OLX_CLIENT_SECRET", "").strip():
            messagebox.showwarning(
                APP_NAME,
                "Brak konfiguracji OLX API.\n\n"
                "Uzupełnij w .env: OLX_CLIENT_ID, OLX_CLIENT_SECRET.\n"
                "Zarejestruj aplikację: https://developer.olx.pl/",
            )
            return

        self.btn_olx_refresh.configure(state="disabled")
        self.status_var.set("OLX: pobieranie kategorii…")
        self._op_start()
        self.progress.configure(mode="indeterminate")
        self.progress.start()

        def _worker():
            try:
                auth = OLXAuth()
                try:
                    auth.get_valid_token()
                except OLXAuthError:
                    self.q.put(("status", "OLX: autoryzacja w przeglądarce…"))
                    auth.interactive_login()
                client = OLXClient(auth)
                with open_cache() as conn:
                    count = refresh_categories(client, conn)
                self.q.put(("olx_refresh_done", count))
            except (OLXAuthError, OLXAPIError) as e:
                self.q.put(("olx_refresh_error", str(e)))
            except Exception as e:
                self.q.put(("olx_refresh_error", f"Nieoczekiwany błąd: {e}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _clear_cache_dialog(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Wyczyść cache")
        win.geometry("420x560")
        win.minsize(420, 500)
        win.resizable(False, True)
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
                "Potwierdź wyczyszczenie cache",
                f"⚠️ Wyczyścić {len(selected)} tabel(e)?\n\n"
                "Konsekwencje:\n"
                "• Stracisz cache opisów AI, tytułów AI, miniatur — będą generowane od zera\n"
                "• Dodatkowe EAN-y klonów zostaną usunięte (jeśli zaznaczone)\n"
                "• Regeneracja opisów AI = koszt $$ (Gemini API)\n"
                "• Operacja NIEODWRACALNA\n\n"
                "Na pewno kontynuować?",
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
        target = self._effective_products(self.products)
        models = [p for p in target if p.model_name]
        if not models:
            messagebox.showinfo(APP_NAME, "Brak przypisanych modeli. Uruchom krok 3 (Transformy).")
            return
        ModelRenameWindow(self, target, on_done=self._render_table)

    def _open_model_audit(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        has_models = any(p.model_name for p in self.products)
        if not has_models:
            messagebox.showinfo(APP_NAME, "Brak przypisanych modeli. Uruchom krok 3 (Transformy).")
            return
        ModelAuditWindow(self, self.products, on_done=self._render_table)

    def _remove_models(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        target = self._effective_products(self.products)
        n = len(target)
        if not messagebox.askyesno(
            APP_NAME,
            f"⚠️ Usunąć model z {n} produktów?\n\n"
            "Konsekwencje:\n"
            "• Cache modelu (SKU → nazwa) zostanie skasowany\n"
            "• Wygenerowane opisy HTML (jeśli zawierają model) zostaną nieaktualne\n"
            "• Trzeba będzie uruchomić 'Zaktualizuj model w opisach' albo regenerować opisy AI\n\n"
            "Na pewno kontynuować?",
        ):
            return
        skus = [p.sku for p in target]
        with open_cache() as conn:
            placeholders = ",".join("?" * len(skus))
            conn.execute(
                f"DELETE FROM sku_model_names WHERE used_for_sku IN ({placeholders})",  # noqa: S608
                skus,
            )
        from app.transformer.title_transformer import TitleTransformer
        tt = TitleTransformer()
        for p in target:
            old_model = p.model_name or ""
            p.model_name = ""
            if old_model:
                pat = re.compile(
                    r'(?<![A-Za-z0-9])' + re.escape(old_model) + r'(?![A-Za-z0-9])',
                    re.IGNORECASE,
                )
                for field in ("name", "description", "description_extra_1", "description_extra_2"):
                    val = getattr(p, field, "") or ""
                    if val:
                        cleaned = pat.sub("", val)
                        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
                        setattr(p, field, cleaned)
            tt.transform(p)
        self._render_table()
        self.status_var.set(f"Usunięto model z {n} produktów.")

    def _reset_models(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Skasować cache nazw modeli dla wczytanych produktów i przeliczyć je na nowo?\n\n"
            "Opisy AI i miniatury NIE zostaną usunięte.",
        ):
            return
        target = self._effective_products(self.products)
        skus = [p.sku for p in target]
        with open_cache() as conn:
            placeholders = ",".join("?" * len(skus))
            conn.execute(
                f"DELETE FROM sku_model_names WHERE used_for_sku IN ({placeholders})",  # noqa: S608
                skus,
            )
        self.status_var.set("Przeliczam modele…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._reset_models_worker, args=(target,), daemon=True).start()

    def _reset_models_worker(self, products: list[Product]) -> None:
        try:
            with open_cache() as conn:
                ModelNameGenerator(conn).assign_all(products)
            TitleTransformer().transform_all(products)
            self.q.put(("transformed",))
        except Exception as e:
            self.q.put(("error", f"Odśwież modele: {e}"))

    def _sync_model_in_descriptions(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return
        pool_path = Path(__file__).resolve().parents[2] / "data" / "model_names.json"
        try:
            import json
            with pool_path.open(encoding="utf-8") as f:
                all_names: set[str] = {n.lower() for names in json.load(f).values() for n in names}
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Nie można wczytać model_names.json: {exc}")
            return

        _BOUNDARY = r'(?<![A-Za-zÀ-ɏ0-9])'
        updated = 0
        with open_cache() as conn:
            for p in self.products:
                if not p.model_name:
                    continue
                current_base = p.model_name.split()[0]
                current_lower = current_base.lower()
                desc_changed = False
                for field in ("description", "description_extra_1", "description_extra_2"):
                    val = getattr(p, field, "") or ""
                    if not val:
                        continue
                    new_val = val
                    for name in all_names:
                        if name == current_lower:
                            continue
                        pat = re.compile(
                            _BOUNDARY + re.escape(name) + r'(?![A-Za-zÀ-ɏ0-9])',
                            re.IGNORECASE,
                        )
                        new_val = pat.sub(current_base, new_val)
                    if new_val != val:
                        setattr(p, field, new_val)
                        if field == "description":
                            save_description(conn, p.sku, new_val)
                            desc_changed = True
                if desc_changed:
                    updated += 1
        self._render_table()
        messagebox.showinfo(APP_NAME, f"Zaktualizowano opisy dla {updated} produktów.")

    def _regen_titles(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        target = self._effective_products(self.products)
        self.status_var.set("Regeneruję tytuły…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        threading.Thread(target=self._regen_titles_worker, args=(target,), daemon=True).start()

    def _regen_titles_worker(self, products: list[Product]) -> None:
        try:
            TitleTransformer().transform_all(products)
            self.q.put(("transformed",))
        except Exception as e:
            self.q.put(("error", f"Regeneruj tytuły: {e}"))

    def _run_ai_titles(self) -> None:
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj XML.")
            return
        target = self._effective_products(self.products)
        if not target:
            return
        opts = _ai_titles_dialog(self, len(target), self._last_ai_custom_instruction)
        if opts is None:
            return
        self._last_ai_custom_instruction = opts["custom_instruction"]
        self._maybe_save_session()
        self.btn_ai_titles.configure(state="disabled")
        status = "Generuję tytuły AI"
        if opts["custom_instruction"].strip():
            status += " z instrukcjami użytkownika"
        self.status_var.set(status + "…")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self._op_start()
        threading.Thread(
            target=self._ai_titles_worker,
            args=(target, opts["force"], opts["custom_instruction"]),
            daemon=True,
        ).start()

    def _ai_titles_worker(self, products: list[Product], force: bool, custom_instruction: str = "") -> None:
        # Heartbeat state — reused by _tick_ai_heartbeat na main thread.
        self._ai_worker_alive = True
        self._ai_worker_op = "Generuję tytuły AI"
        self._ai_worker_start = time.monotonic()
        self._ai_worker_total = len(products)
        self._ai_worker_done = 0
        try:
            from app.ai.title_generator import AITitleGenerator
            from app.cache.sqlite_cache import open_cache
            with open_cache() as conn:
                gen = AITitleGenerator(conn)
                def _progress(done, total, custom_id, error=None):
                    self._ai_worker_done = done
                    self.q.put(("ai_titles_progress", done, total, error))
                updated = gen.apply_to_products(
                    products, force=force, progress_cb=_progress,
                    cancel_check=lambda: self._cancel_event.is_set(),
                    custom_instruction=custom_instruction,
                )
            self.q.put(("ai_titles_done", updated, len(products)))
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[AI_TITLES] EXCEPTION:\n{tb}", flush=True)
            self.q.put(("error", f"Tytuły AI: {e}"))
        finally:
            self._ai_worker_alive = False

    def _open_title_edit(self, product: Product) -> None:
        TitleEditDialog(self, product, on_done=self._render_table)

    def _open_ean_edit(self, product: Product) -> None:
        EanEditDialog(self, product, on_done=self._render_table)

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
            messagebox.showerror(
                APP_NAME,
                "❌ Brak klucza ImgBB\n\n"
                "Co zrobić:\n"
                "1. Sidebar → SYSTEM → Ustawienia\n"
                "2. Sekcja 'ImgBB API' → wklej klucz\n"
                "3. Klucz dostaniesz za darmo na: imgbb.com → konto → API",
            )
            return
        target = self._effective_products(self.products)
        with_thumb = [p for p in target if (THUMB_DIR / f"{p.sku}.jpg").exists()]
        if not with_thumb:
            messagebox.showinfo(APP_NAME, "Brak wygenerowanych miniaturek (w wybranej selekcji).\nUruchom najpierw krok 4.5.")
            return
        scope = f" (z {len(self.products)} wczytanych)" if len(target) < len(self.products) else ""
        if not messagebox.askyesno(APP_NAME, f"Uploadować {len(with_thumb)} miniaturek do ImgBB{scope}?\nURLe zostaną wstawione jako images[0] w eksporcie XML."):
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
            # Also upload infographics — bez URL nie pojawią się jako image_extra_N w XML.
            infographics_dir = OUTPUT_DIR / "infographics"
            info_uploaded = 0
            if not self._cancel_event.is_set() and infographics_dir.exists():
                info_uploaded = upload_infographics(
                    products, infographics_dir, progress_callback=log,
                )
            if self._cancel_event.is_set():
                self.q.put(("cancelled", f"Zatrzymano. Przesłano: {uploaded} miniaturek + {info_uploaded} infografik."))
            else:
                self.q.put(("imgbb_done", uploaded + info_uploaded))
        except Exception as e:
            self.q.put(("error", f"ImgBB: {e}"))

    def _run_thumbnails(self):
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML (krok 3).")
            return

        target = self._effective_products(self.products)
        with_images = [p for p in target if getattr(p, "images", [])]
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

    # ── Infografiki AI ────────────────────────────────────────────────────

    def _run_infographics(self):
        """Generate parameter infographics for all products with a thumbnail."""
        if not self.products:
            messagebox.showinfo(APP_NAME, "Najpierw wczytaj i przetransformuj XML.")
            return

        target = self._effective_products(self.products)
        if not target:
            messagebox.showinfo(APP_NAME, "Brak produktów do przetworzenia.")
            return

        est_seconds = len(target) * 15
        mins = max(1, est_seconds // 60)
        if not messagebox.askyesno(
            APP_NAME,
            f"Wygenerować infografiki dla {len(target)} produktów?\n"
            f"(~15 s / prod. → ok. {mins} min)\n\n"
            f"Wymagane: uprzednio wygenerowane miniatury (packshoty) w output/thumbnails/."
        ):
            return

        self.btn_infographics.configure(state="disabled")
        self.status_var.set(f"Generuję infografiki dla {len(target)} prod…")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self._op_start()
        threading.Thread(
            target=self._infographics_worker, args=(target,), daemon=True
        ).start()

    def _infographics_worker(self, products: list) -> None:
        """Background worker: batch-generate infographics via generate_all_infographics."""
        total = len(products)
        infographics_dir = OUTPUT_DIR / "infographics"

        def cb(done: int, _total: int, sku: str) -> None:
            self.q.put(("status", f"Infografiki: {done}/{total} — {sku}"))
            if total:
                self.q.put(("progress", done / total))

        try:
            generated, skipped = generate_all_infographics(
                products,
                thumb_dir=THUMB_DIR,
                output_dir=infographics_dir,
                progress_cb=cb,
                cancel_check=lambda: self._cancel_event.is_set(),
            )
            if self._cancel_event.is_set():
                self.q.put(("cancelled", f"Zatrzymano. Wygenerowano: {generated} infografik."))
            else:
                self.q.put(("infographics_done", generated, skipped))
        except Exception as e:
            self.q.put(("error", f"Infografiki: {e}"))

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
        product.brand = new_brand
        tt = TitleTransformer()
        # Fix 2026-07-01: TitleTransformer ma atrybut `brand_display` (dict str→str), nie `brand_data`.
        # Poprzedni kod `tt.brand_data.get(...).get("name")` rzucał AttributeError → crash.
        product.manufacturer_name = tt.brand_display.get(new_brand) or new_brand.upper()
        tt.transform(product)
        # Fix 2026-07-03: jeśli aktywny filtr marki nie pasuje do nowej marki,
        # produkt zniknie z listy — user myśli że "nic się nie zmieniło".
        # Reset filter do "Wszystkie" żeby zmiana była widoczna.
        if self._filter_brand not in ("Wszystkie", new_brand):
            self._filter_brand = "Wszystkie"
            try:
                self._brand_menu.set("Wszystkie")
            except Exception:
                pass
            self._page = 0
        self._render_table()
        if self._detail_win is not None:
            try:
                if self._detail_win.winfo_exists():
                    self._detail_win.load_product(product)
            except Exception:
                pass

    def _on_model_change(self, product: Product, new_model: str) -> None:
        old_model = product.model_name or ""
        product.model_name = new_model

        # Persist to SQLite cache (overrides auto-generated name)
        with open_cache() as conn:
            conn.execute("DELETE FROM sku_model_names WHERE used_for_sku = ?", (product.sku,))
            if new_model:
                from app.cache.sqlite_cache import save_sku_model_name
                save_sku_model_name(conn, product.sku, product.brand or "", new_model)

        # Strip/replace old model name in all text fields (word-boundary aware)
        if old_model and old_model.upper() != (new_model or "").upper():
            pat = re.compile(
                r'(?<![A-Za-z0-9])' + re.escape(old_model) + r'(?![A-Za-z0-9])',
                re.IGNORECASE,
            )
            text_fields = ["description", "description_extra_1", "description_extra_2"]
            if not new_model:  # clearing — also clean name so title rebuilds clean
                text_fields = ["name"] + text_fields
            replacement = new_model if new_model else ""
            for field in text_fields:
                val = getattr(product, field, "") or ""
                if val:
                    cleaned = pat.sub(replacement, val)
                    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
                    setattr(product, field, cleaned)

        # Retransform title when model changes (including clearing)
        if old_model != new_model:
            from app.transformer.title_transformer import TitleTransformer
            TitleTransformer().transform(product)

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

        target = self._effective_products(self.products)

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
            win, text=f"{len(target)} produktów zostanie zaktualizowanych",
            text_color="#6B7280", font=ctk.CTkFont(size=11),
        ).pack(pady=(0, 14))

        brand_var = ctk.StringVar(value=brands[0])
        ctk.CTkOptionMenu(win, values=brands, variable=brand_var, width=260).pack(pady=4)

        _target = target  # capture for _apply closure

        def _apply() -> None:
            new_brand = brand_var.get()
            if not new_brand:
                return
            win.destroy()

            tt = TitleTransformer()
            # Fix 2026-07-03: TitleTransformer trzyma dict{str→str} w `brand_display`,
            # NIE `brand_data` (`brand_data` to raw JSON tylko w __init__, nie zapisany na self).
            # Poprzedni kod `tt.brand_data.get(...)` rzucał AttributeError i cały _apply crashował
            # bez widocznego błędu (Tk połyka wyjątki z button command do stderr).
            new_disp = tt.brand_display.get(new_brand, new_brand)

            # Collect per-product old state BEFORE any mutations
            old_states: list[tuple] = []
            for p in _target:
                old_states.append((
                    p,
                    p.brand or "",
                    p.model_name or "",
                    tt.brand_display.get(p.brand or "", (p.brand or "").upper()),
                ))

            # Pass 1: update brand/manufacturer only — title comes AFTER model reassignment
            for p, _, _, _ in old_states:
                p.brand = new_brand
                p.manufacturer_name = tt.brand_display.get(new_brand, new_brand.upper())

            # Pass 2: batch model reassignment (modifies product.name with new series word)
            with open_cache() as conn:
                skus = [p.sku for p in _target]
                conn.execute(
                    f"DELETE FROM sku_model_names WHERE used_for_sku IN ({','.join('?' * len(skus))})",
                    skus,
                )
                ModelNameGenerator(conn).assign_all(_target)

            # Pass 3: regenerate titles now that product.name has the new series names
            for p in _target:
                tt.transform(p)

            # Pass 4: description replacements using the now-correct model names
            for p, old_brand, old_model, old_disp in old_states:
                new_model = p.model_name or ""
                old_base = old_model.split()[0] if old_model else ""
                new_base = new_model.split()[0] if new_model else ""
                replacements = []
                if old_disp and old_disp.upper() != new_disp.upper():
                    replacements.append(
                        (re.compile(re.escape(old_disp), re.IGNORECASE), new_disp)
                    )
                if old_model and new_model and old_model.upper() != new_model.upper():
                    replacements.append((
                        re.compile(
                            r'(?<![A-Za-z0-9])' + re.escape(old_model) + r'(?![A-Za-z0-9])',
                            re.IGNORECASE,
                        ),
                        new_model,
                    ))
                if old_base and new_base and old_base.upper() != new_base.upper():
                    replacements.append((
                        re.compile(
                            r'(?<![A-Za-z0-9])' + re.escape(old_base) + r'(?![A-Za-z0-9])',
                            re.IGNORECASE,
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

            # Fix 2026-07-03: reset filter state — inaczej jeśli user miał aktywny
                # filtr marki (np. "homestein"), po zmianie na "hopla_toys" filter zwraca 0 produktów
                # i user widzi pustą listę (myśli że nic się nie stało).
            self._filter_brand = "Wszystkie"
            self._filter_ai = "Wszystkie"
            self._page = 0
            try:
                self._brand_menu.set("Wszystkie")
                self._ai_seg.set("Wszystkie")
            except Exception:
                pass
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
        # BULLETPROOF: każdy exception w handlerze MUSI być złapany żeby self.after(100, ...) na końcu
        # zawsze się wykonał. Bez tego jedna cicha AttributeError zabija cały polling → statusy zamarzają,
        # user musi klikać żeby cokolwiek się odświeżyło. Nie polegamy na "queue.Empty catches all".
        try:
            self._drain_queue()
            self._tick_ai_heartbeat()
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[POLL_QUEUE] EXCEPTION swallowed:\n{tb}", flush=True)
        self.after(100, self._poll_queue)

    def _tick_ai_heartbeat(self) -> None:
        """Heartbeat statusu podczas generowania AI. Wywoływany co 100ms w _poll_queue.
        Emit co 2s żeby user widział że coś się dzieje nawet podczas długich cooldownów.
        """
        if not getattr(self, "_ai_worker_alive", False):
            return
        now = time.monotonic()
        last = getattr(self, "_ai_hb_last", 0.0)
        if now - last < 2.0:
            return
        self._ai_hb_last = now
        elapsed = int(now - self._ai_worker_start)
        done = getattr(self, "_ai_worker_done", 0)
        total = getattr(self, "_ai_worker_total", 0)
        op = getattr(self, "_ai_worker_op", "AI")
        self.status_var.set(
            f"⏳ {op}… {elapsed}s (postęp: {done}/{total})"
        )

    def _drain_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                try:
                    self._handle_msg(msg)
                except Exception as e:
                    tb = traceback.format_exc()
                    print(f"[HANDLE_MSG] EXCEPTION on tag {msg[0]!r}:\n{tb}", flush=True)
        except queue.Empty:
            pass

    def _handle_msg(self, msg):
        tag = msg[0]
        if tag == "loaded":
            # Backward compat: obsłuż stary 4-tuple i nowy 5-tuple (z xml_hash).
            if len(msg) == 5:
                _, products, path, diff, xml_hash = msg
            else:
                _, products, path, diff = msg
                xml_hash = None
            self.products = products
            self._session_xml_hash = xml_hash
            self._selected_skus.clear()
            self._update_sel_indicator()
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            diff_str = f"  •  Nowe: {diff.new} / Zmienione: {diff.changed} / Bez zmian: {diff.unchanged}"
            self.summary_var.set(
                f"📂 {Path(path).name}  •  produktów: {len(products)}{diff_str}"
            )
            self._render_table()
            self._update_brand_filter_options()
            self._update_stats()
            self._thumb_tab_refresh()
            # Auto-Transformy po Wczytaj XML — user request 2026-07-12d: "nie trzeba było
            # transformów znowu klikać". Deterministyczne (brand/category/title/desc-strip)
            # + load AI cache (descriptions, titles v4). Bezpieczne, idempotent.
            self.status_var.set("Wczytano. Uruchamiam automatycznie Transformy…")
            self._run_transforms()
            # Session state restore — rozpoznaj plik po hash i przywróć filtry/selekcję.
            if xml_hash:
                from app.cache.sqlite_cache import load_session_state, open_cache
                try:
                    with open_cache() as conn:
                        saved = load_session_state(conn, xml_hash)
                    if saved:
                        self._restore_state(saved)
                        self._render_table()
                        self.status_var.set(
                            "Wczytano + wykryto poprzednią sesję dla tego pliku — "
                            "przywrócono filtry, selekcję, custom instruction."
                        )
                except Exception as e:
                    print(f"[SESSION RESTORE] failed: {e}", flush=True)

        elif tag == "transformed":
            ai_done = sum(1 for p in self.products if getattr(p, "ai_done", False))
            self.summary_var.set(
                f"{self.summary_var.get()}  •  transformy OK  •  opisy cache: {ai_done}"
            )
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            self.status_var.set("Transformy OK. Krok 4 — Generuj opisy.")
            self._op_end()  # ← fix: bez tego stop button widoczny + status "Transformuję…" wisiał
            self._render_table()
            self._update_brand_filter_options()  # ← fix: refresh listy marek żeby nie wisiała stara
            self._update_stats()

        elif tag == "status":
            self.status_var.set(msg[1])

        elif tag == "progress":
            self.progress.set(msg[1])

        elif tag == "ai_done":
            _, submitted, cached, cost = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            ai_done = sum(1 for p in self.products if getattr(p, "ai_done", False))
            # Ile było w tej sesji do generacji (pending przed run)
            requested = getattr(self, "_ai_worker_total", 0)
            failed = max(0, requested - submitted)
            self.summary_var.set(
                f"{self.summary_var.get().split('•')[0]}• opisy AI: {ai_done}"
            )
            cost_str = f" | Koszt sesji: ${cost:.4f}" if cost > 0 else ""
            if failed > 0 and submitted == 0:
                self.status_var.set(
                    f"❌ 0/{requested} wygenerowanych. Wszystkie requesty do Gemini nieudane."
                )
                messagebox.showwarning(
                    APP_NAME,
                    f"Wygenerowano 0 z {requested} opisów.\n\n"
                    f"Możliwe przyczyny:\n"
                    f"• Klucz Gemini API nieprawidłowy lub wygasł\n"
                    f"• Chwilowy problem z Google Cloud API\n"
                    f"• Brak internetu / błąd sieci\n\n"
                    f"Sprawdź terminal / stderr — tam będzie stack trace z konkretnym błędem."
                )
            elif failed > 0:
                self.status_var.set(
                    f"⚠️ {submitted}/{requested} wygenerowanych, {failed} fail (quota/error){cost_str}"
                )
            else:
                self.status_var.set(
                    f"✅ Opisy gotowe. Wygenerowano: {submitted} | Cache: {cached}{cost_str}"
                )
            self.btn_ai.configure(state="normal")
            self._op_end()
            # Fix 2026-07-03: jeśli user miał filtr "Bez opisu" gdy odpalał AI,
            # po zakończeniu wszystkie mają ai_done=True → filter zwraca 0 produktów
            # → user widzi pustą listę i myśli "nic się nie stało".
            # Reset AI filter do "Wszystkie" żeby zmiana była widoczna.
            if self._filter_ai != "Wszystkie":
                self._filter_ai = "Wszystkie"
                try:
                    self._ai_seg.set("Wszystkie")
                except Exception:
                    pass
                self._page = 0
            self._render_table()
            self._session_generated += submitted
            self._session_cached += cached
            self._session_cost_usd += cost
            self._update_stats()

        elif tag == "ai_titles_progress":
            _, done, total, error = msg
            # CTkProgressBar API: set(0.0-1.0), nie ttk.Progressbar['value'/'maximum']
            if total > 0:
                self.progress.set(done / total)
            suffix = f" (err: {error})" if error else ""
            self.status_var.set(f"Tytuły AI: {done}/{total}{suffix}")

        elif tag == "ai_titles_done":
            _, updated, total = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            self.btn_ai_titles.configure(state="normal")
            self._op_end()
            self.status_var.set(
                f"Tytuły AI gotowe. Zaktualizowano: {updated}/{total}."
            )
            # Fix 2026-07-03: reset filtra brand (nowe tytuły mogą zawierać nowe brandy)
            # + reset page. Filter AI zostawiamy — tytuły nie zmieniają ai_done.
            if self._filter_brand != "Wszystkie":
                self._filter_brand = "Wszystkie"
                try:
                    self._brand_menu.set("Wszystkie")
                except Exception:
                    pass
                self._page = 0
            self._render_table()

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
                target = getattr(self, "_pending_export_target", None) or self.products
                _n = len(target)
                _clones = sum(len(getattr(p, "extra_eans", []) or []) for p in target)
                _msg = (
                    f"Eksportuję XML ({_n} prod. + {_clones} klonów = {_n + _clones} wpisów)…"
                    if _clones else f"Eksportuję XML ({_n} prod.)…"
                )
                self.status_var.set(_msg)
                threading.Thread(
                    target=self._export_worker, args=(target, output_path), daemon=True
                ).start()
                self._pending_export_target = None

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

        elif tag == "infographics_done":
            _, generated, skipped = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            self.status_var.set(
                f"Wygenerowano {generated} infografik ({skipped} bez packshotu). "
                f"Wgraj na ImgBB przed eksportem."
            )
            self.btn_infographics.configure(state="normal")
            self._op_end()
            messagebox.showinfo(
                APP_NAME,
                f"Infografiki gotowe!\n"
                f"Wygenerowane: {generated}\n"
                f"Bez packshotu (pomijane): {skipped}\n"
                f"Folder: output/infographics/",
            )

        elif tag == "exported":
            _, count, path = msg
            self.status_var.set(f"Wyeksportowano {count} wpisów <product> → {path}")
            messagebox.showinfo(
                APP_NAME,
                f"Eksport zakończony!\n{count} wpisów <product> w XML\n"
                f"(produkty bazowe + klony multi-EAN)\n{path}",
            )

        elif tag == "bl_sync_done":
            _, result = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            self.btn_bl_sync.configure(state="normal")
            self._op_end()
            short = (
                f"Sync zakończony: {result.clones_synced} PIDs zaktualizowanych "
                f"(matched {result.parents_resolved}/{result.total_products} SKU)."
            )
            detail_text = result.summary()
            self.status_var.set(f"BaseLinker: {short}")

            # Zapisz pełny raport diagnostyczny do pliku
            from pathlib import Path
            report_path = Path.home() / "Documents" / "marketia-sync-debug.txt"
            try:
                report_path.write_text(
                    "=" * 70 + "\n"
                    + "MARKETIA SYNC — RAPORT DIAGNOSTYCZNY\n"
                    + "=" * 70 + "\n\n"
                    + short + "\n\n"
                    + result.diagnostic_report(),
                    encoding="utf-8",
                )
                saved_path = report_path
            except Exception:
                saved_path = None

            warnings_text = "\n".join(f"• {w}" for w in result.warnings)
            has_warnings = bool(result.warnings)
            icon = "⚠️" if (has_warnings or result.clones_synced == 0) else "✅"

            SyncReportDialog(
                self,
                title=APP_NAME,
                short_summary=short,
                detail_text=detail_text,
                warnings_text=warnings_text,
                report_path=saved_path,
                icon=icon,
            )

        elif tag == "bl_sync_error":
            _, err = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.btn_bl_sync.configure(state="normal")
            self._op_end()
            self.status_var.set(f"BaseLinker sync: błąd — {err}")
            messagebox.showerror(APP_NAME, f"Sync nie powiódł się:\n\n{err}")

        elif tag == "olx_refresh_done":
            _, count = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(1.0)
            self.btn_olx_refresh.configure(state="normal")
            self._op_end()
            self.status_var.set(f"OLX: {count} kategorii w cache")
            messagebox.showinfo(APP_NAME, f"Pobrano {count} kategorii OLX.\nCache ważny 7 dni.")

        elif tag == "olx_refresh_error":
            _, err = msg
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.btn_olx_refresh.configure(state="normal")
            self._op_end()
            self.status_var.set(f"OLX refresh: błąd — {err}")
            messagebox.showerror(APP_NAME, f"Nie udało się pobrać kategorii OLX:\n\n{err}")

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
            # Widoczny błąd w statusie — nie tylko lakoniczne "Błąd." + messagebox.
            err_short = err.replace("\n", " ")[:200]
            self.status_var.set(f"❌ {err_short}")
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


    def _render_table(self):
        for child in self.list_frame.winfo_children():
            child.destroy()

        # Empty state — pierwsza wizyta lub po wyczyszczeniu
        if not self.products:
            empty = ctk.CTkFrame(self.list_frame, fg_color="#FFFFFF")
            empty.grid(row=0, column=0, sticky="nsew", pady=80)
            ctk.CTkLabel(
                empty, text="📋",
                font=ctk.CTkFont(size=56),
                text_color="#9CA3AF",
            ).pack(pady=(0, 8))
            ctk.CTkLabel(
                empty, text="Wczytaj XML aby zacząć",
                font=ctk.CTkFont(size=18, weight="bold"),
                text_color="#374151",
            ).pack(pady=(0, 6))
            ctk.CTkLabel(
                empty,
                text="Sidebar po lewej → DANE → Wczytaj XML\n"
                     "(albo upuść plik XML w to okno)",
                font=ctk.CTkFont(size=12),
                text_color="#6B7280",
                justify="center",
            ).pack(pady=(0, 16))
            ctk.CTkButton(
                empty, text="📂  Wczytaj plik XML",
                width=200, height=40,
                fg_color="#1D4ED8", hover_color="#1E40AF",
                font=ctk.CTkFont(size=13, weight="bold"),
                command=self._pick_xml,
            ).pack()
            self._update_pagination(0, 1)
            return

        header_row = ctk.CTkFrame(self.list_frame, fg_color="#F3F4F6", corner_radius=4)
        header_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        # "Select all" checkbox dla bieżącej strony
        self._header_sel_var = ctk.BooleanVar(value=False)
        header_sel_cb = ctk.CTkCheckBox(
            header_row, variable=self._header_sel_var, text="",
            width=24, checkbox_width=16, checkbox_height=16,
            command=self._on_header_checkbox,
        )
        header_sel_cb.grid(row=0, column=0, padx=(4, 0), pady=4)
        header_row.grid_columnconfigure(0, minsize=28)

        for i, (text, w) in enumerate(
            zip(("", "SKU", "TYTUŁ / NAZWA", "MARKA", "KAT.", "MODEL", "EAN", "OK", "AI", "Q"),
                ProductRow.COL_WIDTHS[1:])  # skip checkbox width
        ):
            header_row.grid_columnconfigure(i + 1, minsize=w)
            ctk.CTkLabel(
                header_row, text=text, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#6B7280",
            ).grid(row=0, column=i + 1, sticky="w", padx=4, pady=4)

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
                on_select=self._toggle_product_selection,
                on_title_edit=lambda prod=p: self._open_title_edit(prod),
                on_ean_edit=lambda prod=p: self._open_ean_edit(prod),
                is_selected=p.sku in self._selected_skus,
            )
            row.grid(row=idx, column=0, sticky="ew", pady=1)

        # Ustaw stan headera: zaznaczony jeśli WSZYSTKIE na stronie są w selekcji
        if page_items:
            all_sel = all(p.sku in self._selected_skus for p in page_items)
            self._header_sel_var.set(all_sel)

        self._update_pagination(total, total_pages)

        # Reset scroll do góry po każdym renderze — bez tego CTkScrollableFrame
        # trzyma poprzednią pozycję i viewport pokazuje puste miejsce po filter/page/transform.
        try:
            self.after(0, lambda: self.list_frame._parent_canvas.yview_moveto(0))
        except Exception:
            pass

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
            self._maybe_save_session()

    def _next_page(self) -> None:
        self._page += 1
        self._render_table()
        self._maybe_save_session()


if __name__ == "__main__":
    App().mainloop()
