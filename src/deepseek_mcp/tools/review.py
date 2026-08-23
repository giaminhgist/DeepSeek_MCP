"""``deepseek_review`` — first-pass code/diff review by DeepSeek."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from deepseek_mcp.compaction.tool_results import bound_text
from deepseek_mcp.config.models import Config
from deepseek_mcp.deepseek.client import DeepSeekClient
from deepseek_mcp.deepseek.system_prompt import build_review_message, build_system_prompt
from deepseek_mcp.deepseek.worker_loop import WorkerLoop
from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.guard import AccessPolicy
from deepseek_mcp.tools.common import WorkerInputError, compose_result
from deepseek_mcp.usage.footer import format_footer
from deepseek_mcp.usage.tracker import UsageTracker

ReviewFocus = Literal["correctness", "security", "performance", "tests", "maintainability"]
REVIEW_FOCUS_VALUES: tuple[ReviewFocus, ...] = (
    "correctness",
    "security",
    "performance",
    "tests",
    "maintainability",
)


class DeepSeekReviewInput(BaseModel):
    scope: Literal["working", "staged", "head", "paths"] = "working"
    paths: list[str] = Field(default_factory=list, max_length=100)
    review_focus: list[ReviewFocus] = Field(default_factory=list, max_length=5)
    task: str = Field("", max_length=20000)


def _validate_paths(policy: AccessPolicy, paths: list[str]) -> None:
    for rel in paths:
        resolved = policy.check_repo(rel)
        if not resolved.exists():
            raise WorkerInputError(f"review path not found: {rel}")


async def run_review(
    *,
    config: Config,
    policy: AccessPolicy,
    git: GitTools,
    client: DeepSeekClient,
    tracker: UsageTracker,
    run_id_factory: Callable[[], str],
    args: DeepSeekReviewInput,
) -> str:
    """Run one deepseek_review invocation end to end."""
    diff_text = ""
    if args.scope in ("working", "staged", "head"):
        _validate_paths(policy, args.paths)
        raw_diff = await git.diff(args.scope, args.paths or None)
        diff_text = bound_text(raw_diff, config.compaction.max_tool_result_chars)
        if not diff_text.strip():
            usage = tracker.start_run(run_id_factory(), config.model.name)
            tracker.finish(usage, "ok")
            return (
                f"No changes found for review scope: {args.scope}" + "\n\n" + format_footer(usage)
            )
    else:  # scope == "paths"
        if not args.paths:
            raise WorkerInputError("paths must not be empty when scope='paths'")
        _validate_paths(policy, args.paths)

    focus = list(args.review_focus) or list(REVIEW_FOCUS_VALUES)
    message = build_review_message(
        scope=args.scope,
        review_focus=focus,
        extra_task=args.task,
        diff_text=diff_text,
    )
    if args.scope == "paths":
        message += "\n\nFiles under review:\n" + "\n".join(f"- {path}" for path in args.paths)

    system = build_system_prompt(config)
    loop = WorkerLoop(config, policy, client, tracker, git)
    result = await loop.run(message, run_id=run_id_factory(), system=system)
    # Review output always uses the normal detail target; findings are
    # severity/confidence-sorted by the deterministic compactor.
    return compose_result(result, config, "normal", sort_findings=True)
