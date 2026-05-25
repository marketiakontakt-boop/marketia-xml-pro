# Marketia XML Pro

Masowa transformacja XML produktowych z BaseLinker → marketplace (Allegro / Empik / OLX).

## Status

**Faza 1 — w trakcie:** parser, brand mapper, model name generator, title transformer, dummy GUI.
**Faza 2 — TODO:** Description Generator (Gemini), Attribute Extractor.
**Faza 3 — TODO:** GS1 Client, validator, image processor (opcjonalny), py2app packaging.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env  # wpisz GEMINI_API_KEY
```

## Uruchomienie (GUI)

```bash
./venv/bin/python -m app.main
```

## CLI test (parser)

```bash
./venv/bin/python -m app.parser.xml_parser ~/Documents/_meta/marketia-xml-pro/test-xmls/Base__Produkty__domylny_XML_2026-05-07_12_15.xml
```

## Struktura

Patrz [SPEC.md](~/Documents/_meta/marketia-xml-pro/SPEC.md).
