"""Identification pipeline tests with fake models, plus the scan API."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app import db
from app.scan import service
from app.scan.detect import DetectStage
from app.scan.embed import EmbedStage
from app.scan.ocr import OCRStage, parse_collector_line
from app.scan.pipeline import Identifier, ScanContext, decode_image
from app.scan.resolve import ResolveStage
from tests.conftest import card_id

# --- fakes ------------------------------------------------------------------------------------


class FakeDetection:
    def __init__(self, present=True):
        self.card_present = present
        self.corners = (
            np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], dtype=np.float32)
            if present
            else None
        )
        self.confidence = 0.9
        self.sharpness = 0.05

    def dewarp(self, bgr):
        return Image.new("RGB", (448, 448), (20, 20, 20))


class FakeDetector:
    def __init__(self, present=True):
        self.present = present

    def detect(self, image):
        return FakeDetection(self.present)


class FakeEmbedder:
    def embed(self, images):
        n = len(images) if isinstance(images, list) else 1
        return np.zeros((n, 128), dtype=np.float32)


class FakeCatalog:
    def __init__(self, hits):
        self.hits = hits  # [(score, card_id)]
        self.embedder = FakeEmbedder()

    def search(self, embedding, top_k=5):
        return self.hits[:top_k]


class FakeOCR:
    def __init__(self, text):
        self.text = text

    def __call__(self, image, use_cls=None):
        class Out:
            txts = [self.text] if self.text else []
            scores = [0.9] if self.text else []

        return Out()


def photo_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (120, 100, 90)).save(buf, "JPEG")
    return buf.getvalue()


def make_identifier(conn, settings, hits, ocr_text="", present=True, gap=0.10):
    def connect():
        return db.connect(settings.db_path)

    return Identifier(
        [
            DetectStage(FakeDetector(present)),
            EmbedStage(FakeCatalog(hits)),
            OCRStage(
                FakeOCR(ocr_text), lambda: {r[0] for r in conn.execute("SELECT code FROM sets")}
            ),
            ResolveStage(connect, gap=gap, min_score=0.55),
        ],
        {"test": True},
    )


# --- parser -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "0123/0281 R MOM • EN",
            {"collector_number": "123", "set_code": "mom", "language": "en", "set_total": 281},
        ),
        (
            "146/249 C M10 EN",
            {"collector_number": "146", "set_code": "m10", "language": "en", "set_total": 249},
        ),
        (
            "MH2 EN 267/303 U",
            {"collector_number": "267", "set_code": "mh2", "language": "en", "set_total": 303},
        ),
        (
            "0100/0281 U LTR•JP",
            {"collector_number": "100", "set_code": "ltr", "language": "ja", "set_total": 281},
        ),
        (
            "146/249",
            {"collector_number": "146", "set_code": None, "language": None, "set_total": 249},
        ),
        (
            "0410 U CMM",
            {"collector_number": "410", "set_code": "cmm", "language": None, "set_total": None},
        ),
        (
            "garbage text here",
            {"collector_number": None, "set_code": None, "language": None, "set_total": None},
        ),
        (
            "841/1067 U CMM EN",
            {"collector_number": "841", "set_code": "cmm", "language": "en", "set_total": 1067},
        ),
        (
            "0031l0145 M WWK",
            {"collector_number": "31", "set_code": "wwk", "language": None, "set_total": 145},
        ),
        (
            "U0410 & © 2023 Wizards of the Coast CMM·ENMIKE BIEREK",
            {"collector_number": "410", "set_code": "cmm", "language": None, "set_total": None},
        ),
        (
            "U 0410 CMM·ENMIKE BIEREK",
            {"collector_number": "410", "set_code": "cmm", "language": None, "set_total": None},
        ),
        (
            "Christopher Moeller ™ & © 1993-2009 Wizards of the Coast LLC 146/249",
            {"collector_number": "146", "set_code": None, "language": None, "set_total": 249},
        ),
        (
            "Christopher Moeller & 1093-2009 Wizards of the Coa",
            {"collector_number": None, "set_code": None, "language": None, "set_total": None},
        ),
        (
            "0071/0281 R MOM·EN 2023",
            {"collector_number": "71", "set_code": "mom", "language": "en", "set_total": 281},
        ),
    ],
)
def test_parse_collector_line(text, expected):
    known = {"mom", "m10", "mh2", "ltr", "cmm", "wwk"}
    assert parse_collector_line(text, known) == expected


def test_parse_prefers_known_set_codes():
    out = parse_collector_line("ABC 0146/0249 C M10 EN", {"m10"})
    assert out["set_code"] == "m10"


# --- resolver ---------------------------------------------------------------------------------


def test_ocr_pins_the_exact_printing(conn, settings):
    m10 = card_id(conn, "Lightning Bolt", "m10")
    clb = card_id(conn, "Lightning Bolt", "clb")
    ident = make_identifier(
        conn,
        settings,
        [(0.97, clb), (0.95, m10), (0.60, card_id(conn, "Sol Ring"))],
        "146/249 C M10 EN",
    )
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.match.card_id == m10 and ctx.confident and ctx.method == "embedding+ocr"
    assert [c.card_id for c in ctx.candidates[:2]] == [m10, clb]
    assert ctx.candidates[-1].card_id == card_id(conn, "Sol Ring")
    assert ctx.errors == [] and set(ctx.timings_ms) >= {
        "detect",
        "embed",
        "ocr",
        "resolve",
        "total",
    }


def test_ocr_that_disagrees_with_image_is_ignored(conn, settings):
    sol = card_id(conn, "Sol Ring")
    ident = make_identifier(
        conn, settings, [(0.95, sol), (0.40, card_id(conn, "Forest"))], "146/249 C M10 EN"
    )
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.match.card_id == sol and ctx.method == "embedding" and ctx.confident


def test_low_margin_is_not_confident(conn, settings):
    sol = card_id(conn, "Sol Ring")
    forest = card_id(conn, "Forest")
    ident = make_identifier(conn, settings, [(0.80, sol), (0.75, forest)], "")
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.match.card_id == sol and not ctx.confident and "low margin" in ctx.match.reason
    assert any(c.card_id == forest and c.reason == "other image match" for c in ctx.candidates)


def test_same_card_two_printings_is_confident_by_group(conn, settings):
    m10 = card_id(conn, "Lightning Bolt", "m10")
    clb = card_id(conn, "Lightning Bolt", "clb")
    ident = make_identifier(
        conn, settings, [(0.90, m10), (0.89, clb), (0.50, card_id(conn, "Forest"))], ""
    )
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.confident and ctx.match.card_id == m10


def test_number_only_picks_printing_in_group(conn, settings):
    m10 = card_id(conn, "Lightning Bolt", "m10")
    clb = card_id(conn, "Lightning Bolt", "clb")
    ident = make_identifier(conn, settings, [(0.90, m10), (0.89, clb)], "187/361")
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.match.card_id == clb and ctx.method == "embedding+ocr"


def test_unknown_hits_are_dropped(conn, settings):
    ident = make_identifier(
        conn, settings, [(0.99, "not-a-card"), (0.70, card_id(conn, "Ponder"))], ""
    )
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.match.card_id == card_id(conn, "Ponder")


def test_no_card_detected_falls_back_to_full_frame(conn, settings):
    ident = make_identifier(conn, settings, [(0.9, card_id(conn, "Ponder"))], "", present=False)
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.detection["source"] == "fallback_full_frame" and ctx.match is not None


def test_stage_failure_is_recorded_not_fatal(conn, settings):
    class Boom:
        name = "boom"

        def run(self, ctx):
            raise RuntimeError("model exploded")

    ident = Identifier([Boom()])
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes())))
    assert ctx.errors == ["boom: model exploded"] and ctx.match is None


def test_prewarped_skips_detection(conn, settings):
    ident = make_identifier(conn, settings, [(0.9, card_id(conn, "Ponder"))], "")
    ctx = ident.identify(ScanContext(image=decode_image(photo_bytes()), prewarped=True))
    assert ctx.detection == {"card_present": True, "source": "client"} and ctx.crop.size == (
        448,
        448,
    )


def test_ocr_strip_inverts_dark_footer():
    dark = np.zeros((448, 448, 3), dtype=np.uint8)
    strip = OCRStage.strip(dark)
    assert strip.shape[0] >= 90 and strip.mean() > 200


# --- API --------------------------------------------------------------------------------------


@pytest.fixture
def scan_client(client, conn, settings):
    hits = [
        (0.97, card_id(conn, "Lightning Bolt", "clb")),
        (0.95, card_id(conn, "Lightning Bolt", "m10")),
    ]
    enabled = settings.model_copy(update={"scanner_enabled": True})
    client.app.state.scanner = service.Scanner(
        enabled, build=lambda s: make_identifier(conn, s, hits, "146/249 C M10 EN")
    )
    return client


def test_scan_confirm_flow(scan_client, conn):
    files = {"image": ("photo.jpg", io.BytesIO(photo_bytes()), "image/jpeg")}
    r = scan_client.post("/api/v1/scan", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["match"]["card"]["set_code"] == "m10" and body["match"]["confident"] is True
    assert (
        body["ocr"]["collector_number"] == "146"
        and body["candidates"][1]["card"]["set_code"] == "clb"
    )
    assert body["timings_ms"]["total"] >= 0
    scan_id = body["scan_id"]
    assert scan_client.get(f"/api/v1/scan/{scan_id}/image").status_code == 200
    r = scan_client.post(
        f"/api/v1/scan/{scan_id}/confirm",
        json={"card_id": body["match"]["card"]["id"], "finish": "foil", "quantity": 2},
    )
    assert r.status_code == 201 and r.json()["quantity"] == 2 and r.json()["source"] == "scan"
    row = conn.execute(
        "SELECT outcome, chosen_card_id FROM scans WHERE id = ?", (scan_id,)
    ).fetchone()
    assert row["outcome"] == "confirmed_best"
    recent = scan_client.get("/api/v1/scan/recent").json()
    assert recent["items"][0]["outcome"] == "confirmed_best" and recent["outcomes"] == {
        "confirmed_best": 1
    }
    # Correcting to another printing is recorded as such.
    r = scan_client.post(
        "/api/v1/scan", files={"image": ("p.jpg", io.BytesIO(photo_bytes()), "image/jpeg")}
    )
    other = r.json()["candidates"][1]["card"]["id"]
    scan_client.post(f"/api/v1/scan/{r.json()['scan_id']}/confirm", json={"card_id": other})
    assert scan_client.get("/api/v1/scan/recent").json()["outcomes"]["corrected"] == 1
    r = scan_client.post(
        "/api/v1/scan", files={"image": ("p.jpg", io.BytesIO(photo_bytes()), "image/jpeg")}
    )
    assert scan_client.post(f"/api/v1/scan/{r.json()['scan_id']}/reject").json() == {
        "rejected": True
    }
    assert scan_client.post("/api/v1/scan/999/confirm", json={"card_id": other}).status_code == 404


def test_scan_rejects_bad_input(scan_client):
    assert (
        scan_client.post(
            "/api/v1/scan", files={"image": ("p.jpg", io.BytesIO(b""), "image/jpeg")}
        ).status_code
        == 422
    )
    assert (
        scan_client.post(
            "/api/v1/scan", files={"image": ("p.txt", io.BytesIO(b"hello"), "text/plain")}
        ).status_code
        == 422
    )


def test_scanner_disabled_returns_503(client, conn, tmp_path):
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, scanner_enabled=False)
    client.app.state.scanner = service.Scanner(settings)
    r = client.post(
        "/api/v1/scan", files={"image": ("p.jpg", io.BytesIO(photo_bytes()), "image/jpeg")}
    )
    assert r.status_code == 503


def test_scanner_load_error_is_reported(client, tmp_path):
    from tests.conftest import make_settings

    def broken(settings):
        raise RuntimeError("no catalog")

    scanner = service.Scanner(make_settings(tmp_path, scanner_enabled=True), build=broken)
    client.app.state.scanner = scanner
    r = client.post(
        "/api/v1/scan", files={"image": ("p.jpg", io.BytesIO(photo_bytes()), "image/jpeg")}
    )
    assert r.status_code == 503 and "no catalog" in r.json()["detail"]
    assert client.get("/api/v1/status").json()["scanner"]["error"] == "no catalog"


def test_image_pruning(conn, settings, tmp_path):
    settings = settings.model_copy(update={"scan_keep_images": 2})
    for _ in range(4):
        ctx = ScanContext(image=decode_image(photo_bytes()))
        ctx.crop = Image.new("RGB", (448, 448))
        service.record_scan(conn, settings, ctx)
    kept = conn.execute("SELECT COUNT(*) FROM scans WHERE image_path IS NOT NULL").fetchone()[0]
    assert kept == 2 and len(list(service.scans_dir(settings).glob("*.jpg"))) == 2


def test_backup_endpoint(client):
    r = client.get("/api/v1/backup")
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
