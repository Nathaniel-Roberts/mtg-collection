"""Heuristic deck suggestions from owned cards. No model involved; see ARCHITECTURE.md section 8."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from app import catalogue
from app.pricing.fx import Rates

CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    (
        "ramp",
        re.compile(
            r"add \{|search your library for (a|up to \w+) (basic )?land"
            r"|put (it|them|a land card) onto the battlefield",
            re.I,
        ),
    ),
    (
        "wipes",
        re.compile(
            r"destroy all|exile all|each creature gets -|deals \d+ damage to each creature|all creatures get -",
            re.I,
        ),
    ),
    (
        "removal",
        re.compile(
            r"destroy target|exile target"
            r"|deals \d+ damage to (any target|target creature|target creature or planeswalker)"
            r"|target creature gets -\d+/-\d+|return target (creature|nonland permanent) to its owner's hand",
            re.I,
        ),
    ),
    ("counters", re.compile(r"counter target", re.I)),
    ("draw", re.compile(r"draw (a|two|three|x|\d+) cards?|draws? a card", re.I)),
    (
        "tutor",
        re.compile(
            r"search your library for a (card|creature|instant|sorcery|artifact|enchantment)", re.I
        ),
    ),
    ("recursion", re.compile(r"return target .* from your graveyard", re.I)),
]

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "with",
    "for",
    "in",
    "on",
    "you",
    "your",
    "that",
    "this",
    "it",
    "its",
    "is",
    "are",
    "be",
    "at",
    "as",
    "by",
    "from",
    "each",
    "all",
    "any",
    "may",
    "can",
    "when",
    "whenever",
    "if",
    "then",
    "target",
    "card",
    "cards",
    "deck",
    "theme",
    "commander",
}
_WORD = re.compile(r"[a-z][a-z'-]+")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def categorise(card: dict[str, Any]) -> str:
    text = card.get("oracle_text") or ""
    type_line = card.get("type_line") or ""
    for name, pattern in CATEGORIES:
        if pattern.search(text):
            return name
    if "Creature" in type_line:
        return "creatures"
    if "Land" in type_line:
        return "lands"
    return "other"


def suggest(
    conn: sqlite3.Connection,
    rates: Rates,
    *,
    commander_id: str | None = None,
    color_identity: str | None = None,
    theme: str | None = None,
    format_key: str = "commander",
    exclude_deck_id: int | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    commander = catalogue.get_card(conn, commander_id) if commander_id else None
    identity = (commander["color_identity"] if commander else (color_identity or "")).upper()
    identity = "".join(ch for ch in "WUBRG" if ch in identity)
    where = ["c.paper = 1", "json_extract(c.legalities, ?) IN ('legal','restricted')"]
    params: list[Any] = [f"$.{format_key}"]
    for letter in "WUBRG":
        if letter not in identity:
            where.append(f"instr(c.color_identity, '{letter}') = 0")
    if commander is not None:
        where.append("c.oracle_id != ?")
        params.append(commander["oracle_id"])
    if exclude_deck_id is not None:
        where.append(
            "c.oracle_id NOT IN (SELECT c2.oracle_id FROM deck_cards dc "
            "JOIN cards c2 ON c2.id = dc.card_id WHERE dc.deck_id = ?)"
        )
        params.append(exclude_deck_id)
    rows = conn.execute(
        "SELECT c.*, s.name AS set_name, SUM(e.quantity) AS owned_quantity FROM collection_entries e "
        "JOIN cards c ON c.id = e.card_id JOIN sets s ON s.code = c.set_code "
        f"WHERE {' AND '.join(where)} GROUP BY c.oracle_id",
        params,
    ).fetchall()

    theme_tokens = _tokens(theme) if theme else set()
    commander_tokens = (
        _tokens((commander["oracle_text"] or "") + " " + (commander["type_line"] or ""))
        if commander
        else set()
    )
    scored: list[tuple[float, dict[str, Any], str, list[str]]] = []
    for r in rows:
        card = catalogue.card_view(r, rates)
        card["owned_quantity"] = r["owned_quantity"]
        text = f"{card['name']} {card['type_line'] or ''} {card['oracle_text'] or ''} {' '.join(card['keywords'])}"
        tokens = _tokens(text)
        reasons: list[str] = []
        score = 0.0
        if theme_tokens:
            hits = sorted(theme_tokens & tokens)
            if not hits:
                continue
            score += 10.0 * len(hits)
            reasons.append("matches theme: " + ", ".join(hits))
        if commander_tokens:
            overlap = sorted((commander_tokens & tokens) - theme_tokens)[:3]
            if overlap:
                score += 2.0 * len(overlap)
                reasons.append("shares words with the commander: " + ", ".join(overlap))
        rank = card.get("edhrec_rank")
        if rank:
            score += max(0.0, 5.0 - rank / 4000.0)
            reasons.append(f"EDHREC rank {rank}")
        category = categorise(card)
        if category in ("ramp", "draw", "removal", "wipes", "counters"):
            score += 1.0
            reasons.append(category)
        scored.append((score, card, category, reasons))
    scored.sort(key=lambda s: (-s[0], s[1]["name"]))
    top = scored[:limit]
    categories: dict[str, list[str]] = {}
    for _, card, category, _ in top:
        categories.setdefault(category, []).append(card["name"])
    return {
        "commander": catalogue.card_view(commander, rates) if commander else None,
        "color_identity": identity,
        "format": format_key,
        "theme": theme,
        "considered": len(rows),
        "candidates": [
            {
                "card": _trim(card),
                "category": category,
                "why": "; ".join(reasons),
                "score": round(score, 2),
            }
            for score, card, category, reasons in top
        ],
        "categories": categories,
    }


def _trim(card: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "id",
        "oracle_id",
        "name",
        "set_code",
        "collector_number",
        "type_line",
        "mana_cost",
        "cmc",
        "colors",
        "color_identity",
        "rarity",
        "oracle_text",
        "edhrec_rank",
        "owned_quantity",
        "image_small",
    )
    out = {k: card.get(k) for k in keep}
    out["prices"] = {"usd": card["prices"].get("usd"), "aud": card["prices"].get("aud")}
    return out


def owned_card_text(conn: sqlite3.Connection, oracle_id: str) -> str | None:
    row = conn.execute(
        "SELECT oracle_text FROM cards WHERE oracle_id = ? LIMIT 1", (oracle_id,)
    ).fetchone()
    return row["oracle_text"] if row else None
