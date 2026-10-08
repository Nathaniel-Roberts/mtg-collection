import httpx

from app import collection, db
from app.pricing import fx, snapshots
from tests.conftest import card_id, make_settings


def test_snapshot_only_owned_or_decked(conn, rates):
    from app import decks

    collection.add(conn, card_id=card_id(conn, "Sol Ring"))
    deck = decks.create(conn, name="d", format_key="commander")
    decks.set_cards(conn, deck, [{"card_id": card_id(conn, "Forest"), "quantity": 1}])
    n = snapshots.snapshot_prices(conn, "2026-10-09")
    assert n == 2
    hist = snapshots.card_price_history(conn, card_id(conn, "Sol Ring"))
    assert hist[0]["day"] == "2026-10-09" and hist[0]["usd"] is not None


def test_rollup_and_totals(conn, rates):
    bolt = card_id(conn, "Lightning Bolt", "m10")
    collection.add(conn, card_id=bolt, quantity=2)
    usd = float(
        conn.execute(
            "SELECT json_extract(prices, '$.usd') FROM cards WHERE id = ?", (bolt,)
        ).fetchone()[0]
    )
    totals = snapshots.rollup_value(conn, "2026-10-09", rates)
    assert totals["usd"] == round(2 * usd, 2) and totals["aud"] == round(2 * usd * 1.5, 2)
    history = snapshots.value_history(conn)
    assert history[-1]["day"] == "2026-10-09" and history[-1]["usd_aud"] == 1.5


def test_current_rates_precedence(conn, tmp_path):
    settings = make_settings(tmp_path)
    r = fx.current_rates(conn, settings)
    assert (r.usd_aud, r.eur_aud, r.source) == (1.5, 1.6, "test")
    db.set_setting(conn, "fx_override_usd_aud", "1.42")
    r = fx.current_rates(conn, settings)
    assert r.usd_aud == 1.42 and r.eur_aud == 1.6 and r.source == "manual"
    settings = make_settings(tmp_path, fx_eur_aud=1.7)
    assert fx.current_rates(conn, settings).eur_aud == 1.7


def test_fetch_frankfurter():
    def handler(request: httpx.Request) -> httpx.Response:
        base = request.url.params["base"]
        return httpx.Response(
            200,
            json={
                "base": base,
                "date": "2026-10-08",
                "rates": {"AUD": 1.44 if base == "USD" else 1.61},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert fx.fetch_frankfurter("https://api.frankfurter.dev/v1", http) == {
        "USD": 1.44,
        "EUR": 1.61,
    }


def test_refresh_keeps_last_rate_on_failure(conn, tmp_path):
    settings = make_settings(tmp_path, fx_provider="frankfurter")
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    r = fx.refresh_rates(conn, settings, "2026-10-09", http)
    assert r.usd_aud == 1.5 and r.source == "test"
