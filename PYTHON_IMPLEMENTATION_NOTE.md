# Python Implementation Note

This project must be implemented in **Python**.

This file exists to remove any ambiguity for Claude Code before implementation begins.

## Required baseline

```text
Language: Python 3.11+
Package layout: src/deepseek_mcp/
Project metadata: pyproject.toml
Environment/package manager: uv (recommended)
MCP SDK: official Python SDK
DeepSeek API client: anthropic Python SDK
Concurrency: asyncio
Tests: pytest
Lint/format: ruff
Type checking: mypy
```

## Required server execution

The final project should expose a Python console command such as:

```bash
uv run deepseek-mcp
```

and preferably also:

```bash
uv run python -m deepseek_mcp
```

## Important

Do not create a Node/TypeScript MCP server.

Do not create:

```text
package.json
src/index.ts
dist/index.js
```

for the DeepSeek_MCP server.

If a third-party external application such as Claude Code has its own runtime requirements, keep those separate from this project's implementation.

## DeepSeek connection

The Python worker should use the Python `anthropic` SDK with configuration conceptually equivalent to:

```python
from anthropic import AsyncAnthropic

client = AsyncAnthropic(
    api_key=deepseek_api_key,
    base_url="https://api.deepseek.com/anthropic",
)
```

The model name comes from:

```text
config/deepseek-worker.yaml
```

with default:

```text
deepseek-v4-pro
```

Never redirect the parent Claude Code process itself to DeepSeek.
