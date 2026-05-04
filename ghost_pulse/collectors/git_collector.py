"""Git activity collector — polls registered project directories."""

from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ghost_pulse import db


def _run_git(args: list[str], cwd: Path, timeout: int = 5) -> str:
    """Run a git command in cwd; return stdout or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_head_sha(repo: Path) -> str:
    return _run_git(["rev-parse", "HEAD"], repo)


def _get_branch(repo: Path) -> str:
    return _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)


def _get_commit_info(repo: Path, sha: str) -> dict[str, Any]:
    """Return commit metadata for the given sha."""
    msg = _run_git(["log", "-1", "--format=%s", sha], repo)
    stat = _run_git(["show", "--stat", "--format=", sha], repo)

    files_changed = insertions = deletions = 0
    for line in stat.splitlines():
        line = line.strip()
        if "file" in line and "changed" in line:
            parts = line.split(",")
            for part in parts:
                part = part.strip()
                if "file" in part:
                    try:
                        files_changed = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                elif "insertion" in part:
                    try:
                        insertions = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass
                elif "deletion" in part:
                    try:
                        deletions = int(part.split()[0])
                    except (ValueError, IndexError):
                        pass

    return {
        "sha": sha,
        "message": msg,
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
        "branch": _get_branch(repo),
    }


class GitCollector:
    """Polls configured project directories for git activity."""

    def __init__(self, project_paths: list[str], poll_interval: int = 30) -> None:
        self._paths = [Path(p).expanduser().resolve() for p in project_paths]
        self._poll_interval = poll_interval
        self._state: dict[str, dict[str, str]] = {}  # path -> {sha, branch}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _find_git_repos(self) -> list[Path]:
        repos: list[Path] = []
        for p in self._paths:
            if not p.is_dir():
                continue
            if (p / ".git").exists():
                repos.append(p)
            else:
                for child in p.iterdir():
                    if child.is_dir() and (child / ".git").exists():
                        repos.append(child)
        return repos

    def _poll_once(self) -> None:
        for repo in self._find_git_repos():
            project = repo.name
            sha = _get_head_sha(repo)
            branch = _get_branch(repo)
            if not sha:
                continue

            prev = self._state.get(str(repo))
            if prev is None:
                self._state[str(repo)] = {"sha": sha, "branch": branch}
                continue

            if sha != prev["sha"]:
                info = _get_commit_info(repo, sha)
                db.insert_event(
                    event_type="git_commit",
                    data=info,
                    project=project,
                )
                self._state[str(repo)]["sha"] = sha

            if branch != prev["branch"]:
                db.insert_event(
                    event_type="git_branch_switch",
                    data={"from_branch": prev["branch"], "to_branch": branch},
                    project=project,
                )
                self._state[str(repo)]["branch"] = branch

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                pass
            self._stop_event.wait(self._poll_interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="git-collector")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Git history backfill
# ---------------------------------------------------------------------------

def _find_repos_in_paths(paths: list[str]) -> list[Path]:
    """Discover git repos from a list of directory paths."""
    repos: list[Path] = []
    for p in paths:
        p = Path(p).expanduser().resolve()
        if not p.is_dir():
            continue
        if (p / ".git").exists():
            repos.append(p)
        else:
            try:
                for child in p.iterdir():
                    if child.is_dir() and (child / ".git").exists():
                        repos.append(child)
            except PermissionError:
                continue
    return repos


def backfill_git_commits(
    project_paths: list[str],
    since: str | None = None,
    limit: int = 500,
) -> int:
    """Import historical git commits from repos as git_commit events.

    Reads `git log` for each repo and inserts events that don't already
    exist in the database (matched by SHA).  Returns total commits inserted.
    """
    repos = _find_repos_in_paths(project_paths)
    if not repos:
        return 0

    existing_shas: set[str] = set()
    for ev in db.query_events(event_type="git_commit"):
        data = ev.get("data", {})
        if isinstance(data, dict) and data.get("sha"):
            existing_shas.add(data["sha"])

    since_arg = []
    if since:
        since_arg = [f"--since={since}"]

    inserted = 0
    for repo in repos:
        project = repo.name
        log_output = _run_git(
            ["log", "--format=%H|%aI|%s", *since_arg, f"-{limit}"],
            repo,
            timeout=15,
        )
        if not log_output:
            continue

        for line in log_output.splitlines():
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            sha, iso_ts, message = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if sha in existing_shas:
                continue

            try:
                ts = datetime.fromisoformat(iso_ts).strftime("%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError):
                continue

            stat = _run_git(["show", "--stat", "--format=", sha], repo, timeout=10)
            files_changed = insertions = deletions = 0
            for sline in stat.splitlines():
                sline = sline.strip()
                if "file" in sline and "changed" in sline:
                    for part in sline.split(","):
                        part = part.strip()
                        if "file" in part:
                            try:
                                files_changed = int(part.split()[0])
                            except (ValueError, IndexError):
                                pass
                        elif "insertion" in part:
                            try:
                                insertions = int(part.split()[0])
                            except (ValueError, IndexError):
                                pass
                        elif "deletion" in part:
                            try:
                                deletions = int(part.split()[0])
                            except (ValueError, IndexError):
                                pass

            branch = _run_git(
                ["branch", "--contains", sha, "--format=%(refname:short)"],
                repo,
                timeout=5,
            )
            branch = branch.splitlines()[0].strip() if branch else ""

            db.insert_event(
                event_type="git_commit",
                data={
                    "sha": sha,
                    "message": message,
                    "files_changed": files_changed,
                    "insertions": insertions,
                    "deletions": deletions,
                    "branch": branch,
                    "backfilled": True,
                },
                project=project,
                timestamp=ts,
            )
            existing_shas.add(sha)
            inserted += 1

    return inserted
