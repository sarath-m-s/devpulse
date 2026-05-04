"""Toil detector — finds repeated command sequences worth automating."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from ghost_pulse import db

# ---------------------------------------------------------------------------
# Normalisation patterns — strip variable parts from commands
# ---------------------------------------------------------------------------

_NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    # Git branch names
    (re.compile(r"\b(git\s+checkout)\s+\S+"), r"\1 <branch>"),
    (re.compile(r"\b(git\s+merge)\s+\S+"), r"\1 <branch>"),
    (re.compile(r"\b(git\s+rebase)\s+\S+"), r"\1 <branch>"),
    # Commit SHA / refs
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<sha>"),
    # File paths (anything containing / or .)
    (re.compile(r"(?<!\w)(?:[\w./-]+/[\w./-]+|[\w-]+\.[\w]+)(?!\w)"), "<path>"),
    # Port numbers
    (re.compile(r"\b\d{4,5}\b"), "<port>"),
    # Container / image IDs
    (re.compile(r"\b[0-9a-f]{12,64}\b"), "<id>"),
    # Version numbers
    (re.compile(r"\b\d+\.\d+(?:\.\d+)?\b"), "<version>"),
    # Generic long integers (IDs, timestamps)
    (re.compile(r"\b\d{5,}\b"), "<num>"),
]


def normalize_command(cmd: str) -> str:
    """Strip variable parts from a command string, preserving structure."""
    cmd = cmd.strip()
    for pattern, replacement in _NORMALIZERS:
        cmd = pattern.sub(replacement, cmd)
    # Collapse multiple spaces
    return re.sub(r"\s+", " ", cmd).strip()


def _seq_hash(sequence: list[str]) -> str:
    return hashlib.sha256(json.dumps(sequence).encode()).hexdigest()[:16]


def _sliding_windows(items: list[str], sizes: list[int]) -> list[tuple[str, ...]]:
    """Yield all sub-sequences of the given sizes."""
    result: list[tuple[str, ...]] = []
    for size in sizes:
        for i in range(len(items) - size + 1):
            result.append(tuple(items[i : i + size]))
    return result


def detect_toil(days: int = 14, threshold: int = 5) -> list[dict[str, Any]]:
    """
    Scan recent shell commands for repeated sequences.
    Stores detected patterns in DB and returns ranked list.
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    events = db.query_events(event_type="shell_cmd", since=since)

    raw_cmds = [e["data"].get("cmd", "") for e in events if e.get("data")]
    normalized = [normalize_command(c) for c in raw_cmds if c.strip()]

    counter: Counter[tuple[str, ...]] = Counter()
    for seq in _sliding_windows(normalized, sizes=[2, 3, 4, 5]):
        counter[seq] += 1

    patterns = []
    for seq, count in counter.items():
        if count < threshold:
            continue
        ph = _seq_hash(list(seq))
        db.upsert_toil_pattern(ph, list(seq), count=count)
        patterns.append(
            {
                "pattern_hash": ph,
                "commands": list(seq),
                "count": count,
                # Longer repeated sequences score higher
                "score": count * len(seq),
            }
        )

    # Sort by score descending
    patterns.sort(key=lambda x: x["score"], reverse=True)
    return patterns


def get_ranked_patterns() -> list[dict[str, Any]]:
    """Return toil patterns from DB, enriched with a score."""
    rows = db.get_toil_patterns()
    for row in rows:
        row["score"] = row["count"] * len(row.get("commands", []))
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


def estimate_time_wasted(pattern: dict[str, Any], minutes_per_run: float = 2.0) -> float:
    """Rough estimate of time wasted on a toil pattern, in hours."""
    return (pattern["count"] * minutes_per_run) / 60


def find_example_commands(normalized_seq: list[str], days: int = 30) -> list[str]:
    """
    Scan recent events and return the first actual un-normalized command sequence
    that maps to the given normalized pattern. Used so the LLM gets real commands.
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    events = db.query_events(event_type="shell_cmd", since=since)
    raw_cmds = [e["data"].get("cmd", "") for e in events if e.get("data")]

    n = len(normalized_seq)
    for i in range(len(raw_cmds) - n + 1):
        window = raw_cmds[i : i + n]
        if [normalize_command(c) for c in window] == normalized_seq:
            return window
    return []
