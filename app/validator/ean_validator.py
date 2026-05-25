"""GS1 EAN-8 / EAN-13 checksum validator."""
from __future__ import annotations


def validate_ean(ean: str) -> bool:
    """Return True if EAN-8 or EAN-13 checksum is valid. Empty string = True (no EAN)."""
    if not ean:
        return True
    if not ean.isdigit() or len(ean) not in (8, 13):
        return False
    weights = [1, 3] if len(ean) == 13 else [3, 1]
    total = sum(int(d) * weights[i % 2] for i, d in enumerate(ean[:-1]))
    check = (10 - total % 10) % 10
    return check == int(ean[-1])
