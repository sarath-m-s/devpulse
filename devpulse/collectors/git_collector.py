"""Git activity collector — polls registered project directories."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from devpulse import db


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command in cwd; return stdout or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
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
