"""Install Ollama and pull default models during first-time setup (when LLM is Ollama)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from rich.console import Console

# Keep in sync with defaults in devpulse.config and OllamaProvider / embed_ollama
DEFAULT_LLM_MODEL = "llama3.1"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

_STARTUP_TIMEOUT_SEC = 45
_STARTUP_POLL_SEC = 1.0


def _ollama_host_from_config(cfg: dict[str, Any]) -> str:
    llm = cfg.get("llm") or {}
    ollama_cfg = llm.get("ollama") or {}
    host = (ollama_cfg.get("host") or "http://localhost:11434").rstrip("/")
    return host


def _api_tags_url(host: str) -> str:
    return f"{host}/api/tags"


def is_server_reachable(host: str) -> bool:
    try:
        r = httpx.get(_api_tags_url(host), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _parse_installed_models(tags_json: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for m in tags_json.get("models") or []:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        names.add(name)
        base = name.split(":", 1)[0]
        names.add(base)
    return names


def _model_present(installed: set[str], want: str) -> bool:
    if want in installed:
        return True
    for n in installed:
        if n.split(":", 1)[0] == want:
            return True
    return False


def _install_ollama(console: Console) -> bool:
    if sys.platform == "win32":
        winget = shutil.which("winget")
        if winget:
            console.print("[cyan]Installing Ollama via winget…[/cyan]")
            r = subprocess.run(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "Ollama.Ollama",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ],
            )
            return r.returncode == 0
        console.print(
            "[yellow]Install Ollama from https://ollama.com/download/windows[/yellow], "
            "then run [bold]devpulse init[/bold] again."
        )
        return False

    if shutil.which("curl"):
        cmd = "curl -fsSL https://ollama.com/install.sh | sh"
    elif shutil.which("wget"):
        cmd = "wget -qO- https://ollama.com/install.sh | sh"
    else:
        console.print(
            "[yellow]Could not find curl or wget. Install Ollama manually:[/yellow]\n"
            "  https://ollama.com/download"
        )
        return False

    console.print("[cyan]Installing Ollama (official installer)…[/cyan]")
    r = subprocess.run(cmd, shell=True)
    return r.returncode == 0


def _find_ollama_bin() -> str | None:
    return shutil.which("ollama")


def _maybe_refresh_path_after_install() -> None:
    """Best-effort: common install locations may not be on PATH in the same process."""
    extra = ("/usr/local/bin", "/opt/homebrew/bin")
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    for d in extra:
        if d not in parts and os.path.isdir(d):
            os.environ["PATH"] = d + os.pathsep + path
            path = os.environ["PATH"]


def _start_ollama_server(ollama_bin: str) -> subprocess.Popen[Any] | None:
    try:
        return subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None


def _wait_for_server(host: str, console: Console) -> bool:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if is_server_reachable(host):
            return True
        time.sleep(_STARTUP_POLL_SEC)
    console.print(
        f"[yellow]Ollama did not respond at {host} within {_STARTUP_TIMEOUT_SEC}s.[/yellow]\n"
        "  Start it manually ([bold]ollama serve[/bold] or the Ollama app), then run "
        "[bold]devpulse init[/bold] again if pulls did not complete."
    )
    return False


def ensure_server_running(console: Console, host: str) -> bool:
    if is_server_reachable(host):
        return True
    ollama_bin = _find_ollama_bin()
    if not ollama_bin:
        return False
    console.print("[dim]Starting local Ollama server…[/dim]")
    _start_ollama_server(ollama_bin)
    return _wait_for_server(host, console)


def pull_models(
    console: Console,
    host: str,
    models: tuple[str, ...],
) -> bool:
    if not is_server_reachable(host):
        return False
    try:
        r = httpx.get(_api_tags_url(host), timeout=10)
        r.raise_for_status()
        installed = _parse_installed_models(r.json())
    except Exception:
        installed = set()

    ollama_bin = _find_ollama_bin()
    if not ollama_bin:
        console.print("[yellow]ollama CLI not found on PATH after install — open a new terminal and run devpulse init again.[/yellow]")
        return False

    ok = True
    for model in models:
        if _model_present(installed, model):
            console.print(f"[green]✓[/green] Model already present: [bold]{model}[/bold]")
            continue
        console.print(f"[cyan]Pulling model [bold]{model}[/bold] (first time can take a while)…[/cyan]")
        pr = subprocess.run([ollama_bin, "pull", model])
        if pr.returncode != 0:
            console.print(f"[yellow]Pull failed for {model} — you can retry later: ollama pull {model}[/yellow]")
            ok = False
    return ok


def run_ollama_bootstrap(
    console: Console,
    cfg: dict[str, Any],
    *,
    skip: bool = False,
) -> None:
    """If the user relies on Ollama, install the binary, start the server, and pull models."""
    if skip:
        return
    if os.environ.get("DEVPULSE_SKIP_OLLAMA", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return

    llm = cfg.get("llm") or {}
    provider = (llm.get("provider") or "ollama").strip().lower()
    if provider != "ollama":
        return

    host = _ollama_host_from_config(cfg)
    parsed = urlparse(host)
    if parsed.hostname not in (None, "localhost", "127.0.0.1"):
        console.print(
            f"[dim]Skipping automatic Ollama install (non-local host: {host}).[/dim]"
        )
        return

    console.print("[bold]Local LLM (Ollama)[/bold]")

    if not _find_ollama_bin():
        if not _install_ollama(console):
            console.print("[yellow]Ollama install did not complete — AI features stay offline until Ollama is available.[/yellow]")
            return
        _maybe_refresh_path_after_install()

    if not _find_ollama_bin():
        console.print(
            "[yellow]Ollama was installed but [bold]ollama[/bold] is not on PATH in this shell. "
            "Open a new terminal and run [bold]devpulse init[/bold] again.[/yellow]"
        )
        return

    if not ensure_server_running(console, host):
        return

    models = (DEFAULT_LLM_MODEL, DEFAULT_EMBED_MODEL)
    pull_models(console, host, models)

    if is_server_reachable(host):
        try:
            r = httpx.get(_api_tags_url(host), timeout=5)
            if r.status_code == 200:
                inst = _parse_installed_models(r.json())
                if _model_present(inst, DEFAULT_LLM_MODEL):
                    console.print("[green]✓[/green] Ollama ready with default models")
        except Exception:
            pass
