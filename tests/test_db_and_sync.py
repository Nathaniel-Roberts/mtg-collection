import gzip
import json

from app import db
from app.scryfall.bulk import iter_bulk_file, iter_jsonl_gz_chunks
from app.scryfall.sync import card_row, last_run, sync_catalogue
from tests.conftest import FIXTURES


def test_migrations_are_idempotent(settings):
    conn = db.connect(settings.db_path)
    assert db.migrate(conn)
    assert db.migrate(conn) == []
    conn.close()


def test_catalogue_seeded(conn):
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 27
    assert conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0] > 10
    run = last_run(conn, "catalogue")
    assert run is not None and json.loads(run["detail"])["cards"] == 27


def test_fts_tracks_cards(conn):
    rows = conn.execute(
        "SELECT name FROM cards_fts WHERE cards_fts MATCH '\"lightning\"*'"
    ).fetchall()
    assert {r[0] for r in rows} == {"Lightning Bolt"}


def test_card_row_handles_double_faced_cards():
    for obj in iter_bulk_file(FIXTURES / "cards.jsonl"):
        if obj["layout"] == "transform":
            row = card_row(obj, 1, "now")
            assert row["image_normal"] and row["image_back_normal"]
            assert " // " in row["oracle_text"]
            assert row["name"].startswith("Delver of Secrets")
            faces = json.loads(row["card_faces"])
            assert len(faces) == 2 and faces[1]["name"] == "Insectile Aberration"
            assert row["colors"] == "U"
            break
    else:
        raise AssertionError("no transform card in fixtures")


def test_card_row_basic_fields():
    obj = next(o for o in iter_bulk_file(FIXTURES / "cards.jsonl") if o["name"] == "Lightning Bolt")
    row = card_row(obj, 7, "now")
    assert row["set_code"] == obj["set"] and row["paper"] == 1 and row["seen_in_sync"] == 7
    assert json.loads(row["prices"])["usd"] == obj["prices"]["usd"]
    assert row["colors"] == "R" and row["color_identity"] == "R"


def test_jsonl_gz_streaming_in_small_chunks():
    objs = [{"object": "card", "id": str(i), "name": f"Card {i}"} for i in range(50)]
    payload = gzip.compress("\n".join(json.dumps(o) for o in objs).encode())
    chunks = [payload[i : i + 7] for i in range(0, len(payload), 7)]
    out = list(iter_jsonl_gz_chunks(chunks))
    assert [o["id"] for o in out] == [str(i) for i in range(50)]


def test_resync_updates_not_duplicates(conn):
    before = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    sets = json.loads((FIXTURES / "sets.json").read_text())["data"]
    sync_catalogue(conn, client=None, cards=iter_bulk_file(FIXTURES / "cards.jsonl"), sets=sets)
    assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM cards_fts").fetchone()[0] == before
