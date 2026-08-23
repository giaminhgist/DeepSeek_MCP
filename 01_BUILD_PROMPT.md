# Claude Code Build Prompt — DeepSeek_MCP in Python

You are implementing this repository from scratch.

Read `PYTHON_IMPLEMENTATION_NOTE.md` first. Its Python-only constraint is mandatory.

Build a production-quality but intentionally small **DeepSeek_MCP** server for Claude Code.

## Hard requirements

The MCP server must be written in **Python**.

Use:

- Python 3.11+
- `pyproject.toml`
- `src/deepseek_mcp/`
- official MCP Python SDK
- Python `anthropic` SDK
- `asyncio`
- `pytest`
- `ruff`
- `mypy`
- `uv`

Do not implement the server in TypeScript/JavaScript/Node.js.

## Mission

Claude Code must remain powered by real Claude and act as:

- orchestrator,
- planner,
- decision maker,
- editor,
- verifier,
- final reviewer.

DeepSeek is an MCP worker for:

- reading many files,
- repository exploration,
- search,
- summarization,
- architecture mapping,
- large diff review,
- code review,
- dependency tracing,
- log analysis,
- other high-token read-heavy work.

The worker uses:

```text
API format: Anthropic-compatible
default base URL: https://api.deepseek.com/anthropic
default model: deepseek-v4-pro
MCP transport: stdio
implementation: Python
```

## Execution rules

1. Read every specification file before coding.
2. State a short architecture/implementation plan.
3. Define verifiable acceptance checks.
4. Implement the smallest safe architecture satisfying the specs.
5. Do not add a web UI, database, daemon, vector database, telemetry backend, authentication service, or unrelated framework.
6. Keep DeepSeek read-only.
7. Do not expose arbitrary shell execution.
8. Tests must use fake/mock DeepSeek responses and must not require a paid API call.
9. Provide an optional real-API smoke-test path for users with `DEEPSEEK_API_KEY`.
10. Generate `README.md` only after implementation and tests are complete.
11. Continue until tests, lint, type checking, build, and MCP smoke tests pass.
12. Do not stop after scaffolding.

## Recommended repository tree

```text
DeepSeek_MCP/
├─ src/
│  └─ deepseek_mcp/
│     ├─ __init__.py
│     ├─ __main__.py
│     ├─ server.py
│     │
│     ├─ config/
│     │  ├─ __init__.py
│     │  ├─ loader.py
│     │  └─ models.py
│     │
│     ├─ deepseek/
│     │  ├─ __init__.py
│     │  ├─ client.py
│     │  ├─ worker_loop.py
│     │  └─ system_prompt.py
│     │
│     ├─ repo/
│     │  ├─ __init__.py
│     │  ├─ guard.py
│     │  ├─ listing.py
│     │  ├─ reader.py
│     │  ├─ search.py
│     │  └─ git.py
│     │
│     ├─ usage/
│     │  ├─ __init__.py
│     │  ├─ budget.py
│     │  ├─ tracker.py
│     │  └─ footer.py
│     │
│     ├─ compaction/
│     │  ├─ __init__.py
│     │  ├─ working_memory.py
│     │  ├─ tool_results.py
│     │  └─ final_response.py
│     │
│     └─ tools/
│        ├─ __init__.py
│        ├─ task.py
│        ├─ review.py
│        └─ usage.py
│
├─ config/
│  └─ deepseek-worker.yaml
│
├─ tests/
│  ├─ fixtures/
│  ├─ test_config.py
│  ├─ test_guard.py
│  ├─ test_repo_tools.py
│  ├─ test_worker_loop.py
│  ├─ test_compaction.py
│  ├─ test_usage.py
│  └─ test_mcp.py
│
├─ .mcp.json.example
├─ .env.example
├─ .gitignore
├─ pyproject.toml
├─ uv.lock
├─ README.md
├─ LICENSE
└─ GLOBAL_CLAUDE.md
```

Slight naming changes are acceptable only if they improve the actual Python implementation.

## Python project entrypoint

Create a console entrypoint in `pyproject.toml`.

Example shape:

```toml
[project.scripts]
deepseek-mcp = "deepseek_mcp.__main__:main"
```

Support normal development execution such as:

```bash
uv run deepseek-mcp
```

and preferably:

```bash
uv run python -m deepseek_mcp
```

The stdio MCP process must not print normal logs to stdout.

## Required architecture

```text
User
  ↓
Claude Code (real Claude)
  ↓ decides whether delegation helps
deepseek_task / deepseek_review
  ↓
Python MCP server
  ↓
Python Anthropic client configured for DeepSeek
  ↓
DeepSeek V4 Pro
  ↕
safe Python read-only repository tools
  ↓
rolling context compaction
  ↓
compact findings + evidence
  ↓
usage footer
  ↓
Claude verifies and makes final decisions
```

## Python implementation guidance

Prefer:

- `pathlib.Path` for filesystem work
- `asyncio` for the worker loop
- `asyncio.create_subprocess_exec()` for constrained Git calls
- dataclasses and/or Pydantic for typed state
- safe YAML loading
- Python logging to stderr
- dependency injection for fake provider clients in tests
- pure functions for deterministic compaction

Do not use:

```python
os.system(...)
subprocess.run(..., shell=True)
eval(...)
exec(...)
```

Do not construct arbitrary shell commands from worker input.

## DeepSeek client boundary

Create a small abstraction/protocol so tests do not depend on the network.

Conceptual shape:

```python
from typing import Protocol

class DeepSeekClient(Protocol):
    async def run_turn(self, request: "WorkerTurnRequest") -> "WorkerTurnResponse":
        ...
```

The production client may use `AsyncAnthropic` with a configurable `base_url`.

## Definition of done

Done means all of the following are true:

- Python package installs/syncs successfully.
- MCP Python server starts cleanly over stdio.
- Official MCP Python SDK is used.
- Claude can register the server through project `.mcp.json`.
- DeepSeek uses model `deepseek-v4-pro` by default.
- DeepSeek API credentials are scoped to the MCP child.
- DeepSeek can inspect a configured repo without Claude pasting entire files.
- Worker is read-only.
- Arbitrary shell is unavailable.
- Path traversal is blocked.
- Symlink escape is blocked.
- Sensitive deny patterns are present.
- `deepseek_task` works with fake provider tests.
- `deepseek_review` works with fake provider tests.
- `deepseek_usage` makes no provider call.
- Usage is aggregated across the full internal worker run.
- Every worker result ends with a usage footer.
- Run token/cost/API-call budgets are enforced.
- Long tool results are bounded before re-entering the model context.
- Long DeepSeek message history is compacted into structured working memory.
- Raw DeepSeek transcript is not returned to Claude by default.
- Final Claude-facing responses respect configured target/hard limits.
- `config/deepseek-worker.yaml` is the model/budget/compaction source of truth.
- `GLOBAL_CLAUDE.md` exists.
- `README.md` is written last and describes the actual Python project.

Before completion, run the real project equivalents of:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
uv build
```

Also run:

- in-memory MCP smoke test where supported by the MCP SDK,
- stdio startup smoke test.

If a genuinely required implementation detail is not specified, choose the smallest safe Python solution and document the assumption.
