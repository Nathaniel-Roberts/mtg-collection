import io

from tests.conftest import card_id


def test_status_and_formats(client):
    r = client.get("/api/v1/status")
    assert (
        r.status_code == 200
        and r.json()["catalogue"]["cards"] == 27
        and r.json()["dev_mode"] is True
    )
    assert any(f["key"] == "commander" for f in client.get("/api/v1/formats").json())


def test_cards_endpoints(client, conn):
    assert client.get("/api/v1/cards/autocomplete", params={"q": "sol"}).json()["items"] == [
        "Sol Ring"
    ]
    r = client.get("/api/v1/cards/search", params={"q": "bolt", "unique": "prints"})
    assert r.json()["total"] == 2
    bolt = card_id(conn, "Lightning Bolt", "m10")
    r = client.get(f"/api/v1/cards/{bolt}")
    assert r.status_code == 200 and r.json()["printings_count"] == 2 and r.json()["prices"]["aud"]
    assert client.get(f"/api/v1/cards/{bolt}/printings").json()["total"] == 2
    assert client.get("/api/v1/cards/nope").status_code == 404
    assert len(client.get("/api/v1/sets").json()["items"]) > 10


def test_collection_flow(client, conn):
    bolt = card_id(conn, "Lightning Bolt", "m10")
    r = client.post("/api/v1/collection", json={"card_id": bolt, "quantity": 3, "tags": ["burn"]})
    assert r.status_code == 201
    entry = r.json()
    assert (
        entry["quantity"] == 3
        and entry["tags"] == ["burn"]
        and entry["added_by"] == "dev@localhost"
    )
    r = client.patch(f"/api/v1/collection/{entry['id']}", json={"quantity": 5, "condition": "LP"})
    assert r.json()["quantity"] == 5 and r.json()["condition"] == "LP"
    assert client.get("/api/v1/collection", params={"tag": "burn"}).json()["total"] == 1
    s = client.get("/api/v1/collection/summary").json()
    assert s["totals"]["copies"] == 5
    assert client.get("/api/v1/tags").json()["items"] == [{"name": "burn", "entries": 1}]
    assert "Lightning Bolt" in client.get("/api/v1/collection/export.csv").text
    assert client.patch(f"/api/v1/collection/{entry['id']}", json={"quantity": 0}).json() == {
        "deleted": True
    }
    assert client.get(f"/api/v1/collection/{entry['id']}").status_code == 404
    assert (
        client.post("/api/v1/collection", json={"card_id": bolt, "condition": "Mint"}).status_code
        == 422
    )


def test_import_dry_run_then_apply(client):
    text = "Name,Set code,Collector number,Quantity,Foil,Condition\nSol Ring,CMM,410,2,foil,NM\n"
    files = {"file": ("cards.csv", io.BytesIO(text.encode()), "text/csv")}
    r = client.post("/api/v1/collection/import", files=files, data={"dry_run": "true"})
    assert r.status_code == 200 and r.json()["resolved"] == 1 and "applied" not in r.json()
    assert client.get("/api/v1/collection").json()["total"] == 0
    files = {"file": ("cards.csv", io.BytesIO(text.encode()), "text/csv")}
    r = client.post("/api/v1/collection/import", files=files, data={"dry_run": "false"})
    assert r.json()["applied"]["copies"] == 2
    assert client.get("/api/v1/collection").json()["items"][0]["finish"] == "foil"


def test_deck_flow(client, conn):
    r = client.post("/api/v1/decks", json={"name": "Atraxa", "format": "commander"})
    assert r.status_code == 201
    deck_id = r.json()["id"]
    atraxa = card_id(conn, "Atraxa, Praetors' Voice")
    forest = card_id(conn, "Forest")
    r = client.put(
        f"/api/v1/decks/{deck_id}/cards",
        json={
            "cards": [
                {"card_id": atraxa, "quantity": 1, "role": "commander"},
                {"card_id": forest, "quantity": 99},
            ]
        },
    )
    assert r.json()["validation"]["legal"] is True and r.json()["total"] == 100
    v = client.get(f"/api/v1/decks/{deck_id}/validate").json()
    assert v["ownership"]["missing"] == 100
    text = client.get(f"/api/v1/decks/{deck_id}/export").text
    assert "99 Forest (MOM) 281" in text
    r = client.post(
        f"/api/v1/decks/{deck_id}/import",
        json={"text": "1 Sol Ring\n1 Nonsense Card Name", "replace": False},
    )
    assert (
        r.json()["unresolved"][0]["name"] == "Nonsense Card Name"
        and r.json()["counts"]["main"] == 100
    )
    assert client.get("/api/v1/decks").json()["items"][0]["name"] == "Atraxa"
    r = client.post("/api/v1/decks/suggest", json={"commander_id": atraxa})
    assert r.status_code == 200 and r.json()["color_identity"] == "WUBG"
    assert (
        client.patch(f"/api/v1/decks/{deck_id}", json={"archived": True}).json()["archived"] is True
    )
    assert client.get("/api/v1/decks").json()["items"] == []
    assert client.delete(f"/api/v1/decks/{deck_id}").json() == {"deleted": True}
    assert client.get(f"/api/v1/decks/{deck_id}").status_code == 404
    assert client.post("/api/v1/decks", json={"name": "x", "format": "nope"}).status_code == 422


def test_conflicts_api(client, conn):
    sol = card_id(conn, "Sol Ring")
    client.post("/api/v1/collection", json={"card_id": sol, "quantity": 1})
    for name in ("A", "B"):
        d = client.post("/api/v1/decks", json={"name": name, "format": "commander"}).json()["id"]
        client.put(f"/api/v1/decks/{d}/cards", json={"cards": [{"card_id": sol, "quantity": 1}]})
    items = client.get("/api/v1/decks/conflicts").json()["items"]
    assert len(items) == 1 and items[0]["shortfall"] == 1
    oracle = items[0]["card"]["oracle_id"]
    assert client.post(f"/api/v1/decks/conflicts/{oracle}/ack").json() == {"acknowledged": True}
    assert client.get("/api/v1/decks/conflicts").json()["items"] == []
    client.delete(f"/api/v1/decks/conflicts/{oracle}/ack")
    assert len(client.get("/api/v1/decks/conflicts").json()["items"]) == 1


def test_settings_roundtrip(client):
    r = client.put(
        "/api/v1/settings", json={"fx_override_usd_aud": 1.42, "default_condition": "LP"}
    )
    assert r.json()["fx_override_usd_aud"] == "1.42"
    assert client.get("/api/v1/status").json()["fx"]["usd_aud"] == 1.42
    r = client.put("/api/v1/settings", json={"clear": ["fx_override_usd_aud"]})
    assert r.json()["fx_override_usd_aud"] is None


def test_sync_endpoint_rejects_double_run(client, monkeypatch):
    runner = client.app.state.runner
    runner.current = "all"
    assert client.post("/api/v1/sync/run", json={"kind": "fx"}).status_code == 409
    runner.current = None
