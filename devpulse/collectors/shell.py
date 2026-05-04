"""Shell command collector — hook mode and history-backfill mode."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from devpulse import db


def _infer_project_from_cwd(cwd: str) -> str | None:
    """Walk up from cwd to find a git root; return its directory name."""
    p = Path(cwd).expanduser().resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            return parent.name
    return None


def log_command(
    cmd: str,
    cwd: str,
    exit_code: int,
    duration_ms: int,
    session_id: str | None = None,
) -> None:
    """Persist a shell command event to the database. Must be fast (<50 ms)."""
    project = _infer_project_from_cwd(cwd)
    data: dict[str, Any] = {
        "cmd": cmd,
        "cwd": cwd,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }
    db.insert_event(
        event_type="shell_cmd",
        data=data,
        project=project,
        session_id=session_id,
    )

    # v2: Record failed commands in error memory (non-blocking, best-effort)
    if exit_code != 0:
        try:
            from devpulse.config import load_config
            cfg = load_config()
            if cfg.get("v2", {}).get("auto_record_errors", True):
                from devpulse.analyzers.error_memory import ErrorMemory
                error_id = ErrorMemory().record_error(
                    command=cmd,
                    exit_code=exit_code,
                    project=project or "",
                    session_id=session_id or "",
                )
                # v3: Open a fix window so we can track what the developer does next
                if cfg.get("rag", {}).get("auto_track_fixes", True):
                    from devpulse.rag.fix_tracker import open_fix_window
                    open_fix_window(
                        command=cmd,
                        exit_code=exit_code,
                        project=project or "",
                        error_memory_id=error_id if error_id > 0 else None,
                    )
        except Exception:
            pass  # never slow down or crash log-cmd
    else:
        # v3: A successful command may close an open fix window
        try:
            from devpulse.config import load_config
            cfg = load_config()
            if cfg.get("rag", {}).get("auto_track_fixes", True):
                _maybe_close_fix_windows(cmd, project or "")
        except Exception:
            pass


def _maybe_close_fix_windows(success_cmd: str, project: str) -> None:
    """If there are open fix windows for this project, track this command and
    attempt to close them now that a success has been observed."""
    from devpulse.rag.fix_tracker import get_open_windows, track_command, close_fix_window
    from devpulse.rag.fix_tracker import _WINDOW_EXPIRY_HOURS
    from datetime import datetime, timedelta

    open_wins = get_open_windows()
    if not open_wins:
        return
    for win in open_wins:
        # Only handle windows for the same project (or unset project)
        if win.get("project") and project and win["project"] != project:
            continue
        track_command(win["id"], success_cmd)
        # Heuristic: close the window — the success command is strong signal
        close_fix_window(win["id"], resolution="auto")
        # Persist as a fix record
        _save_fix_record(win, success_cmd)


def _save_fix_record(window: dict, final_cmd: str) -> None:
    """Persist a completed fix window as a fix_record for future RAG retrieval."""
    try:
        from devpulse.analyzers.error_memory import _error_hash
        from devpulse import db
        cmds = window.get("commands_after", [])
        if final_cmd not in cmds:
            cmds = cmds + [final_cmd]
        ehash = window.get("error_hash", "")
        if not ehash:
            return
        # Look up the original error pattern
        em_row = db.get_error_memory_by_hash(ehash)
        pattern = em_row.get("error_pattern", "") if em_row else ""
        fix_summary = em_row.get("fix_description") if em_row else None
        db.upsert_fix_record(
            error_hash=ehash,
            error_pattern=pattern,
            fix_summary=fix_summary,
            fix_commands=cmds,
            project=window.get("project"),
            source="auto",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# History backfill
# ---------------------------------------------------------------------------

_ZSH_HISTORY_RE = re.compile(r"^: (\d+):\d+;(.+)$")


def _parse_zsh_history(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return entries

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _ZSH_HISTORY_RE.match(line)
        if m:
            ts = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%dT%H:%M:%S")
            entries.append({"cmd": m.group(2), "timestamp": ts})
        elif not line.startswith(":"):
            entries.append({"cmd": line, "timestamp": None})
    return entries


def _parse_bash_history(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return entries

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#") and line[1:].isdigit():
            ts_val = int(line[1:])
            ts = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%dT%H:%M:%S")
            if i + 1 < len(lines):
                entries.append({"cmd": lines[i + 1].strip(), "timestamp": ts})
                i += 2
                continue
        elif line:
            entries.append({"cmd": line, "timestamp": None})
        i += 1
    return entries


_CD_ABSOLUTE_RE = re.compile(r"^cd\s+(~?/[^\s;|&]+|~\w*)")
_CD_HOME_RE = re.compile(r"^cd\s*$")  # bare "cd" goes to home


def _infer_project_from_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Assign project names to backfilled entries using forward + backward propagation.

    Forward pass: when we see 'cd /absolute/path' resolve the git root and carry
    that project forward. Relative cd's (cd android, cd ..) stay in the same project
    since they're subdirectories. A bare 'cd' or 'cd ~' resets to None.

    Backward pass: fill any remaining gaps by propagating the next known project
    backward, so commands at the start of history aren't left as unknown.
    """
    # --- Forward pass ---
    current_project: str | None = None

    for entry in entries:
        cmd = entry.get("cmd", "").strip()

        abs_match = _CD_ABSOLUTE_RE.match(cmd)
        if abs_match:
            raw = abs_match.group(1)
            new_dir = str(Path(raw).expanduser())
            proj = _infer_project_from_cwd(new_dir)
            # Only update project if the new dir resolves to a git repo;
            # cd to a non-repo dir (e.g. /tmp) clears the project.
            current_project = proj
        elif _CD_HOME_RE.match(cmd):
            # bare 'cd' returns to home — no longer in a project
            current_project = None
        # else: relative cd (cd android, cd .., cd -) → keep current project

        if not entry.get("project"):
            entry["project"] = current_project

    # --- Backward pass: fill gaps before the first absolute cd anchor ---
    next_project: str | None = None
    for entry in reversed(entries):
        if entry.get("project"):
            next_project = entry["project"]
        elif next_project:
            entry["project"] = next_project

    return entries


def backfill_from_history(shell: str = "auto", limit: int = 5000) -> int:
    """Parse shell history file and insert missing commands. Returns count inserted."""
    if shell == "auto":
        shell_bin = Path(subprocess.getoutput("echo $SHELL"))
        if "zsh" in shell_bin.name:
            shell = "zsh"
        else:
            shell = "bash"

    if shell == "zsh":
        hist_path = Path(subprocess.getoutput("echo ${HISTFILE:-~/.zsh_history}")).expanduser()
        entries = _parse_zsh_history(hist_path)
    else:
        hist_path = Path("~/.bash_history").expanduser()
        entries = _parse_bash_history(hist_path)

    entries = entries[-limit:]
    entries = _infer_project_from_entries(entries)

    inserted = 0
    for entry in entries:
        db.insert_event(
            event_type="shell_cmd",
            data={"cmd": entry["cmd"], "cwd": entry.get("cwd", ""), "exit_code": 0, "duration_ms": 0},
            project=entry.get("project"),
            timestamp=entry.get("timestamp"),
        )
        inserted += 1
    return inserted
