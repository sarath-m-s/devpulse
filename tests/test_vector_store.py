"""Tests for the SQLite vector store."""

from __future__ import annotations

import pytest

from ghost_pulse import db
from ghost_pulse.rag.vector_store import (
    _cosine,
    _pack,
    _unpack,
    search_similar_fixes,
    upsert_fix_embedding,
)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


# ------------------------------------------------------------------
# Pack/unpack round-trip
# ------------------------------------------------------------------

def test_pack_unpack_roundtrip():
    vec = [0.1, 0.5, -0.3, 0.9]
    unpacked = _unpack(_pack(vec))
    for a, b in zip(vec, unpacked):
        assert abs(a - b) < 1e-5


def test_pack_empty():
    assert _pack([]) == b""


# ------------------------------------------------------------------
# Cosine similarity
# ------------------------------------------------------------------

def test_cosine_identical():
    v = [1.0, 0.5, -0.5]
    assert _cosine(v, v) == pytest.approx(1.0, abs=1e-4)


def test_cosine_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _cosine(a, b) == pytest.approx(0.0, abs=1e-6)


def test_cosine_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert _cosine(a, b) == pytest.approx(-1.0, abs=1e-4)


def test_cosine_mismatched_lengths():
    assert _cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_empty():
    assert _cosine([], []) == 0.0


# ------------------------------------------------------------------
# Vector store operations
# ------------------------------------------------------------------

def _insert_fix_record(error_hash: str, pattern: str, project: str = "test") -> int:
    return db.upsert_fix_record(
        error_hash=error_hash,
        error_pattern=pattern,
        fix_summary="some fix",
        fix_commands=["fix_cmd"],
        project=project,
    )


def test_upsert_and_search():
    rid = _insert_fix_record("hash1", "pytest tests/")
    vec = [0.9, 0.1, 0.0]
    upsert_fix_embedding(rid, vec)

    # Query with the same vector
    results = search_similar_fixes(vec, top_k=5, min_similarity=0.8)
    assert len(results) == 1
    assert results[0]["error_pattern"] == "pytest tests/"
    assert results[0]["similarity"] >= 0.99


def test_search_respects_min_similarity():
    rid = _insert_fix_record("hash2", "docker build .")
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.0, 0.0, 1.0]  # orthogonal — similarity = 0
    upsert_fix_embedding(rid, vec_a)

    results = search_similar_fixes(vec_b, top_k=5, min_similarity=0.5)
    assert len(results) == 0


def test_search_top_k_limit():
    for i in range(5):
        rid = _insert_fix_record(f"hash_k{i}", f"cmd_{i}")
        # All similar vectors
        upsert_fix_embedding(rid, [1.0 - i * 0.01, i * 0.01, 0.0])

    results = search_similar_fixes([1.0, 0.0, 0.0], top_k=2, min_similarity=0.5)
    assert len(results) <= 2


def test_search_empty_query():
    _insert_fix_record("hash3", "make build")
    results = search_similar_fixes([], top_k=5)
    assert results == []


def test_search_skips_rows_without_embedding():
    _insert_fix_record("hash4", "go build ./...")  # no embedding set
    results = search_similar_fixes([1.0, 0.0], top_k=5, min_similarity=0.0)
    assert len(results) == 0  # row has no embedding


def test_search_project_filter():
    rid_a = _insert_fix_record("ha", "pytest", project="alpha")
    rid_b = _insert_fix_record("hb", "pytest", project="beta")
    vec = [1.0, 0.0]
    upsert_fix_embedding(rid_a, vec)
    upsert_fix_embedding(rid_b, vec)

    results = search_similar_fixes(vec, top_k=10, min_similarity=0.5, project="alpha")
    assert all(r["project"] == "alpha" for r in results)
    assert len(results) == 1


def test_search_sorted_by_similarity_desc():
    for i, sim in enumerate([0.7, 0.9, 0.8]):
        rid = _insert_fix_record(f"hsort{i}", f"cmd_{i}")
        # Create a vector at angle proportional to sim
        import math
        angle = math.acos(min(1.0, sim))
        vec = [math.cos(angle), math.sin(angle)]
        upsert_fix_embedding(rid, vec)

    results = search_similar_fixes([1.0, 0.0], top_k=3, min_similarity=0.6)
    sims = [r["similarity"] for r in results]
    assert sims == sorted(sims, reverse=True)
