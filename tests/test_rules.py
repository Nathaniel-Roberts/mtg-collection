from app import catalogue, collection, decks, rules
from tests.conftest import card_id


def build(conn, rates, deck_format, cards):
    deck_id = decks.create(conn, name="t", format_key=deck_format)
    decks.set_cards(
        conn,
        deck_id,
        [{"card_id": card_id(conn, n, s), "quantity": q, "role": r} for n, s, q, r in cards],
    )
    deck = decks.get(conn, deck_id, rates)
    return deck_id, deck["validation"]


def codes(v):
    return sorted({p["code"] for p in v["problems"]})


def test_legal_commander_deck(conn, rates):
    _, v = build(
        conn,
        rates,
        "commander",
        [("Atraxa, Praetors' Voice", None, 1, "commander"), ("Forest", None, 99, "main")],
    )
    assert v["legal"] is True and v["counts"]["main"] == 99


def test_commander_problems(conn, rates):
    _, v = build(
        conn,
        rates,
        "commander",
        [
            ("Atraxa, Praetors' Voice", None, 1, "commander"),
            ("Forest", None, 95, "main"),
            ("Lightning Bolt", "m10", 1, "main"),  # outside identity
            ("Black Lotus", None, 1, "main"),  # banned
            ("Sol Ring", None, 2, "main"),  # singleton
        ],
    )
    assert codes(v) == ["banned", "color_identity", "copies"]
    assert v["counts"]["main"] == 99


def test_deck_size_and_missing_commander(conn, rates):
    _, v = build(conn, rates, "commander", [("Forest", None, 60, "main")])
    assert codes(v) == ["deck_size", "no_commander"]


def test_non_eligible_commander(conn, rates):
    _, v = build(
        conn,
        rates,
        "commander",
        [("Llanowar Elves", None, 1, "commander"), ("Forest", None, 99, "main")],
    )
    assert "commander_type" in codes(v)


def test_any_number_cards_and_seven_dwarves(conn, rates):
    _, v = build(
        conn,
        rates,
        "modern",
        [
            ("Relentless Rats", None, 30, "main"),
            ("Seven Dwarves", None, 7, "main"),
            ("Forest", None, 23, "main"),
        ],
    )
    assert v["legal"] is True
    _, v = build(
        conn, rates, "modern", [("Seven Dwarves", None, 8, "main"), ("Forest", None, 52, "main")]
    )
    assert codes(v) == ["copies"]


def test_four_of_and_sideboard(conn, rates):
    _, v = build(
        conn,
        rates,
        "modern",
        [
            ("Lightning Bolt", "m10", 3, "main"),
            ("Lightning Bolt", "clb", 2, "main"),
            ("Forest", None, 55, "main"),
            ("Counterspell", None, 16, "sideboard"),
        ],
    )
    assert codes(v) == ["copies", "sideboard_size"]
    assert any("5 copies of Lightning Bolt" in p["message"] for p in v["problems"])


def test_vintage_restricted_single_copy(conn, rates):
    _, v = build(
        conn, rates, "vintage", [("Black Lotus", None, 1, "main"), ("Forest", None, 59, "main")]
    )
    assert v["legal"] is True
    _, v = build(
        conn, rates, "vintage", [("Black Lotus", None, 2, "main"), ("Forest", None, 58, "main")]
    )
    assert codes(v) == ["copies"]


def test_pauper_rarity_checks_any_printing(conn, rates):
    _, v = build(
        conn, rates, "pauper", [("Lightning Bolt", "m10", 4, "main"), ("Forest", None, 56, "main")]
    )
    assert v["legal"] is True
    _, v = build(
        conn,
        rates,
        "pauper",
        [("Jace, the Mind Sculptor", None, 1, "main"), ("Forest", None, 59, "main")],
    )
    assert "not_legal" in codes(v)


def test_ownership_report_and_also_in_decks(conn, rates):
    collection.add(conn, card_id=card_id(conn, "Forest"), quantity=50)
    collection.add(conn, card_id=card_id(conn, "Lightning Bolt", "clb"), quantity=1)
    other = decks.create(conn, name="other", format_key="modern")
    decks.set_cards(
        conn, other, [{"card_id": card_id(conn, "Lightning Bolt", "m10"), "quantity": 4}]
    )
    _, v = build(
        conn, rates, "modern", [("Lightning Bolt", "m10", 4, "main"), ("Forest", None, 56, "main")]
    )
    own = v["ownership"]
    assert own["owned"] == 51 and own["missing"] == 9
    missing = {m["card"]["name"]: m for m in own["missing_cards"]}
    assert missing["Lightning Bolt"]["have"] == 1 and missing["Lightning Bolt"]["need"] == 4
    assert missing["Forest"]["have"] == 50
    assert (
        own["also_in_decks"][0]["card"] == "Lightning Bolt"
        and own["also_in_decks"][0]["decks"][0]["name"] == "other"
    )
    assert float(v["missing_value"]["usd"]) > 0


def test_copy_limit_helper():
    r = rules.FORMATS["modern"]
    assert rules.copy_limit({"type_line": "Basic Land — Forest", "oracle_text": ""}, r) is None
    assert (
        rules.copy_limit(
            {
                "type_line": "Creature",
                "oracle_text": "A deck can have any number of cards named X.",
            },
            r,
        )
        is None
    )
    assert (
        rules.copy_limit(
            {
                "type_line": "Creature",
                "oracle_text": "A deck can have up to seven cards named Seven Dwarves.",
            },
            r,
        )
        == 7
    )
    assert (
        rules.copy_limit(
            {
                "type_line": "Creature",
                "oracle_text": "A deck can have up to nine cards named Nazgûl.",
            },
            r,
        )
        == 9
    )
    assert (
        rules.copy_limit({"type_line": "Instant", "oracle_text": "Counter target spell."}, r) == 4
    )


def test_name_index_exists(conn):
    assert "Lightning Bolt" in catalogue.NameIndex().names(conn)
