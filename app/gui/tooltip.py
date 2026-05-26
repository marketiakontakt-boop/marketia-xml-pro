"""Simple hover tooltip for customtkinter widgets."""
from __future__ import annotations

import tkinter as tk


class Tooltip:
    """Show a dark tooltip after hovering over a widget for `delay` ms."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self._widget = widget
        self._text = text
        self._delay = delay
        self._after_id: str | None = None
        self._tip_win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<ButtonPress>", self._on_leave)

    def _on_enter(self, event=None):
        self._after_id = self._widget.after(self._delay, self._show)

    def _on_leave(self, event=None):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None

    def _show(self):
        if self._tip_win:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() + 8
        y = self._widget.winfo_rooty()
        self._tip_win = tk.Toplevel(self._widget)
        self._tip_win.wm_overrideredirect(True)
        self._tip_win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip_win,
            text=self._text,
            justify="left",
            background="#1F2937",
            foreground="white",
            font=("Helvetica", 11),
            wraplength=280,
            padx=12,
            pady=8,
        ).pack()
