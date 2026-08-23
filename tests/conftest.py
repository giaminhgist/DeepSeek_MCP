"""Shared fixtures: test configs, sample repos, and a fake DeepSeek client."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
import yaml

from deepseek_mcp.config.loader import load_config
from deepseek_mcp.config.models import Config
from deepseek_mcp.deepseek.client import (
    DeepSeekProviderError,
    ToolCallRequest,
    WorkerTurnRequest,
    WorkerTurnResponse,
)
from deepseek_mcp.usage.tracker import ProviderUsage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG_PATH = PROJECT_ROOT / "config" / "deepseek-worker.yaml"


def merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def make_config_file(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Path:
    data = yaml.safe_load(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    if overrides:
        merge_dict(data, overrides)
    path = tmp_path / "config" / "deepseek-worker.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def make_test_config(
    tmp_path: Path,
    overrides: dict[str, Any] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Config:
    """A config with small limits so budgets trigger quickly in tests."""
    base: dict[str, Any] = {
        "model": {
            "context_window_tokens": 8000,
            "max_output_tokens_per_call": 1000,
        },
        "worker": {"max_agent_iterations": 6, "max_run_seconds": 10},
        "tools": {
            "extra_allowed_roots": [],
            "bash_timeout_ms": 5000,
            "max_bash_output_chars": 4000,
        },
        "compaction": {
            "max_tool_result_chars": 2000,
            "worker_context_soft_limit_tokens": 2000,
            "worker_context_hard_limit_tokens": 4000,
            "preserve_recent_messages": 2,
            "final_target_chars": {"brief": 500, "normal": 800, "detailed": 1200},
            "final_hard_limit_chars": 1600,
            "max_findings": {"brief": 3, "normal": 5, "detailed": 8},
            "max_evidence_items": {"brief": 4, "normal": 8, "detailed": 12},
        },
        "repository": {
            "max_file_bytes": 100_000,
            "max_read_lines": 500,
            "max_search_matches": 100,
            "max_list_entries": 200,
            "max_git_diff_bytes": 100_000,
        },
        "budget": {
            "max_api_calls_per_run": 6,
            "max_input_tokens_per_run": 2000,
            "max_output_tokens_per_run": 1000,
            "max_total_tokens_per_run": 3000,
            "max_estimated_cost_usd_per_run": 0.05,
        },
    }
    if overrides:
        merge_dict(base, overrides)
    path = make_config_file(tmp_path, base)
    return load_config(path=path, env=env or {})


@pytest.fixture
def test_config(tmp_path: Path) -> Config:
    return make_test_config(tmp_path)


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A small repository with source files, secrets, and a .gitignore."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "main.py").write_text(
        "def main() -> None:\n    print('hello')\n", encoding="utf-8"
    )
    (repo / "src" / "auth.py").write_text(
        "\n".join(f"# auth line {i}" for i in range(1, 51)) + "\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_auth.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (repo / ".gitignore").write_text("*.log\nignored/\n", encoding="utf-8")
    (repo / "debug.log").write_text("log data\n", encoding="utf-8")
    (repo / "secrets.env").write_text("TOKEN=abc\n", encoding="utf-8")
    (repo / "id_rsa.pem").write_text("-----BEGIN KEY-----\n", encoding="utf-8")
    (repo / "data.bin").write_bytes(b"\x00\x01binary\x00")
    (repo / "large.py").write_text(
        "\n".join(f"line_{i} = {i}" for i in range(1, 201)) + "\n", encoding="utf-8"
    )
    return repo


class FakeDeepSeekClient:
    """Scripted in-memory DeepSeek client for tests. No network, no cost."""

    def __init__(
        self,
        responses: list[WorkerTurnResponse] | None = None,
        *,
        error: DeepSeekProviderError | None = None,
        error_after: int | None = None,
        sleep_s: float = 0.0,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.error_after = error_after
        self.sleep_s = sleep_s
        self.requests: list[WorkerTurnRequest] = []

    async def run_turn(self, request: WorkerTurnRequest) -> WorkerTurnResponse:
        import asyncio

        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        self.requests.append(request)
        if self.error is not None and (
            self.error_after is None or len(self.requests) > self.error_after
        ):
            raise self.error
        if self.responses:
            return self.responses.pop(0)
        return make_response(
            text=("## Worker result\nDone.\n\n## Evidence\n- `src/main.py:1-2` — entry point\n")
        )


def make_response(
    text: str = "",
    *,
    tool_calls: list[ToolCallRequest] | None = None,
    stop_reason: str | None = None,
    usage: ProviderUsage | None = None,
) -> WorkerTurnResponse:
    stop = "tool_use" if tool_calls else stop_reason or "end_turn"
    return WorkerTurnResponse(
        text=text,
        tool_calls=tool_calls or [],
        stop_reason=stop,
        usage=usage or ProviderUsage(input_tokens=100, output_tokens=50, cache_read_tokens=None),
    )


def make_run_id_factory() -> Any:
    counter = itertools.count(1)
    return lambda: f"ds_test_{next(counter):03d}"
