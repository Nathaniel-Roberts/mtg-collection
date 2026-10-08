"""Format rules and deck validation.

Sources, read 09/10/2026:
- Magic Comprehensive Rules (https://magic.wizards.com/en/rules): 100.2a constructed decks
  are at least sixty cards and no more than four of any card by name except basic lands;
  100.4a a sideboard is up to fifteen cards; 903.3 a commander is a legendary creature (or
  a card that says it can be your commander); 903.5a a Commander deck is exactly 100 cards
  including the commander; 903.5b singleton except basic lands and cards that say
  otherwise; 903.5c every card must be within the commander's colour identity; 903.5d
  no sideboard in Commander (companions aside).
- Scryfall legalities drive per-card legality; "restricted" means one copy (Vintage) and,
  in paupercommander, a card that is legal only as a commander.
- Brawl (Arena, 100 cards) and Standard Brawl (60 cards): https://magic.wizards.com/en/formats/brawl
- Oathbreaker (60 cards, planeswalker + signature spell): https://oathbreakermtg.org/rules/
- Pauper (commons only): https://magic.wizards.com/en/formats/pauper
- Duel Commander (100, singleton): https://www.duelcommander.com/rules
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app import collection


@dataclass(frozen=True)
class FormatRules:
    key: str
    name: str
    min_main: int
    exact_main: int | None = None
    max_sideboard: int = 15
    max_copies: int = 4
    singleton: bool = False
    commander: str = "none"  # none, required
    commander_types: str = r"Legendary.*Creature"
    colour_identity: bool = False
    rarity_limit: str | None = None  # 'common' for pauper formats
    notes: str = ""


FORMATS: dict[str, FormatRules] = {
    "standard": FormatRules("standard", "Standard", 60),
    "pioneer": FormatRules("pioneer", "Pioneer", 60),
    "modern": FormatRules("modern", "Modern", 60),
    "legacy": FormatRules("legacy", "Legacy", 60),
    "vintage": FormatRules("vintage", "Vintage", 60, notes="restricted cards limited to one copy"),
    "pauper": FormatRules("pauper", "Pauper", 60, rarity_limit="common"),
    "commander": FormatRules(
        "commander",
        "Commander",
        100,
        exact_main=100,
        max_sideboard=0,
        max_copies=1,
        singleton=True,
        commander="required",
        colour_identity=True,
    ),
    "paupercommander": FormatRules(
        "paupercommander",
        "Pauper Commander",
        100,
        exact_main=100,
        max_sideboard=0,
        max_copies=1,
        singleton=True,
        commander="required",
        commander_types=r"Creature",
        colour_identity=True,
        rarity_limit="common",
        notes="commander is an uncommon creature; Scryfall marks those restricted",
    ),
    "duel": FormatRules(
        "duel",
        "Duel Commander",
        100,
        exact_main=100,
        max_sideboard=0,
        max_copies=1,
        singleton=True,
        commander="required",
        colour_identity=True,
    ),
    "brawl": FormatRules(
        "brawl",
        "Brawl",
        100,
        exact_main=100,
        max_sideboard=0,
        max_copies=1,
        singleton=True,
        commander="required",
        commander_types=r"Legendary.*(Creature|Planeswalker)",
        colour_identity=True,
    ),
    "standardbrawl": FormatRules(
        "standardbrawl",
        "Standard Brawl",
        60,
        exact_main=60,
        max_sideboard=0,
        max_copies=1,
        singleton=True,
        commander="required",
        commander_types=r"Legendary.*(Creature|Planeswalker)",
        colour_identity=True,
    ),
    "oathbreaker": FormatRules(
        "oathbreaker",
        "Oathbreaker",
        60,
        exact_main=60,
        max_sideboard=0,
        max_copies=1,
        singleton=True,
        commander="required",
        commander_types=r"Planeswalker",
        colour_identity=True,
        notes="signature spell is not modelled; put it in the main deck",
    ),
}

_ANY_NUMBER = re.compile(r"A deck can have (any number of|up to (\w+)) cards named", re.IGNORECASE)
_WORD_NUMBERS = {"seven": 7, "nine": 9}
_CAN_BE_COMMANDER = re.compile(r"can be your commander", re.IGNORECASE)


def copy_limit(card: dict[str, Any], rules: FormatRules) -> int | None:
    """Maximum copies of this card, None for unlimited."""
    type_line = card.get("type_line") or ""
    if type_line.startswith("Basic") or " Basic " in f" {type_line} ":
        return None
    match = _ANY_NUMBER.search(card.get("oracle_text") or "")
    if match:
        if match.group(1).lower().startswith("any"):
            return None
        return _WORD_NUMBERS.get(match.group(2).lower(), rules.max_copies)
    return rules.max_copies


def is_commander_eligible(card: dict[str, Any], rules: FormatRules) -> bool:
    front_type = (card.get("type_line") or "").split(" // ")[0]
    if re.search(rules.commander_types, front_type):
        return True
    return bool(_CAN_BE_COMMANDER.search(card.get("oracle_text") or ""))


def _identity_within(card_identity: str, allowed: str) -> bool:
    return all(ch in allowed for ch in card_identity)


@dataclass
class Problem:
    code: str
    message: str
    card: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "card": self.card}


@dataclass
class Validation:
    format: str
    legal: bool
    problems: list[Problem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    ownership: dict[str, Any] = field(default_factory=dict)
    missing_value: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "legal": self.legal,
            "problems": [p.as_dict() for p in self.problems],
            "counts": self.counts,
            "ownership": self.ownership,
            "missing_value": self.missing_value,
        }


def validate(
    conn: sqlite3.Connection,
    format_key: str,
    deck_cards: list[dict[str, Any]],
    *,
    deck_id: int | None = None,
    rates: Any = None,
) -> Validation:
    """``deck_cards``: dicts with card view fields plus ``quantity`` and ``role``."""
    rules = FORMATS.get(format_key)
    result = Validation(format=format_key, legal=True)
    if rules is None:
        result.legal = False
        result.problems.append(
            Problem("unknown_format", f"Unknown format {format_key!r}; known: {', '.join(FORMATS)}")
        )
        return result

    main = [c for c in deck_cards if c["role"] == "main"]
    commanders = [c for c in deck_cards if c["role"] == "commander"]
    companions = [c for c in deck_cards if c["role"] == "companion"]
    side = [c for c in deck_cards if c["role"] == "sideboard"]
    n_main = sum(c["quantity"] for c in main)
    n_cmd = sum(c["quantity"] for c in commanders)
    n_side = sum(c["quantity"] for c in side) + sum(c["quantity"] for c in companions)
    result.counts = {
        "main": n_main,
        "commander": n_cmd,
        "companion": sum(c["quantity"] for c in companions),
        "sideboard": sum(c["quantity"] for c in side),
        "maybeboard": sum(c["quantity"] for c in deck_cards if c["role"] == "maybeboard"),
    }

    total = n_main + n_cmd
    if rules.exact_main is not None:
        if total != rules.exact_main:
            result.problems.append(
                Problem(
                    "deck_size",
                    f"{rules.name} decks are exactly {rules.exact_main} cards including the commander; "
                    f"this one has {total}",
                )
            )
    elif total < rules.min_main:
        result.problems.append(
            Problem(
                "deck_size",
                f"{rules.name} decks need at least {rules.min_main} cards; this one has {total}",
            )
        )
    if n_side > rules.max_sideboard:
        result.problems.append(
            Problem(
                "sideboard_size", f"Sideboard is {n_side} cards; the limit is {rules.max_sideboard}"
            )
        )

    if rules.commander == "required":
        if not commanders:
            result.problems.append(Problem("no_commander", "No commander set"))
        elif n_cmd > 2:
            result.problems.append(Problem("too_many_commanders", "More than two commanders"))
        elif n_cmd == 2 and not all(
            "Partner" in (c.get("keywords") or [])
            or "partner" in (c.get("oracle_text") or "").lower()
            or "Background" in (c.get("type_line") or "")
            or "choose a background" in (c.get("oracle_text") or "").lower()
            for c in commanders
        ):
            result.problems.append(
                Problem(
                    "partner",
                    "Two commanders need Partner, Partner with, Friends forever, Background or similar",
                )
            )
        for c in commanders:
            if not is_commander_eligible(c, rules):
                result.problems.append(
                    Problem(
                        "commander_type",
                        f"{c['name']} cannot be a commander in {rules.name}",
                        c["name"],
                    )
                )
    elif commanders:
        result.problems.append(Problem("commander_not_allowed", f"{rules.name} has no commander"))

    identity = "".join(sorted({ch for c in commanders for ch in (c.get("color_identity") or "")}))

    counts_by_name: dict[str, int] = {}
    for c in deck_cards:
        if c["role"] == "maybeboard":
            continue
        counts_by_name[c["name"]] = counts_by_name.get(c["name"], 0) + c["quantity"]
    seen_names: set[str] = set()
    for c in deck_cards:
        if c["role"] == "maybeboard":
            continue
        legality = (c.get("legalities") or {}).get(format_key, "not_legal")
        commander_only = format_key == "paupercommander" and legality == "restricted"
        if legality == "banned":
            result.problems.append(
                Problem("banned", f"{c['name']} is banned in {rules.name}", c["name"])
            )
        elif legality == "not_legal":
            result.problems.append(
                Problem("not_legal", f"{c['name']} is not legal in {rules.name}", c["name"])
            )
        elif commander_only and c["role"] != "commander":
            result.problems.append(
                Problem(
                    "not_legal",
                    f"{c['name']} is only legal as a commander in {rules.name}",
                    c["name"],
                )
            )
        if c["name"] not in seen_names:
            seen_names.add(c["name"])
            limit = copy_limit(c, rules)
            if format_key == "vintage" and legality == "restricted":
                limit = 1
            if limit is not None and counts_by_name[c["name"]] > limit:
                result.problems.append(
                    Problem(
                        "copies",
                        f"{counts_by_name[c['name']]} copies of {c['name']}; the limit is {limit}",
                        c["name"],
                    )
                )
        if rules.colour_identity and commanders and c["role"] != "commander":
            if not _identity_within(c.get("color_identity") or "", identity):
                result.problems.append(
                    Problem(
                        "color_identity",
                        f"{c['name']} is outside the commander's colour identity ({identity or 'colourless'})",
                        c["name"],
                    )
                )
        if rules.rarity_limit and c["role"] != "commander" and not commander_only:
            if not _printed_at_rarity(conn, c, rules.rarity_limit):
                result.problems.append(
                    Problem(
                        "rarity", f"{c['name']} has no printing at {rules.rarity_limit}", c["name"]
                    )
                )

    result.legal = not result.problems
    result.ownership = ownership_report(conn, deck_cards, deck_id=deck_id, rates=rates)
    result.missing_value = result.ownership.pop("missing_value", {})
    return result


def _printed_at_rarity(conn: sqlite3.Connection, card: dict[str, Any], rarity: str) -> bool:
    if card.get("rarity") == rarity:
        return True
    row = conn.execute(
        "SELECT 1 FROM cards WHERE oracle_id = ? AND rarity = ? AND paper = 1 LIMIT 1",
        (card.get("oracle_id"), rarity),
    ).fetchone()
    return row is not None


def ownership_report(
    conn: sqlite3.Connection,
    deck_cards: list[dict[str, Any]],
    *,
    deck_id: int | None = None,
    rates: Any = None,
) -> dict[str, Any]:
    """Owned versus missing by oracle_id, plus which other decks use the same cards."""
    needed: dict[str, dict[str, Any]] = {}
    for c in deck_cards:
        if c["role"] in ("maybeboard",):
            continue
        key = c.get("oracle_id") or c["id"]
        slot = needed.setdefault(key, {"card": c, "need": 0})
        slot["need"] += c["quantity"]
    owned = collection.owned_by_oracle(conn, list(needed))
    missing: list[dict[str, Any]] = []
    owned_count = 0
    missing_count = 0
    missing_usd = 0.0
    missing_aud = 0.0
    for key, slot in needed.items():
        have = owned.get(key, 0)
        short = max(0, slot["need"] - have)
        owned_count += slot["need"] - short
        missing_count += short
        if short:
            prices = slot["card"].get("prices") or {}
            usd = prices.get("usd")
            aud = prices.get("aud")
            if usd:
                missing_usd += float(usd) * short
            if aud:
                missing_aud += float(aud) * short
            missing.append(
                {
                    "card": _brief(slot["card"]),
                    "need": slot["need"],
                    "have": have,
                    "estimated_usd": f"{float(usd) * short:.2f}" if usd else None,
                    "estimated_aud": f"{float(aud) * short:.2f}" if aud else None,
                }
            )
    also: list[dict[str, Any]] = []
    if needed:
        marks = ",".join("?" * len(needed))
        params: list[Any] = list(needed)
        extra = ""
        if deck_id is not None:
            extra = " AND d.id != ?"
            params.append(deck_id)
        rows = conn.execute(
            f"SELECT c.oracle_id, c.name, d.id AS deck_id, d.name AS deck_name, SUM(dc.quantity) AS qty "
            f"FROM deck_cards dc CROSS JOIN cards c ON c.id = dc.card_id JOIN decks d ON d.id = dc.deck_id "
            f"WHERE c.oracle_id IN ({marks}) AND d.archived = 0 AND dc.role IN ('main','commander','companion'){extra} "
            "GROUP BY c.oracle_id, d.id ORDER BY c.name, d.name",
            params,
        ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for r in rows:
            g = grouped.setdefault(r["oracle_id"], {"card": r["name"], "decks": []})
            g["decks"].append({"id": r["deck_id"], "name": r["deck_name"], "quantity": r["qty"]})
        also = list(grouped.values())
    total_needed = owned_count + missing_count
    return {
        "owned": owned_count,
        "missing": missing_count,
        "percent": round(100.0 * owned_count / total_needed, 1) if total_needed else 100.0,
        "missing_cards": missing,
        "also_in_decks": also,
        "missing_value": {
            "usd": f"{missing_usd:.2f}",
            "aud": f"{missing_aud:.2f}" if missing_aud else None,
        },
    }


def _brief(card: dict[str, Any]) -> dict[str, Any]:
    return {
        k: card.get(k)
        for k in ("id", "name", "set_code", "collector_number", "type_line", "mana_cost", "prices")
    }


def format_summary() -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "name": f.name,
            "deck_size": f.exact_main or f"{f.min_main}+",
            "singleton": f.singleton,
            "commander": f.commander == "required",
            "max_sideboard": f.max_sideboard,
            "notes": f.notes,
        }
        for f in FORMATS.values()
    ]


def legalities_of(row: sqlite3.Row | dict[str, Any]) -> dict[str, str]:
    raw = row["legalities"] if "legalities" in row.keys() else "{}"  # type: ignore[union-attr]
    return json.loads(raw) if isinstance(raw, str) else raw
