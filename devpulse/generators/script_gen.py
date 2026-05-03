"""Auto-generate bash aliases, Makefiles, or git aliases from toil patterns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devpulse.llm.base import LLMProvider, DEVPULSE_SYSTEM_PROMPT


_SCRIPT_PROMPT_TEMPLATE = """\
A developer runs this command sequence {count} times:

{commands}

Write a bash alias that automates it. Rules:
- Use the EXACT commands verbatim — never substitute or invent paths
- Chain with && so failure stops the sequence
- Name must accurately describe what the commands DO (e.g. if commands contain \
"bundleDevelopmentRelease" the name must reflect "dev", not "prod")
- One comment line above describing what it does
- Output ONLY: alias name='cmd1 && cmd2'  — no prose, no markdown, no function wrapper
"""


def generate_script(
    pattern: dict[str, Any],
    provider: LLMProvider,
    output_format: str = "auto",
) -> str:
    """Use LLM to generate an automation script for a toil pattern."""
    from devpulse.analyzers.toil import find_example_commands

    normalized = pattern.get("commands", [])
    count = pattern.get("count", 1)

    # Prefer real commands over normalized placeholders
    actual = find_example_commands(normalized)
    commands_to_show = actual if actual else normalized

    prompt = _SCRIPT_PROMPT_TEMPLATE.format(
        commands="\n".join(f"  {i+1}. {c}" for i, c in enumerate(commands_to_show)),
        count=count,
    )

    response = provider.analyze(prompt, system_prompt=DEVPULSE_SYSTEM_PROMPT)
    return response.content.strip()


def save_script(
    script: str,
    destination: str = "zshrc",
    project_path: Path | None = None,
) -> Path:
    """
    Persist the generated script.

    destination options:
      'zshrc'   → appends to ~/.zshrc
      'aliases' → appends to ~/.aliases
      'makefile' → appends to project Makefile
      'scripts'  → saves to ~/.devpulse/scripts/generated.sh
    """
    if destination == "zshrc":
        target = Path.home() / ".zshrc"
        with open(target, "a") as fh:
            fh.write(f"\n# DevPulse generated\n{script}\n")
        return target

    if destination == "aliases":
        target = Path.home() / ".aliases"
        target.touch(exist_ok=True)
        with open(target, "a") as fh:
            fh.write(f"\n# DevPulse generated\n{script}\n")
        return target

    if destination == "makefile" and project_path:
        target = project_path / "Makefile"
        with open(target, "a") as fh:
            fh.write(f"\n# DevPulse generated\n{script}\n")
        return target

    # Default: save to ~/.devpulse/scripts/
    scripts_dir = Path.home() / ".devpulse" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    # Count existing to avoid overwrites
    existing = len(list(scripts_dir.glob("generated_*.sh")))
    target = scripts_dir / f"generated_{existing + 1:03d}.sh"
    target.write_text(f"#!/bin/bash\n# DevPulse generated\n{script}\n")
    target.chmod(0o755)
    return target
