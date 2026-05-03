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

    with console.status(f"[bold cyan]Analysing {days} days of activity with {provider.name}…[/bold cyan]"):
        result = generate_insights(provider, days=days)

    from rich.markdown import Markdown
    from rich.rule import Rule
    console.print()
    console.print(Rule(f"[bold blue]DevPulse Insights[/bold blue] · last {days} days", style="blue"))
    console.print(Panel(Markdown(result), border_style="blue", box=box.ROUNDED, padding=(1, 2)))
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
