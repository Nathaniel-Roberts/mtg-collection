"""MCP tools (in-memory client) and the /mcp authentication path (HTTP)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from app import collection, decks
from app.main import create_app
from tests.conftest import card_id, make_settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app(settings, conn):
    return create_app(settings, run_scheduler=False)


async def call(client: Client, tool: str, **args):
    result = await client.call_tool(tool, args)
    if result.is_error:
        text = " ".join(c.text for c in result.content if getattr(c, "text", None))
        raise AssertionError(f"{tool} failed: {text}")
    return result.structured_content


@pytest.mark.anyio
async def test_tool_list_and_annotations(app):
    async with Client(app.state.mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    expected = {
        "search_collection",
        "search_cards",
        "get_card",
        "collection_summary",
        "list_decks",
        "get_deck",
        "create_deck",
        "update_deck",
        "add_cards_to_deck",
        "remove_cards_from_deck",
        "validate_deck",
        "suggest_from_collection",
        "deck_conflicts",
        "delete_deck",
    }
    assert expected <= set(tools)
    assert tools["search_collection"].annotations.read_only_hint is True
    assert tools["delete_deck"].annotations.destructive_hint is True
    assert tools["get_card"].output_schema is not None
    assert "misspellings" in tools["get_card"].description


@pytest.mark.anyio
async def test_search_and_summary(app, conn):
    bolt = card_id(conn, "Lightning Bolt", "m10")
    collection.add(conn, card_id=bolt, quantity=4, tags=["burn"])
    collection.add(conn, card_id=card_id(conn, "Sol Ring"), finish="foil")
    async with Client(app.state.mcp_server) as client:
        r = await call(client, "search_collection", query="bolt")
        assert (
            r["total"] == 1
            and r["items"][0]["owned_quantity"] == 4
            and r["items"][0]["tags"] == ["burn"]
        )
        assert r["items"][0]["value_aud"] == round(
            float(r["items"][0]["prices"]["usd"]) * 1.5 * 4, 2
        )
        r = await call(client, "search_collection", color_identity="R")
        assert {i["name"] for i in r["items"]} == {"Lightning Bolt", "Sol Ring"}
        r = await call(client, "search_cards", query="counterspell")
        assert (
            r["total"] == 1
            and r["items"][0]["owned_quantity"] == 0
            and "commander" in r["items"][0]["legal_in"]
        )
        r = await call(client, "search_cards", owned_only=True)
        assert r["total"] == 2
        s = await call(client, "collection_summary", group_by=["finish"])
        assert s["totals"]["copies"] == 5 and {g["k"] for g in s["by_finish"]} == {
            "nonfoil",
            "foil",
        }
        assert s["top_cards"][0]["name"] in {"Sol Ring", "Lightning Bolt"}
        c = await call(client, "get_card", name="lightnng bolt")
        assert (
            c["name"] == "Lightning Bolt"
            and c["owned_quantity"] == 4
            and c["printings_owned"][0]["quantity"] == 4
        )
        c = await call(client, "get_card", set_code="M10", collector_number="0146")
        assert c["id"] == bolt
        result = await client.call_tool("get_card", {"name": "zzzz qqqq"})
        assert result.is_error and "No such card" in result.content[0].text


@pytest.mark.anyio
async def test_deck_lifecycle(app, conn):
    collection.add(conn, card_id=card_id(conn, "Forest"), quantity=50)
    collection.add(conn, card_id=card_id(conn, "Sol Ring"))
    async with Client(app.state.mcp_server) as client:
        d = await call(
            client,
            "create_deck",
            name="Atraxa",
            format="commander",
            commander="Atraxa, Praetors' Voice",
            cards=[
                {"card": "Forest", "quantity": 98},
                {"card": "Sol Ring"},
                {"card": "Not A Card"},
            ],
        )
        assert d["unresolved"] == ["Not A Card"] and d["total"] == 100
        assert (
            d["cards"]["commander"][0]["name"] == "Atraxa, Praetors' Voice"
            and d["validation"]["legal"] is True
        )
        assert d["validation"]["ownership"]["missing"] == 49  # 48 Forests short plus Atraxa
        deck_id = d["id"]
        d = await call(
            client,
            "add_cards_to_deck",
            deck_id=deck_id,
            cards=[{"card": "Lightning Bolt", "quantity": 1}],
        )
        v = await call(client, "validate_deck", deck_id=deck_id)
        assert not v["legal"] and {p["code"] for p in v["problems"]} == {
            "deck_size",
            "color_identity",
        }
        d = await call(
            client,
            "remove_cards_from_deck",
            deck_id=deck_id,
            cards=[{"card": "Lightning Bolt"}, {"card": "Forest", "quantity": 1}],
        )
        assert d["total"] == 99 and d["validation"]["problems"][0]["code"] == "deck_size"
        lst = await call(client, "list_decks")
        assert lst["items"][0]["name"] == "Atraxa" and lst["items"][0]["owned_percent"] > 90
        d = await call(client, "update_deck", deck_id=deck_id, name="Atraxa v2", archived=True)
        assert d["name"] == "Atraxa v2" and d["archived"] is True
        assert (await call(client, "list_decks"))["items"] == []
        g = await call(client, "get_deck", deck_id=deck_id)
        assert g["counts"]["main"] == 98
        r = await call(client, "delete_deck", deck_id=deck_id)
        assert r == {"deleted": True, "deck_id": deck_id}
        result = await client.call_tool("get_deck", {"deck_id": deck_id})
        assert result.is_error
        result = await client.call_tool("create_deck", {"name": "x", "format": "nope"})
        assert result.is_error and "Unknown format" in result.content[0].text
    # The deck existed in the shared database (visible to the web app) before deletion.
    assert decks.list_all(conn, include_archived=True) == []


@pytest.mark.anyio
async def test_suggest_and_conflicts(app, conn):
    for name in (
        "Rampant Growth",
        "Cultivate",
        "Llanowar Elves",
        "Counterspell",
        "Swords to Plowshares",
        "Wrath of God",
        "Lightning Bolt",
    ):
        collection.add(conn, card_id=card_id(conn, name))
    sol = card_id(conn, "Sol Ring")
    collection.add(conn, card_id=sol, quantity=1)
    async with Client(app.state.mcp_server) as client:
        s = await call(client, "suggest_from_collection", commander="Tatyova, Benthic Druid")
        names = [c["card"]["name"] for c in s["candidates"]]
        assert (
            s["color_identity"] == "UG"
            and "Rampant Growth" in names
            and "Lightning Bolt" not in names
        )
        assert "ramp" in s["categories"] and s["candidates"][0]["why"]
        s = await call(client, "suggest_from_collection", color_identity="W", theme="destroy")
        names = [c["card"]["name"] for c in s["candidates"]]
        assert names[0] == "Wrath of God" and "Swords to Plowshares" not in names
        assert s["candidates"][0]["category"] == "wipes"
        result = await client.call_tool("suggest_from_collection", {})
        assert result.is_error
        a = await call(
            client, "create_deck", name="A", format="commander", cards=[{"card": "Sol Ring"}]
        )
        b = await call(
            client, "create_deck", name="B", format="commander", cards=[{"card": "Sol Ring"}]
        )
        c = await call(client, "deck_conflicts")
        assert (
            len(c["items"]) == 1
            and c["items"][0]["card"]["name"] == "Sol Ring"
            and c["items"][0]["shortfall"] == 1
        )
        assert {d["name"] for d in c["items"][0]["decks"]} == {"A", "B"}
        assert a["id"] != b["id"]


def test_mcp_endpoint_requires_auth(tmp_path, conn):
    from app import db
    from app.auth import AccessVerifier
    from tests.conftest import seed_catalogue
    from tests.test_auth import AUD, TEAM, FakeKeys, keypair, make_token  # noqa: F401

    settings = make_settings(
        tmp_path,
        dev_mode=False,
        cf_access_team_domain=TEAM,
        cf_access_aud=AUD,
        mcp_bearer_token="s3cret",
    )
    c = db.connect(settings.db_path)
    db.migrate(c)
    seed_catalogue(c)
    c.close()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    app = create_app(settings, run_scheduler=False)
    app.state.access_verifier = AccessVerifier(settings, key_source=FakeKeys(private.public_key()))
    # The middleware was built with the old verifier; rebuild the endpoint with the fake one.
    from app.mcp_server import MCPAuthMiddleware

    for route in app.router.routes:
        if getattr(route, "name", "") == "mcp":
            route.endpoint = MCPAuthMiddleware(
                route.endpoint.app, settings, app.state.access_verifier
            )
            route.app = route.endpoint
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as client:
        assert client.post("/mcp", json=body, headers=headers).status_code == 401
        assert (
            client.post(
                "/mcp", json=body, headers={**headers, "Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        r = client.post("/mcp", json=body, headers={**headers, "Authorization": "Bearer s3cret"})
        assert r.status_code != 401, r.text
        svc = make_token(pem, common_name="claude-code.access")
        r = client.post("/mcp", json=body, headers={**headers, "Cf-Access-Jwt-Assertion": svc})
        assert r.status_code != 401, r.text
        user = make_token(pem, email="me@example.com")
        assert (
            client.post(
                "/mcp", json=body, headers={**headers, "Cf-Access-Jwt-Assertion": user}
            ).status_code
            != 401
        )
        bad = make_token(pem, email="me@example.com", aud="c" * 64)
        assert (
            client.post(
                "/mcp", json=body, headers={**headers, "Cf-Access-Jwt-Assertion": bad}
            ).status_code
            == 401
        )
        # The web API still refuses the bearer token and the service token.
        assert (
            client.get("/api/v1/status", headers={"Authorization": "Bearer s3cret"}).status_code
            == 401
        )
        assert (
            client.get("/api/v1/status", headers={"Cf-Access-Jwt-Assertion": svc}).status_code
            == 401
        )


def test_mcp_http_round_trip(app):
    """A real JSON-RPC exchange over the mounted transport (dev mode, no auth)."""
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as client:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        r = client.post("/mcp", json=init, headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["result"]["serverInfo"]["name"] == "mtg-collection"
        session = r.headers.get("mcp-session-id")
        h = {**headers, "MCP-Protocol-Version": data["result"]["protocolVersion"]}
        if session:
            h["Mcp-Session-Id"] = session
        client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h
        )
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=h)
        assert r.status_code == 200, r.text
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert "search_collection" in names
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_decks", "arguments": {}},
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert json.loads(r.json()["result"]["content"][0]["text"]) == {"items": []}
