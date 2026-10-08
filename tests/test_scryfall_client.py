import httpx

from app.scryfall.client import FAST_INTERVAL, SLOW_INTERVAL, RateGate, ScryfallClient


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_rate_gate_spaces_requests():
    clock = Clock()
    gate = RateGate(clock=clock.now, sleep=clock.sleep)
    gate.wait("slow", SLOW_INTERVAL)
    gate.wait("slow", SLOW_INTERVAL)
    gate.wait("slow", SLOW_INTERVAL)
    assert clock.slept == [SLOW_INTERVAL, SLOW_INTERVAL]
    gate.wait("fast", FAST_INTERVAL)
    assert len(clock.slept) == 2  # separate bucket, no wait


def test_client_sends_required_headers_and_retries_429():
    seen = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"details": "slow down"})
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "type": "default_cards",
                        "updated_at": "x",
                        "jsonl_download_uri": "https://data.scryfall.io/x.jsonl.gz",
                    }
                ],
            },
        )

    clock = Clock()
    client = ScryfallClient(
        "TestApp/1.0",
        transport=httpx.MockTransport(handler),
        gate=RateGate(clock=clock.now, sleep=clock.sleep),
        sleep=clock.sleep,
    )
    item = client.bulk_item("default_cards")
    assert item["jsonl_download_uri"].endswith(".jsonl.gz")
    assert seen[0].headers["User-Agent"] == "TestApp/1.0"
    assert seen[0].headers["Accept"] == "application/json"
    assert 30.0 in clock.slept


def test_collection_batches_of_75():
    batches = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        ids = json.loads(request.content)["identifiers"]
        batches.append(len(ids))
        return httpx.Response(200, json={"data": [{"id": i["id"]} for i in ids], "not_found": []})

    clock = Clock()
    client = ScryfallClient(
        "TestApp/1.0",
        transport=httpx.MockTransport(handler),
        gate=RateGate(clock=clock.now, sleep=clock.sleep),
        sleep=clock.sleep,
    )
    out = client.collection([{"id": str(i)} for i in range(160)])
    assert batches == [75, 75, 10] and len(out["data"]) == 160
