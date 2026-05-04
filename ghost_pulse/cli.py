"""Ghost Pulse CLI — main entry point using Typer."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from ghost_pulse import db
from ghost_pulse.config import (
    auto_detect_projects,
    ensure_default_config,
    get_config_value,
    load_config,
    save_config,
    set_config_value,
    GHOST_PULSE_DIR,
)

app = typer.Typer(
    name="ghost",
    help="Privacy-first developer productivity copilot.",
    add_completion=True,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _print_optional_tool_hints() -> None:
    """Warn when common external tools are missing (non-fatal)."""
    if not shutil.which("git"):
        console.print(
            "[yellow]⚠[/yellow]  [bold]git[/bold] not found on PATH — "
            "repo discovery, the git collector, and git backfill need Git installed."
        )
    if sys.platform.startswith("linux") and not shutil.which("xdotool"):
        console.print(
            "[dim]Linux: install [bold]xdotool[/bold] if you enable the window tracker "
            "(collectors.window_tracker).[/dim]"
        )


def _get_cfg() -> dict:
    return load_config()


# ---------------------------------------------------------------------------
# ghost init
# ---------------------------------------------------------------------------

@app.command()
def init(
    path: Optional[list[str]] = typer.Option(
        None, "--path", "-p",
        help="Root directory containing your git repos (can be repeated)",
    ),
    skip_ollama: bool = typer.Option(
        False,
        "--skip-ollama",
        help="Do not install Ollama or pull models (also: GHOST_PULSE_SKIP_OLLAMA=1)",
    ),
) -> None:
    """Initialize Ghost Pulse: config, DB, optional tool checks, Ollama bootstrap, hook instructions."""
    GHOST_PULSE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = ensure_default_config()

    existing = set(cfg["projects"].get("paths", []))

    # Use explicitly provided paths first, then fall back to auto-detect
    if path:
        for p in path:
            resolved = str(Path(p).expanduser().resolve())
            if resolved not in existing:
                existing.add(resolved)
        cfg["projects"]["paths"] = sorted(existing)
        save_config(cfg)
        console.print(f"[green]✓[/green] Added {len(path)} project path(s)")
    else:
        found = auto_detect_projects()
        new_paths = [p for p in found if p not in existing]
        if new_paths:
            cfg["projects"]["paths"] = list(existing) + new_paths
            save_config(cfg)
            console.print(f"[green]✓[/green] Detected {len(new_paths)} git repos")
        elif not existing:
            console.print(
                "[yellow]⚠[/yellow]  No git repos found in common directories"
            )
            console.print(
                "  Tell Ghost Pulse where your projects live:\n"
                "    [bold]ghost init --path ~/your-projects[/bold]\n"
                "  or add them individually:\n"
                "    [bold]ghost projects add ~/your-projects[/bold]"
            )

    db.init_db()
    console.print("[green]✓[/green] Database initialized")
    console.print(f"[green]✓[/green] Config at {GHOST_PULSE_DIR / 'config.toml'}")

    _print_optional_tool_hints()

    from ghost_pulse.bootstrap import run_ollama_bootstrap

    run_ollama_bootstrap(console, cfg, skip=skip_ollama)
    cfg = load_config()

    # Show detected projects
    final_paths = cfg["projects"].get("paths", [])
    if final_paths:
        console.print(f"[green]✓[/green] Tracking {len(final_paths)} project path(s):")
        for p in final_paths:
            console.print(f"    [dim]{p}[/dim]")

    # LLM probe
    from ghost_pulse.llm.factory import get_provider
    provider = get_provider(cfg)
    if provider.name != "none":
        console.print(f"[green]✓[/green] LLM provider: [bold]{provider.name}[/bold]")
    else:
        console.print("[yellow]⚠[/yellow]  No LLM provider found — AI features disabled")
        console.print("    Run: ghost config providers  to see options")

    console.print()
    console.print(Panel(
        "[bold]Shell hook installation[/bold]\n\n"
        "Add to [cyan]~/.zshrc[/cyan]:\n"
        '  [dim]source "$(ghost shell-hook --zsh)"[/dim]\n\n'
        "Add to [cyan]~/.bashrc[/cyan]:\n"
        '  [dim]source "$(ghost shell-hook --bash)"[/dim]\n\n'
        "Then restart your shell and run: [bold]ghost start[/bold]",
        title="Next steps",
        box=box.ROUNDED,
    ))


# ---------------------------------------------------------------------------
# ghost start / stop / status
# ---------------------------------------------------------------------------

@app.command()
def start() -> None:
    """Start the background collector daemon."""
    from ghost_pulse.daemon import start_daemon
    start_daemon()


@app.command()
def stop() -> None:
    """Stop the background daemon."""
    from ghost_pulse.daemon import stop_daemon
    stop_daemon()


@app.command()
def status() -> None:
    """Show daemon status and today's activity summary."""
    from ghost_pulse.daemon import is_running, _read_pid

    running = is_running()
    pid = _read_pid()

    status_text = (
        f"[green]● Running[/green] (pid {pid})" if running else "[red]○ Stopped[/red]"
    )

    total_cmds = db.count_events_today()
    today_str = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

    from ghost_pulse.analyzers.time_tracker import compute_time_per_project
    time_data = compute_time_per_project(since=today_str)
    active_project = "—"
    known = {p: v for p, v in time_data.items() if p != "unknown"}
    if known:
        active_project = max(known, key=lambda p: known[p]["total_minutes"])
    elif time_data:
        active_project = max(time_data, key=lambda p: time_data[p]["total_minutes"])

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_row("Daemon", status_text)
    table.add_row("Commands today", str(total_cmds))
    table.add_row("Active project", active_project)
    table.add_row("DB path", str(db.get_db_path()))

    console.print(Panel(table, title="[bold]Ghost Pulse Status[/bold]", box=box.ROUNDED))


# ---------------------------------------------------------------------------
# ghost log-cmd  (hidden — called by shell hooks)
# ---------------------------------------------------------------------------

@app.command(name="log-cmd", hidden=True)
def log_cmd(
    cmd: str = typer.Option(..., "--cmd"),
    cwd: str = typer.Option("", "--cwd"),
    exit_code: int = typer.Option(0, "--exit-code"),
    duration_ms: int = typer.Option(0, "--duration-ms"),
    session: str = typer.Option("", "--session"),
) -> None:
    """Record a shell command (called by shell hooks)."""
    db.init_db()
    from ghost_pulse.collectors.shell import log_command
    log_command(
        cmd=cmd,
        cwd=cwd,
        exit_code=exit_code,
        duration_ms=duration_ms,
        session_id=session or None,
    )


# ---------------------------------------------------------------------------
# ghost shell-hook
# ---------------------------------------------------------------------------

@app.command(name="shell-hook")
def shell_hook(
    zsh: bool = typer.Option(False, "--zsh"),
    bash: bool = typer.Option(False, "--bash"),
) -> None:
    """Print path to shell hook file for sourcing."""
    hooks_dir = GHOST_PULSE_DIR / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write hook files if they don't exist or are stale
    _write_hooks(hooks_dir)

    if zsh:
        print(hooks_dir / "ghost.zsh")
    elif bash:
        print(hooks_dir / "ghost.bash")
    else:
        print(hooks_dir / "ghost.zsh")


def _write_hooks(hooks_dir: Path) -> None:
    """Write shell hook files to the Ghost Pulse hooks directory."""
    zsh_hook = hooks_dir / "ghost.zsh"
    bash_hook = hooks_dir / "ghost.bash"

    shell_dir = Path(__file__).parent.parent / "shell"

    for src, dst in [(shell_dir / "ghost.zsh", zsh_hook), (shell_dir / "ghost.bash", bash_hook)]:
        if src.exists():
            dst.write_text(src.read_text())


# ---------------------------------------------------------------------------
# ghost today
# ---------------------------------------------------------------------------

@app.command()
def today() -> None:
    """Show today's activity dashboard."""
    db.init_db()
    from ghost_pulse.ui.dashboard import render_today
    render_today()


# ---------------------------------------------------------------------------
# ghost week
# ---------------------------------------------------------------------------

@app.command()
def week() -> None:
    """Show weekly summary."""
    db.init_db()
    from ghost_pulse.ui.dashboard import render_week
    render_week()


# ---------------------------------------------------------------------------
# ghost toil
# ---------------------------------------------------------------------------

