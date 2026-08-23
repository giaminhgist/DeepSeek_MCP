# DeepSeek MCP for Claude Code — Python Spec Pack

This folder is the build specification for a project named **DeepSeek_MCP**.

The implementation language is a hard requirement:

> **DeepSeek_MCP MUST be written in Python.**

Claude Code remains the real Claude orchestrator. DeepSeek is available only as a subordinate MCP worker for high-context, read-heavy tasks.

## Product goal

Build a local Python MCP server where:

- **Claude** is the Orchestrator / Planner / Final Reviewer.
- **DeepSeek V4 Pro** is the worker.
- DeepSeek is called through its Anthropic-compatible API.
- The default worker model is `deepseek-v4-pro`.
- DeepSeek handles expensive context work such as:
  - repository exploration,
  - reading many files,
  - code search,
  - code review,
  - large diff analysis,
  - log summarization,
  - architecture mapping,
  - dependency/call-path tracing,
  - evidence collection.
- DeepSeek has read-only access to the configured repository.
- DeepSeek cannot execute arbitrary shell commands or modify repository files.
- Model, context, token, cost, runtime, and compaction budgets are centralized in one YAML file.
- Every worker run reports DeepSeek token usage.
- Long internal messages/tool results are compacted.
- Final DeepSeek output sent back to Claude is compact and evidence-oriented.
- The final implementation includes a complete `README.md` with installation, configuration, use, troubleshooting, updating, and uninstall instructions.

## Python-only implementation requirement

Use:

- Python 3.11+
- `pyproject.toml`
- a `src/deepseek_mcp/` package layout
- official Model Context Protocol Python SDK
- Python `anthropic` SDK configured with the DeepSeek base URL
- `asyncio`
- `pytest`
- `ruff`
- `mypy`
- `uv` as the recommended package/environment manager

Do not implement DeepSeek_MCP in:

- TypeScript,
- JavaScript,
- Node.js,
- Go,
- Rust,
- or another language.

Claude Code itself may be installed by its normal upstream method. That external implementation detail does not change this repository's Python-only requirement.

## Read order for Claude Code

Read and follow these files in order:

1. `PYTHON_IMPLEMENTATION_NOTE.md`
2. `01_BUILD_PROMPT.md`
3. `02_PROJECT_REQUIREMENTS.md`
4. `03_ARCHITECTURE.md`
5. `04_MCP_TOOL_CONTRACT.md`
6. `05_CONFIG_AND_BUDGET.md`
7. `06_TOKEN_ACCOUNTING.md`
8. `07_SECURITY_TESTING_ACCEPTANCE.md`
9. `09_CONTEXT_COMPACTION.md`
10. `08_README_REQUIREMENTS.md`
11. `GLOBAL_CLAUDE.md`

Do not skip files.

If requirements conflict, use this priority:

1. Security constraints
2. Python-only implementation constraint
3. Product/project requirements
4. MCP tool contract
5. Architecture
6. Implementation convenience

## Critical architecture invariant

Never point the parent Claude Code process at DeepSeek.

Do **not** globally configure Claude Code with DeepSeek values through parent-level Anthropic variables such as:

```text
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_AUTH_TOKEN=<DeepSeek key>
ANTHROPIC_MODEL=...
```

That would replace Claude itself.

Instead:

```text
Claude Code (real Claude)
        ↓ MCP stdio
Python DeepSeek_MCP child process
        ↓ Anthropic-compatible API
DeepSeek V4 Pro
```

Only the Python MCP child receives worker-specific environment variables:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_CONFIG
DEEPSEEK_REPO_ROOT
```

## Configuration source of truth

The implementation must create and use:

```text
config/deepseek-worker.yaml
```

Changing normal model/budget/compaction behavior should not require editing Python source.

## Required top-level MCP tools

Keep the Claude-facing MCP surface intentionally small:

- `deepseek_task`
- `deepseek_review`
- `deepseek_usage`

DeepSeek may use additional **internal** read-only repository tools inside its Python worker loop.

## References

- DeepSeek API docs: https://api-docs.deepseek.com/
- DeepSeek Anthropic-compatible API docs: https://api-docs.deepseek.com/guides/anthropic_api/
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- Behavioral inspiration: https://github.com/multica-ai/andrej-karpathy-skills
