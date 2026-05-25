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
from app.transformer.xml_diff import run_diff, STATUS_NEW, STATUS_CHANGED
from app.exporter.xml_exporter import export_xml
from app.images.thumbnail_generator import generate_thumbnails, THUMB_DIR
from app.images.imgbb_uploader import upload_thumbnails
from app.gui.preview import open_preview
from app.validator import validate_ean, get_label

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

APP_NAME = "Marketia XML Pro"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

DIFF_COLORS = {
    "new":       "#1a6f3a",
    "changed":   "#b08000",
    "unchanged": None,
}


class ProductRow(ctk.CTkFrame):
    # SKU | TYTUŁ | MARKA | MODEL | EAN | T | AI | Q
    COL_WIDTHS = (130, 340, 110, 100, 130, 40, 40, 50)

    def __init__(self, master, product: Product, **kwargs):
        diff = getattr(product, "diff_status", None)
        bg = DIFF_COLORS.get(diff) if diff else None
        super().__init__(master, fg_color=bg or "transparent", **kwargs)
        for i, w in enumerate(self.COL_WIDTHS):
            self.grid_columnconfigure(i, minsize=w, weight=0)

        ctk.CTkLabel(self, text=product.sku, anchor="w").grid(row=0, column=0, sticky="w", padx=4)
        ctk.CTkLabel(
            self, text=product.title or product.name, anchor="w", wraplength=330
        ).grid(row=0, column=1, sticky="w", padx=4)
        ctk.CTkLabel(self, text=product.brand or "—", anchor="w").grid(row=0, column=2, sticky="w", padx=4)
        ctk.CTkLabel(self, text=product.model_name or "—", anchor="w").grid(row=0, column=3, sticky="w", padx=4)

        ean_color = "#1f883d" if getattr(product, "ean_valid", True) else "#d1242f"
        ctk.CTkLabel(self, text=product.ean or "—", anchor="w", text_color=ean_color).grid(
            row=0, column=4, sticky="w", padx=4
        )

        title_len = len(product.title or "")
        t_ok = "✓" if 0 < title_len <= 75 else "✗"
        t_color = "#1f883d" if t_ok == "✓" else "#d1242f"
        ctk.CTkLabel(self, text=t_ok, text_color=t_color).grid(row=0, column=5, sticky="w", padx=4)

        ai_sym = "🤖" if getattr(product, "ai_done", False) else "·"
        ctk.CTkLabel(self, text=ai_sym).grid(row=0, column=6, sticky="w", padx=4)

        score = getattr(product, "quality_score", -1)
        if score >= 0:
            _, sc = get_label(score)
            ctk.CTkLabel(self, text=str(score), text_color=sc, font=ctk.CTkFont(weight="bold")).grid(
                row=0, column=7, sticky="w", padx=4
            )
        else:
            ctk.CTkLabel(self, text="—").grid(row=0, column=7, sticky="w", padx=4)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1450x900")
        self.minsize(1200, 700)

        self.products: list[Product] = []
        self.q: queue.Queue = queue.Queue()
        self._xml_path: str | None = None
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
        sidebar = ctk.CTkFrame(self, width=210, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="MARKETIA\nXML PRO", font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            sidebar, text="v2 — z Claude AI",
            text_color="#888", font=ctk.CTkFont(size=10),
        ).pack(pady=(0, 18))

        ctk.CTkButton(sidebar, text="1. Wczytaj XML", command=self._pick_xml).pack(
            fill="x", padx=12, pady=4
        )
        ctk.CTkButton(sidebar, text="2. Marka (auto)", command=self._no_op).pack(
            fill="x", padx=12, pady=4
        )
        ctk.CTkButton(sidebar, text="3. Uruchom transformy", command=self._run_transforms).pack(
            fill="x", padx=12, pady=4
        )
        self.btn_ai = ctk.CTkButton(
            sidebar, text="4. Generuj opisy (AI)", command=self._run_ai,
            fg_color="#1a6f3a", hover_color="#145c2f",
        )
        self.btn_ai.pack(fill="x", padx=12, pady=4)
        self.btn_thumb = ctk.CTkButton(
            sidebar, text="4.5 Generuj miniatury", command=self._run_thumbnails,
            fg_color="#7c3aed", hover_color="#6d28d9",
        )
        self.btn_thumb.pack(fill="x", padx=12, pady=4)
        self.btn_imgbb = ctk.CTkButton(
            sidebar, text="4.6 Upload ImgBB", command=self._run_imgbb,
            fg_color="#9d174d", hover_color="#831843",
        )
        self.btn_imgbb.pack(fill="x", padx=12, pady=4)
        ctk.CTkButton(
            sidebar, text="Podgląd opisów HTML", command=self._open_preview,
            fg_color="#374151", hover_color="#1f2937",
        ).pack(fill="x", padx=12, pady=(12, 4))
        self.btn_export = ctk.CTkButton(
            sidebar, text="5. Eksport XML", command=self._export_xml,
            fg_color="#0a5c99", hover_color="#074880",
        )
        self.btn_export.pack(fill="x", padx=12, pady=4)

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

        self.list_frame = ctk.CTkScrollableFrame(main, label_text="Produkty")
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
        self._stat_ai    = ctk.StringVar(value="Z opisem: —")
        self._stat_q     = ctk.StringVar(value="Q avg: —")
        self._stat_cost  = ctk.StringVar(value="Koszt: —")
        self._stat_cache = ctk.StringVar(value="Cache: —")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))

        for var in (self._stat_total, self._stat_ai, self._stat_q, self._stat_cost, self._stat_cache):
            ctk.CTkLabel(
                bar, textvariable=var, anchor="w",
                font=ctk.CTkFont(size=11),
                text_color="#aaa",
            ).pack(side="left", padx=10)

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
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 4))

        ctk.CTkLabel(bar, text="Marka:").pack(side="left", padx=(8, 2))
        self._brand_menu = ctk.CTkOptionMenu(
            bar,
            values=["Wszystkie"],
            width=160,
            command=self._on_filter_brand,
        )
        self._brand_menu.pack(side="left", padx=(0, 12))

        ctk.CTkLabel(bar, text="Status AI:").pack(side="left", padx=(0, 2))
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

        if not messagebox.askyesno(
            APP_NAME,
            f"Wygenerować miniatury dla {len(with_images)} produktów?\n"
            f"(rembg usuwa tło + białe 1200×1200 + scale trick)\n"
            f"Zapis: output/thumbnails/",
        ):
            return

        self.btn_thumb.configure(state="disabled")
        self.status_var.set(f"Generuję miniatury dla {len(with_images)} prod…")
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        threading.Thread(
            target=self._thumb_worker, args=(with_images,), daemon=True
        ).start()

    def _thumb_worker(self, products: list):
        total = len(products)

        def log(msg: str):
            self.q.put(("status", msg))
            # extract i/total from message "Miniatury: i/total — sku"
            try:
                i = int(msg.split(":")[1].split("/")[0].strip())
                self.q.put(("progress", i / total))
            except Exception:
                pass

        try:
            done, skipped = generate_thumbnails(products, progress_callback=log)
            self.q.put(("thumb_done", done, skipped))
        except Exception as e:
            self.q.put(("error", f"Miniatury: {e}"))

    def _no_op(self):
        messagebox.showinfo(
            APP_NAME,
            "Marka liczona automatycznie podczas kroku 3 (Uruchom transformy).",
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

        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _render_table(self):
        for child in self.list_frame.winfo_children():
            child.destroy()

        header_row = ctk.CTkFrame(self.list_frame, fg_color="#1f1f1f")
        header_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        for i, (text, w) in enumerate(
            zip(("SKU", "TYTUŁ / NAZWA", "MARKA", "MODEL", "EAN", "OK", "AI", "Q"),
                ProductRow.COL_WIDTHS)
        ):
            header_row.grid_columnconfigure(i, minsize=w)
            ctk.CTkLabel(
                header_row, text=text, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#ddd",
            ).grid(row=0, column=i, sticky="w", padx=4, pady=4)

        cap = 300
        filtered = self._filtered_products()
        for idx, p in enumerate(filtered[:cap], 1):
            row = ProductRow(self.list_frame, p)
            row.grid(row=idx, column=0, sticky="ew", pady=1)

        if len(filtered) > cap:
            ctk.CTkLabel(
                self.list_frame,
                text=f"… (+{len(filtered) - cap} kolejnych)",
                text_color="#888",
            ).grid(row=cap + 1, column=0, pady=8)


if __name__ == "__main__":
    App().mainloop()
