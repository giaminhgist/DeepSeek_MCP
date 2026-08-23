"""Internal DeepSeek agent loop with budgets, tool execution, and compaction.

The server — not the model — enforces every safety and budget control here:
iteration caps, run timeouts, token/cost/API-call budgets, context soft/hard
limits, tool-result bounding, and rolling working-memory compaction.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from deepseek_mcp.compaction.tool_results import (
    bound_text,
    format_bash_result,
    format_diff_result,
    format_glob_result,
    format_grep_result,
    format_list_result,
    format_read_result,
    format_search_result,
    format_stat_result,
)
from deepseek_mcp.compaction.working_memory import WorkingMemory, estimate_tokens
from deepseek_mcp.config.models import Config
from deepseek_mcp.deepseek.client import (
    DeepSeekClient,
    DeepSeekProviderError,
    ToolCallRequest,
    WorkerTurnRequest,
)
from deepseek_mcp.repo import (
    AccessPolicy,
    RepoAccessError,
)
from deepseek_mcp.repo.bash import fs_bash
from deepseek_mcp.repo.fs import fs_glob, fs_grep, fs_read
from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.listing import list_directory
from deepseek_mcp.repo.reader import read_file, stat_file
from deepseek_mcp.repo.search import search_repo
from deepseek_mcp.repo.writes import fs_edit, fs_notebook_edit, fs_write
from deepseek_mcp.usage.budget import Budget
from deepseek_mcp.usage.tracker import RunUsage, UsageTracker

logger = logging.getLogger("deepseek_mcp.worker")

RunStatus = Literal["ok", "stopped", "error"]


@dataclass(slots=True)
class WorkerResult:
    status: RunStatus
    text: str
    reason: str | None
    usage: RunUsage
    memory: WorkingMemory
    transcript: list[str] = field(default_factory=list)
    tool_calls: int = 0


class ToolSpec:
    """One internal worker tool: schema + typed handler.

    ``handler`` is dynamically dispatched with an already-validated instance
    of ``args_model``, so the handler parameter is typed loosely on purpose.
    """

    def __init__(
        self,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: Callable[[Any], Awaitable[str]],
    ) -> None:
        self.name = name
        self.description = description
        self.args_model = args_model
        self.handler = handler

    def api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
        }


# --- Internal tool argument models ----------------------------------------


class RepoListArgs(BaseModel):
    path: str = Field("", max_length=2000)
    offset: int = Field(0, ge=0, le=1_000_000)
    limit: int = Field(200, ge=1, le=1000)


class RepoSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    case_sensitive: bool = True
    regex: bool = False
    max_matches: int = Field(50, ge=1, le=5000)


class RepoReadArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000)
    start_line: int = Field(1, ge=1, le=10_000_000)
    end_line: int | None = Field(None, ge=1, le=10_000_000)


class RepoStatArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=2000)


class GitDiffArgs(BaseModel):
    mode: Literal["working", "staged", "head"] = "working"
    path: str = Field("", max_length=2000)


class GitStatusArgs(BaseModel):
    pass


class GitShowArgs(BaseModel):
    rev: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1, max_length=2000)


class FsReadArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=4000)
    start_line: int = Field(1, ge=1, le=10_000_000)
    end_line: int | None = Field(None, ge=1, le=10_000_000)


class FsGlobArgs(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=2000)
    path: str = Field("", max_length=4000)
    max_results: int = Field(200, ge=1, le=5000)


class FsGrepArgs(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=4000)
    path: str = Field("", max_length=4000)
    regex: bool = False
    case_sensitive: bool = True
    max_matches: int = Field(50, ge=1, le=5000)


class FsWriteArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=4000)
    content: str = Field(..., max_length=2_000_000)


class FsEditArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=4000)
    old_string: str = Field(..., min_length=1, max_length=200_000)
    new_string: str = Field(..., max_length=200_000)
    replace_all: bool = False


class FsNotebookEditArgs(BaseModel):
    path: str = Field(..., min_length=1, max_length=4000)
    edit_mode: Literal["replace", "insert", "delete"]
    new_source: str = Field(..., max_length=2_000_000)
    cell_id: str | None = Field(None, max_length=200)
    cell_type: Literal["code", "markdown"] = "code"


class FsBashArgs(BaseModel):
    command: str = Field(..., min_length=1, max_length=200_000)
    cwd: str = Field("", max_length=4000)
    timeout_ms: int | None = Field(None, ge=1000, le=600_000)


# --- Tool registry ----------------------------------------------------------


def build_internal_tools(
    policy: AccessPolicy,
    config: Config,
    git: GitTools,
    fs_handlers: dict[str, Callable[[Any], Awaitable[str]]] | None = None,
) -> dict[str, ToolSpec]:
    """Assemble the enabled internal toolset as an Anthropic-format registry."""
    repo_cfg = config.repository
    max_result = config.compaction.max_tool_result_chars
    fs_handlers = fs_handlers or {}

    async def repo_list(args: RepoListArgs) -> str:
        result = list_directory(policy, repo_cfg, args.path, offset=args.offset, limit=args.limit)
        return format_list_result(result, max_result)

    async def repo_search(args: RepoSearchArgs) -> str:
        result = search_repo(
            policy,
            repo_cfg,
            args.query,
            max_matches=args.max_matches,
            case_sensitive=args.case_sensitive,
            regex=args.regex,
        )
        return format_search_result(result, max_result)

    async def repo_read(args: RepoReadArgs) -> str:
        result = read_file(
            policy,
            repo_cfg,
            args.path,
            start_line=args.start_line,
            end_line=args.end_line,
        )
        return format_read_result(result, max_result)

    async def repo_stat(args: RepoStatArgs) -> str:
        return format_stat_result(stat_file(policy, args.path))

    async def git_diff(args: GitDiffArgs) -> str:
        text = await git.diff(args.mode, [args.path] if args.path else None)
        return format_diff_result(text, max_result)

    async def git_status(args: GitStatusArgs) -> str:
        return format_diff_result(await git.status(), max_result)

    async def git_show(args: GitShowArgs) -> str:
        return format_diff_result(await git.show(args.rev, args.path), max_result)

    tools: dict[str, ToolSpec] = {
        "repo_list": ToolSpec(
            "repo_list",
            "List files and directories under the repository root (bounded, "
            "deny/gitignore filtered). Returns relative paths, kinds, sizes, "
            "and paging info.",
            RepoListArgs,
            repo_list,
        ),
        "repo_search": ToolSpec(
            "repo_search",
            "Search repository text files for a literal substring (or regex "
            "when regex=true). Returns path:line matches, bounded.",
            RepoSearchArgs,
            repo_search,
        ),
        "repo_read": ToolSpec(
            "repo_read",
            "Read a bounded window of a repository text file with line "
            "numbers. Binary, oversized, denied, and ignored files are "
            "rejected.",
            RepoReadArgs,
            repo_read,
        ),
        "repo_stat": ToolSpec(
            "repo_stat",
            "Return size/kind/mtime metadata for a repository path.",
            RepoStatArgs,
            repo_stat,
        ),
        "git_diff": ToolSpec(
            "git_diff",
            "Return a bounded unified diff: mode=working (unstaged), "
            "mode=staged (cached), mode=head (vs HEAD). Optional path filter.",
            GitDiffArgs,
            git_diff,
        ),
        "git_status": ToolSpec(
            "git_status",
            "Return short git status (porcelain).",
            GitStatusArgs,
            git_status,
        ),
        "git_show": ToolSpec(
            "git_show",
            "Return a repository file's content at a safe revision/ref "
            "(HEAD, HEAD~n, commit hex, branch/tag name).",
            GitShowArgs,
            git_show,
        ),
    }

    if config.tools.allow_file_tools:
        tools["fs_read"] = ToolSpec(
            "fs_read",
            "Read a bounded window of any text file inside an allowed root "
            "(absolute path allowed). Binary/oversized/denied files rejected.",
            FsReadArgs,
            fs_handlers["fs_read"],
        )
        tools["fs_glob"] = ToolSpec(
            "fs_glob",
            "Glob for files under an allowed root using a relative pattern (** supported).",
            FsGlobArgs,
            fs_handlers["fs_glob"],
        )
        tools["fs_grep"] = ToolSpec(
            "fs_grep",
            "Search files under an allowed root (or a single file) for a "
            "literal or regex pattern; returns path:line matches, bounded.",
            FsGrepArgs,
            fs_handlers["fs_grep"],
        )
    if config.tools.allow_writes:
        tools["fs_write"] = ToolSpec(
            "fs_write",
            "Create or overwrite a file inside an allowed root (never outside "
            "it; deny globs apply). Use sparingly and report what you wrote.",
            FsWriteArgs,
            fs_handlers["fs_write"],
        )
        tools["fs_edit"] = ToolSpec(
            "fs_edit",
            "Replace a unique old_string with new_string in an existing file "
            "inside an allowed root. Use replace_all=true only deliberately.",
            FsEditArgs,
            fs_handlers["fs_edit"],
        )
        tools["fs_notebook_edit"] = ToolSpec(
            "fs_notebook_edit",
            "Edit one cell of a Jupyter notebook (replace/insert/delete by "
            "cell id) inside an allowed root.",
            FsNotebookEditArgs,
            fs_handlers["fs_notebook_edit"],
        )
    if config.tools.allow_bash:
        tools["fs_bash"] = ToolSpec(
            "fs_bash",
            "Run a shell command in a bounded subprocess under an allowed "
            "root. Timeout and output are bounded; API keys are stripped "
            "from the child environment. NEVER run destructive or "
            "irreversible commands; prefer read-only commands.",
            FsBashArgs,
            fs_handlers["fs_bash"],
        )
    return tools


def build_fs_handlers(
    policy: AccessPolicy, config: Config
) -> dict[str, Callable[[Any], Awaitable[str]]]:
    """Closures over policy/config for the fs_* tool handlers."""
    repo_cfg = config.repository
    max_result = config.compaction.max_tool_result_chars

    async def read_fs(args: FsReadArgs) -> str:
        result = fs_read(
            policy, repo_cfg, args.path, start_line=args.start_line, end_line=args.end_line
        )
        return format_read_result(result, max_result)

    async def glob_fs(args: FsGlobArgs) -> str:
        result = fs_glob(
            policy, repo_cfg, args.pattern, path=args.path, max_results=args.max_results
        )
        return format_glob_result(result, max_result)

    async def grep_fs(args: FsGrepArgs) -> str:
        result = fs_grep(
            policy,
            repo_cfg,
            args.pattern,
            path=args.path,
            regex=args.regex,
            case_sensitive=args.case_sensitive,
            max_matches=args.max_matches,
        )
        return format_grep_result(result, max_result)

    async def write_fs(args: FsWriteArgs) -> str:
        outcome = fs_write(
            policy,
            repo_cfg,
            args.path,
            args.content,
            writes_enabled=config.tools.allow_writes,
        )
        return (
            f"[fs_write] wrote {outcome['bytes_written']} bytes to "
            f"{outcome['path']} — Claude must review this change."
        )

    async def edit_fs(args: FsEditArgs) -> str:
        outcome = fs_edit(
            policy,
            repo_cfg,
            args.path,
            args.old_string,
            args.new_string,
            replace_all=args.replace_all,
            writes_enabled=config.tools.allow_writes,
        )
        return (
            f"[fs_edit] replaced {outcome['occurrences_replaced']} occurrence(s) "
            f"in {outcome['path']} — Claude must review this change."
        )

    async def notebook_edit_fs(args: FsNotebookEditArgs) -> str:
        outcome = fs_notebook_edit(
            policy,
            repo_cfg,
            args.path,
            args.edit_mode,
            args.new_source,
            cell_id=args.cell_id,
            cell_type=args.cell_type,
            writes_enabled=config.tools.allow_writes,
        )
        return (
            f"[fs_notebook_edit] {args.edit_mode} applied to {outcome['path']} "
            f"({outcome['cells']} cells) — Claude must review this change."
        )

    async def bash_fs(args: FsBashArgs) -> str:
        result = await fs_bash(
            policy,
            args.command,
            cwd=args.cwd or None,
            timeout_ms=args.timeout_ms or config.tools.bash_timeout_ms,
            max_output_chars=config.tools.max_bash_output_chars,
            bash_enabled=config.tools.allow_bash,
        )
        return format_bash_result(result, max_result)

    return {
        "fs_read": read_fs,
        "fs_glob": glob_fs,
        "fs_grep": grep_fs,
        "fs_write": write_fs,
        "fs_edit": edit_fs,
        "fs_notebook_edit": notebook_edit_fs,
        "fs_bash": bash_fs,
    }


# --- Worker loop -------------------------------------------------------------


def _messages_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        else:
            for block in content:
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "tool_result":
                    parts.append(str(block.get("content", "")))
                elif block.get("type") == "tool_use":
                    parts.append(f"{block.get('name', '')} {block.get('input', '')}")
    return "\n".join(parts)


class WorkerLoop:
    def __init__(
        self,
        config: Config,
        policy: AccessPolicy,
        client: DeepSeekClient,
        tracker: UsageTracker,
        git: GitTools,
    ) -> None:
        self.config = config
        self.policy = policy
        self.client = client
        self.tracker = tracker
        self.git = git
        self.budget = Budget(config.budget)
        self.tools = build_internal_tools(
            policy, config, git, fs_handlers=build_fs_handlers(policy, config)
        )

    async def _execute_tool(self, call: ToolCallRequest) -> str:
        spec = self.tools.get(call.name)
        if spec is None:
            return (
                f"[tool error] unknown tool {call.name!r}; available tools: "
                f"{', '.join(sorted(self.tools))}"
            )
        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return f"[tool error] invalid arguments for {call.name}: {exc}"
        try:
            return await spec.handler(args)
        except (RepoAccessError, ValueError, OSError, TypeError) as exc:
            return f"[tool error] {call.name}: {exc}"
        except Exception as exc:
            logger.exception("internal tool %s crashed", call.name)
            return f"[tool error] {call.name} crashed: {type(exc).__name__}"

    def _context_tokens(self, system: str, messages: list[dict[str, Any]]) -> int:
        return estimate_tokens(system + "\n" + _messages_text(messages))

    def _compact_history(
        self, messages: list[dict[str, Any]], memory: WorkingMemory
    ) -> list[dict[str, Any]]:
        keep = self.config.compaction.preserve_recent_messages
        recent = messages[-keep:] if keep > 0 else []
        soft = self.config.compaction.worker_context_soft_limit_tokens
        memory_cap = max(4000, soft * 3 // 2)
        memory_message: dict[str, Any] = {
            "role": "user",
            "content": "[Working memory]\n" + memory.bounded_render(memory_cap),
        }
        return [memory_message, *recent]

    def _partial_text(self, memory: WorkingMemory, reason: str, *, last_text: str = "") -> str:
        soft = self.config.compaction.worker_context_soft_limit_tokens
        memory_cap = max(4000, soft * 3 // 2)
        parts = [
            "[Partial result: the run stopped before a final answer]",
            f"Reason: {reason}",
        ]
        if last_text:
            parts.append("Last worker text:\n" + bound_text(last_text, 2000))
        parts.append(memory.bounded_render(memory_cap))
        return "\n\n".join(parts)

    async def run(
        self,
        task_text: str,
        *,
        run_id: str,
        system: str,
    ) -> WorkerResult:
        """Run the internal agent loop until a final answer or a stop limit."""
        comp = self.config.compaction
        usage = self.tracker.start_run(run_id, self.config.model.name)
        memory = WorkingMemory(objective=task_text[:2000])
        messages: list[dict[str, Any]] = [{"role": "user", "content": task_text}]
        tool_defs = [spec.api_schema() for spec in self.tools.values()]
        transcript: list[str] = []
        tool_calls_total = 0
        deadline = asyncio.get_running_loop().time() + self.config.worker.max_run_seconds

        def stop_result(status: RunStatus, reason: str, *, text: str | None = None) -> WorkerResult:
            if text is None:
                text = self._partial_text(memory, reason)
            self.tracker.finish(usage, "ok" if status == "ok" else "stopped", reason)
            return WorkerResult(
                status=status,
                text=text,
                reason=reason,
                usage=usage,
                memory=memory,
                transcript=transcript,
                tool_calls=tool_calls_total,
            )

        while True:
            decision = self.budget.check(usage)
            if not decision.allowed:
                return stop_result("stopped", f"budget stop: {decision.reason}")
            if usage.api_calls >= self.config.worker.max_agent_iterations:
                return stop_result(
                    "stopped",
                    f"iteration limit reached ({self.config.worker.max_agent_iterations})",
                )
            if (
                comp.enabled
                and self._context_tokens(system, messages) >= comp.worker_context_hard_limit_tokens
            ):
                return stop_result("stopped", "worker context hard limit reached")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return stop_result("stopped", "run timeout reached")

            try:
                async with asyncio.timeout(max(remaining, 0.5)):
                    response = await self.client.run_turn(
                        WorkerTurnRequest(
                            model=self.config.model.name,
                            system=system,
                            messages=messages,
                            tools=tool_defs,
                            max_tokens=self.config.model.max_output_tokens_per_call,
                            temperature=self.config.model.temperature,
                            timeout_s=self.config.provider.request_timeout_ms / 1000,
                        )
                    )
            except TimeoutError:
                return stop_result("stopped", "run timeout reached")
            except DeepSeekProviderError as exc:
                logger.warning("provider error in run %s: %s", run_id, exc)
                return stop_result("error", f"provider error: {exc}")
            except Exception as exc:
                logger.exception("unexpected error in run %s", run_id)
                return stop_result("error", f"unexpected error: {type(exc).__name__}: {exc}")

            self.tracker.record(usage, response.usage)
            transcript.append(
                f"call {usage.api_calls}: {len(response.tool_calls)} tool call(s), "
                f"{len(response.text)} chars of text"
            )

            if not response.tool_calls or response.stop_reason != "tool_use":
                text = response.text.strip()
                if not text:
                    return stop_result(
                        "error",
                        "worker returned no final text",
                        text=("[Worker error] the provider returned neither text nor tool calls"),
                    )
                self.tracker.finish(usage, "ok")
                return WorkerResult(
                    status="ok",
                    text=text,
                    reason=None,
                    usage=usage,
                    memory=memory,
                    transcript=transcript,
                    tool_calls=tool_calls_total,
                )

            assistant_blocks: list[dict[str, Any]] = []
            if response.text:
                assistant_blocks.append({"type": "text", "text": response.text})
            tool_result_blocks: list[dict[str, Any]] = []
            for call in response.tool_calls:
                tool_calls_total += 1
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
                result_text = await self._execute_tool(call)
                memory.absorb_tool_result(call.name, result_text)
                transcript.append(f"  -> {call.name}: {len(result_text)} chars of tool result")
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result_text,
                    }
                )
            messages.append({"role": "assistant", "content": assistant_blocks})
            messages.append({"role": "user", "content": tool_result_blocks})

            if (
                comp.enabled
                and self._context_tokens(system, messages) >= comp.worker_context_soft_limit_tokens
            ):
                messages = self._compact_history(messages, memory)
                transcript.append("  [rolling compaction: history folded into working memory]")
