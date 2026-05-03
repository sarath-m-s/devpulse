"""DevPulse CLI — main entry point using Typer."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from devpulse import db
from devpulse.config import (
    auto_detect_projects,
    ensure_default_config,
    get_config_value,
    load_config,
    save_config,
    set_config_value,
    DEVPULSE_DIR,
)

app = typer.Typer(
    name="devpulse",
    help="Privacy-first developer productivity copilot.",
    add_completion=True,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _get_cfg() -> dict:
    return load_config()


# ---------------------------------------------------------------------------
# devpulse init
# ---------------------------------------------------------------------------

@app.command()
def init() -> None:
    """Initialize DevPulse: create config, detect projects, show hook instructions."""
    DEVPULSE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = ensure_default_config()

    # Auto-detect projects
    found = auto_detect_projects()
    existing = set(cfg["projects"].get("paths", []))
    new_paths = [p for p in found if p not in existing]
    if new_paths:
        cfg["projects"]["paths"] = list(existing) + new_paths
        save_config(cfg)
        console.print(f"[green]✓[/green] Detected {len(new_paths)} git repos")

    db.init_db()
    console.print("[green]✓[/green] Database initialized")
    console.print(f"[green]✓[/green] Config at {DEVPULSE_DIR / 'config.toml'}")

    # LLM probe
    from devpulse.llm.factory import get_provider
    provider = get_provider(cfg)
    if provider.name != "none":
        console.print(f"[green]✓[/green] LLM provider: [bold]{provider.name}[/bold]")
    else:
        console.print("[yellow]⚠[/yellow]  No LLM provider found — AI features disabled")
        console.print("    Run: devpulse config providers  to see options")

    console.print()
    console.print(Panel(
        "[bold]Shell hook installation[/bold]\n\n"
        "Add to [cyan]~/.zshrc[/cyan]:\n"
        '  [dim]source "$(devpulse shell-hook --zsh)"[/dim]\n\n'
        "Add to [cyan]~/.bashrc[/cyan]:\n"
        '  [dim]source "$(devpulse shell-hook --bash)"[/dim]\n\n'
        "Then restart your shell and run: [bold]devpulse start[/bold]",
        title="Next steps",
        box=box.ROUNDED,
    ))


# ---------------------------------------------------------------------------
# devpulse start / stop / status
# ---------------------------------------------------------------------------

@app.command()
def start() -> None:
    """Start the background collector daemon."""
    from devpulse.daemon import start_daemon
    start_daemon()


@app.command()
def stop() -> None:
    """Stop the background daemon."""
    from devpulse.daemon import stop_daemon
    stop_daemon()


@app.command()
def status() -> None:
    """Show daemon status and today's activity summary."""
    from devpulse.daemon import is_running, _read_pid

    running = is_running()
    pid = _read_pid()

    status_text = (
        f"[green]● Running[/green] (pid {pid})" if running else "[red]○ Stopped[/red]"
    )

    total_cmds = db.count_events_today()
    today_str = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

    from devpulse.analyzers.time_tracker import compute_time_per_project
    time_data = compute_time_per_project(since=today_str)
    active_project = "—"
    if time_data:
        active_project = max(time_data, key=lambda p: time_data[p]["total_minutes"])

    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    table.add_row("Daemon", status_text)
    table.add_row("Commands today", str(total_cmds))
    table.add_row("Active project", active_project)
    table.add_row("DB path", str(db.get_db_path()))

    console.print(Panel(table, title="[bold]DevPulse Status[/bold]", box=box.ROUNDED))


# ---------------------------------------------------------------------------
# devpulse log-cmd  (hidden — called by shell hooks)
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
    from devpulse.collectors.shell import log_command
    log_command(
        cmd=cmd,
        cwd=cwd,
        exit_code=exit_code,
        duration_ms=duration_ms,
        session_id=session or None,
    )


# ---------------------------------------------------------------------------
# devpulse shell-hook
# ---------------------------------------------------------------------------

@app.command(name="shell-hook")
def shell_hook(
    zsh: bool = typer.Option(False, "--zsh"),
    bash: bool = typer.Option(False, "--bash"),
) -> None:
    """Print path to shell hook file for sourcing."""
    hooks_dir = DEVPULSE_DIR / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write hook files if they don't exist or are stale
    _write_hooks(hooks_dir)

    if zsh:
        print(hooks_dir / "devpulse.zsh")
    elif bash:
        print(hooks_dir / "devpulse.bash")
    else:
        print(hooks_dir / "devpulse.zsh")


