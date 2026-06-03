"""Settings window — manage API keys stored in .env."""
from __future__ import annotations

import os
from pathlib import Path

import customtkinter as ctk
from dotenv import load_dotenv, set_key

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _mask(key: str) -> str:
    if len(key) <= 12:
        return "*" * len(key)
    return key[:8] + "…" + key[-4:]


def _load_gemini_keys() -> list[str]:
    load_dotenv(_ENV_PATH, override=True)
    multi = os.getenv("GEMINI_API_KEYS", "").strip()
    if multi:
        return [k.strip() for k in multi.split(",") if k.strip()]
    numbered: list[str] = []
    for i in range(1, 20):
        k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
        if k:
            numbered.append(k)
        else:
            break
    if numbered:
        return numbered
    single = os.getenv("GEMINI_API_KEY", "").strip()
    return [single] if single else []


def _save_gemini_keys(keys: list[str]) -> None:
    _ENV_PATH.touch(exist_ok=True)
    combined = ",".join(keys)
    set_key(str(_ENV_PATH), "GEMINI_API_KEYS", combined)
    # Update live env so the running session picks it up immediately
    os.environ["GEMINI_API_KEYS"] = combined


def _load_imgbb_key() -> str:
    return os.getenv("IMGBB_API_KEY", "").strip()


def _save_imgbb_key(key: str) -> None:
    _ENV_PATH.touch(exist_ok=True)
    set_key(str(_ENV_PATH), "IMGBB_API_KEY", key)
    os.environ["IMGBB_API_KEY"] = key