@app.command()
def toil(
    days: int = typer.Option(14, "--days", help="Days to analyse"),
    suggest: bool = typer.Option(False, "--suggest", help="Generate automation for top pattern"),
) -> None:
    """List detected toil patterns ranked by impact."""
    db.init_db()
    from ghost_pulse.analyzers.toil import detect_toil, get_ranked_patterns, estimate_time_wasted

    cfg = _get_cfg()
    threshold = cfg.get("general", {}).get("toil_threshold", 5)
    detect_toil(days=days, threshold=threshold)
    patterns = get_ranked_patterns()

    if not patterns:
        console.print("[dim]No toil patterns detected yet.[/dim]")
        return

    table = Table("ID", "Commands", "Count", "Est. wasted", box=box.ROUNDED, show_header=True)
    table.columns[0].style = "dim"
    table.columns[2].style = "yellow bold"
    table.columns[3].style = "red"
    for p in patterns:
        cmds = " → ".join(p.get("commands", []))[:60]
        wasted = estimate_time_wasted(p)
        table.add_row(str(p.get("id", "?")), cmds, f"×{p['count']}", f"~{wasted:.1f}h")

    console.print(table)
    console.print(f"\n[dim]Run [bold]ghost suggest <id>[/bold] to generate automation[/dim]")

    if suggest and patterns:
        top = patterns[0]
        _do_suggest(top, cfg)


# ---------------------------------------------------------------------------
# ghost suggest
# ---------------------------------------------------------------------------

@app.command()
def suggest(
    pattern_id: Optional[int] = typer.Argument(None, help="Toil pattern ID"),
) -> None:
    """Generate an automation script for a toil pattern using LLM."""
    db.init_db()
    cfg = _get_cfg()

    from ghost_pulse.analyzers.toil import get_ranked_patterns
    from ghost_pulse.llm.factory import get_provider

    patterns = get_ranked_patterns()
    if not patterns:
        console.print("[yellow]No toil patterns detected yet. Run: ghost toil[/yellow]")
        raise typer.Exit(1)

    if pattern_id is not None:
        matches = [p for p in patterns if p.get("id") == pattern_id]
        pattern = matches[0] if matches else patterns[0]
    else:
        pattern = patterns[0]

    _do_suggest(pattern, cfg)


_DEST_ALIASES = {"yes": "scripts", "y": "scripts", "": "scripts", "s": "scripts"}


def _do_suggest(pattern: dict, cfg: dict) -> None:
    from rich.markdown import Markdown
    from rich.syntax import Syntax
    from ghost_pulse.llm.factory import get_provider
    from ghost_pulse.generators.script_gen import generate_script, save_script

    provider = get_provider(cfg)
    if provider.name == "none":
        console.print("[yellow]No LLM provider configured. Run: ghost config providers[/yellow]")
        return

    cmds = " → ".join(pattern.get("commands", []))
    console.print(
        Panel(
            f"[bold white]{cmds[:70]}[/bold white]\n[dim]Detected [yellow]{pattern['count']}×[/yellow] — est. [red]{pattern['count'] * 2 / 60:.1f}h wasted[/red][/dim]",
            title="[bold yellow]🔄 Toil pattern[/bold yellow]",
            border_style="yellow",
            box=box.ROUNDED,
        )
    )

    with console.status("[bold cyan]Asking Mistral to generate automation…[/bold cyan]"):
        script = generate_script(pattern, provider)

    # Strip markdown fences if LLM wrapped in ```bash blocks — extract just the code
    code = _extract_code(script)

    console.print(
        Panel(
            Syntax(code, "bash", theme="monokai", line_numbers=False),
            title="[bold green]✨ Generated automation[/bold green]",
            border_style="green",
            box=box.ROUNDED,
        )
    )

    # Let user confirm or rename the alias/function before saving
    detected_name = _extract_name(code)
    if detected_name:
        confirmed_name = typer.prompt(
            "Alias name",
            default=detected_name,
            prompt_suffix=" (edit to rename, Enter to keep) > ",
        )
        # Strip escape sequences and whitespace; validate as a shell identifier
        import re as _re
        clean_name = _re.sub(r"[^\w\-]", "", confirmed_name.strip())
        if clean_name and clean_name != detected_name:
            code = code.replace(detected_name, clean_name, 1)
            console.print(
                Panel(
                    Syntax(code, "bash", theme="monokai", line_numbers=False),
                    title="[bold green]✨ Renamed[/bold green]",
                    border_style="green",
                    box=box.ROUNDED,
                )
            )
        elif not clean_name or clean_name != confirmed_name.strip():
            # Invalid input — silently keep original name
            console.print(f"[dim]Keeping name: [bold]{detected_name}[/bold][/dim]")

    dest_raw = typer.prompt(
        "\nSave to",
        default="scripts",
        prompt_suffix=" [zshrc / aliases / scripts / skip] > ",
    )
    dest = _DEST_ALIASES.get(dest_raw.lower().strip(), dest_raw.lower().strip())
    if dest == "skip":
        return

    saved_path = save_script(code, destination=dest)
    db.update_toil_automation(pattern["id"], code)
    _print_script_usage(code, saved_path, dest)

    # Offer to auto-add to zshrc
    if dest == "scripts":
        auto_add = typer.confirm(
            f"\nAdd 'source {saved_path}' to ~/.zshrc permanently?", default=True
        )
        if auto_add:
            zshrc = Path.home() / ".zshrc"
            with open(zshrc, "a") as fh:
                fh.write(f"\nsource {saved_path}\n")
            console.print(f"[green]✓[/green] Added to ~/.zshrc")
            console.print(f"[dim]Run [cyan]source ~/.zshrc[/cyan] or open a new terminal to activate.[/dim]")


def _extract_code(llm_output: str) -> str:
    """Pull the code block out of an LLM response; fall back to raw text."""
    import re
    # Match ```bash ... ``` or ``` ... ```
    m = re.search(r"```(?:bash|sh|shell)?\s*\n(.*?)```", llm_output, re.DOTALL)
    if m:
        return m.group(1).strip()
    return llm_output.strip()


def _extract_name(code: str) -> str | None:
    """Return the alias/function name from a bash snippet."""
    import re
    m = re.search(r"alias\s+(\w+)\s*=", code)
    if m:
        return m.group(1)
    m = re.search(r"(?:^|\n)(?:function\s+)?(\w[\w_]*)\s*\(\s*\)", code)
    if m:
        return m.group(1)
    return None


def _print_script_usage(code: str, saved_path: Path, dest: str) -> None:
    """Print load + invoke instructions after saving a script."""
    name = _extract_name(code)

    lines: list[str] = []

    if dest == "scripts":
        lines.append(f"[bold]1. Load in current shell:[/bold]")
        lines.append(f"   [cyan]source {saved_path}[/cyan]")
    elif dest == "zshrc":
        lines.append(f"[bold]1. Reload shell config:[/bold]")
        lines.append(f"   [cyan]source ~/.zshrc[/cyan]")
    elif dest == "aliases":
        lines.append(f"[bold]1. Reload aliases:[/bold]")
        lines.append(f"   [cyan]source ~/.aliases[/cyan]")

    if name:
        lines.append(f"")
        lines.append(f"[bold]2. Invoke:[/bold]")
        lines.append(f"   [bold green]{name}[/bold green]")

    lines.append(f"")
    lines.append(f"[bold]Review:[/bold]")
    lines.append(f"   [cyan]cat {saved_path}[/cyan]")

    console.print(
        Panel("\n".join(lines), title="[bold]⚡ How to use[/bold]", border_style="blue", box=box.ROUNDED)
    )


# ---------------------------------------------------------------------------
# ghost next
# ---------------------------------------------------------------------------

