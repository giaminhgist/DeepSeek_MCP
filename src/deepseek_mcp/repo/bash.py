"""Bash tool for the worker.

Registered only when ``tools.allow_bash`` is enabled. Commands run in a
fixed-argument subprocess (never ``shell=True``/``os.system``), with a
validated working directory, bounded output, a timeout, and with API-key
environment variables stripped from the child process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass

from deepseek_mcp.repo.guard import AccessPolicy, PathNotFoundError, RepoAccessError

logger = logging.getLogger("deepseek_mcp.bash")

# Secret-bearing variables removed from the child environment.
_SECRET_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)

_TRUNCATION_MARKER = "\n[bash output truncated: {n} chars omitted]\n"


@dataclass(slots=True)
class BashResult:
    exit_code: int | None
    output: str
    truncated: bool
    timed_out: bool


def _require_bash_enabled(allowed: bool) -> None:
    if not allowed:
        raise RepoAccessError("bash tool is disabled (tools.allow_bash: false)")


def _child_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _SECRET_ENV_VARS}
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _shell_command() -> list[str]:
    """Return the shell argv used to run a command string (cross-platform)."""
    if os.name == "nt":
        return ["cmd", "/c"]
    bash = shutil.which("bash")
    if bash:
        return [bash, "-c"]
    return ["sh", "-c"]


async def fs_bash(
    policy: AccessPolicy,
    command: str,
    *,
    cwd: str | None = None,
    timeout_ms: int | None = None,
    max_output_chars: int = 24000,
    bash_enabled: bool,
) -> BashResult:
    """Run a shell command in a bounded subprocess (Bash tool)."""
    _require_bash_enabled(bash_enabled)
    if not command or not command.strip():
        raise RepoAccessError("command must not be empty")
    if timeout_ms is None:
        timeout_ms = 60_000
    timeout_ms = max(timeout_ms, 100)

    if cwd:
        workdir = policy.check_any(cwd)
        if not workdir.is_dir():
            raise PathNotFoundError(f"not a directory: {cwd}")
    else:
        workdir = policy.repo_root

    argv = [*_shell_command(), command]
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=workdir,
            env=_child_env(),
        )
    except OSError as exc:
        raise RepoAccessError(f"failed to launch shell: {exc}") from exc

    timed_out = False
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_ms / 1000)
    except TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()
        stdout = b""
        exit_code = None
    else:
        exit_code = process.returncode

    output = stdout.decode("utf-8", errors="replace")
    truncated = len(output) > max_output_chars
    if truncated:
        output = output[:max_output_chars] + _TRUNCATION_MARKER.format(
            n=len(output) - max_output_chars
        )
    if timed_out:
        output += f"\n[bash command timed out after {timeout_ms} ms]\n"
    logger.info("fs_bash exit=%s cwd=%s timed_out=%s", exit_code, workdir, timed_out)
    return BashResult(exit_code=exit_code, output=output, truncated=truncated, timed_out=timed_out)
