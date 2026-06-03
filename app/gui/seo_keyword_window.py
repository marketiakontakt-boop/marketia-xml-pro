"""SEO Keyword Injector — extract top SEO phrases from competitor Allegro listings
and inject them naturally into product descriptions via Gemini.

Flow:
  1. User pastes competitor titles/descriptions
  2. Gemini extracts 10-15 Polish SEO phrases
  3. User reviews/edits phrase list
  4. User picks products (filter by brand)
  5. Gemini rewrites each description injecting phrases
  6. Original description saved as version in SQLite; product.description updated
"""
from __future__ import annotations

import os
import threading
from tkinter import messagebox

import customtkinter as ctk

from app.cache.sqlite_cache import open_cache, save_description
from app.parser.normalizer import Product
from app.validator.quality_scorer import score_description


class SeoKeywordWindow(ctk.CTkToplevel):

    def __init__(self, parent, products: list[Product], on_done=None):
        super().__init__(parent)
        self.title("SEO Keyword Injector")
        self.geometry("980x740")
        self.minsize(820, 620)
        self.grab_set()
        self.focus_force()

        self._products = products
        self._on_done = on_done
        self._selected_skus: set[str] = set()
        self._check_vars: dict[str, ctk.BooleanVar] = {}

        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, fg_color="#F9FAFB", corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # --- Step 1: paste competitor text ---
        ctk.CTkLabel(
            left, text="1. Wklej tytuły / opisy konkurencji (Allegro)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))

        self._input_text = ctk.CTkTextbox(left, height=200, font=ctk.CTkFont(size=11))
        self._input_text.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._input_text.insert(
            "0.0",
            "Przykład:\n"
            "Taboret obrotowy regulowany 37-49 cm biały czarny\n"
            "Krzesło barowe wysokie do kuchni baru regulacja wysokości\n"
            "Stołek barowy tapicerowany ergonomiczny do biura\n",
        )

        # --- Step 2: phrase list ---
        phrases_card = ctk.CTkFrame(left, fg_color="white", corner_radius=6)
        phrases_card.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        phrases_card.grid_rowconfigure(1, weight=1)
        phrases_card.grid_columnconfigure(0, weight=1)

        ph_hdr = ctk.CTkFrame(phrases_card, fg_color="transparent")
        ph_hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))
        ctk.CTkLabel(
            ph_hdr, text="2. Frazy SEO",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            ph_hdr, text="+ Dodaj", width=72, height=24,
            fg_color="#374151", hover_color="#1f2937",
            command=self._add_empty_phrase,
        ).pack(side="right")

        self._phrases_scroll = ctk.CTkScrollableFrame(phrases_card, fg_color="transparent", height=220)
        self._phrases_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._phrases_scroll.grid_columnconfigure(0, weight=1)

        self._hint = ctk.CTkLabel(
            self._phrases_scroll,
            text="Kliknij 'Ekstraktuj frazy SEO' aby wyodrębnić frazy z tekstu powyżej.",
            text_color="#9CA3AF", font=ctk.CTkFont(size=11), wraplength=330,
        )
        self._hint.pack(padx=8, pady=24)

        ctk.CTkButton(
            left, text="Ekstraktuj frazy SEO (Gemini)",
            fg_color="#1a6f3a", hover_color="#145c2f",
            command=self._extract_phrases,
        ).grid(row=3, column=0, padx=12, pady=(0, 12), sticky="ew")

        # --- Right panel: product selector ---
        right = ctk.CTkFrame(self, fg_color="#F9FAFB", corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            right, text="3. Wybierz produkty (z opisem AI)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))

        filter_f = ctk.CTkFrame(right, fg_color="transparent")
        filter_f.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        brands = sorted({p.brand for p in self._products if p.brand})
        self._brand_filter = ctk.StringVar(value="Wszystkie")
        ctk.CTkLabel(filter_f, text="Marka:", font=ctk.CTkFont(size=11)).pack(side="left")
        ctk.CTkOptionMenu(
            filter_f, variable=self._brand_filter,
            values=["Wszystkie"] + brands,
            width=130, height=26, font=ctk.CTkFont(size=11),
            command=self._refresh_list,
        ).pack(side="left", padx=(4, 10))
        ctk.CTkButton(
            filter_f, text="Zaznacz wszystkie", width=130, height=26,
            fg_color="#374151", hover_color="#1f2937",
            command=self._select_all,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            filter_f, text="Odznacz", width=80, height=26,
            fg_color="#374151", hover_color="#1f2937",
            command=self._deselect_all,
        ).pack(side="left")

        self._prod_scroll = ctk.CTkScrollableFrame(right, fg_color="white", corner_radius=6)
        self._prod_scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self._prod_scroll.grid_columnconfigure(0, weight=1)

        self._refresh_list()

        self._inject_btn = ctk.CTkButton(
            right, text="Wstrzyknij SEO w opisy (Gemini)",
            fg_color="#7c3aed", hover_color="#6d28d9",
            command=self._inject_seo,
        )
        self._inject_btn.grid(row=3, column=0, padx=12, pady=(0, 12), sticky="ew")

        # Status + close
        self._status_var = ctk.StringVar(value="Gotowy.")
        ctk.CTkLabel(
            self, textvariable=self._status_var,
            text_color="#6B7280", font=ctk.CTkFont(size=10), anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 2))

        ctk.CTkButton(
            self, text="Zamknij", fg_color="#6B7280", hover_color="#4B5563",
            command=self.destroy,
        ).grid(row=2, column=0, columnspan=2, pady=(0, 12))

    # ------------------------------------------------------------------
    # Product list
    # ------------------------------------------------------------------

    def _refresh_list(self, *_):
        for child in self._prod_scroll.winfo_children():
            child.destroy()
        self._check_vars.clear()

        brand = self._brand_filter.get()
        visible = [
            p for p in self._products
            if (brand == "Wszystkie" or p.brand == brand)
            and getattr(p, "ai_done", False)
        ]

        if not visible:
            ctk.CTkLabel(
                self._prod_scroll,
                text="Brak produktów z opisem AI w tej marce.\nUruchom krok 4.",
                text_color="#9CA3AF", font=ctk.CTkFont(size=11),
            ).pack(pady=20)
            return

        for p in visible:
            var = ctk.BooleanVar(value=p.sku in self._selected_skus)
            self._check_vars[p.sku] = var
            row = ctk.CTkFrame(self._prod_scroll, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkCheckBox(
                row, text="", variable=var, width=24,
                command=lambda s=p.sku, v=var: self._toggle(s, v),
            ).pack(side="left")
            label = f"{p.sku}  {(p.title or p.name or '')[:46]}"
            ctk.CTkLabel(
                row, text=label, font=ctk.CTkFont(size=10), anchor="w",
            ).pack(side="left", padx=4)

    def _toggle(self, sku: str, var: ctk.BooleanVar):
        if var.get():
            self._selected_skus.add(sku)
        else:
            self._selected_skus.discard(sku)

    def _select_all(self):
        for sku, var in self._check_vars.items():
            var.set(True)
            self._selected_skus.add(sku)

    def _deselect_all(self):
        for var in self._check_vars.values():
            var.set(False)
        self._selected_skus.clear()

    # ------------------------------------------------------------------
    # Phrase management
    # ------------------------------------------------------------------

    def _add_empty_phrase(self):
        try:
            self._hint.pack_forget()
        except Exception:
            pass
        self._add_phrase_row("")

    def _add_phrase_row(self, text: str):
        row = ctk.CTkFrame(self._phrases_scroll, fg_color="transparent")
        row.pack(fill="x", pady=1)
        row.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(row, font=ctk.CTkFont(size=11), height=28)
        entry.insert(0, text)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            row, text="✕", width=28, height=28,
            fg_color="#FEE2E2", text_color="#DC2626", hover_color="#FECACA",
            command=row.destroy,
        ).grid(row=0, column=1)

    def _get_phrases(self) -> list[str]:
        phrases = []
        for row in self._phrases_scroll.winfo_children():
            for widget in row.winfo_children():
                if isinstance(widget, ctk.CTkEntry):
                    val = widget.get().strip()
                    if val:
                        phrases.append(val)
        return phrases

    # ------------------------------------------------------------------
    # Gemini: extract phrases
    # ------------------------------------------------------------------

    def _extract_phrases(self):
        text = self._input_text.get("0.0", "end").strip()
        if len(text) < 20:
            messagebox.showwarning("SEO", "Wklej co najmniej kilka tytułów produktów z Allegro.")
            return
        key = self._api_key()
        if not key:
            messagebox.showerror("SEO", "Brak klucza Gemini API.\nDodaj GEMINI_API_KEYS lub GEMINI_API_KEY w .env.")
            return
        self._status_var.set("Ekstraktuję frazy SEO…")
        threading.Thread(target=self._extract_worker, args=(text, key), daemon=True).start()

    def _extract_worker(self, text: str, key: str):
        try:
            import google.genai as genai
            from google.genai import types

            client = genai.Client(api_key=key)
            prompt = (
                "Jesteś ekspertem SEO dla polskiego Allegro. "
                "Przeanalizuj poniższe tytuły i opisy produktów od konkurencji. "
                "Wyodrębnij 10-15 UNIKALNYCH fraz kluczowych SEO po polsku, "
                "które warto naturalnie wplatać w opisy produktów. "
                "Odpowiedz WYŁĄCZNIE listą fraz — jedna fraza na linię, bez numeracji, "
                "bez wyjaśnień, bez punktorów.\n\n"
                f"{text}"
            )
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    max_output_tokens=500,
                    temperature=0.3,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
                contents=[prompt],
            )
            phrases = [
                line.strip("•-– ").strip()
                for line in resp.text.strip().splitlines()
                if line.strip() and len(line.strip()) > 3
            ]
            self.after(0, lambda: self._show_phrases(phrases))
        except Exception as e:
            self.after(0, lambda: self._status_var.set(f"Błąd ekstrakcji: {e}"))

    def _show_phrases(self, phrases: list[str]):
        for child in self._phrases_scroll.winfo_children():
            child.destroy()
        try:
            self._hint.pack_forget()
        except Exception:
            pass
        for ph in phrases:
            self._add_phrase_row(ph)
        self._status_var.set(
            f"Wyodrębniono {len(phrases)} fraz. Edytuj listę, zaznacz produkty i kliknij 'Wstrzyknij'."
        )

    # ------------------------------------------------------------------
    # Gemini: inject SEO into descriptions
    # ------------------------------------------------------------------

    def _inject_seo(self):
        phrases = self._get_phrases()
        if not phrases:
            messagebox.showwarning("SEO", "Najpierw wyodrębnij lub dodaj frazy SEO (lewa kolumna).")
            return

        selected = [p for p in self._products if p.sku in self._selected_skus]
        if not selected:
            messagebox.showwarning("SEO", "Nie zaznaczono żadnych produktów.")
            return

        preview = ", ".join(phrases[:3]) + ("…" if len(phrases) > 3 else "")
        if not messagebox.askyesno(
            "SEO Keyword Injector",
            f"Wstrzyknąć frazy SEO w opisy {len(selected)} produktów?\n\n"
            f"Frazy: {preview}\n\n"
            "Oryginalne opisy zostaną zachowane w historii wersji (SQLite).",
        ):
            return

        key = self._api_key()
        if not key:
            messagebox.showerror("SEO", "Brak klucza Gemini API.")
            return

        self._inject_btn.configure(state="disabled")
        self._status_var.set(f"Wstrzykuję SEO w {len(selected)} opisów…")
        threading.Thread(
            target=self._inject_worker, args=(selected, phrases, key), daemon=True
        ).start()

    def _inject_worker(self, products: list[Product], phrases: list[str], key: str):
        import google.genai as genai
        from google.genai import types

        client = genai.Client(api_key=key)
        phrase_list = "\n".join(f"- {ph}" for ph in phrases)

        done = 0
        errors = 0

        with open_cache() as conn:
            for i, p in enumerate(products, 1):
                self.after(0, lambda i=i, sku=p.sku: self._status_var.set(
                    f"SEO: {i}/{len(products)} — {sku}"
                ))
                try:
                    prompt = (
                        "Jesteś copywriterem SEO dla polskiego Allegro. "
                        "Wstrzyknij NATURALNIE poniższe frazy kluczowe w podany opis produktu HTML. "
                        "Frazy mają pojawiać się płynnie w tekście — nie twórz osobnych sekcji. "
                        "Zachowaj oryginalną strukturę HTML (tagi, klasy CSS). "
                        "Nie zmieniaj ani nie wymyślaj faktów. "
                        "Odpowiedz WYŁĄCZNIE zmodyfikowanym kodem HTML — bez komentarzy.\n\n"
                        f"FRAZY DO WSTRZYKNIĘCIA:\n{phrase_list}\n\n"
                        f"OPIS HTML:\n{p.description}"
                    )
                    resp = client.models.generate_content(
                        model="gemini-2.5-flash",
                        config=types.GenerateContentConfig(
                            max_output_tokens=4096,
                            temperature=0.3,
                            thinking_config=types.ThinkingConfig(thinking_budget=0),
                        ),
                        contents=[prompt],
                    )
                    new_desc = resp.text.strip()
                    # Strip markdown code fences if Gemini wraps the response
                    if new_desc.startswith("```"):
                        new_desc = new_desc.split("\n", 1)[-1]
                    if new_desc.endswith("```"):
                        new_desc = new_desc.rsplit("```", 1)[0]
                    new_desc = new_desc.strip()

                    # Score the new description
                    new_score = score_description(new_desc)

                    # Persist to cache (saves version history + updates descriptions table)
                    save_description(conn, p.sku, new_desc, quality_score=new_score)

                    # Update product in memory
                    p.description = new_desc
                    p.quality_score = new_score
                    done += 1
                except Exception as e:
                    errors += 1
                    self.after(0, lambda err=str(e): self._status_var.set(f"Błąd: {err}"))

        self.after(0, lambda: self._finish(done, errors))

    def _finish(self, done: int, errors: int):
        self._inject_btn.configure(state="normal")
        msg = f"SEO wstrzyknięte w {done} opisów."
        if errors:
            msg += f"  Błędy: {errors}."
        self._status_var.set(msg)
        messagebox.showinfo(
            "SEO Keyword Injector",
            msg + "\n\nOpisy zaktualizowane w pamięci.\nKliknij 'Eksport XML' aby zapisać.",
        )
        if self._on_done:
            self._on_done()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _api_key(self) -> str:
        return (
            os.getenv("GEMINI_API_KEYS", "").split(",")[0].strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
        )
