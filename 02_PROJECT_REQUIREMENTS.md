# Project Requirements — Python DeepSeek_MCP

## 0. Python implementation requirement

DeepSeek_MCP must be implemented in **Python 3.11+**.

The repository must use:

```text
pyproject.toml
src/deepseek_mcp/
tests/
```

Use the official MCP Python SDK, Python `anthropic` SDK, pytest, ruff, mypy, and preferably uv.

Do not implement the MCP server in TypeScript/JavaScript/Node.js.

## 1. Product goal

Build an MCP server that lets **real Claude Code** delegate expensive repository-reading work to **DeepSeek V4 Pro** without replacing Claude as the primary model.

Claude is the authority for:

- interpreting the user's real intent,
- planning,
- architecture decisions,
- sensitive reasoning,
- deciding whether delegation is worthwhile,
- validating DeepSeek output,
- making code changes,
- final code review,
- final user-facing answer.

DeepSeek Worker is optimized for:

- scanning a repository,
- locating relevant files and symbols,
- reading many files,
- summarizing modules,
- summarizing large logs,
- analyzing large diffs,
- first-pass code review,
- dependency/call-flow tracing,
- identifying candidate bugs,
- collecting evidence for Claude,
- other repetitive or high-token read-heavy work.

## 2. Non-goals

Do not implement:

- a replacement Claude client,
- a proxy that redirects Claude Code itself to DeepSeek,
- autonomous writes to the repository,
- arbitrary command execution by DeepSeek,
- a general remote execution platform,
- a multi-user server,
- persistent cloud telemetry,
- a vector database,
- a web dashboard.

## 3. Primary design principle

Use DeepSeek as a **context worker**, not as the final authority.

A worker result is advisory and untrusted until Claude checks it.

## 4. Required top-level MCP tools

Expose only a small set to Claude:

### `deepseek_task`

General read-heavy analysis. Examples:

- "Map the authentication flow and list the important files."
- "Read this repository and explain how configuration is loaded."
- "Find all likely call sites affected by changing this interface."
- "Summarize the test architecture."

### `deepseek_review`

Focused code/diff review. Examples:

- review working tree changes,
- review staged changes,
- review a requested commit range if supported safely,
- review named files.

The output must prioritize concrete findings with file paths and line references when possible.

### `deepseek_usage`

Return cumulative process/session usage statistics and configured budget/pricing information. This is informational and must not call DeepSeek.

Avoid exposing ten narrow aliases that all do the same thing.

## 5. Internal worker capabilities

DeepSeek may use an internal tool loop controlled by the MCP server. Internal tools are not shell tools and are not necessarily exposed to Claude.

Required:

- `repo_list`
- `repo_search`
- `repo_read`
- `git_diff`

Optional if small and useful:

- `repo_stat`
- `git_status`
- `git_show` restricted to safe read-only arguments

Every path must pass the repository-root guard.

## 6. Read-only guarantee

The DeepSeek worker must not write, delete, rename, chmod, or otherwise mutate files.

Do not give DeepSeek:

- `bash`
- `sh`
- `powershell`
- arbitrary subprocess
- `git checkout`
- `git reset`
- `git clean`
- package-manager install commands
- network-fetch tools

If the worker proposes a patch, it returns a textual suggestion to Claude. Claude decides whether to apply it.

## 7. Repository root

Resolve the repository root using, in priority order:

1. Explicit safe tool argument if the server is configured to allow it.
2. MCP roots, when available and unambiguous.
3. `DEEPSEEK_REPO_ROOT`.
4. The MCP process working directory.

Canonicalize the path before use.

## 8. Output discipline

DeepSeek output should save Claude context, not flood it.

This applies both to the **final response sent to Claude** and to **intermediate tool/message history inside the DeepSeek agent loop**.

Required compaction behavior:

- bound each repository tool result before adding it to the model history,
- compact old DeepSeek messages into structured working memory near the context soft limit,
- never return raw agent transcripts to Claude by default,
- compact final results to findings + evidence + uncertainty + next checks,
- preserve file paths and line ranges,
- preserve the usage footer even when text is truncated/compacted,
- use deterministic server-side final compaction rather than another paid model call when the final answer is oversized.

See `09_CONTEXT_COMPACTION.md` for the required algorithm and defaults.

Default result shape:

```markdown
## Worker result
<concise answer>

## Evidence
- `path/to/file.py:10-42` — why it matters
- ...

## Uncertainties
- ...

## Suggested next checks
- ...

---
DeepSeek Worker Usage
run_id: ...
model: deepseek-v4-pro
api_calls: ...
input_tokens: ...
output_tokens: ...
cache_read_tokens: ...
cache_write_tokens: ...
total_tokens: ...
estimated_cost_usd: ...
budget_status: ok|stopped
```

The usage footer is mandatory even when a run stops because of a budget or recoverable error.

## 9. Error behavior

Return useful typed errors for:

- missing API key,
- invalid config,
- DeepSeek API authentication error,
- timeout,
- rate limit,
- budget exceeded,
- repository root unavailable,
- file denied,
- path traversal attempt,
- unsupported/binary/oversized file.

Never print secrets in errors.

## 10. Cross-platform baseline

Support:

- macOS
- Linux
- Windows where Python, `uv`, and Claude Code support the stdio MCP configuration

Use Python cross-platform path APIs such as `pathlib`. Do not assume `/bin/bash`.

## 11. Source-of-truth configuration

Runtime model and budget settings belong in:

`config/deepseek-worker.yaml`

Do not scatter default model IDs or budget numbers through the codebase. Schema-level fallback values may exist only to safely load the file; the documented configuration file remains the human-editable source of truth.

## 12. Secrets

`DEEPSEEK_API_KEY` must come from the environment. Never put a live key in:

- source,
- YAML config,
- `.mcp.json.example`,
- tests,
- README examples.

Provide `.env.example` only as documentation; do not automatically require dotenv if unnecessary.