class SettingsWindow(ctk.CTkToplevel):
    """Modal settings window for managing API keys."""

    def __init__(self, parent: ctk.CTk):
        super().__init__(parent)
        self.title("Ustawienia — klucze API")
        self.geometry("540x560")
        self.resizable(False, False)
        self.grab_set()
        self.focus_force()

        self._gemini_keys: list[str] = _load_gemini_keys()
        self._revealed: dict[int, bool] = {}  # index → revealed?
        self._key_rows: list[dict] = []

        self._build()

    # ------------------------------------------------------------------

    def _build(self):
        pad = {"padx": 20, "pady": (16, 4)}

        # ---- Gemini section ----
        ctk.CTkLabel(
            self, text="Gemini API — generowanie opisów",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", **pad)

        ctk.CTkLabel(
            self,
            text="Kolejność ma znaczenie: wyczerpany klucz → automatyczne przełączenie.",
            text_color="#6B7280",
            font=ctk.CTkFont(size=11),
            wraplength=500,
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self._gemini_frame = ctk.CTkFrame(self, fg_color="#F3F4F6", corner_radius=8)
        self._gemini_frame.pack(fill="x", padx=20, pady=(0, 4))

        self._rebuild_gemini_rows()

        # Add key row
        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(4, 0))
        self._new_key_var = ctk.StringVar()
        self._new_key_entry = ctk.CTkEntry(
            add_row, textvariable=self._new_key_var,
            placeholder_text="Nowy klucz Gemini (AIza…)",
            width=360, show="•",
        )
        self._new_key_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            add_row, text="Dodaj", width=80,
            fg_color="#1a6f3a", hover_color="#145c2f",
            command=self._add_gemini_key,
        ).pack(side="left")

        ctk.CTkLabel(self, text="", height=4).pack()  # spacer

        # ---- ImgBB section ----
        ctk.CTkLabel(
            self, text="ImgBB API — upload zdjęć",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(8, 4))

        imgbb_row = ctk.CTkFrame(self, fg_color="transparent")
        imgbb_row.pack(fill="x", padx=20, pady=(0, 4))
        self._imgbb_var = ctk.StringVar(value=_load_imgbb_key())
        ctk.CTkEntry(
            imgbb_row, textvariable=self._imgbb_var,
            placeholder_text="Klucz ImgBB",
            width=360, show="•",
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            imgbb_row, text="Pokaż", width=80,
            fg_color="#374151", hover_color="#1f2937",
            command=lambda: self._toggle_entry_show(imgbb_row),
        ).pack(side="left")

        ctk.CTkLabel(
            self,
            text="Zdobądź klucz za darmo na imgbb.com — konto → API.",
            text_color="#6B7280", font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(0, 12))

        # ---- Save button ----
        ctk.CTkButton(
            self, text="Zapisz ustawienia",
            fg_color="#0a5c99", hover_color="#074880",
            height=40,
            command=self._save,
        ).pack(fill="x", padx=20, pady=(8, 4))

        ctk.CTkButton(
            self, text="Anuluj",
            fg_color="transparent",
            border_width=1, border_color="#D1D5DB",
            text_color="#374151",
            hover_color="#F3F4F6",
            command=self.destroy,
        ).pack(fill="x", padx=20, pady=(0, 16))

    # ------------------------------------------------------------------

    def _rebuild_gemini_rows(self):
        for widget in self._gemini_frame.winfo_children():
            widget.destroy()
        self._key_rows.clear()
        self._revealed.clear()

        if not self._gemini_keys:
            ctk.CTkLabel(
                self._gemini_frame,
                text="Brak kluczy — dodaj poniżej.",
                text_color="#9CA3AF",
            ).pack(padx=12, pady=8)
            return

        for i, key in enumerate(self._gemini_keys):
            self._add_gemini_row(i, key)

    def _add_gemini_row(self, index: int, key: str):
        row = ctk.CTkFrame(self._gemini_frame, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=3)

        badge = ctk.CTkLabel(
            row,
            text=f"#{index + 1}",
            width=28,
            text_color="#6B7280",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        badge.pack(side="left")

        label_var = ctk.StringVar(value=_mask(key))
        label = ctk.CTkLabel(
            row, textvariable=label_var,
            font=ctk.CTkFont(family="Courier", size=12),
            anchor="w", width=320,
        )
        label.pack(side="left", padx=(4, 0), expand=True, fill="x")

        def toggle_reveal(idx=index, var=label_var, k=key):
            self._revealed[idx] = not self._revealed.get(idx, False)
            var.set(k if self._revealed[idx] else _mask(k))

        ctk.CTkButton(
            row, text="👁", width=32, fg_color="transparent",
            text_color="#6B7280", hover_color="#E5E7EB",
            command=toggle_reveal,
        ).pack(side="left", padx=2)

        def remove_key(idx=index):
            self._gemini_keys.pop(idx)
            self._rebuild_gemini_rows()

        ctk.CTkButton(
            row, text="✕", width=32, fg_color="transparent",
            text_color="#EF4444", hover_color="#FEE2E2",
            command=remove_key,
        ).pack(side="left", padx=(0, 4))

        self._key_rows.append({"label_var": label_var, "key": key})

    def _toggle_entry_show(self, parent_frame):
        for child in parent_frame.winfo_children():
            if isinstance(child, ctk.CTkEntry):
                child.configure(show="" if child.cget("show") == "•" else "•")

    # ------------------------------------------------------------------

    def _add_gemini_key(self):
        key = self._new_key_var.get().strip()
        if not key:
            return
        if key in self._gemini_keys:
            return
        self._gemini_keys.append(key)
        self._new_key_var.set("")
        self._rebuild_gemini_rows()

    def _save(self):
        if not self._gemini_keys:
            import tkinter.messagebox as mb
            mb.showwarning("Brak kluczy", "Dodaj co najmniej jeden klucz Gemini.", parent=self)
            return

        _save_gemini_keys(self._gemini_keys)

        imgbb = self._imgbb_var.get().strip()
        if imgbb:
            _save_imgbb_key(imgbb)

        import tkinter.messagebox as mb
        mb.showinfo(
            "Zapisano",
            f"Zapisano {len(self._gemini_keys)} klucz(e) Gemini.\n"
            "Zmiany obowiązują od następnego uruchomienia AI.",
            parent=self,
        )
        self.destroy()
