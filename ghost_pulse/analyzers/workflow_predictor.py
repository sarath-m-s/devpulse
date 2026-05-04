"""Workflow sequence predictor — learns per-project command routines."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from devpulse import db
from devpulse.analyzers.toil import normalize_command


def _seq_hash(project: str, sequence: list[str]) -> str:
    key = project + ":" + json.dumps(sequence)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _group_by_session(events: list[dict[str, Any]], gap_minutes: int = 30) -> list[list[dict[str, Any]]]:
    """Split events into sessions separated by gaps >= gap_minutes."""
    if not events:
        return []

    sessions: list[list[dict[str, Any]]] = [[events[0]]]
    gap = timedelta(minutes=gap_minutes)
    for ev in events[1:]:
        try:
            prev_ts = datetime.strptime(sessions[-1][-1]["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
            cur_ts = datetime.strptime(ev["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, KeyError):
            sessions[-1].append(ev)
            continue
        if cur_ts - prev_ts >= gap:
            sessions.append([ev])
        else:
            sessions[-1].append(ev)
    return sessions


class WorkflowPredictor:
    def __init__(self) -> None:
        pass

    def learn_sequences(self, days: int = 30) -> int:
        """Analyze recent history and store learned sequences. Returns count upserted."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        events = db.query_events(event_type="shell_cmd", since=since)

        if not events:
            return 0

        # Group events by project then by session
        by_project: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            proj = ev.get("project") or "unknown"
            if proj not in by_project:
                by_project[proj] = []
            by_project[proj].append(ev)

        upserted = 0
        for project, proj_events in by_project.items():
            if project == "unknown":
                continue

            sessions = _group_by_session(proj_events)
            counter: Counter[tuple[str, ...]] = Counter()

            for session in sessions:
                raw_cmds = [e["data"].get("cmd", "") for e in session if e.get("data")]
                normalized = [normalize_command(c) for c in raw_cmds if c.strip()]

                # Sliding windows of 3–6 commands (routines are longer than toil pairs)
                for size in range(3, 7):
                    for i in range(len(normalized) - size + 1):
                        seq = tuple(normalized[i : i + size])
                        counter[seq] += 1

            # Store sequences seen at least twice
            ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            for seq_tuple, freq in counter.items():
                if freq < 2:
                    continue
                seq_list = list(seq_tuple)
                h = _seq_hash(project, seq_list)
                db.upsert_workflow_sequence(project, h, seq_list, frequency=freq, now=ts)
                upserted += 1

        return upserted

    def predict_next(
        self,
        project: str,
        recent_commands: list[str],
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Predict next commands based on recent activity.

        Returns: [{"commands": [...], "confidence": 0.8, "sequence_id": 1, "frequency": 12}]
        """
        if not recent_commands:
            return []

        sequences = db.get_workflow_sequences(
            project=project,
            min_confidence=0.3,
            active_only=True,
        )
        if not sequences:
            return []

        normalized_recent = [normalize_command(c) for c in recent_commands]
        n = len(normalized_recent)
        predictions: list[dict[str, Any]] = []

        for seq in sequences:
            seq_cmds: list[str] = seq.get("sequence", [])
            if len(seq_cmds) <= n:
                continue
            # Check if the start of the stored sequence matches recent normalized commands
            if seq_cmds[:n] == normalized_recent:
                predictions.append(
                    {
                        "sequence_id": seq["id"],
                        "commands": seq_cmds[n:],
                        "full_sequence": seq_cmds,
                        "confidence": seq["confidence"],
                        "frequency": seq["frequency"],
                    }
                )

        predictions.sort(key=lambda x: x["confidence"], reverse=True)
        return predictions[:top_k]

    def get_project_routines(self, project: str) -> list[dict[str, Any]]:
        """Get all learned routines for a project, sorted by frequency."""
        return db.get_workflow_sequences(project=project, active_only=True)

    def dismiss_sequence(self, sequence_id: int) -> None:
        """Mark a sequence as dismissed so it's no longer suggested."""
        db.dismiss_workflow_sequence(sequence_id)
