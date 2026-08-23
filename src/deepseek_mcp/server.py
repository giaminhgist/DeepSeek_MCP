"""MCP server assembly: FastMCP stdio server with the three Claude-facing tools."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field, ValidationError

from deepseek_mcp.config.loader import load_config
from deepseek_mcp.config.models import Config, OutputDetail
from deepseek_mcp.deepseek.client import AnthropicDeepSeekClient, DeepSeekClient
from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.guard import AccessPolicy
from deepseek_mcp.tools.common import WorkerInputError
from deepseek_mcp.tools.review import DeepSeekReviewInput, run_review
from deepseek_mcp.tools.task import DeepSeekTaskInput, run_task
from deepseek_mcp.tools.usage import DeepSeekUsageInput, usage_report
from deepseek_mcp.usage.tracker import UsageTracker, new_run_id

_SERVER_INSTRUCTIONS = """\
deepseek-worker delegates high-context repository execution to a DeepSeek
worker over its Anthropic-compatible API. Tools:
- deepseek_task: repository exploration, architecture tracing, evidence
  collection, debugging, and — when write/Bash tools are enabled — bounded
  implementation, targeted tests, and status/diff inspection.
- deepseek_review: first-pass review of working/staged/head diffs or named
  files, with severity/confidence findings and path:line evidence.
- deepseek_usage: process/last-run token usage and configured budgets. Free.

DeepSeek should perform most repository reading, implementation, and targeted
test execution. Its output remains ADVISORY: Claude owns planning, high-value
decisions, selective verification, approval, and the final answer.

Every worker response ends with a DeepSeek token usage footer. Repository and
filesystem tools are guarded by allowed roots and deny rules. fs_bash has a
validated working directory and bounded timeout/output, but is not a complete
filesystem sandbox."""

_DETAIL_VALUES = "brief | normal | detailed"
_REVIEW_SCOPE_VALUES = "working | staged | head | paths"
_REVIEW_FOCUS_VALUES = "correctness | security | performance | tests | maintainability"
_USAGE_SCOPE_VALUES = "last_run | process"


def default_client_factory(config: Config) -> DeepSeekClient:
    if not config.api_key:
        raise WorkerInputError(
            f"{config.provider.api_key_env} is not set; deepseek_task/"
            f"deepseek_review cannot run. deepseek_usage works without a key."
        )
    return AnthropicDeepSeekClient(
        api_key=config.api_key,
        base_url=config.provider.base_url,
        timeout_ms=config.provider.request_timeout_ms,
    )


class WorkerApp:
    """Shared worker state: config, usage tracker, lazy provider client."""

    def __init__(
        self,
        config: Config,
        *,
        client_factory: Callable[[Config], DeepSeekClient],
        run_id_factory: Callable[[], str],
    ) -> None:
        self.config = config
        self.tracker = UsageTracker(config.pricing)
        self._client_factory = client_factory
        self.run_id_factory = run_id_factory
        self._client: DeepSeekClient | None = None
        self.mcp = FastMCP("deepseek-worker", instructions=_SERVER_INSTRUCTIONS)

    def get_client(self) -> DeepSeekClient:
        if self._client is None:
            self._client = self._client_factory(self.config)
        return self._client

    async def _root_from_mcp_roots(self, ctx: Context | None) -> Path | None:
        """Use the MCP client's roots when unambiguous (exactly one file root)."""
        if ctx is None:
            return None
        try:
            session = getattr(ctx, "session", None)
        except Exception:
            session = None  # no live request context (e.g. in-memory calls)
        if session is None:
            return None
        list_roots = getattr(session, "list_roots", None)
        if list_roots is None:
            return None
        try:
            result = await list_roots()
        except Exception:
            return None
        roots = getattr(result, "roots", None) or []
        if len(roots) != 1:
            return None
        # mcp.types.Root.uri is validated as pydantic FileUrl, which is not a
        # str subclass; urlparse calls .decode() on non-str input. Coerce first.
        uri = str(getattr(roots[0], "uri", ""))
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        if parsed.netloc not in ("", "localhost"):
            return None
        return Path(unquote(parsed.path))

    async def resolve_policy(
        self, repo_root_arg: str | None, ctx: Context | None = None
    ) -> AccessPolicy:
        """Resolve the repository root: tool arg (if enabled) → MCP roots →
        env → cwd. All paths are canonicalized before use."""
        root: Path | None = None
        if repo_root_arg:
            if not self.config.repository.allow_repo_root_argument:
                raise WorkerInputError(
                    "repo_root argument is disabled by config "
                    "(repository.allow_repo_root_argument: false)"
                )
            root = Path(repo_root_arg).expanduser()
        if root is None:
            root = await self._root_from_mcp_roots(ctx)
        if root is None:
            env_root = os.environ.get(self.config.repository.root_env)
            if env_root:
                root = Path(env_root).expanduser()
        if root is None:
            root = Path.cwd()
        root = root.resolve()
        if not root.is_dir():
            raise WorkerInputError(f"repository root is not a directory: {root}")
        policy = AccessPolicy(root, self.config.repository)
        policy.add_extra_roots(self.config.tools.extra_allowed_roots)
        return policy

    def make_git(self, policy: AccessPolicy) -> GitTools:
        return GitTools(policy, self.config.repository)