def _write_hooks(hooks_dir: Path) -> None:
    """Write shell hook files to the devpulse hooks directory."""
    zsh_hook = hooks_dir / "devpulse.zsh"
    bash_hook = hooks_dir / "devpulse.bash"

    import importlib.resources as pkg_resources
    shell_dir = Path(__file__).parent.parent / "shell"

    for src, dst in [(shell_dir / "devpulse.zsh", zsh_hook), (shell_dir / "devpulse.bash", bash_hook)]:
        if src.exists():
            dst.write_text(src.read_text())


# ---------------------------------------------------------------------------
# devpulse today
# ---------------------------------------------------------------------------

@app.command()
def today() -> None:
    """Show today's activity dashboard."""
    db.init_db()
    from devpulse.ui.dashboard import render_today
    render_today()


# ---------------------------------------------------------------------------
# devpulse week
# ---------------------------------------------------------------------------

@app.command()
def week() -> None:
    """Show weekly summary."""
    db.init_db()
    from devpulse.ui.dashboard import render_week
    render_week()


# ---------------------------------------------------------------------------
# devpulse toil
# ---------------------------------------------------------------------------

@app.command()
def toil(
    days: int = typer.Option(14, "--days", help="Days to analyse"),
    suggest: bool = typer.Option(False, "--suggest", help="Generate automation for top pattern"),
) -> None:
    """List detected toil patterns ranked by impact."""
    db.init_db()
    from devpulse.analyzers.toil import detect_toil, get_ranked_patterns, estimate_time_wasted

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
    console.print(f"\n[dim]Run [bold]devpulse suggest <id>[/bold] to generate automation[/dim]")

    if suggest and patterns:
        top = patterns[0]
        _do_suggest(top, cfg)


# ---------------------------------------------------------------------------
# devpulse suggest
# ---------------------------------------------------------------------------

