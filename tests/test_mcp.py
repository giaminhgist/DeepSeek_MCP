"""MCP layer tests: tool registration, schemas, in-memory calls, stdio smoke."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from deepseek_mcp.deepseek.client import ToolCallRequest
from deepseek_mcp.server import create_app
from tests.conftest import (
    FakeDeepSeekClient,
    make_response,
    make_run_id_factory,
    make_test_config,
)


@pytest.fixture
def app(tmp_path: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch):
    config = make_test_config(tmp_path)
    monkeypatch.setenv("DEEPSEEK_REPO_ROOT", str(sample_repo))
    client = FakeDeepSeekClient(
        [
            make_response(tool_calls=[ToolCallRequest(id="t1", name="repo_list", arguments={})]),
            make_response(
                text=(
                    "## Worker result\nRepo has 3 source files.\n\n"
                    "## Evidence\n- `src/main.py:1-2` — entry point\n\n"
                    "## Uncertainties\n- none\n"
                )
            ),
        ]
    )
    return create_app(config, client_factory=lambda c: client, run_id_factory=make_run_id_factory())


async def test_tool_list_contains_required_three(app) -> None:
    tools = await app.mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {"deepseek_task", "deepseek_review", "deepseek_usage"} <= names


async def test_tool_schemas(app) -> None:
    tools = {tool.name: tool for tool in await app.mcp.list_tools()}
    task_schema = tools["deepseek_task"].inputSchema
    assert "task" in task_schema["properties"]
    assert "task" in task_schema.get("required", [])
    assert task_schema["properties"]["output_detail"]  # detail enum exists

    review_schema = tools["deepseek_review"].inputSchema
    assert "scope" in review_schema["properties"]
    assert "paths" in review_schema["properties"]

    usage_schema = tools["deepseek_usage"].inputSchema
    assert "scope" in usage_schema["properties"]


async def test_deepseek_task_in_memory(app) -> None:
    content = (
        await app.mcp.call_tool(
            "deepseek_task",
            {"task": "Summarize this repo", "output_detail": "brief"},
        )
    )[0]
    text = content[0].text
    assert "Repo has 3 source files" in text
    assert "src/main.py:1-2" in text
    # Raw transcript absent by default; footer last and complete.
    assert "## Worker transcript" not in text
    assert text.rstrip().endswith("budget_status: ok")
    assert "DeepSeek Worker Usage" in text
    assert "input_tokens: 200" in text
    assert "api_calls: 2" in text


async def test_deepseek_review_in_memory_scope_paths(app, sample_repo: Path) -> None:
    content = (
        await app.mcp.call_tool(
            "deepseek_review",
            {"scope": "paths", "paths": ["src/main.py"]},
        )
    )[0]
    text = content[0].text
    assert "Repo has 3 source files" in text
    assert text.rstrip().endswith("budget_status: ok")


async def test_deepseek_review_empty_paths_rejected(app) -> None:
    with pytest.raises(ToolError, match="paths must not be empty"):
        await app.mcp.call_tool("deepseek_review", {"scope": "paths", "paths": []})


async def test_deepseek_usage_makes_no_provider_call(
    tmp_path: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_test_config(tmp_path)
    monkeypatch.setenv("DEEPSEEK_REPO_ROOT", str(sample_repo))
    called = []

    def factory(c):
        called.append(True)
        raise AssertionError("client must not be constructed for deepseek_usage")

    app = create_app(config, client_factory=factory)
    content = (await app.mcp.call_tool("deepseek_usage", {"scope": "process"}))[0]
    text = content[0].text
    assert "DeepSeek Worker Process Usage" in text
    assert "model: deepseek-v4-pro" in text
    assert called == []


async def test_deepseek_usage_last_run(
    tmp_path: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_test_config(tmp_path)
    monkeypatch.setenv("DEEPSEEK_REPO_ROOT", str(sample_repo))
    app = create_app(config, client_factory=lambda c: FakeDeepSeekClient([]))
    before = (await app.mcp.call_tool("deepseek_usage", {"scope": "last_run"}))[0][0].text
    assert "No completed" in before
    await app.mcp.call_tool("deepseek_task", {"task": "hello"})
    after = (await app.mcp.call_tool("deepseek_usage", {"scope": "last_run"}))[0][0].text
    assert "DeepSeek Worker Usage" in after
    assert "api_calls: 1" in after


async def test_missing_api_key_typed_error(
    tmp_path: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_test_config(tmp_path)
    monkeypatch.setenv("DEEPSEEK_REPO_ROOT", str(sample_repo))
    app = create_app(config)  # default client factory; no key in config
    with pytest.raises(ToolError, match="DEEPSEEK_API_KEY"):
        await app.mcp.call_tool("deepseek_task", {"task": "hello"})


async def test_invalid_output_detail_rejected(app) -> None:
    with pytest.raises(ToolError):
        await app.mcp.call_tool("deepseek_task", {"task": "x", "output_detail": "verbose"})


async def test_repo_root_arg_disabled_by_default(app, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="allow_repo_root_argument"):
        await app.mcp.call_tool("deepseek_task", {"task": "x", "repo_root": str(tmp_path)})


async def test_budget_stop_result_ends_with_footer(
    tmp_path: Path, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_test_config(tmp_path, {"budget": {"max_api_calls_per_run": 1}})
    monkeypatch.setenv("DEEPSEEK_REPO_ROOT", str(sample_repo))
    client = FakeDeepSeekClient(
        [make_response(tool_calls=[ToolCallRequest(id="t1", name="repo_list", arguments={})])] * 10
    )
    app = create_app(config, client_factory=lambda c: client)
    content = (await app.mcp.call_tool("deepseek_task", {"task": "loop"}))[0]
    text = content[0].text
    assert "budget_status: stopped" in text
    assert text.rstrip().endswith("budget_status: stopped")
    assert "Partial result" in text


async def test_stdio_startup_smoke(tmp_path: Path, sample_repo: Path) -> None:
    """Launch the real MCP server over stdio and exercise protocol handshake."""
    config_path = tmp_path / "smoke.yaml"
    config_path.write_text(
        (Path(__file__).parent.parent / "config" / "deepseek-worker.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["DEEPSEEK_CONFIG"] = str(config_path)
    env["DEEPSEEK_REPO_ROOT"] = str(sample_repo)
    env.pop("DEEPSEEK_API_KEY", None)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "deepseek_mcp"],
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init_result = await asyncio.wait_for(session.initialize(), timeout=30)
        assert init_result.serverInfo.name == "deepseek-worker"
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert {"deepseek_task", "deepseek_review", "deepseek_usage"} <= names
        result = await session.call_tool("deepseek_usage", {"scope": "process"})
        assert result.isError is False
        text = "".join(getattr(block, "text", "") for block in result.content)
        assert "DeepSeek Worker Process Usage" in text