def create_app(
    config: Config | None = None,
    *,
    client_factory: Callable[[Config], DeepSeekClient] | None = None,
    run_id_factory: Callable[[], str] | None = None,
) -> WorkerApp:
    """Build a WorkerApp with the three tools registered."""
    resolved_config = config or load_config()
    app = WorkerApp(
        resolved_config,
        client_factory=client_factory or default_client_factory,
        run_id_factory=run_id_factory or new_run_id,
    )
    mcp = app.mcp

    @mcp.tool(
          description=(
              "Delegate a high-context repository task to the DeepSeek worker. "
              "Use it for exploration, architecture tracing, evidence collection, "
              "debugging, and — when write/Bash tools are enabled — bounded "
              "implementation, targeted test execution, and status/diff inspection. "
              "The worker should complete the assigned repository work end to end "
              "when safe and supported. Output is advisory and ends with a DeepSeek "
              "token usage footer."
          )
    )
    async def deepseek_task(
        ctx: Context,
        task: Annotated[
            str,
            Field(
                min_length=1,
                max_length=20000,
                description=(
                    "The repository task to delegate. For code changes, request the "
                    "complete loop: inspect, implement, write/update targeted tests, run "
                    "checks, inspect status/diff, and report evidence. Keep the task "
                    "bounded, recoverable, and testable."
                ),
            ),
        ],
        focus_paths: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=100,
                description="Optional repository-relative paths to inspect "
                "first; the worker may follow evidence elsewhere.",
            ),
        ],
        output_detail: Annotated[
            OutputDetail | None,
            Field(
                default=None,
                description="Result compactness: brief, normal (default), or "
                f"detailed. One of: {_DETAIL_VALUES}.",
            ),
        ],
        repo_root: Annotated[
            str | None,
            Field(
                default=None,
                max_length=4000,
                description="Repository root override; only honored when "
                "repository.allow_repo_root_argument is true.",
            ),
        ],
    ) -> str:
        try:
            policy = await app.resolve_policy(repo_root, ctx)
            args = DeepSeekTaskInput(
                task=task,
                focus_paths=focus_paths or [],
                output_detail=output_detail,
                repo_root=repo_root,
            )
            return await run_task(
                config=app.config,
                policy=policy,
                git=app.make_git(policy),
                client=app.get_client(),
                tracker=app.tracker,
                run_id_factory=app.run_id_factory,
                args=args,
            )
        except WorkerInputError as exc:
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            raise ToolError(f"invalid deepseek_task arguments: {exc}") from exc

    @mcp.tool(
        description=(
            "First-pass code review by the DeepSeek worker of working/staged/"
            "head diffs or named files. Findings carry severity, confidence, "
            "and path:line evidence. Output is advisory — Claude does final "
            "review — and ends with a DeepSeek token usage footer."
        )
    )
    async def deepseek_review(
        ctx: Context,
        scope: Annotated[
            str,
            Field(
                default="working",
                description=f"Review scope. One of: {_REVIEW_SCOPE_VALUES}.",
            ),
        ],
        paths: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=100,
                description="Optional repository-relative path filters (diff "
                "scopes) or files under review (scope=paths).",
            ),
        ],
        review_focus: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=5,
                description=f"Focus areas. Subset of: {_REVIEW_FOCUS_VALUES}.",
            ),
        ],
        task: Annotated[
            str,
            Field(
                default="",
                max_length=20000,
                description="Optional additional review instruction.",
            ),
        ],
    ) -> str:
        try:
            policy = await app.resolve_policy(None, ctx)
            args = DeepSeekReviewInput(
                scope=scope,  # type: ignore[arg-type]
                paths=paths or [],
                review_focus=review_focus or [],  # type: ignore[arg-type]
                task=task,
            )
            return await run_review(
                config=app.config,
                policy=policy,
                git=app.make_git(policy),
                client=app.get_client(),
                tracker=app.tracker,
                run_id_factory=app.run_id_factory,
                args=args,
            )
        except WorkerInputError as exc:
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            raise ToolError(f"invalid deepseek_review arguments: {exc}") from exc

    @mcp.tool(
        description=(
            "Report DeepSeek worker usage statistics (last run or process-wide "
            "totals) plus configured budgets and pricing. Makes no DeepSeek "
            "API call and costs nothing."
        )
    )
    async def deepseek_usage(
        ctx: Context,
        scope: Annotated[
            str,
            Field(
                default="process",
                description=f"Which statistics to show. One of: {_USAGE_SCOPE_VALUES}.",
            ),
        ],
    ) -> str:
        del ctx  # usage reporting needs no request context
        args = DeepSeekUsageInput(scope=scope)  # type: ignore[arg-type]
        return usage_report(config=app.config, tracker=app.tracker, scope=args.scope)

    return app