@app.command()
def suggest(
    pattern_id: Optional[int] = typer.Argument(None, help="Toil pattern ID"),
) -> None:
    """Generate an automation script for a toil pattern using LLM."""
    db.init_db()
    cfg = _get_cfg()

    from devpulse.analyzers.toil import get_ranked_patterns
    from devpulse.llm.factory import get_provider

    patterns = get_ranked_patterns()
    if not patterns:
        console.print("[yellow]No toil patterns detected yet. Run: devpulse toil[/yellow]")
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
    from devpulse.llm.factory import get_provider
    from devpulse.generators.script_gen import generate_script, save_script

    provider = get_provider(cfg)
    if provider.name == "none":
        console.print("[yellow]No LLM provider configured. Run: devpulse config providers[/yellow]")
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
# devpulse next
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

    from devpulse.analyzers.workflow_predictor import WorkflowPredictor
    from devpulse.collectors.shell import _infer_project_from_cwd
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
                    "DevPulse needs 2+ occurrences of the same command sequence to predict.\n"
                    "Keep working and check back later![/dim]",
                    title=f"[bold blue]DevPulse · Next actions for {project}[/bold blue]",
                    border_style="blue",
                    box=box.ROUNDED,
                )
            )
        else:
            console.print(
                f"[yellow]No confident predictions based on your last commands in '{project}'.[/yellow]\n"
                f"[dim]Run [bold]devpulse next --list[/bold] to see all learned routines.[/dim]"
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
            title=f"[bold blue]DevPulse · Next actions for {project}[/bold blue]",
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
# devpulse recall
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

    from devpulse.analyzers.error_memory import ErrorMemory
    cfg = _get_cfg()
    from devpulse.llm.factory import get_provider
    em = ErrorMemory(llm_provider=get_provider(cfg) if cfg else None)

    if show_diff is not None:
        errors = em.get_frequent_errors(days=365, limit=1000)
        match = next((e for e in errors if e.get("id") == show_diff), None)
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
                title="[bold blue]DevPulse · Error recall[/bold blue]",
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
            title="[bold blue]DevPulse · Error recall[/bold blue]",
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
        console.print(
            Panel(
                "\n".join(detail_lines),
                title=f"[bold]Error #{e.get('id')} detail[/bold]",
                box=box.ROUNDED,
                border_style="green",
            )
        )


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
# devpulse resume
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
    from devpulse.llm.factory import get_provider
    from devpulse.analyzers.context_restorer import ContextRestorer

    provider = get_provider(cfg)
    restorer = ContextRestorer(llm_provider=provider if provider.name != "none" else None)

    if not project:
        # Show all projects with their last session
        projects = db.get_all_snapshot_projects()
        if not projects:
            console.print("[dim]No session snapshots yet. DevPulse will capture snapshots automatically as you work.[/dim]")
            return
        table = Table("Project", "Last session", "Branch", "Duration", box=box.ROUNDED)
        for proj in projects[:15]:
            snap = db.get_latest_snapshot(proj)
            if snap:
                ago = _days_ago(snap.get("snapshot_time", ""))
                branch = snap.get("branch") or "—"
                from devpulse.analyzers.context_restorer import _fmt_duration
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
            title=f"[bold blue]DevPulse · Resume {project}[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )

    if checkout and ctx.get("branch"):
        import subprocess
        from devpulse.analyzers.context_restorer import _find_project_path
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
# devpulse profile
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
    from devpulse.llm.factory import get_provider
    from devpulse.analyzers.developer_fingerprint import DeveloperFingerprint

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
        max_cmds = max((h["commands"] for h in hourly), default=1)
        peak_hours = energy.get("peak_hours", [])
        bar_lines = []
        for h in hourly:
            if h["commands"] == 0 and h["hour"] not in peak_hours:
                continue
            filled = round(h["commands"] / max(max_cmds, 1) * 21)
            bar = "█" * filled + "░" * (21 - filled)
            label = "⚡ peak" if h["hour"] in peak_hours else ""
            bar_lines.append(
                f"  {h['hour']:02d}  [cyan]{bar}[/cyan]  [dim]{label}[/dim]"
            )
        sections.append(
            f"  [bold cyan]⚡ Energy map[/bold cyan]\n"
            + "\n".join(bar_lines[:12])
            + f"\n  Best day: [green]{energy.get('best_day', '?')}[/green]  │  "
            + f"Low energy: [red]{energy.get('low_energy_hours', [])}[/red]"
        )

    if focus:
        trend_icon = {"improving": "↑", "worsening": "↓", "stable": "→"}.get(
            focus.get("trend", "stable"), "→"
        )
        trend_color = {"improving": "green", "worsening": "red", "stable": "yellow"}.get(
            focus.get("trend", "stable"), "yellow"
        )
        distractors = ", ".join(focus.get("top_distractors", [])[:3]) or "none"
        sections.append(
            f"  [bold cyan]🎯 Focus pattern[/bold cyan]\n"
            f"  Avg focus block: [white]{focus.get('avg_focus_block_min', 0)} min[/white]  │  "
            f"Longest: [white]{focus.get('longest_focus_block_min', 0)} min[/white]\n"
            f"  Best focus day: [green]{focus.get('best_focus_day', '?')}[/green]  │  "
            f"Trend: [{trend_color}]{trend_icon} {focus.get('trend', 'stable')}[/{trend_color}]\n"
            f"  Top distractors: [yellow]{distractors}[/yellow]"
        )

    if not sections:
        console.print("[dim]No profile data available. Run: devpulse profile --regenerate[/dim]")
        return

    content = ("\n\n" + "  " + "─" * 55 + "\n\n").join(sections)
    console.print(
        Panel(
            content,
            title="[bold blue]DevPulse · Developer profile[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )


# ---------------------------------------------------------------------------
# devpulse focus
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
    from devpulse.analyzers.context_switch import compute_context_switches
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
            end_str = (s.get("ended_at") or "now")[:16].replace("T", " ")
            dur = s.get("duration_minutes")
            dur_str = f"{int(dur or 0)//60}h {int(dur or 0)%60:02d}m"
            active = "[bold green]active 🟢[/bold green]" if not s.get("ended_at") else ""
            score = s.get("quality_score") or 0
            score_c = "green" if score >= 70 else "yellow"
            bar_len = round(score / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            lines.append(
                f"  [dim]{start_str} - {end_str}[/dim]  "
                f"[cyan]{s.get('project', '?'):<14}[/cyan]  "
                f"[white]{dur_str}[/white]  "
                f"[{score_c}]{bar}[/{score_c}]  "
                f"{active or f'score: {score:.0f}'}"
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
            title="[bold blue]DevPulse · Focus today[/bold blue]",
            border_style="blue",
            box=box.ROUNDED,
        )
    )


# ---------------------------------------------------------------------------
# devpulse insights
# ---------------------------------------------------------------------------

@app.command()
def insights(
    days: int = typer.Option(7, "--days", help="Days of history to analyse"),
) -> None:
    """Get personalized productivity insights powered by LLM."""
    db.init_db()
    cfg = _get_cfg()

    from devpulse.llm.factory import get_provider
    from devpulse.generators.report_gen import generate_insights

    provider = get_provider(cfg)
    if provider.name == "none":
        console.print("[yellow]No LLM provider configured. Run: devpulse config providers[/yellow]")
        raise typer.Exit(1)

    from rich.markdown import Markdown
    from rich.rule import Rule

    with console.status(f"[bold cyan]Analysing {days} days of activity with {provider.name}…[/bold cyan]"):
        # v1 activity insights
        result = generate_insights(provider, days=days)

        # v2 developer fingerprint insights
        v2_result = ""
        try:
            from devpulse.analyzers.developer_fingerprint import DeveloperFingerprint
            fp = DeveloperFingerprint(llm_provider=provider)
            v2_result = fp.generate_insights(days=days)
        except Exception:
            pass

        # v2 error patterns
        error_summary = ""
        try:
            from devpulse.analyzers.error_memory import ErrorMemory
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
    console.print(Rule(f"[bold blue]DevPulse Insights[/bold blue] · last {days} days", style="blue"))
    console.print(Panel(Markdown(result), border_style="blue", box=box.ROUNDED, padding=(1, 2)))
    if v2_result and v2_result != result:
        console.print(Panel(Markdown(v2_result), title="[bold]Energy & Focus Patterns[/bold]", border_style="cyan", box=box.ROUNDED, padding=(1, 2)))
    if error_summary:
        console.print(Panel(Markdown(error_summary), title="[bold]Error patterns[/bold]", border_style="red", box=box.ROUNDED, padding=(1, 2)))
    console.print(Rule(style="dim"))


# ---------------------------------------------------------------------------
# devpulse config
# ---------------------------------------------------------------------------

config_app = typer.Typer(help="Manage DevPulse configuration.")
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
    """Set a config value (e.g. devpulse config set llm.provider ollama)."""
    cfg = _get_cfg()
    set_config_value(cfg, key, value)
    save_config(cfg)
    console.print(f"[green]✓[/green] Set {key} = {value}")


@config_app.command(name="providers")
def config_providers() -> None:
    """Test all LLM providers and show which are available."""
    cfg = _get_cfg()
    from devpulse.llm.factory import probe_all

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
# devpulse projects
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

        from devpulse.analyzers.time_tracker import compute_time_per_project
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        time_data = compute_time_per_project(since=week_ago)

        table = Table("Project", "Path", "7d hours", "Last active", box=box.ROUNDED)
        for p in paths:
            proj_name = Path(p).name
            stats = time_data.get(proj_name, {})
            hours = f"{stats.get('total_minutes', 0)/60:.1f}h"
            table.add_row(proj_name, str(p), hours, "—")
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
# devpulse export
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
        default_name = Path(f"devpulse_export_{from_date}_{to_date}{suffix}")
        default_name.write_text(content)
        console.print(f"[green]✓[/green] Exported {len(events)} events to {default_name}")


# ---------------------------------------------------------------------------
# devpulse reset
# ---------------------------------------------------------------------------

@app.command()
def reset(keep_config: bool = typer.Option(False, "--keep-config")) -> None:
    """Clear all collected data (with confirmation)."""
    confirmed = typer.confirm(
        "This will permanently delete all DevPulse data. Continue?", default=False
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
        from devpulse.config import CONFIG_PATH
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            console.print("[green]✓[/green] Config deleted")
    else:
        console.print("[dim]Config preserved.[/dim]")


# ---------------------------------------------------------------------------
# devpulse backfill
# ---------------------------------------------------------------------------

@app.command()
def backfill(
    shell: str = typer.Option("auto", "--shell", help="Shell type: auto, zsh, bash"),
    limit: int = typer.Option(5000, "--limit", help="Max commands to import"),
) -> None:
    """Import shell history as a one-time backfill."""
    db.init_db()
    from devpulse.collectors.shell import backfill_from_history
    with console.status("Importing history…"):
        n = backfill_from_history(shell=shell, limit=limit)
    console.print(f"[green]✓[/green] Imported {n} commands from shell history")


# ---------------------------------------------------------------------------
# devpulse web
# ---------------------------------------------------------------------------

@app.command()
def web(
    port: int = typer.Option(8765, "--port", "-p", help="Port to listen on"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't open browser automatically"),
) -> None:
    """Start the DevPulse web UI (no extra dependencies required)."""
    from devpulse.web.server import run
    import threading, webbrowser, time

    url = f"http://localhost:{port}"
    console.print(f"[green]✓[/green] DevPulse UI → [bold cyan]{url}[/bold cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    if not no_open:
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    run(port=port)


if __name__ == "__main__":
    app()
