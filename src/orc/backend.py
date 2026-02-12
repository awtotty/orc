"""Backend registry — pluggable coding agent CLI support."""

import os
from dataclasses import dataclass, field
from typing import Callable, Optional


def _claude_prompt_handler(prompt, cwd):
    """Inject prompt via --append-system-prompt flag."""
    escaped = prompt.replace("'", "'\\''")
    return f"--append-system-prompt $'{escaped}'"


def _codex_prompt_handler(prompt, cwd):
    """Write prompt to AGENTS.md in the worktree."""
    if cwd:
        with open(os.path.join(cwd, "AGENTS.md"), "w") as f:
            f.write(prompt)
    return ""


def _aider_prompt_handler(prompt, cwd):
    """Write prompt to a file and pass via --read flag."""
    if cwd:
        with open(os.path.join(cwd, ".orc-system-prompt.md"), "w") as f:
            f.write(prompt)
    return "--read .orc-system-prompt.md"


@dataclass
class Backend:
    name: str
    command: str
    model_flag: Optional[Callable[[str], str]] = None
    prompt_handler: Optional[Callable[[str, Optional[str]], str]] = None
    sandbox_flag: Optional[str] = None
    settings_files: list[str] = field(default_factory=list)

    def build_command(self, role_prompt="", model=None, cwd=None):
        """Build the full CLI command string."""
        parts = [self.command]
        if model and self.model_flag:
            parts.append(self.model_flag(model))
        if os.environ.get("ORC_SANDBOX") and self.sandbox_flag:
            parts.append(self.sandbox_flag)
        if role_prompt and self.prompt_handler:
            flag = self.prompt_handler(role_prompt, cwd)
            if flag:
                parts.append(flag)
        return " ".join(parts)


BACKENDS = {
    "claude": Backend(
        name="claude",
        command="claude",
        model_flag=lambda m: f"--model {m}",
        prompt_handler=_claude_prompt_handler,
        sandbox_flag="--dangerously-skip-permissions",
        settings_files=[".claude/settings.local.json"],
    ),
    "codex": Backend(
        name="codex",
        command="codex",
        model_flag=lambda m: f"--model {m}",
        prompt_handler=_codex_prompt_handler,
        sandbox_flag="--full-auto",
        settings_files=[],
    ),
    "aider": Backend(
        name="aider",
        command="aider",
        model_flag=lambda m: f"--model {m}",
        prompt_handler=_aider_prompt_handler,
        sandbox_flag=None,
        settings_files=[],
    ),
}


def get_backend(name):
    """Look up a backend by name. Returns None if not a built-in."""
    return BACKENDS.get(name)


def resolve_backend(agent_data, config):
    """Resolve backend from agent.json data and project config.

    Resolution order:
    1. agent.json "backend" field (per-room)
    2. config.toml [agent] backend (project-wide default)
    3. Fallback: "claude"
    """
    name = agent_data.get("backend")
    if not name:
        agent_cfg = config.get("agent", {})
        name = agent_cfg.get("backend", "claude")
    backend = get_backend(name)
    if backend is None:
        backend = Backend(name=name, command=name)
    return backend
