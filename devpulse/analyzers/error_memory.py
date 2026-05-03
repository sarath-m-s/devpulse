"""Error memory — records failed commands and their fixes."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any

from devpulse import db
from devpulse.analyzers.toil import normalize_command

# Error types mapped from common patterns in command strings
_ERROR_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bpytest\b|\bpython\b.*test"), "test"),
    (re.compile(r"\bdocker\b|\bdocker-compose\b"), "build"),
    (re.compile(r"\bgradle\b|gradlew\b|\bnpm\b|\byarn\b|\bpip\b|\bmake\b"), "build"),
    (re.compile(r"\bgit\b"), "deploy"),
    (re.compile(r"\bterraform\b|\baws\b|\bkubectl\b"), "deploy"),
    (re.compile(r"\bconfig\b|\benv\b"), "config"),
]


def _classify_error_type(command: str) -> str:
    for pattern, err_type in _ERROR_TYPE_PATTERNS:
        if pattern.search(command):
            return err_type
    return "runtime"


def _error_hash(command: str, exit_code: int) -> str:
    normalized = normalize_command(command)
    key = f"{normalized}:{exit_code}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class ErrorMemory:
    def __init__(self, llm_provider: Any = None) -> None:
        self.llm = llm_provider

    def record_error(
        self,
        command: str,
        exit_code: int,
        output: str = "",
        project: str = "",
        session_id: str = "",
    ) -> int:
        """Record a new error occurrence. Returns error_memory id."""
        if exit_code == 0:
            return -1
        error_hash = _error_hash(command, exit_code)
        error_type = _classify_error_type(command)
        error_id = db.upsert_error_memory(
            error_hash=error_hash,
            error_pattern=normalize_command(command),
            project=project or None,
            error_type=error_type,
        )
        return error_id

    def record_fix(
        self,
        error_id: int,
        fix_commands: list[str],
        fix_diff: str | None = None,
        fix_duration_ms: int | None = None,
    ) -> None:
        """Record the commands that fixed an error."""
        fix_description: str | None = None
        if self.llm and fix_commands:
            try:
                error_row = self._get_error_by_id(error_id)
                if error_row:
                    fix_description = self.generate_fix_description(
                        error_row.get("error_pattern", ""),
                        fix_commands,
                        fix_diff,
                    )
            except Exception:
                pass

        db.update_error_fix(
            error_id=error_id,
            fix_commands=fix_commands,
            fix_description=fix_description,
            fix_diff=fix_diff,
            fix_duration_ms=fix_duration_ms,
        )

    def _get_error_by_id(self, error_id: int) -> dict[str, Any] | None:
        errors = db.get_frequent_errors(days=365, limit=1000)
        for e in errors:
            if e.get("id") == error_id:
                return e
        return None

    def check_known_error(
        self,
        command: str,
        exit_code: int = 1,
        project: str | None = None,
    ) -> dict[str, Any] | None:
        """Check if this error has been seen before.

        Returns fix info or None.
        """
        error_hash = _error_hash(command, exit_code)
        row = db.get_error_memory_by_hash(error_hash)
        if not row:
            return None
        return {
            "error_id": row["id"],
            "occurrences": row["occurrences"],
            "fix_description": row.get("fix_description"),
            "fix_commands": row.get("fix_commands", []),
            "last_fix_duration_ms": row.get("last_fix_duration_ms"),
            "resolved": bool(row.get("resolved")),
            "project": row.get("project"),
        }

    def get_frequent_errors(
        self,
        project: str | None = None,
        days: int = 30,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get most frequent errors, optionally filtered by project."""
        return db.get_frequent_errors(project=project, days=days, limit=limit)

    def generate_fix_description(
        self,
        error_pattern: str,
        fix_commands: list[str],
        fix_diff: str | None = None,
    ) -> str:
        """Use LLM to generate a human-readable description of the fix."""
        if not self.llm:
            cmds_str = " → ".join(fix_commands[:5])
            return f"Fixed by running: {cmds_str}"

        diff_section = f"\nDiff:\n{fix_diff[:800]}" if fix_diff else ""
        prompt = (
            f"A developer's command failed (normalized: '{error_pattern}').\n"
            f"They fixed it by running: {' → '.join(fix_commands[:5])}"
            f"{diff_section}\n\n"
            "Write ONE short sentence (max 20 words) describing what the fix did. "
            "No preamble, just the sentence."
        )
        try:
            from devpulse.llm.base import DEVPULSE_SYSTEM_PROMPT
            resp = self.llm.analyze(prompt, system_prompt=DEVPULSE_SYSTEM_PROMPT)
            return resp.content.strip()
        except Exception:
            return f"Fixed by running: {' → '.join(fix_commands[:3])}"

    def detect_fixes_from_history(self, session_gap_minutes: int = 10) -> int:
        """
        Scan recent events and link successful commands to prior errors.
        Called periodically by the daemon. Returns count of fixes recorded.
        """
        since = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        events = db.query_events(event_type="shell_cmd", since=since)
        if not events:
            return 0

        gap = timedelta(minutes=session_gap_minutes)
        recorded = 0
        i = 0
        while i < len(events):
            ev = events[i]
            exit_code = ev.get("data", {}).get("exit_code", 0)
            if exit_code == 0:
                i += 1
                continue

            cmd = ev.get("data", {}).get("cmd", "")
            project = ev.get("project") or ""
            error_hash = _error_hash(cmd, exit_code)
            error_row = db.get_error_memory_by_hash(error_hash)
            if not error_row or error_row.get("resolved"):
                i += 1
                continue

            # Collect successful commands that follow within gap window
            try:
                err_ts = datetime.strptime(ev["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                i += 1
                continue

            fix_cmds: list[str] = []
            j = i + 1
            while j < len(events):
                nxt = events[j]
                try:
                    nxt_ts = datetime.strptime(nxt["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    j += 1
                    continue
                if nxt_ts - err_ts > gap:
                    break
                nxt_exit = nxt.get("data", {}).get("exit_code", 0)
                nxt_cmd = nxt.get("data", {}).get("cmd", "")
                if nxt_cmd:
                    fix_cmds.append(nxt_cmd)
                if nxt_exit == 0 and fix_cmds:
                    # Enough evidence — record the fix
                    db.update_error_fix(
                        error_id=error_row["id"],
                        fix_commands=fix_cmds,
                    )
                    recorded += 1
                    break
                j += 1
            i += 1

        return recorded
