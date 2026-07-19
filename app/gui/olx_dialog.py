"""OLX Publish dialog — SHELL structure.

Layout:
    - Left: list of selected products (checkboxes, incl. per-product status).
    - Right: form (category dropdown, required attributes, contact defaults).
    - Bottom: [Waliduj] [Wystaw N produktów] + progress bar + errors log.

Threading: publishing runs in a worker thread; GUI updates via `after()`.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

import customtkinter as ctk
from tkinter import messagebox

from app.cache.sqlite_cache import (
    get_olx_category_attributes,
    get_olx_offer,
    open_cache,
)
from app.olx.auth import OLXAuth, OLXAuthError
from app.olx.api import OLXClient, OLXAPIError
from app.olx.categories import (
    cache_is_stale,
    find_category_by_name,
    get_required_attributes,
    refresh_categories,
    refresh_attributes,
)
from app.olx.publisher import publish_products
from app.olx.validator import validate_product


_DARK = "#1F2937"
_TEXT = "#F9FAFB"
_MUTED = "#9CA3AF"
_ACCENT = "#0E7490"
_DANGER = "#7F1D1D"


class OLXPublishDialog(ctk.CTkToplevel):
    """Multi-product OLX publisher dialog."""

    def __init__(
        self,
        master,
        products: list,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._products = products
        self._on_done = on_done
        self._cancel_flag = False

        self.title(f"Wystaw na OLX ({len(products)} produktów)")
        self.geometry("980x680")
        self.minsize(880, 600)
        self.configure(fg_color=_DARK)
        self.grab_set()
        self.after(80, self.lift)

        # State
        self._selected: dict[str, bool] = {p.sku: True for p in products}
        self._category_id_var = ctk.StringVar(value="")
        self._city_id_var = ctk.StringVar(value=os.getenv("OLX_LOCATION_CITY_ID", ""))
        self._contact_name_var = ctk.StringVar(value=os.getenv("OLX_CONTACT_NAME", ""))
        self._contact_phone_var = ctk.StringVar(value=os.getenv("OLX_CONTACT_PHONE", ""))
        self._attr_vars: dict[str, ctk.StringVar] = {}
        self._status_labels: dict[str, ctk.CTkLabel] = {}

        self._build_ui()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Sidebar — product list with checkboxes
        sidebar = ctk.CTkFrame(self, fg_color="#111827", width=320, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(
            sidebar, text="Produkty do wystawienia",
            text_color=_TEXT, font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(fill="x", padx=14, pady=(14, 6))

        prod_scroll = ctk.CTkScrollableFrame(sidebar, fg_color="#111827")
        prod_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        for p in self._products:
            row = ctk.CTkFrame(prod_scroll, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)

            var = ctk.BooleanVar(value=self._selected[p.sku])
            cb = ctk.CTkCheckBox(
                row, text=f"{p.sku}",
                variable=var,
                text_color=_TEXT, font=ctk.CTkFont(size=11),
                command=lambda s=p.sku, v=var: self._selected.__setitem__(s, v.get()),
            )
            cb.pack(side="left", anchor="w")

            status = ctk.CTkLabel(
                row, text="·", text_color=_MUTED,
                font=ctk.CTkFont(size=10, weight="bold"), width=24,
            )
            status.pack(side="right", padx=(4, 2))
            self._status_labels[p.sku] = status

            with open_cache() as conn:
                offer = get_olx_offer(conn, p.sku)
            if offer:
                if offer["status"] == "published":
                    status.configure(text="✓", text_color="#22C55E")
                elif offer["status"]:
                    status.configure(text="✗", text_color="#EF4444")

        # Center — form
        form = ctk.CTkScrollableFrame(self, fg_color=_DARK)
        form.pack(side="left", fill="both", expand=True, padx=16, pady=12)

        self._build_form(form)

        # Bottom bar — actions
        bottom = ctk.CTkFrame(self, fg_color="#111827", height=110, corner_radius=0)
        bottom.pack(side="bottom", fill="x")
        bottom.pack_propagate(False)

        self._progress = ctk.CTkProgressBar(bottom, height=6, corner_radius=0)
        self._progress.set(0)
        self._progress.pack(fill="x", padx=0, pady=(0, 6))

        self._status_var = ctk.StringVar(value="Gotowe — kliknij 'Waliduj', potem 'Wystaw'.")
        ctk.CTkLabel(
            bottom, textvariable=self._status_var,
            text_color=_MUTED, font=ctk.CTkFont(size=11), anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 6))

        btns = ctk.CTkFrame(bottom, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkButton(
            btns, text="Waliduj", command=self._on_validate,
            fg_color="#374151", hover_color="#4B5563", width=140,
        ).pack(side="left", padx=(0, 8))

        self._btn_publish = ctk.CTkButton(
            btns, text="Wystaw zaznaczone", command=self._on_publish,
            fg_color=_ACCENT, hover_color="#0C6177", width=200,
        )
        self._btn_publish.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btns, text="Anuluj", command=self._on_cancel,
            fg_color=_DANGER, hover_color="#6B1919", width=100,
        ).pack(side="right")

    def _build_form(self, parent) -> None:
        def _label(text: str) -> None:
            ctk.CTkLabel(
                parent, text=text, text_color=_MUTED,
                font=ctk.CTkFont(size=10, weight="bold"), anchor="w",
            ).pack(fill="x", padx=4, pady=(10, 2))

        _label("KATEGORIA OLX")
        cat_row = ctk.CTkFrame(parent, fg_color="transparent")
        cat_row.pack(fill="x", padx=4)
        self._cat_search = ctk.CTkEntry(
            cat_row, placeholder_text="np. Meble ogrodowe",
        )
        self._cat_search.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            cat_row, text="Szukaj", width=90,
            command=self._on_search_category,
        ).pack(side="right")

        self._cat_result_var = ctk.StringVar(value="(brak wyboru)")
        ctk.CTkLabel(
            parent, textvariable=self._cat_result_var,
            text_color=_TEXT, font=ctk.CTkFont(size=11), anchor="w",
        ).pack(fill="x", padx=4, pady=(6, 0))

        _label("MIASTO (city_id)")
        ctk.CTkEntry(
            parent, textvariable=self._city_id_var,
            placeholder_text="ID miasta z GET /cities?query=",
        ).pack(fill="x", padx=4)

        _label("KONTAKT — IMIĘ (max 30 zn.)")
        ctk.CTkEntry(parent, textvariable=self._contact_name_var).pack(fill="x", padx=4)

        _label("KONTAKT — TELEFON (+48… lub 9 cyfr)")
        ctk.CTkEntry(parent, textvariable=self._contact_phone_var).pack(fill="x", padx=4)

        _label("WYMAGANE ATRYBUTY")
        self._attr_container = ctk.CTkFrame(parent, fg_color="transparent")
        self._attr_container.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkLabel(
            self._attr_container, text="(wybierz kategorię aby wczytać atrybuty)",
            text_color=_MUTED, font=ctk.CTkFont(size=10),
        ).pack(anchor="w")

    # ── Handlers ───────────────────────────────────────────────────────────

    def _on_search_category(self) -> None:
        query = self._cat_search.get().strip()
        if not query:
            return
        with open_cache() as conn:
            results = find_category_by_name(conn, query, limit=30)
        if not results:
            messagebox.showinfo(
                "OLX",
                "Brak kategorii w cache. Użyj przycisku 'Odśwież kategorie OLX' w sidebarze.",
            )
            return
        if len(results) == 1:
            self._pick_category(results[0])
            return
        self._show_category_picker(results)

    def _pick_category(self, cat: dict) -> None:
        self._category_id_var.set(str(cat["id"]))
        self._cat_result_var.set(f"[{cat['id']}] {cat['path']}")
        self._reload_attributes(int(cat["id"]))

    def _show_category_picker(self, matches: list[dict]) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Wybierz kategorię OLX")
        win.geometry("560x400")
        win.transient(self)
        win.grab_set()

        ctk.CTkLabel(
            win, text=f"Znaleziono {len(matches)} kategorii — wybierz:",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(pady=(12, 6))

        scroll = ctk.CTkScrollableFrame(win, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for cat in matches:
            btn = ctk.CTkButton(
                scroll,
                text=f"[{cat['id']}]  {cat['path']}",
                anchor="w",
                fg_color="transparent",
                text_color=_TEXT,
                hover_color="#E5E7EB",
                command=lambda c=cat: (self._pick_category(c), win.destroy()),
            )
            btn.pack(fill="x", pady=1)

    def _reload_attributes(self, cat_id: int, _refreshed: bool = False) -> None:
        for widget in self._attr_container.winfo_children():
            widget.destroy()
        self._attr_vars.clear()

        with open_cache() as conn:
            cached = get_olx_category_attributes(conn, cat_id)
            stale = cache_is_stale(conn, cat_id)

        if (not cached or stale) and not _refreshed:
            ctk.CTkLabel(
                self._attr_container, text="⏳ Ładowanie atrybutów OLX…",
                text_color=_MUTED, font=ctk.CTkFont(size=10),
            ).pack(anchor="w")
            threading.Thread(
                target=self._fetch_attrs_worker, args=(cat_id,), daemon=True,
            ).start()
            return

        with open_cache() as conn:
            required = get_required_attributes(conn, cat_id)

        if not required:
            ctk.CTkLabel(
                self._attr_container, text="(brak wymaganych atrybutów w cache)",
                text_color=_MUTED, font=ctk.CTkFont(size=10),
            ).pack(anchor="w")
            return

        for attr in required:
            row = ctk.CTkFrame(self._attr_container, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row, text=f"{attr['label']} ({attr['code']})",
                text_color=_TEXT, font=ctk.CTkFont(size=11), width=180, anchor="w",
            ).pack(side="left")

            var = ctk.StringVar()
            self._attr_vars[attr["code"]] = var
            options = attr.get("options") or []
            if options:
                values = [str(o.get("code") or o.get("value") or o) for o in options]
                ctk.CTkComboBox(
                    row, values=values, variable=var, width=220,
                ).pack(side="left", fill="x", expand=True)
            else:
                ctk.CTkEntry(row, textvariable=var).pack(
                    side="left", fill="x", expand=True
                )

    def _fetch_attrs_worker(self, cat_id: int) -> None:
        try:
            auth = OLXAuth()
            auth.get_valid_token()
            client = OLXClient(auth)
            with open_cache() as conn:
                refresh_attributes(client, conn, cat_id)
            self.after(0, lambda: self._reload_attributes(cat_id, _refreshed=True))
        except (OLXAuthError, OLXAPIError) as e:
            msg = str(e)
            self.after(0, lambda: self._show_attrs_error(msg))
        except Exception as e:
            msg = f"Nieoczekiwany błąd: {e}"
            self.after(0, lambda: self._show_attrs_error(msg))

    def _show_attrs_error(self, msg: str) -> None:
        for widget in self._attr_container.winfo_children():
            widget.destroy()
        ctk.CTkLabel(
            self._attr_container, text=f"❌ Błąd fetch atrybutów: {msg}",
            text_color="#DC2626", font=ctk.CTkFont(size=10),
            wraplength=380, justify="left",
        ).pack(anchor="w")

    def _on_validate(self) -> None:
        """Run validator for each selected product; count errors."""
        cat_id_raw = self._category_id_var.get().strip()
        if not cat_id_raw:
            messagebox.showwarning("OLX", "Wybierz kategorię przed walidacją.")
            return
        try:
            category_id = int(cat_id_raw)
        except ValueError:
            messagebox.showerror("OLX", "category_id musi być liczbą.")
            return

        attribute_values = {k: v.get() for k, v in self._attr_vars.items()}
        contact_name = self._contact_name_var.get().strip()
        contact_phone = self._contact_phone_var.get().strip()

        total = 0
        with_errors = 0
        with open_cache() as conn:
            for p in self._products:
                if not self._selected.get(p.sku):
                    continue
                total += 1
                errors = validate_product(
                    product=p, category_id=category_id,
                    attribute_values=attribute_values, conn=conn,
                    contact_name=contact_name, contact_phone=contact_phone,
                )
                lbl = self._status_labels.get(p.sku)
                if errors:
                    with_errors += 1
                    if lbl:
                        lbl.configure(text="!", text_color="#EAB308")
                else:
                    if lbl:
                        lbl.configure(text="✓", text_color="#22C55E")

        self._status_var.set(
            f"Walidacja: {total - with_errors}/{total} OK, {with_errors} z błędami."
        )

    def _on_publish(self) -> None:
        """Kick off publish worker thread."""
        client_id = os.getenv("OLX_CLIENT_ID", "").strip()
        client_secret = os.getenv("OLX_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            messagebox.showerror(
                "OLX",
                "Brak OLX_CLIENT_ID / OLX_CLIENT_SECRET w .env — nie można się zalogować.",
            )
            return

        try:
            category_id = int(self._category_id_var.get().strip())
            city_id = int(self._city_id_var.get().strip())
        except ValueError:
            messagebox.showerror("OLX", "category_id i city_id muszą być liczbami.")
            return

        selected_products = [p for p in self._products if self._selected.get(p.sku)]
        if not selected_products:
            messagebox.showinfo("OLX", "Nic nie zaznaczono.")
            return

        category_mapping = {p.sku: category_id for p in selected_products}
        attributes_map = {p.sku: {k: v.get() for k, v in self._attr_vars.items()} for p in selected_products}
        contact_name = self._contact_name_var.get().strip()
        contact_phone = self._contact_phone_var.get().strip()

        self._btn_publish.configure(state="disabled")
        self._status_var.set("Publikacja…")
        self._progress.set(0)
        self._cancel_flag = False

        def _worker() -> None:
            try:
                auth = OLXAuth(client_id=client_id, client_secret=client_secret)
                try:
                    auth.get_valid_token()
                except OLXAuthError:
                    self.after(0, lambda: self._status_var.set(
                        "OAuth: otwieram przeglądarkę…"
                    ))
                    auth.interactive_login()
                client = OLXClient(auth)

                def _progress(done: int, total: int, sku: str) -> None:
                    frac = done / max(total, 1)
                    self.after(0, lambda: self._progress.set(frac))
                    self.after(0, lambda: self._status_var.set(f"[{done}/{total}] {sku}"))

                result = publish_products(
                    client=client,
                    products=selected_products,
                    category_mapping=category_mapping,
                    attributes_map=attributes_map,
                    contact_name=contact_name,
                    contact_phone=contact_phone,
                    city_id=city_id,
                    on_progress=_progress,
                    cancel_check=lambda: self._cancel_flag,
                )
                self.after(0, lambda: self._on_publish_done(result))
            except OLXAuthError as exc:
                self.after(0, lambda: self._on_publish_error(f"OAuth: {exc}"))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._on_publish_error(str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_publish_done(self, result) -> None:
        self._btn_publish.configure(state="normal")
        self._progress.set(1.0)
        msg = (
            f"Wystawiono: {result.published}/{result.total} "
            f"(błędów: {result.failed})"
        )
        self._status_var.set(msg)
        for sku, advert_id in result.advert_ids.items():
            lbl = self._status_labels.get(sku)
            if lbl:
                lbl.configure(text="✓", text_color="#22C55E")
        for sku, _ in result.errors:
            lbl = self._status_labels.get(sku)
            if lbl:
                lbl.configure(text="✗", text_color="#EF4444")
        if result.errors:
            err_txt = "\n".join(f"{s}: {m[:120]}" for s, m in result.errors[:10])
            messagebox.showwarning("OLX — błędy publikacji", err_txt)
        if self._on_done:
            self._on_done()

    def _on_publish_error(self, message: str) -> None:
        self._btn_publish.configure(state="normal")
        self._status_var.set(f"Błąd: {message[:120]}")
        messagebox.showerror("OLX", message)

    def _on_cancel(self) -> None:
        self._cancel_flag = True
        self.destroy()
