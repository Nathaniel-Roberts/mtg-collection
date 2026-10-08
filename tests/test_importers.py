from app import catalogue
from app.importers import csv as imp
from tests.conftest import card_id

MANABOX = """Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency
Lightning Bolt,M10,Magic 2010,146,foil,common,2,123,,1.00,false,false,near_mint,en,AUD
Sol Ring,CMM,Commander Masters,410,normal,uncommon,1,124,,0.50,false,false,lightly_played,en,AUD
Made Up Card,ZZZ,Nope,1,normal,common,1,125,,0,false,false,near_mint,en,AUD
"""

MOXFIELD = """Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,Collector Number,Alter,Proxy,Purchase Price
3,0,Counterspell,mh2,Near Mint,English,,,2026-01-01,267,False,False,
1,0,Forest,mom,Good (Lightly Played),English,foil,"lands,basics",2026-01-01,281,False,False,
"""

DECKBOX = """Count,Tradelist Count,Name,Edition,Edition Code,Card Number,Condition,Language,Foil,Signed,Artist Proof,Altered Art,Misprint,Promo,Textless,Printing Id,Printing Note,Tags,My Price,Cost,Rarity,Price,TcgPlayer ID,Scryfall ID
1,0,Jace the Mind Sculptor,Worldwake,WWK,31,Played,English,,,,,,,,,,,,,,,,
"""

GENERIC = """name,quantity,set,number
Ponder,4,m12,73
"""


def resolve(conn, text, fmt="auto"):
    fmt, rows = imp.parse(text, fmt)
    imp.resolve(conn, rows, catalogue.NameIndex())
    return fmt, rows


def test_manabox(conn):
    fmt, rows = resolve(conn, MANABOX)
    assert fmt == "manabox"
    assert (
        rows[0].card_id == card_id(conn, "Lightning Bolt", "m10")
        and rows[0].finish == "foil"
        and rows[0].quantity == 2
    )
    assert rows[1].condition == "LP" and rows[1].matched_by == "set_number"
    assert rows[2].card_id is None and "not found" in rows[2].problem
    p = imp.preview(rows, fmt)
    assert p["resolved"] == 2 and p["unresolved"][0]["name"] == "Made Up Card" and p["copies"] == 3


def test_moxfield(conn):
    fmt, rows = resolve(conn, MOXFIELD)
    assert fmt == "moxfield"
    assert (
        rows[0].language == "en"
        and rows[0].condition == "NM"
        and rows[0].card_id == card_id(conn, "Counterspell")
    )
    assert (
        rows[1].finish == "foil"
        and rows[1].condition == "LP"
        and rows[1].tags == ["lands", "basics"]
    )


def test_deckbox_name_fallback(conn):
    fmt, rows = resolve(conn, DECKBOX)
    assert (
        fmt == "deckbox"
        and rows[0].card_id == card_id(conn, "Jace, the Mind Sculptor")
        and rows[0].condition == "MP"
    )


def test_generic_and_apply(conn):
    fmt, rows = resolve(conn, GENERIC)
    assert fmt == "generic" and rows[0].card_id == card_id(conn, "Ponder")
    result = imp.apply(conn, rows, fmt, added_by="test")
    assert result == {"entries": 1, "copies": 4, "skipped": 0}
    assert conn.execute("SELECT source, quantity FROM collection_entries").fetchone()[:] == (
        "import:generic",
        4,
    )


def test_name_mismatch_is_flagged(conn):
    text = "Name,Set code,Collector number,Quantity\nSol Ring,M10,146,1\n"
    _, rows = resolve(conn, text)
    assert rows[0].card_id is None and "mismatch" in rows[0].problem
