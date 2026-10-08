"""CSV import from the common collection apps.

Formats are recognised by their header set (see RESEARCH.md section 9). Each is mapped
onto a normalised row, then resolved against the local catalogue by Scryfall ID, then
set code + collector number, then name. Purchase price and date columns are ignored by
design.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app import catalogue, collection

LANGUAGES = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "japanese": "ja",
    "korean": "ko",
    "russian": "ru",
    "chinese simplified": "zhs",
    "simplified chinese": "zhs",
    "chinese traditional": "zht",
    "traditional chinese": "zht",
    "hebrew": "he",
    "latin": "la",
    "ancient greek": "grc",
    "arabic": "ar",
    "sanskrit": "sa",
    "phyrexian": "ph",
    "en": "en",
    "es": "es",
    "sp": "es",
    "fr": "fr",
    "de": "de",
    "it": "it",
    "pt": "pt",
    "ja": "ja",
    "jp": "ja",
    "ko": "ko",
    "kr": "ko",
    "ru": "ru",
    "zhs": "zhs",
    "cs": "zhs",
    "zht": "zht",
    "ct": "zht",
    "ph": "ph",
}
CONDITIONS = {
    "nm": "NM",
    "near mint": "NM",
    "near_mint": "NM",
    "mint": "NM",
    "m": "NM",
    "near mint or better": "NM",
    "lp": "LP",
    "lightly played": "LP",
    "lightly_played": "LP",
    "good (lightly played)": "LP",
    "good": "LP",
    "excellent": "LP",
    "ex": "LP",
    "slightly played": "LP",
    "sp": "LP",
    "mp": "MP",
    "moderately played": "MP",
    "moderately_played": "MP",
    "played": "MP",
    "pl": "MP",
    "hp": "HP",
    "heavily played": "HP",
    "heavily_played": "HP",
    "dmg": "DMG",
    "damaged": "DMG",
    "poor": "DMG",
    "d": "DMG",
}
FINISHES = {
    "": "nonfoil",
    "normal": "nonfoil",
    "nonfoil": "nonfoil",
    "non-foil": "nonfoil",
    "false": "nonfoil",
    "no": "nonfoil",
    "foil": "foil",
    "true": "foil",
    "yes": "foil",
    "etched": "etched",
    "foil etched": "etched",
    "etched foil": "etched",
}

# Format signatures: a header that must be present, and the column names used.
FORMATS: dict[str, dict[str, Any]] = {
    "manabox": {
        "signature": {"Set code", "Collector number", "Quantity"},
        "id": "Scryfall ID",
        "set": "Set code",
        "number": "Collector number",
        "name": "Name",
        "qty": "Quantity",
        "finish": "Foil",
        "condition": "Condition",
        "language": "Language",
        "tags": "Binder Name",
    },
    "moxfield": {
        "signature": {"Count", "Edition", "Collector Number"},
        "set": "Edition",
        "number": "Collector Number",
        "name": "Name",
        "qty": "Count",
        "finish": "Foil",
        "condition": "Condition",
        "language": "Language",
        "tags": "Tags",
    },
    "deckbox": {
        "signature": {"Count", "Edition Code", "Card Number"},
        "id": "Scryfall ID",
        "set": "Edition Code",
        "number": "Card Number",
        "name": "Name",
        "qty": "Count",
        "finish": "Foil",
        "condition": "Condition",
        "language": "Language",
        "tags": "Tags",
    },
    "archidekt": {
        "signature": {"Quantity", "Edition Code", "Finish"},
        "id": "Scryfall ID",
        "set": "Edition Code",
        "number": "Collector Number",
        "name": "Name",
        "qty": "Quantity",
        "finish": "Finish",
        "condition": "Condition",
        "language": "Language",
        "tags": "Tags",
    },
    "tcgplayer": {
        "signature": {"Quantity", "Set Code", "Card Number", "Printing"},
        "set": "Set Code",
        "number": "Card Number",
        "name": "Name",
        "qty": "Quantity",
        "finish": "Printing",
        "condition": "Condition",
        "language": "Language",
    },
    "dragonshield": {
        "signature": {"Card Name", "Set Code", "Card Number", "Printing"},
        "set": "Set Code",
        "number": "Card Number",
        "name": "Card Name",
        "qty": "Quantity",
        "finish": "Printing",
        "condition": "Condition",
        "language": "Language",
        "tags": "Folder Name",
    },
    "mtggoldfish": {
        "signature": {"Card", "Set ID", "Collector Number"},
        "id": "Scryfall ID",
        "set": "Set ID",
        "number": "Collector Number",
        "name": "Card",
        "qty": "Quantity",
        "finish": "Foil",
    },
    "generic": {
        "signature": set(),
        "id": "Scryfall ID",
        "set": "Set code",
        "number": "Collector number",
        "name": "Name",
        "qty": "Quantity",
        "finish": "Foil",
        "condition": "Condition",
        "language": "Language",
        "tags": "Tags",
    },
}
_GENERIC_ALIASES = {
    "id": ("Scryfall ID", "scryfall_id", "ScryfallId", "id"),
    "set": ("Set code", "Set Code", "set", "set_code", "Edition Code", "Set"),
    "number": ("Collector number", "Collector Number", "collector_number", "Card Number", "number"),
    "name": ("Name", "name", "Card Name", "Card"),
    "qty": ("Quantity", "quantity", "Count", "count", "Qty"),
    "finish": ("Foil", "foil", "Finish", "finish", "Printing"),
    "condition": ("Condition", "condition"),
    "language": ("Language", "language", "Lang"),
    "tags": ("Tags", "tags", "Binder Name", "Folder Name"),
}


@dataclass
class ImportRow:
    line: int
    name: str | None
    quantity: int
    finish: str
    condition: str
    language: str | None
    scryfall_id: str | None = None
    set_code: str | None = None
    number: str | None = None
    tags: list[str] = field(default_factory=list)
    card_id: str | None = None
    matched_by: str | None = None
    problem: str | None = None


def detect_format(headers: list[str]) -> str:
    present = set(headers)
    for key, spec in FORMATS.items():
        if spec["signature"] and spec["signature"] <= present:
            return key
    return "generic"


def _column_map(headers: list[str], fmt: str) -> dict[str, str]:
    spec = FORMATS[fmt]
    if fmt != "generic":
        return {k: v for k, v in spec.items() if k != "signature" and v in headers}
    lower = {h.lower(): h for h in headers}
    out: dict[str, str] = {}
    for key, aliases in _GENERIC_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower:
                out[key] = lower[alias.lower()]
                break
    return out


def parse(text: str, fmt: str = "auto") -> tuple[str, list[ImportRow]]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = headers
    fmt = detect_format(headers) if fmt == "auto" else fmt
    if fmt not in FORMATS:
        raise ValueError(f"Unknown import format {fmt!r}")
    cols = _column_map(headers, fmt)
    rows: list[ImportRow] = []
    for i, raw in enumerate(reader, start=2):

        def get(key: str, raw: dict[str, str] = raw) -> str:
            return (raw.get(cols[key]) or "").strip() if key in cols else ""

        qty_text = get("qty") or "1"
        try:
            quantity = int(float(qty_text))
        except ValueError:
            quantity = 0
        finish_text = get("finish").lower()
        finish = FINISHES.get(finish_text, "foil" if "foil" in finish_text else "nonfoil")
        condition = CONDITIONS.get(get("condition").lower(), "NM")
        lang_text = get("language").lower()
        language = LANGUAGES.get(lang_text) if lang_text else None
        tags = (
            [t.strip() for t in get("tags").replace(";", ",").split(",") if t.strip()]
            if "tags" in cols
            else []
        )
        row = ImportRow(
            line=i,
            name=get("name") or None,
            quantity=quantity,
            finish=finish,
            condition=condition,
            language=language,
            scryfall_id=get("id") or None,
            set_code=(get("set") or None),
            number=get("number") or None,
            tags=tags,
        )
        if row.set_code:
            row.set_code = row.set_code.lower()
        if quantity <= 0:
            row.problem = "quantity is zero or unreadable"
        elif not (row.scryfall_id or row.name or (row.set_code and row.number)):
            row.problem = "no Scryfall ID, name, or set and collector number"
        rows.append(row)
    return fmt, rows


def resolve(
    conn: sqlite3.Connection, rows: list[ImportRow], name_index: catalogue.NameIndex
) -> None:
    for row in rows:
        if row.problem:
            continue
        card = None
        if row.scryfall_id:
            card = catalogue.get_card(conn, row.scryfall_id)
            if card:
                row.matched_by = "scryfall_id"
        if card is None and row.set_code and row.number:
            card = catalogue.by_set_and_number(conn, row.set_code, row.number, row.language)
            if card is None:
                card = catalogue.by_set_and_number(conn, row.set_code, row.number)
            if card:
                row.matched_by = "set_number"
        if card is None and row.name:
            card = catalogue.fuzzy_name(conn, name_index, row.name, row.set_code)
            if card is None and row.set_code:
                card = catalogue.fuzzy_name(conn, name_index, row.name)
            if card:
                row.matched_by = "name"
        if card is None:
            row.problem = "not found in the catalogue"
            continue
        if row.name and row.matched_by != "name" and not _names_match(row.name, card["name"]):
            row.problem = f"name mismatch: file says {row.name!r}, catalogue has {card['name']!r}"
            continue
        row.card_id = card["id"]
        if row.language is None:
            row.language = card["lang"]


_PUNCT = re.compile(r"[^a-z0-9]+")


def _names_match(a: str, b: str) -> bool:
    """Same card name ignoring case, punctuation and a single face of a double-faced name."""
    na, nb = _PUNCT.sub("", a.lower()), _PUNCT.sub("", b.lower())
    if na == nb or not na:
        return True
    faces = [_PUNCT.sub("", f.lower()) for f in b.split(" // ")]
    if na in faces:
        return True
    return fuzz.ratio(na, nb) >= 90


def preview(rows: list[ImportRow], fmt: str, limit: int = 50) -> dict[str, Any]:
    resolved = [r for r in rows if r.card_id]
    unresolved = [r for r in rows if not r.card_id]
    return {
        "format_detected": fmt,
        "rows": len(rows),
        "resolved": len(resolved),
        "copies": sum(r.quantity for r in resolved),
        "unresolved": [
            {
                "line": r.line,
                "name": r.name,
                "set_code": r.set_code,
                "number": r.number,
                "reason": r.problem,
            }
            for r in unresolved
        ],
        "preview": [
            {
                "line": r.line,
                "card_id": r.card_id,
                "name": r.name,
                "set_code": r.set_code,
                "number": r.number,
                "quantity": r.quantity,
                "finish": r.finish,
                "condition": r.condition,
                "language": r.language,
                "tags": r.tags,
                "matched_by": r.matched_by,
            }
            for r in resolved[:limit]
        ],
    }


def apply(
    conn: sqlite3.Connection, rows: list[ImportRow], fmt: str, added_by: str | None = None
) -> dict[str, int]:
    added = 0
    copies = 0
    for r in rows:
        if not r.card_id:
            continue
        collection.add(
            conn,
            card_id=r.card_id,
            finish=r.finish,
            condition=r.condition,
            language=r.language,
            quantity=r.quantity,
            tags=r.tags or None,
            source=f"import:{fmt}",
            added_by=added_by,
        )
        added += 1
        copies += r.quantity
    return {"entries": added, "copies": copies, "skipped": sum(1 for r in rows if not r.card_id)}
