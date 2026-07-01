"""Marketia Produktyzator — entry point.
Run via: ./venv/bin/python -m app.main
"""
from app.gui.main_window import App


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
