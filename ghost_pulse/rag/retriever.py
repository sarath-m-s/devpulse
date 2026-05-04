"""Hybrid retriever — three-tier lookup for fix suggestions.

Tier 1 (exact):   error_hash match in error_memory or fix_records
Tier 2 (fuzzy):   token-overlap between normalized command patterns
Tier 3 (semantic): cosine similarity of embeddings (if provider available)
"""

from __future__ import annotations

import json
import re
from typing import Any

from ghost_pulse import db
from ghost_pulse.analyzers.toil import normalize_command
from ghost_pulse.analyzers.error_memory import _error_hash
from ghost_pulse.rag.embeddings import EmbeddingProvider, NullEmbeddingProvider
from ghost_pulse.rag.vector_store import search_similar_fixes


def _tokenize(text: str) -> set[str]:
    """Return lowercase word tokens from a command/pattern string."""
    return set(re.findall(r"[a-z0-9_\-\.]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


class FixRetriever:
    """Retrieves relevant fix suggestions for a failing command."""

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        fuzzy_threshold: float = 0.25,
        semantic_threshold: float = 0.60,
    ) -> None:
        self._embed = embedding_provider or NullEmbeddingProvider()
        self._fuzzy_threshold = fuzzy_threshold
        self._semantic_threshold = semantic_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def suggest(
        self,
        command: str,
        exit_code: int = 1,
        project: str | None = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Return up to top_k fix suggestions for a failing command.

        Each result has keys:
          tier       — "exact" | "fuzzy" | "semantic"
          score      — float similarity/confidence
          fix_summary — human-readable fix description
          fix_commands — list of commands to run
          fix_diff   — optional git diff string
          source     — "error_memory" | "fix_records"
        """
        seen_ids: set[str] = set()
        results: list[dict[str, Any]] = []

        # Tier 1 — exact hash match
        exact = self._tier1_exact(command, exit_code)
        for r in exact:
            uid = r.get("_uid", "")
            if uid not in seen_ids:
                seen_ids.add(uid)
                results.append(r)
        if len(results) >= top_k:
            return results[:top_k]

        # Tier 2 — fuzzy token overlap against known patterns
        fuzzy = self._tier2_fuzzy(command, project=project, top_k=top_k * 2)
        for r in fuzzy:
            uid = r.get("_uid", "")
            if uid not in seen_ids:
                seen_ids.add(uid)
                results.append(r)
        if len(results) >= top_k:
            return results[:top_k]

        # Tier 3 — semantic embedding search
        if self._embed.is_available():
            semantic = self._tier3_semantic(command, project=project, top_k=top_k)
            for r in semantic:
                uid = r.get("_uid", "")
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    results.append(r)

        return results[:top_k]

    # ------------------------------------------------------------------
    # Internal tiers
    # ------------------------------------------------------------------

    def _tier1_exact(self, command: str, exit_code: int) -> list[dict[str, Any]]:
        ehash = _error_hash(command, exit_code)

        # Check error_memory first
        row = db.get_error_memory_by_hash(ehash)
        if row and (row.get("fix_description") or row.get("fix_commands")):
            return [_format_result(row, "exact", 1.0, "error_memory")]

        # Check fix_records
        with db._get_conn(readonly=True) as conn:
            fr = conn.execute(
                "SELECT * FROM fix_records WHERE error_hash=? ORDER BY created_at DESC LIMIT 1",
                (ehash,),
            ).fetchone()
        if fr:
            d = _fix_record_to_dict(dict(fr))
            return [_format_result(d, "exact", 1.0, "fix_records")]

        return []

    def _tier2_fuzzy(
        self,
        command: str,
        project: str | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        normalized = normalize_command(command)
        query_tokens = _tokenize(normalized)
        if not query_tokens:
            return []

        # Pull recent error_memory rows
        errors = db.get_frequent_errors(project=project, days=180, limit=100)
        scored: list[tuple[float, dict]] = []
        for e in errors:
            pattern = e.get("error_pattern", "")
            tokens = _tokenize(pattern)
            score = _jaccard(query_tokens, tokens)
            if score >= self._fuzzy_threshold and (e.get("fix_description") or e.get("fix_commands")):
                scored.append((score, e))

        # Pull fix_records too
        with db._get_conn(readonly=True) as conn:
            rows = conn.execute(
                "SELECT * FROM fix_records ORDER BY created_at DESC LIMIT 200",
            ).fetchall()
        for row in rows:
            d = _fix_record_to_dict(dict(row))
            tokens = _tokenize(d.get("error_pattern", ""))
            score = _jaccard(query_tokens, tokens)
            if score >= self._fuzzy_threshold and (d.get("fix_summary") or d.get("fix_commands")):
                scored.append((score, d))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, row in scored[:top_k]:
            src = "fix_records" if "fix_summary" in row else "error_memory"
            results.append(_format_result(row, "fuzzy", score, src))
        return results

    def _tier3_semantic(
        self,
        command: str,
        project: str | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        try:
            query_vec = self._embed.embed(normalize_command(command))
        except Exception:
            return []
        hits = search_similar_fixes(
            query_vec,
            top_k=top_k,
            min_similarity=self._semantic_threshold,
            project=project,
        )
        return [_format_result(h, "semantic", h.get("similarity", 0.0), "fix_records") for h in hits]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fix_record_to_dict(row: dict) -> dict[str, Any]:
    row.pop("embedding", None)
    try:
        row["fix_commands"] = json.loads(row["fix_commands"]) if row["fix_commands"] else []
    except (json.JSONDecodeError, TypeError):
        row["fix_commands"] = []
    return row


def _format_result(
    row: dict[str, Any],
    tier: str,
    score: float,
    source: str,
) -> dict[str, Any]:
    if source == "fix_records":
        fix_summary = row.get("fix_summary") or ""
        fix_cmds = row.get("fix_commands") or []
        fix_diff = row.get("fix_diff") or ""
        error_pattern = row.get("error_pattern") or ""
        project = row.get("project") or ""
        uid = f"fr:{row.get('id', id(row))}"
    else:  # error_memory
        fix_summary = row.get("fix_description") or ""
        fix_cmds = row.get("fix_commands") or []
        fix_diff = row.get("fix_diff") or ""
        error_pattern = row.get("error_pattern") or ""
        project = row.get("project") or ""
        uid = f"em:{row.get('id', id(row))}"

    return {
        "tier": tier,
        "score": round(score, 4),
        "fix_summary": fix_summary,
        "fix_commands": fix_cmds,
        "fix_diff": fix_diff,
        "error_pattern": error_pattern,
        "project": project,
        "source": source,
        "_uid": uid,
    }
