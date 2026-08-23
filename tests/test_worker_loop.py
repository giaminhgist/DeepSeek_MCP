"""Worker loop tests with a fake DeepSeek client (no network, no cost)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deepseek_mcp.deepseek.client import (
    ProviderAuthError,
    ToolCallRequest,
)
from deepseek_mcp.deepseek.system_prompt import build_system_prompt
from deepseek_mcp.deepseek.worker_loop import WorkerLoop
from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.guard import AccessPolicy
from deepseek_mcp.usage.tracker import UsageTracker
from tests.conftest import (
    FakeDeepSeekClient,
    make_response,
    make_run_id_factory,
    make_test_config,
)


@pytest.fixture
def env(tmp_path: Path, sample_repo: Path) -> SimpleNamespace:
    config = make_test_config(tmp_path)
    policy = AccessPolicy(sample_repo, config.repository)
    tracker = UsageTracker(config.pricing)
    git = GitTools(policy, config.repository)
    run_ids = make_run_id_factory()

    def build(
        client: FakeDeepSeekClient,
        config_override=None,
    ) -> tuple[WorkerLoop, FakeDeepSeekClient, str]:
        loop = WorkerLoop(config_override or config, policy, client, tracker, git)
        return loop, client, run_ids()

    return SimpleNamespace(config=config, policy=policy, tracker=tracker, git=git, build=build)


def make_tool_call(
    name: str, arguments: dict | None = None, tool_id: str = "t1"
) -> ToolCallRequest:
    return ToolCallRequest(id=tool_id, name=name, arguments=arguments or {})


async def test_read_tool_then_final_answer(env) -> None:
    client = FakeDeepSeekClient(
        [
            make_response(tool_calls=[make_tool_call("repo_read", {"path": "src/main.py"})]),
            make_response(
                text="## Worker result\nDone.\n\n## Evidence\n- `src/main.py:1` — entry\n"
            ),
        ]
    )
    loop, _, run_id = env.build(client)
    result = await loop.run(
        "Read src/main.py and summarize", run_id=run_id, system=build_system_prompt(env.config)
    )
    assert result.status == "ok"
    assert "Done." in result.text
    assert result.usage.api_calls == 2
    assert result.tool_calls == 1
    # The tool result was compacted and appended to the model history.
    second_request = client.requests[1]
    tool_results = second_request.messages[-1]["content"]
    assert tool_results[0]["type"] == "tool_result"
    assert "def main" in tool_results[0]["content"]
    assert len(tool_results[0]["content"]) <= env.config.compaction.max_tool_result_chars


async def test_multiple_tool_calls_in_one_turn(env) -> None:
    client = FakeDeepSeekClient(
        [
            make_response(
                tool_calls=[
                    make_tool_call("repo_read", {"path": "src/main.py"}, "t1"),
                    make_tool_call("repo_list", {}, "t2"),
                ]
            ),
            make_response(text="## Worker result\nInspected."),
        ]
    )
    loop, _, run_id = env.build(client)
    result = await loop.run("inspect", run_id=run_id, system=build_system_prompt(env.config))
    assert result.status == "ok"
    assert result.tool_calls == 2
    second_request = client.requests[1]
    tool_results = second_request.messages[-1]["content"]
    assert [block["tool_use_id"] for block in tool_results] == ["t1", "t2"]


async def test_invalid_tool_request_rejected(env) -> None:
    client = FakeDeepSeekClient(
        [
            make_response(tool_calls=[make_tool_call("evil_tool", {})]),
            make_response(text="## Worker result\nRecovered."),
        ]
    )
    loop, _, run_id = env.build(client)
    result = await loop.run("do something", run_id=run_id, system=build_system_prompt(env.config))
    assert result.status == "ok"
    tool_results = client.requests[1].messages[-1]["content"]
    assert "unknown tool" in tool_results[0]["content"]


async def test_invalid_tool_arguments_rejected(env) -> None:
    client = FakeDeepSeekClient(
        [
            make_response(tool_calls=[make_tool_call("repo_read", {"path": "../../etc/passwd"})]),
            make_response(text="## Worker result\nRecovered."),
        ]
    )
    loop, _, run_id = env.build(client)
    result = await loop.run("read", run_id=run_id, system=build_system_prompt(env.config))
    assert result.status == "ok"
    tool_results = client.requests[1].messages[-1]["content"]
    assert "tool error" in tool_results[0]["content"]


async def test_max_iterations_stop(env, tmp_path: Path) -> None:
    small = make_test_config(tmp_path, {"budget": {"max_api_calls_per_run": 50}})
    client = FakeDeepSeekClient([make_response(tool_calls=[make_tool_call("repo_list", {})])] * 20)
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run("loop forever", run_id=run_id, system=build_system_prompt(small))
    assert result.status == "stopped"
    assert "iteration limit" in (result.reason or "")
    assert result.usage.api_calls == small.worker.max_agent_iterations


async def test_run_timeout_stops(env, tmp_path: Path) -> None:
    small = make_test_config(tmp_path, {"worker": {"max_run_seconds": 0.3}})
    client = FakeDeepSeekClient(
        [make_response(tool_calls=[make_tool_call("repo_list", {})])] * 50,
        sleep_s=0.2,
    )
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run("slow task", run_id=run_id, system=build_system_prompt(small))
    assert result.status == "stopped"
    assert "timeout" in (result.reason or "")
    assert result.usage.api_calls >= 1


async def test_token_budget_stops_next_call(env, tmp_path: Path) -> None:
    small = make_test_config(
        tmp_path,
        {"budget": {"max_input_tokens_per_run": 150}},
    )
    client = FakeDeepSeekClient([make_response(tool_calls=[make_tool_call("repo_list", {})])] * 10)
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run("loop", run_id=run_id, system=build_system_prompt(small))
    assert result.status == "stopped"
    assert "input token limit" in (result.reason or "")
    # 100 input tokens per call: exactly 2 calls (second crosses 150).
    assert result.usage.api_calls == 2


async def test_cost_budget_stops(env, tmp_path: Path) -> None:
    small = make_test_config(
        tmp_path,
        {"budget": {"max_estimated_cost_usd_per_run": 0.0001}},
    )
    client = FakeDeepSeekClient([make_response(tool_calls=[make_tool_call("repo_list", {})])] * 10)
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run("loop", run_id=run_id, system=build_system_prompt(small))
    assert result.status == "stopped"
    assert "cost limit" in (result.reason or "")
    assert result.usage.estimated_cost_usd > 0


async def test_hard_context_limit_blocks_call(env, tmp_path: Path) -> None:
    small = make_test_config(
        tmp_path,
        {
            "compaction": {
                "worker_context_soft_limit_tokens": 200,
                "worker_context_hard_limit_tokens": 800,
            },
            "model": {"context_window_tokens": 2000},
        },
    )
    # First call returns a massive text so the next pre-call estimate
    # crosses the hard limit.
    client = FakeDeepSeekClient(
        [
            make_response(text="x" * 5000, tool_calls=[make_tool_call("repo_list", {})]),
            make_response(text="y" * 5000),
        ]
    )
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run("task", run_id=run_id, system=build_system_prompt(small))
    assert result.status == "stopped"
    assert "context hard limit" in (result.reason or "")
    assert result.usage.api_calls == 1


async def test_rolling_compaction_preserves_objective(env, tmp_path: Path) -> None:
    small = make_test_config(
        tmp_path,
        {
            "compaction": {
                "worker_context_soft_limit_tokens": 50,  # very low: always compact
                "worker_context_hard_limit_tokens": 3000,
                "preserve_recent_messages": 2,
            },
            "model": {"context_window_tokens": 5000},
        },
    )
    client = FakeDeepSeekClient(
        [
            make_response(
                text="thinking...",
                tool_calls=[make_tool_call("repo_read", {"path": "src/auth.py"}, "t1")],
            ),
            make_response(
                text="thinking again...",
                tool_calls=[make_tool_call("repo_read", {"path": "src/auth.py"}, "t2")],
            ),
            make_response(text="## Worker result\nMapped the auth flow."),
        ]
    )
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run(
        "OBJECTIVE-MARKER: map the auth flow",
        run_id=run_id,
        system=build_system_prompt(small),
    )
    assert result.status == "ok"
    # Compaction must have folded history into working memory.
    request_after = client.requests[-1]
    first_message = request_after.messages[0]["content"]
    assert "Working memory" in first_message
    assert "OBJECTIVE-MARKER" in first_message
    # Evidence from the compacted tool results survives.
    assert "src/auth.py" in first_message


async def test_provider_error_has_footer_and_error_text(env) -> None:
    client = FakeDeepSeekClient(error=ProviderAuthError("401 invalid key"))
    loop, _, run_id = env.build(client)
    result = await loop.run("task", run_id=run_id, system=build_system_prompt(env.config))
    assert result.status == "error"
    assert "provider error" in result.text
    assert result.usage.budget_status == "stopped"
    assert result.usage.api_calls == 0


async def test_budget_stop_makes_no_extra_call(env, tmp_path: Path) -> None:
    small = make_test_config(tmp_path, {"budget": {"max_api_calls_per_run": 2}})
    client = FakeDeepSeekClient([make_response(tool_calls=[make_tool_call("repo_list", {})])] * 50)
    loop, _, run_id = env.build(client, config_override=small)
    result = await loop.run("loop", run_id=run_id, system=build_system_prompt(small))
    assert result.status == "stopped"
    assert result.usage.api_calls == 2
    assert len(client.requests) == 2  # no extra summarization call


async def test_worker_registers_fs_tools_only_when_enabled(env, tmp_path: Path) -> None:
    loop, _, _ = env.build(FakeDeepSeekClient([]))
    names = set(loop.tools)
    assert {"repo_list", "repo_search", "repo_read", "git_diff"} <= names
    assert "fs_bash" in names  # enabled by default test env.config

    disabled = make_test_config(
        tmp_path,
        {"tools": {"allow_bash": False, "allow_writes": False, "allow_file_tools": False}},
    )
    loop_disabled = WorkerLoop(
        disabled, env.policy, FakeDeepSeekClient([]), UsageTracker(disabled.pricing), env.git
    )
    assert "fs_bash" not in loop_disabled.tools
    assert "fs_write" not in loop_disabled.tools
    assert "fs_read" not in loop_disabled.tools
    assert "repo_read" in loop_disabled.tools
