"""Streaming reader for Scryfall bulk files.

Bulk files are gzipped JSON Lines (one card object per line), fetched from the
``jsonl_download_uri`` in the bulk-data object. The file is decompressed on the fly so
memory stays flat regardless of its size.
"""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from app.scryfall.client import ScryfallClient


def iter_jsonl_gz_chunks(chunks: Iterable[bytes]) -> Iterator[dict[str, Any]]:
    """Decode gzip chunks into card objects, one per complete line."""
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    buffer = b""
    for chunk in chunks:
        buffer += decoder.decompress(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line = buffer[:newline].strip()
            buffer = buffer[newline + 1 :]
            if line and line not in (b"[", b"]"):
                yield json.loads(line.rstrip(b","))
    buffer += decoder.flush()
    line = buffer.strip()
    if line and line not in (b"[", b"]"):
        yield json.loads(line.rstrip(b","))


def iter_bulk_file(path: Path) -> Iterator[dict[str, Any]]:
    """Read a local bulk file; gzipped JSONL, plain JSONL or a JSON array."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as fh:  # type: ignore[operator]
        head = fh.read(1)
        fh.seek(0)
        if head == b"[":
            yield from json.load(fh)
            return
        for raw in fh:
            line = raw.strip()
            if line:
                yield json.loads(line)


def iter_bulk_download(client: ScryfallClient, url: str) -> Iterator[dict[str, Any]]:
    response = client.stream(url)
    try:
        response.raise_for_status()
        yield from iter_jsonl_gz_chunks(response.iter_bytes(chunk_size=1 << 16))
    finally:
        response.close()
