# Marketia XML Pro — Prompt główny do Claude Code

> **Wklej to do Claude Code w nowym katalogu projektu** `~/Projects/marketia-xml-pro/`

---

V4 rules. Tier 4. Domena: dropshipping + infra.

Zbuduj aplikację desktopową macOS **Marketia XML Pro** — narzędzie do masowej transformacji XML produktowych z BaseLinker/hurtowni na zoptymalizowane XML-e pod marketplace (Allegro, Empik, OLX).

## KROK 0 — Przeczytaj kontekst PRZED planowaniem

1. `~/Documents/_meta/_START.md`
2. `~/Documents/_meta/V4_RULES_ALEKSANDER.md`
3. `~/Documents/_meta/toolbox-dropshipping.md`
4. `~/Documents/_meta/marketia-xml-pro/SPEC.md` (zawiera pełną specyfikację — czytaj jako pierwsze)
5. `~/Documents/_meta/opisy/` (przykłady opisów HTML — wzorce stylu)
6. `~/Documents/_meta/przyklad miniatury/` (wzorce miniatur)
7. `~/Documents/_meta/logo/` (loga marek)
8. `~/Projects/marketia-image-pro/` (istniejąca aplikacja — wzorzec GUI i .app packaging)

Skille do użycia:
- frontend-design-aleksander (GUI)
- product-self-knowledge (Claude API integracja)

## KROK 1 — Complexity Assessment

Wykonaj normalną sekwencję V4: spawn complexity-assessor → 3 parallel scanners → plan → czekaj na "ok".

W planie OBOWIĄZKOWO uwzględnij:
- Decyzję czy budujesz monolit czy moduły (sugeruję moduły: parser, transformer, ai_client, gs1_client, gui, packager)
- Background agents: continuity-guardian + performance-monitor
- Checkpointy po każdym module

## CEL APLIKACJI

Wejście: XML z BaseLinker (różne hurtownie, różna jakość danych)
Wyjście: XML zoptymalizowany pod marketplace z:
- Zachowanymi SKU/product_id/price/weight/stock
- Przetransformowanymi name/description/attributes
- Wygenerowanym lub zachowanym EAN (przez GS1 API)
- Zdjęciami z XML użytymi w sekcjach opisu

Przetwarzanie: 500 produktów/batch (limit Base 2MB).

## FUNKCJONALNOŚCI — szczegóły w SPEC.md

Główne moduły (w kolejności priorytetu):

1. **XML Parser** (lxml) — parsuje wejściowy XML, ekstraktuje produkty z różnych formatów hurtowni
2. **Brand Mapper** — auto-mapuje markę po słowach kluczowych (Hopla/GardenStein/Villago/MarketiaHome/ZooVera), z możliwością ręcznej korekty w GUI
3. **Model Name Generator** — losuje nazwę modelu (Galen/Milan/Toronto/Maestro) per produkt z puli per marka, deduplikacja w SQLite
4. **Title Transformer** — generuje tytuł: WIELKIE LITERY, marka + model + kluczowe parametry, max 75 znaków
5. **Description Generator** — hybryda: szablony lokalne (70%) + Claude API Batch (30% kluczowych sekcji)
6. **Attribute Extractor** — wyciąga atrybuty z opisu oryginalnego (regex + Claude API fallback) jeśli `<attributes>` puste
7. **GS1 Client** — integracja z mojegs1.pl/api/v2 do generowania EAN-ów
8. **Image Processor** — opcjonalne miniatury (rembg + Pillow + logo brandu)
9. **GUI** (customtkinter) — wzorowane na marketia-image-pro
10. **Packager** (py2app) — `.app` z ikoną dla macOS

## STACK

- Python 3.10+
- lxml (XML)
- anthropic (Claude API SDK)
- customtkinter (GUI)
- Pillow, rembg (zdjęcia)
- requests, httpx (GS1 API)
- sqlite3 (cache)
- python-dotenv (credentials)
- py2app (packaging)

## ZASADY (z V4 + SPEC)

- NIGDY nie zmieniaj `<sku>`, `<product_id>`, `<price>`, `<weight>`, `<stock>`
- Tytuł zawsze ≤75 znaków, WIELKIE LITERY, zaczyna od marki
- 8 sekcji opisu (struktura w SPEC.md) — każda z własnym zdjęciem z `<images>`
- FAQ: 6-7 pytań (mix standardowych + produktowych z AI)
- Każda sekcja "cecha → korzyść" (benefit selling)
- Credentials (Claude API, GS1) z `.env`, nigdy w kodzie
- Cache opisów w SQLite — SKU jako klucz, regeneracja tylko na żądanie
- Walidacja na końcu: tytuł, EAN check digit, kompletność sekcji, min. 5 atrybutów

## START

Zacznij od KROK 1 V4. Po `ok` na plan → buduj moduł po module z checkpointami.

Pierwszy moduł: **XML Parser** (najważniejszy, blokuje resztę). Testuj na 3 XML-ach w `~/Documents/_meta/marketia-xml-pro/test-xmls/`.
