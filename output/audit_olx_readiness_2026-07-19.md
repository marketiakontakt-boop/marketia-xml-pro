# Audyt 111048 OLX Testy — 2026-07-19

## Podsumowanie globalne
- 3/15 SKU gotowe (pass all critical, no warns)
- 11/15 gotowe z warningami
- 1/15 z blockerami
- Gotowość: **71%** (weighted avg: pass=1.0, warn=0.7, blocker=0.0)

## Per-SKU tabela
| SKU | PID | Title | Desc | Price | EAN | Waga | Wym(WxLxH) | Img | Kat | Stock | OLX-fields | Status |
|-----|-----|-------|------|-------|-----|------|------------|-----|-----|-------|------------|--------|
| 10 | 649119844 | 51/70 ✓ | 1227 ✓ | 27.0 ✓ | ✓ | 2.4 | 30.0x27.0x8.0 ✓ | 5 ✓ | 8874146 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |
| 100 | 649119853 | 73/70 ✗ | 1915 ✓ | 125.0 ✓ | ✓ | 3.5 | 113.0x30.0x13.0 ✓ | 11 ✓ | 8874144 ✓ | 21 ✓ | — ℹ | BLOCKER (1) |
| 1004 | 649119859 | 59/70 ✓ | 5850 ✓ | 34.0 ✓ | ✓ | 0.75 | 95.0x72.0x102.0 ✓ | 7 ✓ | 8874145 ✓ | 161 ✓ | — ℹ | READY |
| 1005 | 649119870 | 53/70 ✓ | 6758 ✓ | 35.0 ✓ | ✓ | 0.75 | 95.0x72.0x102.0 ✓ | 6 ✓ | 8874145 ✓ | 117 ✓ | — ℹ | READY |
| 1162 | 649119880 | 52/70 ✓ | 1270 ✓ | 53.0 ✓ | ✓ | 4.7 | 24.0x17.0x17.0 ✓ | 5 ✓ | 8874146 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |
| 1168 | 649119888 | 47/70 ✓ | 917 ✓ | 15.0 ✓ | ✓ | 0.65 | 30.0x25.0x8.0 ✓ | 4 ✓ | 8874148 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |
| 1192 | 649119899 | 57/70 ✓ | 1818 ✓ | 459.0 ✓ | ✓ | 12.95 | 60.0x40.0x33.0 ✓ | 10 ✓ | 8874144 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |
| 12 | 649119911 | 55/70 ✓ | 1105 ✓ | 24.0 ✓ | ✓ | 2.1 | 305.0x183.0x1.0 ⚠ | 5 ✓ | 8874146 ✓ | 0 ⚠ | — ℹ | READY-warn (2) |
| 1201 | 649119921 | 59/70 ✓ | 918 ✓ | 60.0 ✓ | ✓ | 5.0 | 203.0x183.0x25.0 ✓ | 5 ✓ | 8874147 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |
| 1202 | 649119927 | 52/70 ✓ | 1728 ✓ | 36.0 ✓ | ✓ | 2.21 | 300.0x200.0x1.0 ⚠ | 5 ✓ | 8874146 ✓ | 0 ⚠ | — ℹ | READY-warn (2) |
| 1204 | 649119933 | 59/70 ✓ | 1315 ✓ | 45.0 ✓ | ✓ | 3.0 | 191.0x137.0x1.0 ⚠ | 6 ✓ | 8874147 ✓ | 0 ⚠ | — ℹ | READY-warn (2) |
| 1259 | 649119941 | 68/70 ✓ | 6205 ✓ | 51.0 ✓ | ✓ | 1.2 | 150.0x75.0x110.0 ✓ | 7 ✓ | 8874145 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |
| 1261 | 649119946 | 56/70 ✓ | 6496 ✓ | 27.0 ✓ | ✓ | 0.9 | 100.0x100.0x135.0 ✓ | 7 ✓ | 8874145 ✓ | 83 ✓ | — ℹ | READY |
| 1463 | 649119953 | 70/70 ✓ | 6115 ✓ | 95.0 ✓ | ✓ | 11.0 | 180.0x74.0x1.0 ⚠ | 9 ✓ | 8874149 ✓ | 1241 ✓ | — ℹ | READY-warn (1) |
| 1600 | 649119958 | 67/70 ✓ | 1438 ✓ | 40.0 ✓ | ✓ | 3.0 | 191.0x99.0x25.0 ✓ | 7 ✓ | 8874147 ✓ | 0 ⚠ | — ℹ | READY-warn (1) |

## Per-SKU szczegóły (WARN/FAIL/INFO)
### SKU 10 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 100 — BLOCKER (1 fail, 0 warn)
- **FAIL:** TITLE: za długi (73 znaków, max 70)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1004 — READY (info-only)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1005 — READY (info-only)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1162 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1168 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1192 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 12 — READY-warn (2 warn)
- **WARN:** WYMIARY: height=1 (placeholder — basen okrągły?)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1201 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1202 — READY-warn (2 warn)
- **WARN:** WYMIARY: height=1 (placeholder — basen okrągły?)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1204 — READY-warn (2 warn)
- **WARN:** WYMIARY: height=1 (placeholder — basen okrągły?)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1259 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1261 — READY (info-only)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1463 — READY-warn (1 warn)
- **WARN:** WYMIARY: height=1 (placeholder — basen okrągły?)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

### SKU 1600 — READY-warn (1 warn)
- **WARN:** STOCK: 0 (oferta wystawi się ale nie do zakupu)
- INFO: OLX-fields: brak name|pl|olx_15037, description|pl|olx_15037 (BL użyje domyślnych name/description)

## Wnioski i rekomendacje
### Blockers (napraw PRZED konfiguracją BL panel):
- **TITLE**: SKU 100 (1/15)

### Warnings (można żyć, ale doprecyzuj później):
- **STOCK**: SKU 10, 1162, 1168, 1192, 12, 1201, 1202, 1204, 1259, 1600 (10/15)
- **WYMIARY**: SKU 12, 1202, 1204, 1463 (4/15)

### Werdykt: **NIE** — 1/15 SKU ma blockery. Napraw (TITLE) przed konfiguracją BL panel.