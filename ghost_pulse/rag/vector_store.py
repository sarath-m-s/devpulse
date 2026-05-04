"""SQLite-backed vector store with brute-force cosine similarity.

Optimised for small datasets (<500 vectors) — no external vector DB needed.
Embeddings stored as BLOB (packed float32 array via struct).
"""

from __future__ import annotations

import json
import math
import struct
from typing import Any

from devpulse import db as _db


def _pack(vec: list[float]) -> bytes:
    """Pack a list of floats to bytes (float32)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(data: bytes) -> list[float]:
    """Unpack float32 bytes back to a list."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


def _cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def upsert_fix_embedding(fix_record_id: int, embedding: list[float]) -> None:
    """Store or update the embedding for a fix_record row."""
    blob = _pack(embedding)
    with _db._write_lock, _db._get_conn() as conn:
        conn.execute(
            "UPDATE fix_records SET embedding=? WHERE id=?",
            (blob, fix_record_id),
        )


def search_similar_fixes(
    query_vec: list[float],
    top_k: int = 5,
    min_similarity: float = 0.60,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Return top-k fix records by cosine similarity to query_vec.

    Results are sorted by descending similarity.
    Only rows with a stored embedding are considered.
    """
    if not query_vec:
        return []

    clauses = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if project:
        clauses.append("project=?")
        params.append(project)
    where = "WHERE " + " AND ".join(clauses)

    with _db._get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM fix_records {where} ORDER BY created_at DESC LIMIT 500",
            params,
        ).fetchall()

    results: list[tuple[float, dict]] = []
    for row in rows:
        d = dict(row)
        raw = d.pop("embedding", None)
        if not raw:
            continue
        vec = _unpack(raw)
        sim = _cosine(query_vec, vec)
        if sim >= min_similarity:
            try:
                d["fix_commands"] = json.loads(d["fix_commands"]) if d["fix_commands"] else []
            except (json.JSONDecodeError, TypeError):
                d["fix_commands"] = []
            d["similarity"] = round(sim, 4)
            results.append((sim, d))

    results.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in results[:top_k]]
