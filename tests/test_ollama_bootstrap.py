"""Tests for Ollama first-run bootstrap."""

from unittest.mock import MagicMock, patch

import pytest

from devpulse.bootstrap.ollama_setup import (
    _model_present,
    _ollama_compare_key,
    _parse_installed_models,
    pull_models,
    run_ollama_bootstrap,
)


def test_ollama_compare_key():
    assert _ollama_compare_key("llama3.2:3b:latest") == "llama3.2:3b"
    assert _ollama_compare_key("llama3.2:3b") == "llama3.2:3b"
    assert _ollama_compare_key("llama3.1:latest") == "llama3.1"


def test_parse_installed_models():
    data = {"models": [{"name": "llama3.2:3b:latest"}, {"name": "nomic-embed-text"}]}
    names = _parse_installed_models(data)
    assert "llama3.2:3b:latest" in names
    assert "llama3.2:3b" in names
    assert "nomic-embed-text" in names


@pytest.mark.parametrize(
    "installed,want,expected",
    [
        ({"llama3.2:3b:latest"}, "llama3.2:3b", True),
        ({"nomic-embed-text"}, "nomic-embed-text", True),
        ({"other"}, "llama3.2:3b", False),
    ],
)
def test_model_present(installed, want, expected):
    assert _model_present(installed, want) is expected


def test_run_ollama_bootstrap_skip_flag():
    console = MagicMock()
    cfg = {"llm": {"provider": "ollama"}}
    with patch("devpulse.bootstrap.ollama_setup._install_ollama") as inst:
        run_ollama_bootstrap(console, cfg, skip=True)
        inst.assert_not_called()


def test_run_ollama_bootstrap_skips_non_ollama_provider():
    console = MagicMock()
    cfg = {"llm": {"provider": "claude"}}
    with patch("devpulse.bootstrap.ollama_setup._install_ollama") as inst:
        run_ollama_bootstrap(console, cfg, skip=False)
        inst.assert_not_called()


def test_pull_models_skips_when_already_present():
    console = MagicMock()
    with patch("devpulse.bootstrap.ollama_setup.is_server_reachable", return_value=True), patch(
        "devpulse.bootstrap.ollama_setup.httpx.get"
    ) as get, patch("devpulse.bootstrap.ollama_setup.shutil.which", return_value="/bin/ollama"), patch(
        "devpulse.bootstrap.ollama_setup.subprocess.run"
    ) as run:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {
            "models": [{"name": "llama3.2:3b:latest"}, {"name": "nomic-embed-text:latest"}]
        }
        ok = pull_models(console, "http://localhost:11434", ("llama3.2:3b", "nomic-embed-text"))
        assert ok is True
        run.assert_not_called()
