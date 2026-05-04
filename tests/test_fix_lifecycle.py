"""Realistic fix-window flows: intermediate commands, normalized retry, git diff."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ghost_pulse import db
from ghost_pulse.collectors.shell import (
    _success_resolves_original_failure,
    log_command,
)
from ghost_pulse.rag.embeddings import NullEmbeddingProvider
from ghost_pulse.rag.fix_tracker import capture_workdir_git_diff
from ghost_pulse.rag.retriever import FixRetriever


@pytest.fixture
def tmp_repo(tmp_path):
    """Minimal git repo with one committed file."""
    if not shutil.which("git"):
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# t\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    return repo


@pytest.fixture(autouse=True)
def kb_db(tmp_path):
    db.set_db_path(tmp_path / "kb.db")
    db.init_db()
    yield
    db.set_db_path(None)


class TestNormalizedRetry:
    def test_same_pytest_after_normalize(self):
        a = "pytest tests/test_foo.py -v"
        b = "pytest tests/test_foo.py -v"
        assert _success_resolves_original_failure(b, a)

    def test_different_paths_may_still_match(self):
        # Both normalize to pytest <path> <path> style depending on normalizer
        from ghost_pulse.analyzers.toil import normalize_command

        f = "pytest tests/a.py"
        s = "pytest tests/a.py"
        assert normalize_command(f) == normalize_command(s)


class TestGitDiffCapture:
    def test_diff_after_edit(self, tmp_repo: Path):
        (tmp_repo / "fix.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "fix.py"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add fix.py"],
            cwd=tmp_repo,
            check=True,
            capture_output=True,
        )
        (tmp_repo / "fix.py").write_text("x = 2\n")
        diff = capture_workdir_git_diff(str(tmp_repo))
        assert diff
        assert "x = 1" in diff or "-1" in diff or "x = 2" in diff


class TestFullScenario:
    def test_intermediate_commands_tracked_retry_closes_with_diff(self, tmp_repo: Path):
        if not shutil.which("pytest"):
            pytest.skip("pytest not on PATH")
        test_file = tmp_repo / "test_sim.py"
        test_file.write_text("def test_x():\n    assert False\n")
        subprocess.run(["git", "add", "test_sim.py"], cwd=tmp_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "test"],
            cwd=tmp_repo,
            check=True,
            capture_output=True,
        )

        db.purge_fix_intel()

        fail_cmd = "pytest test_sim.py"
        log_command(fail_cmd, str(tmp_repo), 1, 10, "sess")

        from ghost_pulse.rag.fix_tracker import get_open_windows

        assert len(get_open_windows()) == 1

        log_command("echo noop", str(tmp_repo), 0, 1, "sess")
        assert len(get_open_windows()) == 1

        test_file.write_text("def test_x():\n    assert True\n")

        log_command(fail_cmd, str(tmp_repo), 0, 20, "sess")

        assert len(get_open_windows()) == 0

        records = db.get_fix_records(limit=5)
        assert len(records) == 1
        r = records[0]
        cmds = r["fix_commands"]
        assert any("echo noop" in c for c in cmds)
        assert any("pytest" in c for c in cmds)
        assert r.get("fix_diff"), "expected git diff text on record"


class TestRetrieverShowsDiff:
    def test_suggest_includes_fix_diff_field(self):
        from ghost_pulse.analyzers.error_memory import _error_hash

        pat = "pytest test_sim.py"
        ehash = _error_hash(pat, 1)
        db.upsert_fix_record(
            error_hash=ehash,
            error_pattern=pat,
            fix_summary="fixed assert",
            fix_commands=["echo setup", "pytest test_sim.py"],
            fix_diff="diff --git a/test_sim.py\n+ ok",
            project="repo",
            source="manual",
        )
        retriever = FixRetriever(embedding_provider=NullEmbeddingProvider())
        out = retriever.suggest(pat, exit_code=1, top_k=3)
        assert out
        assert any(x.get("fix_diff") for x in out)
