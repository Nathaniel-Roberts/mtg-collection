import csv
import io

import pytest

from app import collection
from tests.conftest import card_id


def test_add_merges_same_key(conn, rates):
    bolt = card_id(conn, "Lightning Bolt", "m10")
    a = collection.add(conn, card_id=bolt, quantity=2, tags=["binder"])
    b = collection.add(conn, card_id=bolt, quantity=3)
    assert a["id"] == b["id"] and b["quantity"] == 5
    foil = collection.add(conn, card_id=bolt, finish="foil", quantity=1)
    assert foil["id"] != a["id"]
    view = collection.entry_view(b, rates)
    assert view["tags"] == ["binder"] and view["value"]["aud"] == round(
        float(view["unit_price"]["usd"]) * 1.5 * 5, 2
    )


def test_update_merges_into_existing_key(conn):
    bolt = card_id(conn, "Lightning Bolt", "m10")
    nm = collection.add(conn, card_id=bolt, condition="NM", quantity=2)
    lp = collection.add(conn, card_id=bolt, condition="LP", quantity=1)
    merged = collection.update(conn, lp["id"], condition="NM")
    assert merged["id"] == nm["id"] and merged["quantity"] == 3
    assert collection.get_entry(conn, lp["id"]) is None


def test_update_quantity_zero_deletes_and_prunes_tags(conn):
    sol = card_id(conn, "Sol Ring")
    e = collection.add(conn, card_id=sol, tags=["commander staples"])
    assert collection.list_tags(conn) == [{"name": "commander staples", "entries": 1}]
    assert collection.update(conn, e["id"], quantity=0) is None
    assert collection.list_tags(conn) == []


def test_validation(conn):
    with pytest.raises(collection.CollectionError):
        collection.add(conn, card_id=card_id(conn, "Sol Ring"), condition="Mint")
    with pytest.raises(collection.CollectionError):
        collection.add(conn, card_id="nope")


def test_list_filters_and_summary(conn, rates):
    collection.add(conn, card_id=card_id(conn, "Lightning Bolt", "m10"), quantity=4, tags=["burn"])
    collection.add(conn, card_id=card_id(conn, "Forest"), quantity=10)
    collection.add(conn, card_id=card_id(conn, "Sol Ring"), finish="foil", quantity=1)
    rows, total = collection.list_entries(conn, tag="burn")
    assert total == 1 and rows[0]["name"] == "Lightning Bolt"
    rows, total = collection.list_entries(conn, finish="foil")
    assert total == 1
    rows, total = collection.list_entries(conn, type_line="Land")
    assert total == 1 and rows[0]["quantity"] == 10
    s = collection.summary(conn, rates)
    assert s["totals"]["copies"] == 15 and s["totals"]["cards"] == 3
    assert {g["k"] for g in s["by_color_identity"]} == {"R", "G", "C"}
    assert any(t["k"] == "Land" and t["copies"] == 10 for t in s["by_type"])
    assert s["top_cards"][0]["card"]["name"] in {"Sol Ring", "Lightning Bolt"}


def test_export_csv_round_trips_through_importer(conn):
    collection.add(
        conn,
        card_id=card_id(conn, "Lightning Bolt", "m10"),
        quantity=4,
        condition="LP",
        tags=["burn"],
    )
    text = collection.export_csv(conn)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert (
        rows[0]["Name"] == "Lightning Bolt"
        and rows[0]["Quantity"] == "4"
        and rows[0]["Condition"] == "lightly_played"
    )
    from app import catalogue
    from app.importers import csv as imp

    fmt, parsed = imp.parse(text)
    assert fmt == "manabox"
    imp.resolve(conn, parsed, catalogue.NameIndex())
    assert (
        parsed[0].card_id == card_id(conn, "Lightning Bolt", "m10") and parsed[0].condition == "LP"
    )
