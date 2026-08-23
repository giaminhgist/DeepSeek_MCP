#!/usr/bin/env python3
"""Optional real-DeepSeek API smoke test. NOT part of normal CI.

Runs only when DEEPSEEK_API_KEY is available. Delegates one tiny read-only
task to the real DeepSeek API against a small fixture repository, verifies a
non-empty answer and the usage footer, and prints the result.

Usage:
    DEEPSEEK_API_KEY=sk-... uv run python scripts/real_api_smoke.py [REPO_DIR]

Cost: one bounded worker run (a few API calls, ~a few cents at default
budgets). The run budgets in config/deepseek-worker.yaml still apply.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from deepseek_mcp.config.loader import ConfigError, load_config
from deepseek_mcp.deepseek.client import AnthropicDeepSeekClient
from deepseek_mcp.deepseek.system_prompt import build_system_prompt, build_task_message
from deepseek_mcp.deepseek.worker_loop import WorkerLoop
from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.guard import AccessPolicy
from deepseek_mcp.usage.footer import format_footer
from deepseek_mcp.usage.tracker import UsageTracker, new_run_id


def _fixture_repo() -> Path:
    """A tiny fixture repo (or a user-supplied one, left untouched)."""
    repo = Path(tempfile.mkdtemp(prefix="deepseek-smoke-"))
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "math.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        '    """Add two integers."""\n'
        "    return a + b\n"
        "\n"
        "def multiply(a: int, b: int) -> int:\n"
        '    """Multiply two integers."""\n'
        "    return a * b\n",
        encoding="utf-8",
    )
    (repo / "README.txt").write_text("Tiny math helpers.\n", encoding="utf-8")
    return repo


async def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print(
            "DEEPSEEK_API_KEY is not set; real API smoke test skipped.\n"
            "Set it and re-run:  DEEPSEEK_API_KEY=sk-... "
            "uv run python scripts/real_api_smoke.py",
            file=sys.stderr,
        )
        return 0
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _fixture_repo()
    if not repo.is_dir():
        print(f"not a directory: {repo}", file=sys.stderr)
        return 1

    policy = AccessPolicy(repo, config.repository)
    git = GitTools(policy, config.repository)
    tracker = UsageTracker(config.pricing)
    client = AnthropicDeepSeekClient(
        api_key=config.api_key or "",
        base_url=config.provider.base_url,
        timeout_ms=config.provider.request_timeout_ms,
    )
    loop = WorkerLoop(config, policy, client, tracker, git)
    run_id = new_run_id()
    task = build_task_message(
        "Read this repository and tell me what src/math.py does. Cite "
        "path:line evidence for the two functions.",
        focus_paths=["src/math.py"],
    )
    result = await loop.run(task, run_id=run_id, system=build_system_prompt(config))

    print(f"run_id: {run_id}")
    print(f"status: {result.status}")
    if result.reason:
        print(f"reason: {result.reason}")
    print(result.text)
    print(format_footer(result.usage))

    if not result.text.strip():
        print("SMOKE FAILED: empty answer", file=sys.stderr)
        return 1
    if result.usage.api_calls == 0:
        print("SMOKE FAILED: no billed API calls", file=sys.stderr)
        return 1
    print("\nSMOKE OK: non-empty answer and usage footer received.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
