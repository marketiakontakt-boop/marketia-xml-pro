"""Sync report dialog — scrollable popup po zakończeniu BaseLinker sync.

Zastępuje `messagebox.showinfo` który przy długiej liście katalogów (11+)
nie mieścił się na ekranie i nie miał przycisku do zamknięcia.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

import customtkinter as ctk


class SyncReportDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        title: str,
        short_summary: str,
        detail_text: str,
        warnings_text: str = "",
        report_path: Path | None = None,
        icon: str = "✅",
    ):
        super().__init__(master)
        self.title(title)
        self.geometry("680x560")
        self.minsize(560, 360)
        self.resizable(True, True)
        self.grab_set()
        self._report_path = report_path

        # ── BOTTOM BAR (fixed, zawsze widoczny) ──
        bottom = ctk.CTkFrame(self, fg_color="#F9FAFB", corner_radius=0, height=64)
        bottom.pack(side="bottom", fill="x")
        bottom.pack_propagate(False)
        ctk.CTkFrame(bottom, height=1, fg_color="#E5E7EB").pack(fill="x")
        btn_row = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_row.pack(expand=True)
        ctk.CTkButton(
            btn_row, text="Zamknij", width=120, height=36,
            fg_color="#0a5c99", hover_color="#074880",
            command=self.destroy,
        ).pack(side="right", padx=6, pady=10)
        if report_path:
            ctk.CTkButton(
                btn_row, text="📄 Otwórz raport", width=160, height=36,
                fg_color="transparent", border_width=1, border_color="#D1D5DB",
                text_color="#374151", hover_color="#F3F4F6",
                command=self._open_report,
            ).pack(side="right", padx=6, pady=10)

        # ── HEADER (fixed top) ──
        header = ctk.CTkFrame(self, fg_color="#F3F4F6", corner_radius=0)
        header.pack(side="top", fill="x")
        ctk.CTkLabel(
            header, text=f"{icon}  {short_summary}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#111827", anchor="w", justify="left",
            wraplength=620,
        ).pack(anchor="w", padx=20, pady=(14, 6))
        if report_path:
            ctk.CTkLabel(
                header,
                text=f"Pełny raport: {report_path}",
                font=ctk.CTkFont(size=10, family="Menlo"),
                text_color="#6B7280", anchor="w",
            ).pack(anchor="w", padx=20, pady=(0, 12))

        # ── SCROLLABLE BODY ──
        body = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color="#D1D5DB",
            scrollbar_button_hover_color="#9CA3AF",
        )
        body.pack(side="top", fill="both", expand=True)

        if detail_text.strip():
            ctk.CTkLabel(
                body, text="Wyniki per katalog:",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#374151", anchor="w",
            ).pack(anchor="w", padx=20, pady=(12, 4))
            ctk.CTkLabel(
                body, text=detail_text,
                font=ctk.CTkFont(size=11, family="Menlo"),
                text_color="#1F2937", anchor="w", justify="left",
                wraplength=620,
            ).pack(anchor="w", fill="x", padx=20, pady=(0, 12))

        if warnings_text.strip():
            ctk.CTkLabel(
                body, text="⚠️ Ostrzeżenia:",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#92400E", anchor="w",
            ).pack(anchor="w", padx=20, pady=(8, 4))
            ctk.CTkLabel(
                body, text=warnings_text,
                font=ctk.CTkFont(size=11),
                text_color="#78350F", anchor="w", justify="left",
                wraplength=620,
            ).pack(anchor="w", fill="x", padx=20, pady=(0, 16))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, self.lift)

    def _open_report(self):
        if not self._report_path or not self._report_path.exists():
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(self._report_path)], check=False)
            elif sys.platform == "win32":
                subprocess.run(["start", "", str(self._report_path)], shell=True, check=False)
            else:
                subprocess.run(["xdg-open", str(self._report_path)], check=False)
        except Exception:
            pass
