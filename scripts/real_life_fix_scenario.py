#!/usr/bin/env python3
"""
Real-life fix-window scenario (runnable demo).

  1. Creates a temp git repo with a tiny failing \"gate\" script.
  2. Simulates: failure → intermediate successes → edit tracked file → same command succeeds.
  3. Prints fix-status-style verification (records, commands trail, git diff on record).

Usage:
  python scripts/real_life_fix_scenario.py           # isolated DB under tempfile
  python scripts/real_life_fix_scenario.py --real-db # uses ~/.ghost-pulse/ghost-pulse.db

Requires: git on PATH. Does not require pytest.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo root on sys.path for `python scripts/...` from ghost_pulse checkout
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _setup_repo(repo: Path) -> str:
    """Return the repeatable failing command (cwd-relative)."""
    if shutil.which("git") is None:
        sys.stderr.write("git not found on PATH; clone this repo or install git.\n")
        sys.exit(1)

    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init")
    (repo / "README.md").write_text("# scenario\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "init")

    # Tracked gate file — editing this produces git diff vs HEAD
    (repo / "state.txt").write_text("bad\n")
    (repo / "check_gate.py").write_text(
        '''#!/usr/bin/env python3
import sys
from pathlib import Path
p = Path(__file__).resolve().parent / "state.txt"
sys.exit(0 if p.read_text().strip() == "good" else 1)
'''
    )
    _run_git(repo, "add", "state.txt", "check_gate.py")
    _run_git(repo, "commit", "-m", "add gate")

    return "python check_gate.py"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--real-db",
        action="store_true",
        help="Use ~/.ghost-pulse/ghost-pulse.db (otherwise a temp sqlite file)",
    )
    ap.add_argument(
        "--no-purge",
        action="store_true",
        help="Do not call fix-purge first (isolated DB is empty anyway)",
    )
    args = ap.parse_args()

    from ghost_pulse import db
    from ghost_pulse.collectors.shell import log_command
    from ghost_pulse.rag.fix_tracker import get_open_windows

    if args.real_db:
        db.set_db_path(None)  # default ~/.ghost-pulse/ghost-pulse.db
    else:
        db.set_db_path(Path(tempfile.mkdtemp(prefix="ghost_pulse_scenario_")) / "scenario.db")

    db.init_db()
    if not args.no_purge and not args.real_db:
        db.purge_fix_intel()

    with tempfile.TemporaryDirectory(prefix="ghost_pulse_fix_repo_") as tmp:
        repo = Path(tmp)
        fail_cmd = _setup_repo(repo)
        repo_s = str(repo)

        print("─── Step 1: failing command (opens fix window) ───")
        log_command(fail_cmd, repo_s, exit_code=1, duration_ms=40, session_id="scenario-1")
        open_w = get_open_windows()
        print(f"  Open windows: {len(open_w)} (expect 1)")
        if not open_w:
            print("  ABORT: expected an open fix window.")
            sys.exit(2)

        print("─── Step 2: intermediate successes (should NOT close window) ───")
        log_command("echo noop", repo_s, exit_code=0, duration_ms=5, session_id="scenario-1")
        log_command("git status --short", repo_s, exit_code=0, duration_ms=20, session_id="scenario-1")
        print(f"  Open windows: {len(get_open_windows())} (expect 1)")

        print("─── Step 3: Ghost Pulse meta (ignored for close — not appended to trail) ───")
        log_command(
            "python -m ghost_pulse.cli fix-status",
            repo_s,
            exit_code=0,
            duration_ms=50,
            session_id="scenario-1",
        )
        print(f"  Open windows: {len(get_open_windows())} (expect 1; meta exits early in log_command)")

        print("─── Step 4: fix tracked file (developer edit) ───")
        (repo / "state.txt").write_text("good\n")

        print("─── Step 5: retry same command (normalized match → close + save record) ───")
        log_command(fail_cmd, repo_s, exit_code=0, duration_ms=30, session_id="scenario-1")

        print(f"  Open windows: {len(get_open_windows())} (expect 0)")

    records = db.get_fix_records(limit=5)
    print("\n─── Result: fix_records (latest) ───")
    if not records:
        print("  No records — check rag.auto_track_fixes and shell hook path.")
        sys.exit(3)

    r = records[0]
    print(f"  id={r.get('id')}  project={r.get('project')}  source={r.get('source')}")
    print(f"  pattern: {r.get('error_pattern')}")
    print(f"  commands: {r.get('fix_commands')}")
    diff = r.get("fix_diff") or ""
    if diff:
        preview = diff[:500] + ("..." if len(diff) > 500 else "")
        print(f"  fix_diff ({len(diff)} chars):\n{preview}")
    else:
        print("  fix_diff: (none — ensure workdir was set on failure; git repo must exist)")

    from ghost_pulse.config import load_config
    from ghost_pulse.rag.embed_factory import get_embedding_provider
    from ghost_pulse.rag.retriever import FixRetriever

    cfg = load_config()
    rag_cfg = cfg.get("rag", {})
    embed = get_embedding_provider(cfg)
    retriever = FixRetriever(
        embedding_provider=embed,
        fuzzy_threshold=rag_cfg.get("fuzzy_threshold", 0.25),
        semantic_threshold=rag_cfg.get("semantic_threshold", 0.60),
    )
    q = records[0].get("error_pattern") or fail_cmd
    hits = retriever.suggest(q, exit_code=1, project=r.get("project"), top_k=3)
    print("\n─── Retriever smoke (same pattern) ───")
    for i, h in enumerate(hits, 1):
        print(f"  {i}. {h.get('tier')}  score={h.get('score')}  summary={str(h.get('fix_summary'))[:60]}")
        if h.get("fix_diff"):
            print(f"     fix_diff present: {len(h['fix_diff'])} chars")

    db_path = db.get_db_path()
    print(f"\n─── Done. DB: {db_path}")
    if not args.real_db:
        print("  (isolated DB — discarded when temp dir is cleaned except we kept db path)")
        print(f"  To inspect again: copy DB or run with --real-db after understanding risks.")


if __name__ == "__main__":
    main()
