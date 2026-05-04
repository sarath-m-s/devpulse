"""Tests for the hybrid fix retriever."""

from __future__ import annotations

import pytest

from devpulse import db
from devpulse.rag.embeddings import NullEmbeddingProvider
from devpulse.rag.retriever import FixRetriever, _jaccard, _tokenize


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


# ------------------------------------------------------------------
# Helper utilities
# ------------------------------------------------------------------

def test_tokenize_basic():
    tokens = _tokenize("pytest tests/foo.py -v")
    assert "pytest" in tokens
    assert "tests" in tokens
    assert "foo.py" in tokens


def test_jaccard_identical():
    a = {"pytest", "tests"}
    assert _jaccard(a, a) == pytest.approx(1.0)


def test_jaccard_disjoint():
    assert _jaccard({"a", "b"}, {"c", "d"}) == pytest.approx(0.0)


def test_jaccard_partial():
    score = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
    assert 0.0 < score < 1.0


# ------------------------------------------------------------------
# Tier 1 — exact match
# ------------------------------------------------------------------

def test_tier1_exact_match_from_error_memory():
    # Insert error_memory row with a fix
    ehash_row = db.upsert_error_memory(
        error_hash="abc123",
        error_pattern="pytest tests/",
        project="myapp",
    )
    db.update_error_fix(
        error_id=ehash_row,
        fix_commands=["pytest --tb=short tests/"],
        fix_description="Use --tb=short flag",
    )

    # We need the hash to match what the retriever computes
    from devpulse.analyzers.error_memory import _error_hash
    ehash = _error_hash("pytest tests/", 1)
    db.upsert_error_memory(
        error_hash=ehash,
        error_pattern="pytest tests/",
        project="myapp",
    )
    db.update_error_fix(
        error_id=db.get_error_memory_by_hash(ehash)["id"],
        fix_commands=["pytest --tb=short tests/"],
        fix_description="Use --tb=short flag",
    )

    retriever = FixRetriever(embedding_provider=NullEmbeddingProvider())
    results = retriever.suggest("pytest tests/", exit_code=1)
    assert len(results) >= 1
    assert results[0]["tier"] == "exact"
    assert results[0]["score"] == 1.0


# ------------------------------------------------------------------
# Tier 2 — fuzzy match
# ------------------------------------------------------------------

def test_tier2_fuzzy_match():
    # Insert a fix record for a similar command
    from devpulse.analyzers.error_memory import _error_hash
    ehash = _error_hash("docker build -t myapp .", 1)
    db.upsert_fix_record(
        error_hash=ehash,
        error_pattern="docker build -t myapp .",
        fix_summary="Run docker system prune first",
        fix_commands=["docker system prune -f", "docker build -t myapp ."],
        project="myapp",
    )

    retriever = FixRetriever(
        embedding_provider=NullEmbeddingProvider(),
        fuzzy_threshold=0.1,  # low threshold for test
    )
    # Query with a slightly different docker command
    results = retriever.suggest("docker build -t app .", exit_code=1, top_k=3)
    # Should find the fuzzy match
    fuzzy_results = [r for r in results if r["tier"] == "fuzzy"]
    assert len(fuzzy_results) >= 1


# ------------------------------------------------------------------
# Tier 3 — semantic (skipped without real embeddings)
# ------------------------------------------------------------------

def test_tier3_skipped_with_null_provider():
    retriever = FixRetriever(embedding_provider=NullEmbeddingProvider())
    # With NullEmbeddingProvider, semantic tier should silently skip
    results = retriever.suggest("some totally unique command xyz", exit_code=1)
    semantic_results = [r for r in results if r["tier"] == "semantic"]
    assert len(semantic_results) == 0


# ------------------------------------------------------------------
# Empty results
# ------------------------------------------------------------------

def test_no_suggestions_when_db_empty():
    retriever = FixRetriever(embedding_provider=NullEmbeddingProvider())
    results = retriever.suggest("some totally unknown command", exit_code=1)
    assert results == []


def test_top_k_limit():
    from devpulse.analyzers.error_memory import _error_hash
    # Insert multiple error records
    for i in range(10):
        ehash = _error_hash(f"pytest test_{i}.py", 1)
        db.upsert_error_memory(
            error_hash=ehash,
            error_pattern=f"pytest test_{i}.py",
            project="proj",
        )
        db.update_error_fix(
            error_id=db.get_error_memory_by_hash(ehash)["id"],
            fix_commands=[f"fix_{i}"],
            fix_description=f"Fix {i}",
        )

    retriever = FixRetriever(
        embedding_provider=NullEmbeddingProvider(),
        fuzzy_threshold=0.01,
    )
    results = retriever.suggest("pytest test_0.py", exit_code=1, top_k=2)
    assert len(results) <= 2
