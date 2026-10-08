from app import catalogue, collection, decks
from tests.conftest import card_id


def test_parse_text_formats():
    text = """Commander
1 Atraxa, Praetors' Voice (2XM) 190

Deck
4 Lightning Bolt (M10) 146
2x Forest
Sol Ring *F*

Sideboard
1 Counterspell (MH2) 267
"""
    lines = decks.parse_text(text)
    assert lines[0] == {
        "name": "Atraxa, Praetors' Voice",
        "quantity": 1,
        "set": "2xm",
        "number": "190",
        "role": "commander",
    }
    assert lines[1]["quantity"] == 4 and lines[1]["set"] == "m10"
    assert lines[2] == {
        "name": "Forest",
        "quantity": 2,
        "set": None,
        "number": None,
        "role": "main",
    }
    assert lines[3]["name"] == "Sol Ring"
    assert lines[4]["role"] == "sideboard"


def test_import_export_round_trip(conn, rates):
    deck_id = decks.create(conn, name="Atraxa", format_key="commander")
    lines = decks.parse_text(
        "Commander\n1 Atraxa, Praetors' Voice\n\nDeck\n2 Forest (MOM) 281\n1 Sol Ring\n1 Jace the Mind Sculpter\n1 Not A Real Card\n"
    )
    resolved, unresolved = decks.resolve_lines(conn, lines, catalogue.NameIndex())
    assert [u["name"] for u in unresolved] == ["Not A Real Card"]
    decks.set_cards(conn, deck_id, resolved)
    text = decks.export_text(conn, deck_id, rates)
    assert text.startswith("Commander\n1 Atraxa, Praetors' Voice (2XM) 190\n\nDeck\n")
    assert "2 Forest (MOM) 281" in text and "1 Jace, the Mind Sculptor (WWK) 31" in text
    again = decks.parse_text(text)
    resolved2, unresolved2 = decks.resolve_lines(conn, again, catalogue.NameIndex())
    assert not unresolved2 and sorted(r["card_id"] for r in resolved2) == sorted(
        r["card_id"] for r in resolved
    )


def test_prefer_owned_printing(conn):
    clb = card_id(conn, "Lightning Bolt", "clb")
    collection.add(conn, card_id=clb)
    row = decks.resolve_name(conn, "Lightning Bolt", catalogue.NameIndex())
    assert row["id"] == clb
    row = decks.resolve_name(conn, "Lightning Bolt", catalogue.NameIndex(), prefer_owned=False)
    assert row["set_code"] in {"m10", "clb"}


def test_set_add_remove(conn, rates):
    deck_id = decks.create(conn, name="x", format_key="modern")
    bolt = card_id(conn, "Lightning Bolt", "m10")
    decks.set_cards(conn, deck_id, [{"card_id": bolt, "quantity": 2}], mode="add")
    decks.set_cards(conn, deck_id, [{"card_id": bolt, "quantity": 2}], mode="add")
    assert decks.get(conn, deck_id, rates)["counts"]["main"] == 4
    decks.set_cards(conn, deck_id, [{"card_id": bolt, "quantity": 1}], mode="set")
    assert decks.get(conn, deck_id, rates)["counts"]["main"] == 1
    decks.remove_cards(conn, deck_id, [{"card_id": bolt}])
    assert decks.get(conn, deck_id, rates)["counts"]["main"] == 0


def test_conflicts_lifecycle(conn, rates):
    sol = card_id(conn, "Sol Ring")
    collection.add(conn, card_id=sol, quantity=1)
    a = decks.create(conn, name="A", format_key="commander")
    b = decks.create(conn, name="B", format_key="commander")
    decks.set_cards(conn, a, [{"card_id": sol, "quantity": 1}])
    assert decks.conflicts(conn, rates) == []
    decks.set_cards(conn, b, [{"card_id": sol, "quantity": 1}])
    c = decks.conflicts(conn, rates)
    assert (
        len(c) == 1 and c[0]["shortfall"] == 1 and {d["name"] for d in c[0]["decks"]} == {"A", "B"}
    )
    oracle = c[0]["card"]["oracle_id"]
    assert decks.acknowledge_conflict(conn, oracle, rates, by="me")
    assert decks.conflicts(conn, rates) == []
    assert decks.conflicts(conn, rates, include_acknowledged=True)[0]["acknowledged"] is True
    # Shortfall grows: the warning comes back.
    decks.set_cards(conn, b, [{"card_id": sol, "quantity": 2}], mode="set")
    assert decks.conflicts(conn, rates)[0]["shortfall"] == 2
    # Conflict clears: the acknowledgement row is removed.
    collection.add(conn, card_id=sol, quantity=5)
    assert decks.conflicts(conn, rates, include_acknowledged=True) == []
    assert conn.execute("SELECT COUNT(*) FROM deck_conflict_acks").fetchone()[0] == 0
    # Archived decks do not count.
    collection.update(conn, collection.list_entries(conn)[0][0]["id"], quantity=1)
    decks.update(conn, b, archived=True)
    assert decks.conflicts(conn, rates) == []


def test_list_all_owned_percent(conn):
    deck_id = decks.create(conn, name="p", format_key="modern")
    decks.set_cards(conn, deck_id, [{"card_id": card_id(conn, "Forest"), "quantity": 4}])
    assert decks.list_all(conn)[0]["owned_percent"] == 0.0
    collection.add(conn, card_id=card_id(conn, "Forest"), quantity=2)
    assert decks.list_all(conn)[0]["owned_percent"] == 50.0