@app.command()
def next(
    project: Optional[str] = typer.Argument(None, help="Project name (default: infer from cwd)"),
    run: bool = typer.Option(False, "--run", "-r", help="Execute predictions immediately"),
    routine: Optional[str] = typer.Option(None, "--routine", help="Run a specific routine by partial name"),
    list_: bool = typer.Option(False, "--list", "-l", help="List all learned routines"),
    dismiss: Optional[int] = typer.Option(None, "--dismiss", help="Dismiss a routine by ID"),
) -> None:
    """Show predicted next actions based on learned workflow sequences."""
    db.init_db()

    from ghost_pulse.analyzers.workflow_predictor import WorkflowPredictor
    from ghost_pulse.collectors.shell import _infer_project_from_cwd
    import os

    predictor = WorkflowPredictor()

    if dismiss is not None:
        predictor.dismiss_sequence(dismiss)
        console.print(f"[green]✓[/green] Dismissed routine #{dismiss}")
        return

    # Resolve project
    if not project:
        project = _infer_project_from_cwd(os.getcwd()) or "unknown"

    if list_:
        routines = predictor.get_project_routines(project)
        if not routines:
            console.print(f"[dim]No routines learned for '{project}' yet.[/dim]")
            return
        table = Table("ID", "Sequence", "Frequency", "Confidence", box=box.ROUNDED)
        table.columns[0].style = "dim"
        table.columns[2].style = "cyan"
        table.columns[3].style = "green"
        for r in routines[:15]:
            cmds = " → ".join(r.get("sequence", []))[:65]
            conf = f"{r['confidence']*100:.0f}%"
            table.add_row(str(r["id"]), cmds, f"×{r['frequency']}", conf)
        console.print(table)
        return

    # Learn new sequences if no data
    sequences = db.get_workflow_sequences(project=project, active_only=True)
    if not sequences:
        with console.status("[dim]Learning workflow patterns…[/dim]"):
            predictor.learn_sequences()

    # Get recent commands for prediction context
    since = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    recent_events = db.query_events(event_type="shell_cmd", project=project, since=since)
    recent_cmds = [e["data"].get("cmd", "") for e in recent_events[-3:] if e.get("data")]

    predictions = predictor.predict_next(project, recent_cmds, top_k=3)

    # Also check for named routines filter
    if routine:
        routines_all = predictor.get_project_routines(project)
        matching = [r for r in routines_all
                    if routine.lower() in " → ".join(r.get("sequence", [])).lower()]
        if matching:
            r = matching[0]
            predictions = [{
                "sequence_id": r["id"],
                "commands": r.get("sequence", []),
                "full_sequence": r.get("sequence", []),
                "confidence": r["confidence"],
                "frequency": r["frequency"],
            }]

    if not predictions:
        routines = predictor.get_project_routines(project)
        if not routines:
            console.print(
                Panel(
                    f"[dim]No workflow patterns learned for [bold]{project}[/bold] yet.\n"
                    "Ghost Pulse needs 2+ occurrences of the same command sequence to predict.\n"
                    "Keep working and check back later![/dim]",
                    title=f"[bold blue]Ghost Pulse · Next actions for {project}[/bold blue]",
                    border_style="blue",
                    box=box.ROUNDED,
                )
            )
        else:
            console.print(
                f"[yellow]No confident predictions based on your last commands in '{project}'.[/yellow]\n"
                f"[dim]Run [bold]ghost next --list[/bold] to see all learned routines.[/dim]"
            )
        return

    # Build display
    lines: list[str] = [f"  Based on your recent commands in [bold cyan]{project}[/bold cyan]:\n"]
    for i, pred in enumerate(predictions, 1):
        conf_pct = f"{pred['confidence']*100:.0f}%"
        conf_color = "green" if pred["confidence"] >= 0.7 else "yellow"
        cmds_str = " && ".join(pred["commands"])
        lines.append(
            f"  [bold]{i}.[/bold] [white]{cmds_str[:60]}[/white]"
            f"  [dim]([{conf_color}]confidence: {conf_pct}[/{conf_color}])[/dim]"
        )

    lines.append("")

    # Other routines
    all_routines = predictor.get_project_routines(project)
    other = [r for r in all_routines if r["id"] not in {p["sequence_id"] for p in predictions}][:3]
    if other:
        lines.append("  [dim]Other routines for this project:[/dim]")
        for r in other:
            cmds_short = " → ".join(r.get("sequence", [])[:3])[:50]
            if len(r.get("sequence", [])) > 3:
                cmds_short += "…"
            lines.append(f"  [dim]•[/dim] [yellow]{cmds_short}[/yellow]  [dim](×{r['frequency']} this period)[/dim]")

    lines.append("")
    lines.append("  [dim]▶ Run all?  [bold][Y/n/pick][/bold][/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title=f"[bold blue]Ghost Pulse · Next actions for {project}[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )

    if run:
        _execute_commands(predictions[0]["commands"])
        return

    answer = typer.prompt("Run predicted commands? [Y/n/1/2/3]", default="Y")
    ans = answer.strip().lower()
    if ans in ("n", "no", ""):
        return
    if ans in ("y", "yes"):
        _execute_commands(predictions[0]["commands"])
    elif ans in ("1", "2", "3") and int(ans) <= len(predictions):
        _execute_commands(predictions[int(ans) - 1]["commands"])


def _execute_commands(commands: list[str]) -> None:
    """Execute a list of commands sequentially, stopping on failure."""
    import subprocess
    for cmd in commands:
        console.print(f"[bold cyan]▶ {cmd}[/bold cyan]")
        result = subprocess.run(cmd, shell=True)
        if result.returncode != 0:
            console.print(f"[red]✗ Command failed (exit {result.returncode})[/red]")
            raise typer.Exit(result.returncode)


# ---------------------------------------------------------------------------
# ghost recall
# ---------------------------------------------------------------------------

@app.command()
def recall(
    query: Optional[str] = typer.Argument(None, help="Search query for error patterns"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project"),
    days: int = typer.Option(90, "--days", help="Search window in days"),
    show_diff: Optional[int] = typer.Option(None, "--show-diff", help="Show git diff for error ID"),
) -> None:
    """Search error memory and past fixes."""
    db.init_db()

    from ghost_pulse.analyzers.error_memory import ErrorMemory
    cfg = _get_cfg()
    from ghost_pulse.llm.factory import get_provider
    em = ErrorMemory(llm_provider=get_provider(cfg) if cfg else None)

    if show_diff is not None:
        errors = em.get_frequent_errors(days=365, limit=1000)
        # NOTE: can't use next() here — the Typer command named "next" shadows
        # Python's built-in next() at module scope.
        _candidates = [e for e in errors if e.get("id") == show_diff]
        match = _candidates[0] if _candidates else None
        if match and match.get("fix_diff"):
            from rich.syntax import Syntax
            console.print(
                Panel(
                    Syntax(match["fix_diff"], "diff", theme="monokai"),
                    title=f"[bold]Fix diff for error #{show_diff}[/bold]",
                    box=box.ROUNDED,
                )
            )
        else:
            console.print(f"[yellow]No diff available for error #{show_diff}[/yellow]")
        return

    errors = em.get_frequent_errors(project=project, days=days, limit=20)

    if query:
        ql = query.lower()
        errors = [
            e for e in errors
            if ql in (e.get("error_pattern") or "").lower()
            or ql in (e.get("fix_description") or "").lower()
            or ql in (e.get("project") or "").lower()
        ]

    if not errors:
        empty_msg = (
            f"[dim]No errors matching '{query}' in the last {days} days.[/dim]"
            if query
            else "[dim]No error history yet. Errors are recorded automatically when commands fail.[/dim]"
        )
        console.print(
            Panel(
                empty_msg,
                title="[bold blue]Ghost Pulse · Error recall[/bold blue]",
                border_style="blue",
                box=box.ROUNDED,
            )
        )
        return

    header = f"  🔍 [bold]Searching for:[/bold] [cyan]{query}[/cyan]\n" if query else ""
    header += f"  Found [bold cyan]{len(errors)}[/bold cyan] match{'es' if len(errors) != 1 else ''}\n"

    lines = [header]
    for i, e in enumerate(errors[:10], 1):
        ago = _days_ago(e.get("last_seen", ""))
        proj_str = f"[dim]{e.get('project', '?')}[/dim]  " if e.get("project") else ""
        lines.append(
            f"  [bold]{i}.[/bold] [yellow]{e.get('error_pattern', '?')[:55]}[/yellow]"
            f"  [dim](×{e.get('occurrences', 1)}, last: {ago})[/dim]"
        )
        lines.append(f"     {proj_str}")
        if e.get("fix_description"):
            lines.append(f"     [green]Fix:[/green] {e['fix_description']}")
        if e.get("fix_commands"):
            cmds = " → ".join(e["fix_commands"][:3])
            if len(e["fix_commands"]) > 3:
                cmds += "…"
            lines.append(f"     [dim]Commands:[/dim] {cmds}")
        lines.append(f"     [dim]───[/dim]")

    lines.append("")
    lines.append(f"  [dim]Show full fix? [bold][1–{min(len(errors),10)}/n][/bold][/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold blue]Ghost Pulse · Error recall[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )

    answer = typer.prompt("Show fix", default="n")
    ans = answer.strip().lower()
    if ans.isdigit() and 1 <= int(ans) <= len(errors):
        e = errors[int(ans) - 1]
        detail_lines = [
            f"  [bold]Pattern:[/bold] {e.get('error_pattern')}",
            f"  [bold]Type:[/bold] {e.get('error_type', 'unknown')}",
            f"  [bold]Project:[/bold] {e.get('project', 'unknown')}",
            f"  [bold]Occurrences:[/bold] {e.get('occurrences', 1)}",
            f"  [bold]First seen:[/bold] {e.get('first_seen', '?')}",
            f"  [bold]Last seen:[/bold] {e.get('last_seen', '?')}",
        ]
        if e.get("fix_description"):
            detail_lines.append(f"\n  [green]Fix summary:[/green] {e['fix_description']}")
        if e.get("fix_commands"):
            detail_lines.append(f"\n  [bold]Fix commands:[/bold]")
            for cmd in e["fix_commands"]:
                detail_lines.append(f"    [cyan]{cmd}[/cyan]")
        if e.get("last_fix_duration_ms"):
            detail_lines.append(
                f"\n  [dim]Fix took: {e['last_fix_duration_ms']/1000:.0f}s last time[/dim]"
            )
        tip = _get_error_tip(e, em)
        if tip:
            detail_lines.append(f"\n  [bold yellow]💡 Tip:[/bold yellow] {tip}")

        console.print(
            Panel(
                "\n".join(detail_lines),
                title=f"[bold]Error #{e.get('id')} detail[/bold]",
                box=box.ROUNDED,
                border_style="green",
            )
        )


_HEURISTIC_TIPS: dict[str, list[str]] = {
    "test": [
        "Run a single test file to isolate the failure: `pytest path/to/test_file.py -v`",
        "Check for missing fixtures or setup in conftest.py",
        "Look for import errors by running `python -c \"import your_module\"`",
        "Try `pytest --tb=long` for full tracebacks",
    ],
    "build": [
        "Clear the build cache first: try `./gradlew clean`, `npm cache clean`, or `pip install --force-reinstall`",
        "Check dependency versions in your lock file — a recent install may have broken something",
        "Look for missing environment variables with `printenv | grep <your_prefix>`",
        "Try a fresh virtual environment: `python -m venv .venv && pip install -e .`",
    ],
    "deploy": [
        "Check git status for uncommitted changes that might conflict: `git status`",
        "Verify you are on the right branch: `git branch`",
        "Look for conflicts with `git diff HEAD`",
        "For kubectl/terraform: verify your context with `kubectl config current-context` / `terraform workspace show`",
    ],
    "config": [
        "Check your .env file exists and has the right values",
        "Compare against .env.example to spot missing keys",
        "Verify the config file path is correct for the current working directory",
    ],
    "runtime": [
        "Add `--verbose` or `-v` to get more output from the failing command",
        "Check recent file changes with `git diff HEAD~1`",
        "Try running with a clean state — delete temp files, caches, or __pycache__",
    ],
}


def _get_error_tip(error: dict, em: Any) -> str | None:
    """Return a tip string for the given error record, using LLM if available."""
    # Try LLM first
    if em.llm:
        try:
            pattern = error.get("error_pattern", "")
            fix_cmds = error.get("fix_commands", [])
            fix_desc = error.get("fix_description", "")
            prompt = (
                f"A developer's command `{pattern}` keeps failing "
                f"({error.get('occurrences', 1)}x).\n"
                + (f"Known fix: {fix_desc}\n" if fix_desc else "")
                + "Give ONE actionable debugging tip in under 25 words. No preamble."
            )
            from ghost_pulse.llm.base import GHOST_PULSE_SYSTEM_PROMPT
            resp = em.llm.analyze(prompt, system_prompt=GHOST_PULSE_SYSTEM_PROMPT)
            tip = resp.content.strip()
            if tip:
                return tip
        except Exception:
            pass

    # Fall back to heuristic tips by error type
    err_type = error.get("error_type", "runtime")
    tips = _HEURISTIC_TIPS.get(err_type, _HEURISTIC_TIPS["runtime"])
    import hashlib
    idx = int(hashlib.md5((error.get("error_pattern", "") or "").encode()).hexdigest(), 16) % len(tips)
    return tips[idx]


def _days_ago(ts: str) -> str:
    if not ts:
        return "unknown"
    try:
        then = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        delta = datetime.now() - then
        days = delta.days
        if days == 0:
            return "today"
        if days == 1:
            return "yesterday"
        return f"{days} days ago"
    except ValueError:
        return "unknown"


# ---------------------------------------------------------------------------
# ghost resume
# ---------------------------------------------------------------------------

@app.command()
def resume(
    project: Optional[str] = typer.Argument(None, help="Project to resume"),
    open_editor: bool = typer.Option(False, "--open", "-o", help="Open last file in $EDITOR"),
    checkout: bool = typer.Option(False, "--checkout", help="Checkout the saved branch"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Restore context for a project you haven't worked on recently."""
    db.init_db()
    cfg = _get_cfg()
    from ghost_pulse.llm.factory import get_provider
    from ghost_pulse.analyzers.context_restorer import ContextRestorer

    provider = get_provider(cfg)
    restorer = ContextRestorer(llm_provider=provider if provider.name != "none" else None)

    if not project:
        # Show all projects with their last session
        projects = db.get_all_snapshot_projects()
        if not projects:
            console.print("[dim]No session snapshots yet. Ghost Pulse will capture snapshots automatically as you work.[/dim]")
            return
        table = Table("Project", "Last session", "Branch", "Duration", box=box.ROUNDED)
        for proj in projects[:15]:
            snap = db.get_latest_snapshot(proj)
            if snap:
                ago = _days_ago(snap.get("snapshot_time", ""))
                branch = snap.get("branch") or "—"
                from ghost_pulse.analyzers.context_restorer import _fmt_duration
                dur = _fmt_duration(snap.get("duration_minutes"))
                table.add_row(proj, ago, branch, dur)
        console.print(table)
        project = typer.prompt("\nResume which project?", default="")
        if not project.strip():
            return
        project = project.strip()

    ctx = restorer.resume(project)

    if json_output:
        console.print(json.dumps(ctx, indent=2, default=str))
        return

    if "error" in ctx:
        console.print(f"[yellow]{ctx['error']}[/yellow]")
        return

    # Build display
    last_error_str = ""
    if ctx.get("last_error"):
        last_error_str = f"  [bold]Last command:[/bold] [red]{ctx['last_error']}[/red] [dim](❌ FAILED)[/dim]\n"
    elif ctx.get("last_command"):
        last_error_str = f"  [bold]Last command:[/bold] [white]{ctx['last_command']}[/white]\n"

    unstaged = ctx.get("unstaged_files", [])
    unstaged_str = ""
    if unstaged:
        unstaged_str = f"\n  [bold]Uncommitted changes ({len(unstaged)} files):[/bold]\n"
        for f in unstaged[:6]:
            unstaged_str += f"    [cyan]{f}[/cyan]\n"

    summary_str = ""
    if ctx.get("summary"):
        summary_str = f"\n  [bold yellow]💡 AI Summary:[/bold yellow]\n  {ctx['summary']}\n"

    content = (
        f"  ⏸  [bold]Last session:[/bold] {ctx.get('snapshot_time', '?')[:10]}, "
        f"{ctx.get('time_away', '?')} ago "
        f"[dim]({ctx.get('session_duration', '?')})[/dim]\n"
        f"\n"
        f"  [bold]Branch:[/bold] [cyan]{ctx.get('branch') or 'unknown'}[/cyan]\n"
    )
    if ctx.get("last_file"):
        content += f"  [bold]Last file:[/bold] [white]{ctx['last_file']}[/white]\n"
    content += last_error_str
    content += unstaged_str
    content += summary_str
    content += "\n  [dim]▶ Open in editor?  [bold][Y/n][/bold][/dim]"

    console.print(
        Panel(
            content,
            title=f"[bold blue]Ghost Pulse · Resume {project}[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )

    if json_output:
        return

    if checkout and ctx.get("branch"):
        import subprocess
        from ghost_pulse.analyzers.context_restorer import _find_project_path
        proj_path = _find_project_path(project)
        if proj_path:
            subprocess.run(["git", "checkout", ctx["branch"]], cwd=proj_path)

    if open_editor or typer.confirm("Open in editor?", default=True):
        import os, subprocess as sp
        editor = os.environ.get("EDITOR", "vi")
        target = ctx.get("last_file") or "."
        if ctx.get("last_file"):
            sp.run([editor, target])


# ---------------------------------------------------------------------------
# ghost profile
# ---------------------------------------------------------------------------

@app.command()
def profile(
    regenerate: bool = typer.Option(False, "--regenerate", help="Force regenerate profile"),
    type_: Optional[str] = typer.Option(None, "--type", help="energy | workflow | focus"),
    days: int = typer.Option(30, "--days", help="Days of data to analyze"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show your developer fingerprint and productivity profile."""
    db.init_db()
    cfg = _get_cfg()
    from ghost_pulse.llm.factory import get_provider
    from ghost_pulse.analyzers.developer_fingerprint import DeveloperFingerprint

    provider = get_provider(cfg)
    fp = DeveloperFingerprint(llm_provider=provider if provider.name != "none" else None)

    if regenerate:
        with console.status("[bold cyan]Generating developer profile…[/bold cyan]"):
            data = fp.generate_full_profile(days=days)
    else:
        # Load from cache, fall back to generating
        cached = db.get_latest_profile()
        if not cached:
            with console.status("[bold cyan]Generating developer profile…[/bold cyan]"):
                data = fp.generate_full_profile(days=days)
        else:
            # Load all three profiles
            energy_row = db.get_latest_profile("energy_map")
            workflow_row = db.get_latest_profile("workflow_fingerprint")
            focus_row = db.get_latest_profile("focus_pattern")
            data = {
                "energy_map": energy_row["data"] if energy_row else {},
                "workflow_fingerprint": workflow_row["data"] if workflow_row else {},
                "focus_pattern": focus_row["data"] if focus_row else {},
            }

    if type_:
        key_map = {"energy": "energy_map", "workflow": "workflow_fingerprint", "focus": "focus_pattern"}
        key = key_map.get(type_.lower(), type_)
        data = {key: data.get(key, {})}

    if json_output:
        console.print(json.dumps(data, indent=2, default=str))
        return

    _print_profile(data)


def _print_profile(data: dict) -> None:
    from rich.rule import Rule

    wf = data.get("workflow_fingerprint", {})
    energy = data.get("energy_map", {})
    focus = data.get("focus_pattern", {})

    sections: list[str] = []

    if wf and not wf.get("error"):
        tools = ", ".join(wf.get("tools_top_5", []))
        sections.append(
            f"  [bold cyan]🧬 Workflow fingerprint[/bold cyan]\n"
            f"  Style: [white]{wf.get('style', '?')}[/white]\n"
            f"  Avg commit size: [cyan]{wf.get('avg_commit_size_lines', 0)}[/cyan] lines  │  "
            f"Commits/day: [cyan]{wf.get('commits_per_day', 0)}[/cyan]\n"
            f"  Top tools: [yellow]{tools or '?'}[/yellow]"
        )

    if energy and energy.get("hourly"):
        hourly = energy["hourly"]
        max_cmds = max((h["commands"] for h in hourly), default=1) or 1
        peak_hours = set(energy.get("peak_hours", []))
        low_hours  = set(energy.get("low_energy_hours", []))

        def _tod_label(h: int) -> str:
            if 5  <= h < 12: return "morning"
            if 12 <= h < 17: return "afternoon"
            if 17 <= h < 21: return "evening"
            if h >= 21 or h < 5: return "night"
            return ""

        bar_lines = ["  [dim]hour  activity (commands)                    peak[/dim]"]
        for h in hourly:
            cmds = h["commands"]
            if cmds == 0 and h["hour"] not in peak_hours:
                continue  # skip completely empty, non-peak hours
            filled = round(cmds / max_cmds * 28)
            bar = f"[cyan]{'█' * filled}[/cyan][dim]{'░' * (28 - filled)}[/dim]"
            peak_marker = " [bold yellow]⚡ peak[/bold yellow]" if h["hour"] in peak_hours else ""
            low_marker  = " [dim red]low[/dim red]" if h["hour"] in low_hours else ""
            bar_lines.append(
                f"  [bold]{h['hour']:02d}:00[/bold] [dim]{_tod_label(h['hour'])[:9]:<9}[/dim]"
                f"  {bar}  [dim]{cmds:>3}[/dim]{peak_marker}{low_marker}"
            )

        best_day = energy.get("best_day", "?")
        total_cmds = energy.get("total_commands", 0)
        total_commits = energy.get("total_commits", 0)
        sections.append(
            f"  [bold cyan]⚡ Energy map[/bold cyan]  [dim](30-day command activity by hour)[/dim]\n"
            + "\n".join(bar_lines[:14])
            + f"\n\n  [dim]Best day: [green]{best_day}[/green]  ·  "
            + f"Total: {total_cmds} commands · {total_commits} commits[/dim]"
        )

    if focus:
        trend_icon = {"improving": "↑", "worsening": "↓", "stable": "→"}.get(
            focus.get("trend", "stable"), "→"
        )
        trend_color = {"improving": "green", "worsening": "red", "stable": "yellow"}.get(
            focus.get("trend", "stable"), "yellow"
        )
        _skip = {"unknown", "Unknown", "~/home", ""}
        distractors = ", ".join(
            d for d in focus.get("top_distractors", [])[:5] if d not in _skip
        ) or "none"
        sections.append(
            f"  [bold cyan]🎯 Focus pattern[/bold cyan]\n"
            f"  Avg focus block: [white]{focus.get('avg_focus_block_min', 0)} min[/white]  │  "
            f"Longest: [white]{focus.get('longest_focus_block_min', 0)} min[/white]\n"
            f"  Best focus day: [green]{focus.get('best_focus_day', '?')}[/green]  │  "
            f"Trend: [{trend_color}]{trend_icon} {focus.get('trend', 'stable')}[/{trend_color}]\n"
            f"  Top distractors: [yellow]{distractors}[/yellow]"
        )

    if not sections:
        console.print("[dim]No profile data available. Run: ghost profile --regenerate[/dim]")
        return

    content = ("\n\n" + "  " + "─" * 55 + "\n\n").join(sections)
    console.print(
        Panel(
            content,
            title="[bold blue]Ghost Pulse · Developer profile[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )


# ---------------------------------------------------------------------------
# ghost focus
# ---------------------------------------------------------------------------

@app.command()
def focus(
    guard: Optional[str] = typer.Option(None, "--guard", help="on | off"),
    threshold: Optional[int] = typer.Option(None, "--threshold", help="Minutes to count as focus session"),
) -> None:
    """Show today's focus sessions and configure the focus guard."""
    db.init_db()
    cfg = _get_cfg()

    if guard is not None:
        val = guard.lower() in ("on", "true", "1", "yes")
        set_config_value(cfg, "v2.focus_guard_enabled", str(val).lower())
        save_config(cfg)
        state = "enabled" if val else "disabled"
        console.print(f"[green]✓[/green] Focus guard {state}")
        return

    if threshold is not None:
        set_config_value(cfg, "v2.focus_threshold_minutes", str(threshold))
        save_config(cfg)
        console.print(f"[green]✓[/green] Focus threshold set to {threshold} minutes")
        return

    # Show today's focus sessions
    today_str = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    sessions = db.get_focus_sessions(since=today_str)
    guard_enabled = cfg.get("v2", {}).get("focus_guard_enabled", True)

    # Also compute from context switch data
    from ghost_pulse.analyzers.context_switch import compute_context_switches
    ctx = compute_context_switches(since=today_str)
    focus_score = max(0, 100 - int(ctx.get("fragmentation_score", 0)))
    score_color = "green" if focus_score >= 70 else "yellow" if focus_score >= 40 else "red"

    guard_status = "[green]● on[/green]" if guard_enabled else "[red]○ off[/red]"

    lines: list[str] = [
        f"  🎯 Focus score today: [{score_color}][bold]{focus_score}/100[/bold][/{score_color}]\n"
        f"  Focus guard: {guard_status}\n"
    ]

    if sessions:
        lines.append("  [bold]Sessions:[/bold]")
        now = datetime.now()
        for s in sessions:
            start_str = s.get("started_at", "")[:16].replace("T", " ")
            is_active = not s.get("ended_at")
            if is_active:
                try:
                    start_dt = datetime.strptime(s["started_at"][:19], "%Y-%m-%dT%H:%M:%S")
                    dur = (now - start_dt).total_seconds() / 60
                except (ValueError, KeyError):
                    dur = 0.0
                end_str = "now"
            else:
                dur = s.get("duration_minutes") or 0.0
                end_str = (s.get("ended_at") or "")[:16].replace("T", " ")
            dur_str = f"{int(dur)//60}h {int(dur)%60:02d}m"
            score = s.get("quality_score") or 0
            if is_active and dur > 0:
                score = min(100.0, (dur / 90.0) * 100)
            score_c = "green" if score >= 70 else "yellow" if score >= 40 else "red"
            bar_len = round(score / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            active_label = "[bold green]🟢 active[/bold green]" if is_active else f"score: {score:.0f}"
            lines.append(
                f"  [dim]{start_str} – {end_str}[/dim]  "
                f"[cyan]{s.get('project', '?'):<14}[/cyan]  "
                f"[white]{dur_str}[/white]  "
                f"[{score_c}]{bar}[/{score_c}]  "
                f"{active_label}"
            )

        # Interruption summary
        all_interruptions: list[str] = []
        for s in sessions:
            all_interruptions.extend(s.get("interruption_sources", []))
        if all_interruptions:
            from collections import Counter
            irq_counts = Counter(all_interruptions)
            lines.append(f"\n  [bold]Interruptions today:[/bold] {len(all_interruptions)}")
            for proj, count in irq_counts.most_common(4):
                lines.append(f"  [dim]•[/dim] switched to [yellow]{proj}[/yellow] (×{count})")
    else:
        # Fall back to context switch deep work blocks
        blocks = ctx.get("deep_work_blocks", [])
        if blocks:
            lines.append("  [bold]Deep work blocks:[/bold]")
            for b in blocks:
                dur = f"{b['duration_minutes']}m"
                lines.append(
                    f"  [dim]{b['start']} - {b['end']}[/dim]  "
                    f"[cyan]{b['project']:<14}[/cyan]  [white]{dur}[/white]"
                )
        else:
            lines.append("  [dim]No focus sessions recorded yet today.[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold blue]Ghost Pulse · Focus today[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )


# ---------------------------------------------------------------------------
# ghost insights
# ---------------------------------------------------------------------------

@app.command()
def insights(
    days: int = typer.Option(7, "--days", help="Days of history to analyse"),
) -> None:
    """Get personalized productivity insights powered by LLM."""
    db.init_db()
    cfg = _get_cfg()

    from ghost_pulse.llm.factory import get_provider
    from ghost_pulse.generators.report_gen import generate_insights

    provider = get_provider(cfg)
    if provider.name == "none":
        console.print("[yellow]No LLM provider configured. Run: ghost config providers[/yellow]")
        raise typer.Exit(1)

    from rich.markdown import Markdown
    from rich.rule import Rule

    with console.status(f"[bold cyan]Analysing {days} days of activity with {provider.name}…[/bold cyan]"):
        # v1 activity insights
        result = generate_insights(provider, days=days)

        # v2 developer fingerprint insights
        v2_result = ""
        try:
            from ghost_pulse.analyzers.developer_fingerprint import DeveloperFingerprint
            fp = DeveloperFingerprint(llm_provider=provider)
            v2_result = fp.generate_insights(days=days)
        except Exception:
            pass

        # v2 error patterns
        error_summary = ""
        try:
            from ghost_pulse.analyzers.error_memory import ErrorMemory
            errors = ErrorMemory().get_frequent_errors(days=days, limit=3)
            if errors:
                error_lines = []
                for e in errors:
                    times = e.get("occurrences", 1)
                    if times >= 2:
                        error_lines.append(
                            f"- `{e.get('error_pattern', '?')}` failed {times}× "
                            + (f"— {e.get('fix_description')}" if e.get("fix_description") else "")
                        )
                if error_lines:
                    error_summary = "**Recurring errors:**\n" + "\n".join(error_lines)
        except Exception:
            pass

    console.print()
    console.print(Rule(f"[bold blue]Ghost Pulse Insights[/bold blue] · last {days} days", style="blue"))
    console.print(Panel(Markdown(result), border_style="blue", box=box.ROUNDED, padding=(1, 2)))
    if v2_result and v2_result != result:
        console.print(Panel(Markdown(v2_result), title="[bold]Energy & Focus Patterns[/bold]", border_style="cyan", box=box.ROUNDED, padding=(1, 2)))
    if error_summary:
        console.print(Panel(Markdown(error_summary), title="[bold]Error patterns[/bold]", border_style="red", box=box.ROUNDED, padding=(1, 2)))
    console.print(Rule(style="dim"))


# ---------------------------------------------------------------------------
# ghost config
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="Manage Ghost Pulse configuration.")
app.add_typer(config_app, name="config")


@config_app.callback(invoke_without_command=True)
def config_root(ctx: typer.Context) -> None:
    """Show current configuration."""
    if ctx.invoked_subcommand is None:
        cfg = _get_cfg()
        import sys
        if sys.version_info >= (3, 11):
            import tomllib  # type: ignore[import]
        else:
            import tomli as tomllib  # type: ignore[import,no-redef]
        import tomli_w
        console.print(tomli_w.dumps(cfg))


@config_app.command(name="set")
def config_set(key: str, value: str) -> None:
    """Set a config value (e.g. ghost config set llm.provider ollama)."""
    cfg = _get_cfg()
    set_config_value(cfg, key, value)
    save_config(cfg)
    console.print(f"[green]✓[/green] Set {key} = {value}")


@config_app.command(name="providers")
def config_providers() -> None:
    """Test all LLM providers and show which are available."""
    cfg = _get_cfg()
    from ghost_pulse.llm.factory import probe_all

    results, active_name = probe_all(cfg)
    table = Table("Provider", "Model", "Status", "Notes", box=box.ROUNDED)
    descriptions = {
        "ollama": "local, free",
        "groq": "cloud, free tier",
        "claude": "cloud, paid",
        "openai": "cloud, paid",
    }
    for r in results:
        status = "[green]✅ Available[/green]" if r["available"] else "[red]❌ Not configured[/red]"
        active_marker = " [bold](active)[/bold]" if r["provider"] == active_name else ""
        table.add_row(
            r["provider"] + active_marker,
            r.get("model", "—"),
            status,
            descriptions.get(r["provider"], ""),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# ghost projects
# ---------------------------------------------------------------------------

projects_app = typer.Typer(help="Manage tracked projects.")
app.add_typer(projects_app, name="projects")


@projects_app.callback(invoke_without_command=True)
def projects_root(ctx: typer.Context) -> None:
    """List tracked projects."""
    if ctx.invoked_subcommand is None:
        db.init_db()
        cfg = _get_cfg()
        paths = cfg.get("projects", {}).get("paths", [])

        from ghost_pulse.analyzers.time_tracker import compute_time_per_project
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        time_data = compute_time_per_project(since=week_ago)

        # Expand folder paths to their individual git repos
        repo_rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for p in paths:
            p_path = Path(p).expanduser().resolve()
            if (p_path / ".git").exists():
                key = str(p_path)
                if key not in seen:
                    seen.add(key)
                    repo_rows.append((p_path.name, str(p_path)))
            elif p_path.is_dir():
                try:
                    for child in sorted(p_path.iterdir()):
                        if child.is_dir() and (child / ".git").exists():
                            key = str(child.resolve())
                            if key not in seen:
                                seen.add(key)
                                repo_rows.append((child.name, str(child)))
                except PermissionError:
                    pass

        table = Table("Project", "Path", "7d hours", box=box.ROUNDED)
        for proj_name, proj_path in sorted(repo_rows, key=lambda r: -time_data.get(r[0], {}).get("total_minutes", 0)):
            stats = time_data.get(proj_name, {})
            hours = f"{stats.get('total_minutes', 0)/60:.1f}h"
            table.add_row(proj_name, proj_path, hours)
        if not repo_rows:
            console.print("[dim]No projects tracked. Run: ghost init --path ~/your-projects[/dim]")
            return
        console.print(table)


@projects_app.command(name="add")
def projects_add(path: str) -> None:
    """Add a project path to track."""
    cfg = _get_cfg()
    resolved = str(Path(path).expanduser().resolve())
    paths: list = cfg["projects"].get("paths", [])
    if resolved not in paths:
        paths.append(resolved)
        cfg["projects"]["paths"] = paths
        save_config(cfg)
        console.print(f"[green]✓[/green] Added {resolved}")
    else:
        console.print("[yellow]Already tracked.[/yellow]")


@projects_app.command(name="remove")
def projects_remove(name: str) -> None:
    """Remove a project by name or path."""
    cfg = _get_cfg()
    paths: list = cfg["projects"].get("paths", [])
    before = len(paths)
    cfg["projects"]["paths"] = [
        p for p in paths if Path(p).name != name and p != name
    ]
    if len(cfg["projects"]["paths"]) < before:
        save_config(cfg)
        console.print(f"[green]✓[/green] Removed {name}")
    else:
        console.print(f"[yellow]Project '{name}' not found.[/yellow]")


# ---------------------------------------------------------------------------
# ghost export
# ---------------------------------------------------------------------------

@app.command()
def export(
    from_date: str = typer.Option(
        (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        "--from",
        help="Start date (YYYY-MM-DD)",
    ),
    to_date: str = typer.Option(
        datetime.now().strftime("%Y-%m-%d"),
        "--to",
        help="End date (YYYY-MM-DD)",
    ),
    format: str = typer.Option("json", "--format", help="Output format: json or csv"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
) -> None:
    """Export collected data to JSON or CSV."""
    db.init_db()
    events = db.query_events(
        since=from_date + "T00:00:00",
        until=to_date + "T23:59:59",
    )

    if format == "json":
        content = json.dumps(events, indent=2, default=str)
        suffix = ".json"
    else:
        import csv
        import io
        buf = io.StringIO()
        if events:
            writer = csv.DictWriter(buf, fieldnames=events[0].keys())
            writer.writeheader()
            for ev in events:
                ev_flat = dict(ev)
                if isinstance(ev_flat.get("data"), dict):
                    ev_flat["data"] = json.dumps(ev_flat["data"])
                writer.writerow(ev_flat)
        content = buf.getvalue()
        suffix = ".csv"

    if output:
        output.write_text(content)
        console.print(f"[green]✓[/green] Exported {len(events)} events to {output}")
    else:
        default_name = Path(f"ghost-pulse_export_{from_date}_{to_date}{suffix}")
        default_name.write_text(content)
        console.print(f"[green]✓[/green] Exported {len(events)} events to {default_name}")


# ---------------------------------------------------------------------------
# ghost reset
# ---------------------------------------------------------------------------

@app.command()
def reset(keep_config: bool = typer.Option(False, "--keep-config")) -> None:
    """Clear all collected data (with confirmation)."""
    confirmed = typer.confirm(
        "This will permanently delete all Ghost Pulse data. Continue?", default=False
    )
    if not confirmed:
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit()

    db_path = db.get_db_path()
    if db_path.exists():
        db_path.unlink()
        console.print("[green]✓[/green] Database deleted")
    db.init_db()
    console.print("[green]✓[/green] Fresh database created")

    if not keep_config:
        from ghost_pulse.config import CONFIG_PATH
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            console.print("[green]✓[/green] Config deleted")
    else:
        console.print("[dim]Config preserved.[/dim]")


# ---------------------------------------------------------------------------
# ghost backfill
# ---------------------------------------------------------------------------

@app.command()
def backfill(
    shell: str = typer.Option("auto", "--shell", help="Shell type: auto, zsh, bash"),
    limit: int = typer.Option(5000, "--limit", help="Max commands to import"),
    git: bool = typer.Option(True, "--git/--no-git", help="Also backfill git commit history"),
    git_since: str = typer.Option("", "--git-since", help="Only import git commits after this date (YYYY-MM-DD)"),
    git_limit: int = typer.Option(500, "--git-limit", help="Max git commits per repo"),
) -> None:
    """Import shell history and git commits as a one-time backfill."""
    db.init_db()
    from ghost_pulse.collectors.shell import backfill_from_history
    with console.status("Importing shell history…"):
        n = backfill_from_history(shell=shell, limit=limit)
    console.print(f"[green]✓[/green] Imported {n} commands from shell history")

    if git:
        from ghost_pulse.collectors.git_collector import backfill_git_commits
        from ghost_pulse.config import load_config
        cfg = load_config()
        project_paths = cfg.get("projects", {}).get("paths", [])
        if not project_paths:
            console.print("[yellow]⚠[/yellow] No project paths configured — skipping git backfill")
            console.print("  Run [bold]ghost projects add <path>[/bold] first")
        else:
            with console.status("Importing git commit history…"):
                gc = backfill_git_commits(
                    project_paths=project_paths,
                    since=git_since or None,
                    limit=git_limit,
                )
            console.print(f"[green]✓[/green] Imported {gc} commits from git history")


# ---------------------------------------------------------------------------
# ghost web
# ---------------------------------------------------------------------------

@app.command()
def web(
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't open browser automatically"),
) -> None:
    """Start the Ghost Pulse web UI (no extra dependencies required)."""
    from ghost_pulse.web.server import run
    import threading, webbrowser, time

    url = f"http://localhost:{port}"
    console.print(f"[green]✓[/green] Ghost Pulse UI → [bold cyan]{url}[/bold cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    if not no_open:
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    run(port=port)


@app.command()
def tui() -> None:
    """Launch the interactive k9s-inspired terminal UI."""
    db.init_db()
    try:
        from ghost_pulse.ui.tui import GhostPulseTUI
    except ImportError as exc:
        console.print(
            f"[red]TUI requires the `textual` package: {exc}[/red]\n"
            "[dim]Install with: pip install 'textual>=0.79'[/dim]"
        )
        raise typer.Exit(code=1)
    GhostPulseTUI().run()


# ---------------------------------------------------------------------------
# ghost fix-done  — manually close a fix window and record the fix
# ---------------------------------------------------------------------------

@app.command(name="fix-done")
def fix_done(
    error_id: Optional[int] = typer.Argument(None, help="Error memory ID (from ghost_pulse recall)"),
    commands: Optional[list[str]] = typer.Option(None, "--cmd", "-c", help="Commands that fixed it (repeat flag)"),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="Short description of the fix"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Project name"),
) -> None:
    """Mark the current error as fixed and record what you did.

    This closes any open fix windows for the project and saves the fix
    commands to the RAG knowledge base for future suggestions.

    Examples:
      ghost fix-done                          # closes most recent open window
      ghost fix-done 5 -c "pip install -e ." # close window for error #5
      ghost fix-done --note "updated deps"
    """
    db.init_db()
    from ghost_pulse.rag.fix_tracker import get_open_windows, close_fix_window
    from ghost_pulse.analyzers.error_memory import ErrorMemory

    cfg = _get_cfg()
    from ghost_pulse.llm.factory import get_provider
    em = ErrorMemory(llm_provider=get_provider(cfg))

    open_wins = get_open_windows()
    if not open_wins:
        console.print("[yellow]No open fix windows. Run a failing command first to start tracking.[/yellow]")
        return

    # Filter by project if given
    if project:
        filtered = [w for w in open_wins if w.get("project") == project]
        if not filtered:
            console.print(f"[yellow]No open fix windows for project '{project}'.[/yellow]")
            return
        open_wins = filtered

    # Filter by error_id if given
    if error_id is not None:
        filtered = [w for w in open_wins if w.get("error_memory_id") == error_id]
        if not filtered:
            console.print(f"[yellow]No open fix window for error #{error_id}.[/yellow]")
            return
        open_wins = filtered

    # Close the most recent open window
    win = open_wins[-1]
    fix_cmds = list(commands) if commands else win.get("commands_after", [])
    closed = close_fix_window(win["id"], resolution="manual")

    # Save as fix record
    ehash = win.get("error_hash", "")
    em_row = db.get_error_memory_by_hash(ehash) if ehash else None
    pattern = em_row.get("error_pattern", "") if em_row else ""

    fix_summary = note
    if not fix_summary and fix_cmds and em.llm:
        try:
            fix_summary = em.generate_fix_description(pattern, fix_cmds)
        except Exception:
            pass
    if not fix_summary and fix_cmds:
        fix_summary = f"Run: {' → '.join(fix_cmds[:3])}"

    from ghost_pulse.rag.fix_tracker import capture_workdir_git_diff

    fix_diff = capture_workdir_git_diff(win.get("workdir"))

    fix_record_id = db.upsert_fix_record(
        error_hash=ehash,
        error_pattern=pattern,
        fix_summary=fix_summary,
        fix_commands=fix_cmds,
        fix_diff=fix_diff,
        project=win.get("project"),
        source="manual",
    )

    # Update error_memory too
    if em_row:
        db.update_error_fix(
            error_id=em_row["id"],
            fix_commands=fix_cmds,
            fix_description=fix_summary,
            fix_diff=fix_diff,
        )

    # Try to embed (best-effort)
    try:
        from ghost_pulse.rag.embed_factory import get_embedding_provider
        from ghost_pulse.rag.vector_store import upsert_fix_embedding
        embed = get_embedding_provider(cfg)
        if embed.is_available() and pattern:
            vec = embed.embed(pattern + " " + (fix_summary or ""))
            upsert_fix_embedding(fix_record_id, vec)
    except Exception:
        pass

    dur = closed.get("fix_duration_ms") if closed else None
    dur_str = f" ({dur/1000:.0f}s to fix)" if dur else ""

    console.print(
        Panel(
            f"  [green]✓[/green] Fix recorded for: [yellow]{pattern[:60] or 'error'}[/yellow]{dur_str}\n"
            + (f"  [bold]Fix:[/bold] {fix_summary}\n" if fix_summary else "")
            + (f"  [bold]Commands:[/bold] {' → '.join(fix_cmds[:3])}\n" if fix_cmds else "")
            + f"\n  [dim]ID #{fix_record_id} — visible in: ghost fix-history[/dim]",
            title="[bold blue]Ghost Pulse · Fix recorded[/bold blue]",
            border_style="green",
            box=box.ROUNDED,
        )
    )


# ---------------------------------------------------------------------------
# ghost fix-suggest — get RAG-powered fix suggestions for a command
# ---------------------------------------------------------------------------

@app.command(name="fix-suggest")
def fix_suggest(
    command: Optional[str] = typer.Argument(None, help="Failing command to look up"),
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project"),
    top_k: int = typer.Option(3, "--top", help="Number of suggestions to show"),
) -> None:
    """Get AI-powered fix suggestions for a failing command.

    Searches your fix history using exact match → fuzzy token overlap → semantic embeddings.

    Examples:
      ghost fix-suggest "pytest tests/"
      ghost fix-suggest "docker build ." --project myapp
      ghost fix-suggest   # suggests for the most recently failed command
    """
    db.init_db()
    cfg = _get_cfg()

    # Auto-detect most recent failed command if not given
    if not command:
        from ghost_pulse.analyzers.time_tracker import _parse_ts
        today_str = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        events = db.query_events(event_type="shell_cmd", since=today_str, limit=200)
        failed = [e for e in reversed(events) if e.get("data", {}).get("exit_code", 0) != 0]
        if not failed:
            console.print("[yellow]No recent failed commands found. Provide a command explicitly.[/yellow]")
            return
        command = failed[0]["data"]["cmd"]
        console.print(f"[dim]Looking up fixes for most recent failure: [bold]{command[:60]}[/bold][/dim]\n")

    from ghost_pulse.rag.embed_factory import get_embedding_provider
    from ghost_pulse.rag.retriever import FixRetriever

    rag_cfg = cfg.get("rag", {})
    embed = get_embedding_provider(cfg)
    retriever = FixRetriever(
        embedding_provider=embed,
        fuzzy_threshold=rag_cfg.get("fuzzy_threshold", 0.25),
        semantic_threshold=rag_cfg.get("semantic_threshold", 0.60),
    )

    with console.status("[bold cyan]Searching fix knowledge base…[/bold cyan]"):
        suggestions = retriever.suggest(command, project=project, top_k=top_k)

    if not suggestions:
        console.print(
            Panel(
                f"  [dim]No known fixes for: [yellow]{command[:60]}[/yellow][/dim]\n\n"
                "  Ghost Pulse learns from your fixes over time.\n"
                "  After fixing a problem, run: [bold]ghost fix-done[/bold]\n"
                "  to record what you did so it can help next time.",
                title="[bold blue]Ghost Pulse · Fix suggest[/bold blue]",
                border_style="blue",
                box=box.ROUNDED,
            )
        )
        return

    tier_icons = {"exact": "🎯", "fuzzy": "🔍", "semantic": "🧠"}
    lines = [f"  Suggestions for: [yellow]{command[:60]}[/yellow]\n"]
    for i, s in enumerate(suggestions, 1):
        icon = tier_icons.get(s["tier"], "•")
        score_pct = f"{s['score']*100:.0f}%"
        lines.append(
            f"  [bold]{i}.[/bold] {icon} [dim]({s['tier']}, {score_pct} match)[/dim]"
        )
        if s.get("fix_summary"):
            lines.append(f"     [green]{s['fix_summary']}[/green]")
        if s.get("fix_commands"):
            cmds = " → ".join(s["fix_commands"][:3])
            if len(s["fix_commands"]) > 3:
                cmds += "…"
            lines.append(f"     [dim]Commands:[/dim] [cyan]{cmds}[/cyan]")
        if s.get("fix_diff"):
            fd = s["fix_diff"][:500]
            fd_esc = fd.replace("[", "\\[").replace("]", "\\]")
            lines.append(f"     [dim]Git diff:[/dim]\n     [dim]{fd_esc}[/dim]")
            if len(s["fix_diff"]) > 500:
                lines.append("     [dim]…[/dim]")
        if s.get("project"):
            lines.append(f"     [dim]Project:[/dim] {s['project']}")
        lines.append("")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold blue]Ghost Pulse · Fix suggestions[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )


# ---------------------------------------------------------------------------
# ghost fix-history — browse the fix knowledge base
# ---------------------------------------------------------------------------

@app.command(name="fix-history")
def fix_history(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project"),
    limit: int = typer.Option(20, "--limit", help="Number of records to show"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Browse your fix knowledge base.

    Shows all recorded fixes — both manually recorded (ghost fix-done)
    and automatically captured by the fix window tracker.
    """
    db.init_db()
    records = db.get_fix_records(project=project, limit=limit)

    if json_output:
        console.print(json.dumps(records, indent=2, default=str))
        return

    if not records:
        msg = (
            f"[dim]No fix records for project '{project}'.[/dim]"
            if project
            else "[dim]No fix records yet.\n\n  Ghost Pulse automatically records fixes as you work.\n"
               "  You can also run [bold]ghost fix-done[/bold] after fixing an error.[/dim]"
        )
        console.print(Panel(msg, title="[bold blue]Ghost Pulse · Fix history[/bold blue]", box=box.ROUNDED))
        return

    table = Table(
        "ID", "Pattern", "Fix", "Commands", "Project", "Date",
        box=box.ROUNDED,
        show_lines=False,
    )
    for r in records:
        pattern = (r.get("error_pattern") or "?")[:40]
        fix_sum = (r.get("fix_summary") or "—")[:45]
        cmds = " → ".join((r.get("fix_commands") or [])[:2])
        if len(r.get("fix_commands") or []) > 2:
            cmds += "…"
        cmds = cmds[:35] or "—"
        proj = r.get("project") or "—"
        date = (r.get("created_at") or "")[:10]
        src_icon = "👤" if r.get("source") == "manual" else "🤖"
        table.add_row(
            str(r.get("id", "?")),
            f"{src_icon} {pattern}",
            fix_sum,
            cmds,
            proj,
            date,
        )

    console.print(Panel(table, title="[bold blue]Ghost Pulse · Fix history[/bold blue]", box=box.ROUNDED))
    console.print(f"[dim]👤 = manual (fix-done)  🤖 = auto-detected   Total: {len(records)}[/dim]")


# ---------------------------------------------------------------------------
# ghost fix-purge — reset fix knowledge tables
# ---------------------------------------------------------------------------

@app.command(name="fix-purge")
def fix_purge(
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm deletion"),
) -> None:
    """Delete all fix_records and fix_windows (full reset of the fix KB)."""
    if not yes:
        console.print(
            "[yellow]This deletes every saved fix and fix window.[/yellow]\n"
            "Run with [bold]--yes[/bold] to confirm."
        )
        raise typer.Exit(1)
    db.init_db()
    counts = db.purge_fix_intel()
    console.print(
        Panel(
            f"  Removed [bold]{counts['fix_records_deleted']}[/bold] fix record(s)\n"
            f"  Removed [bold]{counts['fix_windows_deleted']}[/bold] fix window(s)",
            title="[bold green]Fix knowledge purged[/bold green]",
            border_style="green",
            box=box.ROUNDED,
        )
    )


# ---------------------------------------------------------------------------
# ghost fix-status — show open fix windows and RAG stats
# ---------------------------------------------------------------------------

@app.command(name="fix-status")
def fix_status() -> None:
    """Show open fix windows and fix knowledge base stats."""
    db.init_db()
    from ghost_pulse.rag.fix_tracker import get_open_windows, expire_stale_windows

    # Expire stale windows first
    expired = expire_stale_windows()
    open_wins = get_open_windows()
    stats = db.get_fix_stats()

    cfg = _get_cfg()
    from ghost_pulse.rag.embed_factory import get_embedding_provider
    embed = get_embedding_provider(cfg)
    embed_status = f"[green]● {embed.name}[/green]" if embed.is_available() else "[red]○ none[/red]"

    lines = [
        f"  [bold]Embedding provider:[/bold] {embed_status}\n",
        f"  [bold]Fix records:[/bold]  {stats['fix_records_total']} total  "
        f"({stats['fix_records_with_embedding']} with embeddings)\n",
        f"  [bold]Fix windows:[/bold]  {stats['windows_total']} total  "
        f"{stats['windows_resolved']} resolved  {stats['windows_open']} open",
    ]
    if stats.get("avg_fix_duration_ms"):
        avg_s = stats["avg_fix_duration_ms"] / 1000
        lines.append(f"  [bold]Avg fix time:[/bold] {avg_s:.0f}s")
    if expired:
        lines.append(f"\n  [dim]Expired {expired} stale window(s)[/dim]")

    if open_wins:
        lines.append(f"\n  [bold yellow]⏳ Open fix windows ({len(open_wins)}):[/bold yellow]")
        for w in open_wins:
            since = _days_ago(w.get("started_at", ""))
            pattern = w.get("error_hash", "?")[:16]
            # Look up pattern
            em_row = db.get_error_memory_by_hash(w.get("error_hash", ""))
            if em_row:
                pattern = (em_row.get("error_pattern") or "?")[:45]
            proj = w.get("project") or "?"
            cmds_count = len(w.get("commands_after", []))
            lines.append(
                f"  [dim]•[/dim] [yellow]{pattern}[/yellow]  "
                f"[dim]{proj} · {since} · {cmds_count} cmds tracked[/dim]"
            )
        lines.append(
            f"\n  [dim]Close with: [bold]ghost fix-done[/bold][/dim]"
        )
    else:
        lines.append("\n  [dim]No open fix windows.[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold blue]Ghost Pulse · Fix status[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )


if __name__ == "__main__":
    app()
