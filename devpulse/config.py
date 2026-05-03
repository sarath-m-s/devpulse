"""Config management — reads/writes ~/.devpulse/config.toml."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

DEVPULSE_DIR = Path.home() / ".devpulse"
CONFIG_PATH = DEVPULSE_DIR / "config.toml"

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "data_retention_days": 90,
        "toil_threshold": 5,
        "idle_timeout_minutes": 15,
        "poll_interval_seconds": 30,
    },
    "projects": {
        "paths": [],
    },
    "llm": {
        "provider": "ollama",
        "model": "",
        "claude": {
            "api_key": "",
            "model": "claude-sonnet-4-20250514",
        },
        "openai": {
            "api_key": "",
            "model": "gpt-4o-mini",
            "base_url": "",
        },
        "ollama": {
            "host": "http://localhost:11434",
            "model": "llama3.1",
        },
        "groq": {
            "api_key": "",
            "model": "llama-3.1-70b-versatile",
        },
    },
    "collectors": {
        "shell": True,
        "git": True,
        "file_watcher": True,
        "window_tracker": False,
    },
    "ui": {
        "color_theme": "auto",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base recursively, returning new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config() -> dict[str, Any]:
    """Load config from disk, merging with defaults for missing keys."""
    if not CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "rb") as fh:
        try:
            on_disk = tomllib.load(fh)
        except Exception:
            return dict(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, on_disk)


def save_config(cfg: dict[str, Any]) -> None:
    """Write config to disk."""
    DEVPULSE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as fh:
        tomli_w.dump(cfg, fh)


def ensure_default_config() -> dict[str, Any]:
    """Write default config if none exists; return the loaded config."""
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    return load_config()


def set_config_value(cfg: dict[str, Any], key_path: str, value: str) -> None:
    """Set a dotted key path (e.g. 'llm.provider') to value in-place."""
    parts = key_path.split(".")
    node: Any = cfg
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]

    last = parts[-1]
    # Coerce to bool / int / float when appropriate
    if value.lower() in ("true", "false"):
        node[last] = value.lower() == "true"
    else:
        try:
            node[last] = int(value)
        except ValueError:
            try:
                node[last] = float(value)
            except ValueError:
                node[last] = value


def get_config_value(cfg: dict[str, Any], key_path: str) -> Any:
    """Get a dotted key path from config dict."""
    parts = key_path.split(".")
    node: Any = cfg
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def auto_detect_projects() -> list[str]:
    """Scan common locations for git repos and return their paths as strings."""
    candidates = [
        Path.home() / "work",
        Path.home() / "projects",
        Path.home() / "code",
        Path.home() / "src",
        Path.home(),
    ]
    found: list[str] = []
    for base in candidates:
        if not base.is_dir():
            continue
        try:
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / ".git").exists():
                    found.append(str(child))
                    if len(found) >= 20:
                        return found
        except PermissionError:
            continue
    return found
