from app import catalogue
from tests.conftest import card_id


def test_search_unique_cards_collapses_printings(conn, rates):
    rows, total = catalogue.search(conn, q="lightning bolt")
    assert total == 1 and len(rows) == 1
    rows, total = catalogue.search(conn, q="lightning bolt", unique="prints")
    assert total == 2


def test_search_filters(conn):
    rows, total = catalogue.search(conn, identity="G", type_line="Creature")
    names = {r["name"] for r in rows}
    assert "Llanowar Elves" in names and "Krenko, Mob Boss" not in names
    rows, _ = catalogue.search(conn, legal_in="pauper")
    assert all(
        r["rarity"] == "common" or r["name"] in {"Relentless Rats", "Counterspell", "Sol Ring"}
        for r in rows
    )
    rows, _ = catalogue.search(conn, rarity="mythic")
    assert {r["name"] for r in rows} >= {"Jace, the Mind Sculptor"}


def test_search_owned(conn):
    from app import collection

    collection.add(conn, card_id=card_id(conn, "Sol Ring"), quantity=1)
    rows, total = catalogue.search(conn, owned=True)
    assert total == 1 and rows[0]["name"] == "Sol Ring" and rows[0]["owned_quantity"] == 1


def test_autocomplete_prefix_then_contains(conn):
    assert catalogue.autocomplete(conn, "ligh")[0] == "Lightning Bolt"
    assert "Goblin Guide" in catalogue.autocomplete(conn, "guide")
    assert catalogue.autocomplete(conn, "") == []


def test_by_set_and_number_tolerates_leading_zeros(conn):
    row = catalogue.by_set_and_number(conn, "M10", "0146")
    assert row is not None and row["name"] == "Lightning Bolt"
    assert catalogue.by_set_and_number(conn, "m10", "9999") is None


def test_fuzzy_name(conn):
    index = catalogue.NameIndex()
    assert catalogue.fuzzy_name(conn, index, "lightning bolt")["name"] == "Lightning Bolt"
    assert (
        catalogue.fuzzy_name(conn, index, "Jace the Mind Sculpter")["name"]
        == "Jace, the Mind Sculptor"
    )
    assert catalogue.fuzzy_name(conn, index, "Delver of Secrets")["layout"] == "transform"
    assert catalogue.fuzzy_name(conn, index, "zzzz qqq") is None


def test_card_view_has_aud(conn, rates):
    row = catalogue.get_card(conn, card_id(conn, "Lightning Bolt", "m10"))
    view = catalogue.card_view(row, rates)
    assert view["prices"]["aud"] == round(float(view["prices"]["usd"]) * 1.5, 2)
    assert view["set_name"] == "Magic 2010"
