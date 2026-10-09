"""Turn embedding hits and OCR into a ranked list of printings."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from app.scan.pipeline import Candidate, ScanContext

PRINTING_COLUMNS = (
    "id, oracle_id, name, set_code, collector_number, released_at, paper, lang, digital"
)


class ResolveStage:
    name = "resolve"

    def __init__(
        self, connect: Callable[[], sqlite3.Connection], gap: float = 0.10, min_score: float = 0.55
    ) -> None:
        self.connect = connect
        self.gap = gap
        self.min_score = min_score

    def run(self, ctx: ScanContext) -> None:
        conn = self.connect()
        try:
            self._resolve(conn, ctx)
        finally:
            conn.close()

    def _resolve(self, conn: sqlite3.Connection, ctx: ScanContext) -> None:
        hit_ids = [cid for _, cid in ctx.hits]
        rows = _fetch(conn, hit_ids)
        hits = [(score, cid) for score, cid in ctx.hits if cid in rows]  # drop ids we do not know
        if not hits:
            ctx.method = "none"
            return
        # Group by oracle_id; the group score is its best hit.
        groups: dict[str, dict[str, Any]] = {}
        for score, cid in hits:
            key = rows[cid]["oracle_id"] or cid
            group = groups.setdefault(key, {"score": score, "ids": []})
            group["ids"].append(cid)
        ordered = sorted(groups.items(), key=lambda g: -g[1]["score"])
        best_key, best = ordered[0]
        runner_up = ordered[1][1]["score"] if len(ordered) > 1 else 0.0
        gap_ok = best["score"] >= self.min_score and (best["score"] - runner_up) >= self.gap
        close_keys = {k for k, g in ordered if best["score"] - g["score"] < self.gap}

        number = ctx.ocr.get("collector_number")
        set_code = ctx.ocr.get("set_code") or ctx.hint_set
        pinned: sqlite3.Row | None = None
        if set_code and number:
            pinned = _by_set_number(conn, set_code, number)
            if pinned is not None and (pinned["oracle_id"] or pinned["id"]) not in close_keys:
                pinned = None  # OCR disagrees with the image; trust the image
        if pinned is None and number:
            # Number only (older frames): look for that number among the best group's printings.
            for row in _printings(conn, best_key):
                if row["collector_number"].lower() == str(number).lower():
                    pinned = row
                    break

        candidates: list[Candidate] = []
        seen: set[str] = set()

        def add(row: sqlite3.Row, score: float, reason: str) -> None:
            if row["id"] in seen:
                return
            seen.add(row["id"])
            candidates.append(Candidate(row["id"], score, reason, row["oracle_id"]))

        if pinned is not None:
            ctx.method = "embedding+ocr"
            ctx.confident = True
            add(
                pinned,
                groups.get(pinned["oracle_id"] or pinned["id"], best)["score"],
                "image match, printing from collector line",
            )
        else:
            ctx.method = "embedding"
            ctx.confident = gap_ok
            top_id = best["ids"][0]
            add(rows[top_id], best["score"], "image match" + ("" if gap_ok else ", low margin"))
        ctx.match = candidates[0]

        # Other printings of the matched card, hit printings first, then by recency.
        match_key = candidates[0].oracle_id or candidates[0].card_id
        hit_scores = {cid: s for s, cid in hits}
        for row in sorted(
            _printings(conn, match_key),
            key=lambda r: (
                -(hit_scores.get(r["id"], -1.0)),
                -(r["paper"]),
                r["released_at"] or "",
                r["set_code"],
            ),
        ):
            add(row, hit_scores.get(row["id"], 0.0), "another printing of the same card")
        # Then the other oracle groups the image matched.
        for key, group in ordered:
            if key == match_key:
                continue
            add(rows[group["ids"][0]], group["score"], "other image match")
        ctx.candidates = candidates[:40]


def _fetch(conn: sqlite3.Connection, ids: list[str]) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT {PRINTING_COLUMNS} FROM cards WHERE id IN ({marks})", ids
    ).fetchall()
    return {r["id"]: r for r in rows}


def _printings(conn: sqlite3.Connection, oracle_or_id: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        f"SELECT {PRINTING_COLUMNS} FROM cards WHERE oracle_id = ? AND paper = 1 ORDER BY released_at DESC",
        (oracle_or_id,),
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        f"SELECT {PRINTING_COLUMNS} FROM cards WHERE id = ?", (oracle_or_id,)
    ).fetchall()


def _by_set_number(conn: sqlite3.Connection, set_code: str, number: str) -> sqlite3.Row | None:
    for n in dict.fromkeys([number, number.lstrip("0") or number]):
        row = conn.execute(
            f"SELECT {PRINTING_COLUMNS} FROM cards WHERE set_code = ? AND collector_number = ? COLLATE NOCASE "
            "ORDER BY (lang = 'en') DESC LIMIT 1",
            (set_code.lower(), n),
        ).fetchone()
        if row:
            return row
    return None
