"""``deepseek_task`` — delegate a read-heavy analysis task to DeepSeek."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from deepseek_mcp.config.models import Config, OutputDetail
from deepseek_mcp.deepseek.client import DeepSeekClient
from deepseek_mcp.deepseek.system_prompt import build_system_prompt, build_task_message
from deepseek_mcp.deepseek.worker_loop import WorkerLoop
from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.guard import AccessPolicy
from deepseek_mcp.tools.common import WorkerInputError, compose_result
from deepseek_mcp.usage.tracker import UsageTracker


class DeepSeekTaskInput(BaseModel):
    task: str = Field(..., min_length=1, max_length=20000)
    focus_paths: list[str] = Field(default_factory=list, max_length=100)
    output_detail: OutputDetail | None = None
    repo_root: str | None = Field(None, max_length=4000)


async def run_task(
    *,
    config: Config,
    policy: AccessPolicy,
    git: GitTools,
    client: DeepSeekClient,
    tracker: UsageTracker,
    run_id_factory: Callable[[], str],
    args: DeepSeekTaskInput,
) -> str:
    """Run one deepseek_task invocation end to end."""
    for focus_path in args.focus_paths:
        resolved = policy.check_repo(focus_path)
        if not resolved.exists():
            raise WorkerInputError(f"focus path not found: {focus_path}")

    system = build_system_prompt(config)
    task_text = build_task_message(args.task, args.focus_paths or None)
    loop = WorkerLoop(config, policy, client, tracker, git)
    result = await loop.run(task_text, run_id=run_id_factory(), system=system)
    detail: OutputDetail = args.output_detail or config.worker.default_output_detail
    return compose_result(result, config, detail)
