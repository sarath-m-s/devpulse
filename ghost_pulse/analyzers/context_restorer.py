"""Context restorer — captures session snapshots and generates resume summaries."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from devpulse import db


def _run_git(cwd: str, *args: str) -> str:
    """Run a git command in cwd, returning stdout. Returns '' on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _find_project_path(project: str) -> str | None:
    """Try to locate the project directory by scanning tracked projects and common dirs."""
    from devpulse.config import load_config
    cfg = load_config()
    for p in cfg.get("projects", {}).get("paths", []):
        p_path = Path(p)
        # Direct match
        if p_path.name == project or str(p_path.resolve()) == str(Path(project).resolve()):
            return p
        # One level deep — for folders containing many repos
        if p_path.is_dir() and not (p_path / ".git").exists():
            candidate = p_path / project
            if candidate.is_dir() and (candidate / ".git").exists():
                return str(candidate)
    return None


def _fmt_time_away(snapshot_time: str) -> str:
    try:
        then = datetime.strptime(snapshot_time[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return "unknown time"
    delta = datetime.now() - then
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


def _fmt_duration(minutes: int | None) -> str:
    if not minutes:
        return "unknown"
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


class ContextRestorer:
    def __init__(self, llm_provider: Any = None) -> None:
        self.llm = llm_provider

    def capture_snapshot(self, project: str, session_id: str) -> int:
        """Capture current state of a project session. Returns snapshot id."""
        project_path = _find_project_path(project)

        branch: str | None = None
        unstaged_files: list[str] = []
        last_file_edited: str | None = None

        if project_path:
            branch = _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD") or None
            status_out = _run_git(project_path, "status", "--porcelain")
            if status_out:
                unstaged_files = [
                    line.strip() for line in status_out.splitlines() if line.strip()
                ]

        # Get last command and last error for this session from events
        since = (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
        events = db.query_events(
            event_type="shell_cmd", project=project, since=since
        )

        last_command: str | None = None
        last_error: str | None = None
        duration_minutes: int | None = None

        if events:
            last_ev = events[-1]
            last_command = last_ev.get("data", {}).get("cmd")
            if last_ev.get("data", {}).get("exit_code", 0) != 0:
                last_error = last_command

            # Compute session duration
            try:
                first_ts = datetime.strptime(events[0]["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
                last_ts = datetime.strptime(last_ev["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
                duration_minutes = max(1, int((last_ts - first_ts).total_seconds() / 60))
            except (ValueError, KeyError):
                pass

            # Try to identify last file edited from commands
            for ev in reversed(events):
                cmd = ev.get("data", {}).get("cmd", "")
                for editor in ("vim", "nvim", "nano", "code", "subl", "emacs"):
                    if cmd.startswith(editor + " "):
                        parts = cmd.split(None, 1)
                        if len(parts) > 1:
                            last_file_edited = parts[1].strip()
                            break
                if last_file_edited:
                    break

        # LLM-generated notes
        notes: str | None = None
        if self.llm:
            notes = self._generate_notes(
                project, branch, last_command, last_error, unstaged_files
            )

        return db.insert_session_snapshot(
            project=project,
            session_id=session_id,
            branch=branch,
            last_file_edited=last_file_edited,
            last_command=last_command,
            last_error=last_error,
            unstaged_files=unstaged_files,
            duration_minutes=duration_minutes,
            notes=notes,
        )

    def _generate_notes(
        self,
        project: str,
        branch: str | None,
        last_command: str | None,
        last_error: str | None,
        unstaged_files: list[str],
    ) -> str | None:
        if not self.llm:
            return None
        prompt = (
            f"Developer session summary for project '{project}'.\n"
            f"Branch: {branch or 'unknown'}\n"
            f"Last command: {last_command or 'unknown'}\n"
            f"Last error: {last_error or 'none'}\n"
            f"Uncommitted files: {', '.join(unstaged_files[:5]) or 'none'}\n\n"
            "Write a 2-3 sentence summary of what the developer was working on. "
            "Be specific and actionable. No preamble."
        )
        try:
            from devpulse.llm.base import DEVPULSE_SYSTEM_PROMPT
            resp = self.llm.analyze(prompt, system_prompt=DEVPULSE_SYSTEM_PROMPT)
            return resp.content.strip()
        except Exception:
            return None

    def resume(self, project: str) -> dict[str, Any]:
        """Get resume context for a project."""
        snapshot = db.get_latest_snapshot(project)
        if not snapshot:
            return {"error": f"No session data found for '{project}'"}

        time_away = _fmt_time_away(snapshot.get("snapshot_time", ""))
        duration = _fmt_duration(snapshot.get("duration_minutes"))

        summary: str | None = snapshot.get("notes")
        if not summary and self.llm:
            summary = self._generate_notes(
                project,
                snapshot.get("branch"),
                snapshot.get("last_command"),
                snapshot.get("last_error"),
                snapshot.get("unstaged_files", []),
            )

        return {
            "project": project,
            "summary": summary,
            "branch": snapshot.get("branch"),
            "last_file": snapshot.get("last_file_edited"),
            "last_command": snapshot.get("last_command"),
            "last_error": snapshot.get("last_error"),
            "unstaged_files": snapshot.get("unstaged_files", []),
            "time_away": time_away,
            "session_duration": duration,
            "snapshot_time": snapshot.get("snapshot_time"),
        }

    def get_session_history(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent session summaries for a project."""
        rows = db.get_session_history(project, limit=limit)
        result = []
        for r in rows:
            result.append(
                {
                    "project": r["project"],
                    "snapshot_time": r["snapshot_time"],
                    "branch": r.get("branch"),
                    "last_command": r.get("last_command"),
                    "duration": _fmt_duration(r.get("duration_minutes")),
                    "unstaged_count": len(r.get("unstaged_files", [])),
                    "notes": r.get("notes"),
                }
            )
        return result

    def capture_on_gap(self, gap_minutes: int = 30) -> list[str]:
        """
        Detect projects with sessions that ended > gap_minutes ago and capture snapshots.
        Returns list of projects snapshotted. Called by daemon periodically.
        """
        cutoff = (datetime.now() - timedelta(minutes=gap_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
        snapshotted: list[str] = []

        # Find projects with recent activity that has a gap now
        events = db.query_events(
            event_type="shell_cmd",
            since=(datetime.now() - timedelta(hours=gap_minutes / 60 + 4)).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
        )
        if not events:
            return []

        # Find projects whose last event is older than gap_minutes
        last_by_project: dict[str, dict[str, Any]] = {}
        for ev in events:
            proj = ev.get("project") or "unknown"
            if proj == "unknown":
                continue
            last_by_project[proj] = ev

        for project, last_ev in last_by_project.items():
            last_ts_str = last_ev.get("timestamp", "")
            if last_ts_str < cutoff:
                # Check if we already snapshotted this recently
                existing = db.get_latest_snapshot(project)
                if existing and existing.get("snapshot_time", "") >= last_ts_str:
                    continue
                try:
                    session_id = last_ev.get("session_id") or "unknown"
                    self.capture_snapshot(project, session_id)
                    snapshotted.append(project)
                except Exception:
                    pass

        return snapshotted
