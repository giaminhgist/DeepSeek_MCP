# Architecture

## 1. System boundary

There are two separate model clients:

### Parent: Claude Code

The parent process must authenticate to Anthropic/Claude normally.

It performs orchestration and code changes.

### Child: DeepSeek MCP Server

The MCP server is launched by Claude Code over stdio.

Only the child process receives:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_CONFIG
DEEPSEEK_REPO_ROOT
```

The child calls the DeepSeek Anthropic-compatible API.

This separation is a hard requirement.

## 2. Why not use DeepSeek as Claude Code's `ANTHROPIC_BASE_URL`?

DeepSeek officially supports running Claude Code directly against its Anthropic-compatible endpoint. That is useful for replacing the backend, but it is **not** this project's design.

This project needs:

```text
Claude → MCP → DeepSeek
```

not:

```text
Claude Code UI → DeepSeek pretending to be Claude
```

Therefore never ask users to export DeepSeek values as the parent Claude Code process's `ANTHROPIC_*` variables.

## 3. Recommended implementation stack

- Node.js 20+ preferred
- TypeScript
- `@modelcontextprotocol/sdk`
- `@anthropic-ai/sdk`
- a small YAML parser
- a small schema validator such as Zod if it reduces validation complexity
- Vitest or Node's built-in test runner

Keep dependencies limited.

## 4. DeepSeek client

Create a provider wrapper so the rest of the code does not depend on SDK details.

Conceptual interface:

```ts
interface DeepSeekClient {
  runTurn(request: WorkerTurnRequest): Promise<WorkerTurnResponse>;
}
```

The client should be configured with:

```text
baseURL = config.provider.base_url
apiKey = process.env[config.provider.api_key_env]
model = config.model.name
```

The default model is exactly:

```text
deepseek-v4-pro
```

Do not use `deepseek-v4-pro[1m]` unless the API actually requires that alias for this direct worker path. The official API model ID is `deepseek-v4-pro`; keep it configurable.

## 5. Internal agent loop

The key optimization is that DeepSeek can decide what repository content it needs.

Pseudo-flow:

```text
messages = [system, task]

repeat until complete or budget/loop limit:
    call DeepSeek with internal read-only tool definitions

    record API usage

    if model returns final text:
        return final text

    if model requests tools:
        validate each tool call
        execute read-only repo tool
        compact/bound the tool result
        append compact tool result
        compact older message history when soft context threshold is reached
        continue

    otherwise:
        fail with protocol error
```

The server, not DeepSeek, enforces:

- repository root,
- ignore rules,
- max bytes per read,
- max matches,
- max tool iterations,
- run token budget,
- timeout.

## 6. Internal tool design

### `repo_list`

Inputs:

- relative directory
- optional depth
- optional max entries

Returns compact paths and basic type/size metadata.

### `repo_search`

Inputs:

- query
- optional glob
- optional max matches

Use a safe implementation. Prefer an installed fast search backend only if it is optional and has a pure Node fallback. Do not expose arbitrary CLI flags.

Return:

- path,
- line number,
- short matching line/context.

### `repo_read`

Inputs:

- relative path
- optional start line
- optional end line

Rules:

- text only,
- reject binary,
- enforce byte/line caps,
- return numbered lines,
- canonical path must remain inside repository root.

### `git_diff`

Inputs should be a constrained enum/mode rather than arbitrary command strings.

Suggested modes:

- `working`
- `staged`
- `head`

Optional bounded fields:

- `paths: string[]`
- `max_bytes`

Implement with fixed subprocess arguments. No shell.

## 7. Prompt strategy

The DeepSeek worker system prompt should instruct it to:

- behave as a read-only senior code-analysis worker,
- inspect evidence before conclusions,
- use repository tools instead of guessing,
- cite paths and line ranges,
- distinguish facts from hypotheses,
- avoid requesting writes or shell execution,
- return a concise final report,
- stop reading once enough evidence exists,
- respect the orchestrator role of Claude.

Do not embed changing budgets in the system prompt; pass them from config/runtime.

## 8. Claude-facing result size

Large worker output defeats the point of delegation.

Default target:

- `brief`: roughly 4,000 characters,
- `normal`: roughly 8,000 characters,
- `detailed`: roughly 12,000 characters,
- hard final cap: configurable, default 16,000 characters,
- prefer evidence references over copying full files,
- raw DeepSeek transcript is excluded by default.

The server must deterministically compact an oversized final DeepSeek answer before returning it to Claude. This final compactor must preserve the usage footer and must not require another DeepSeek API call.

Claude Code warns on very large MCP output, so keep results compact by design. See `09_CONTEXT_COMPACTION.md`.

## 9. Context compaction layer

Compaction is a first-class server responsibility.

There are three bounded layers:

1. **Repository-tool output bound** — compact search/list/read/diff results before adding them to DeepSeek messages.
2. **Rolling worker memory** — near the configured context soft limit, replace older messages/tool results with structured working memory while retaining recent messages.
3. **Final Claude-facing compactor** — enforce `brief|normal|detailed` targets and a hard character cap.

Structured working memory must preserve stable evidence references such as `path:line-range` so Claude can selectively verify claims without receiving the entire worker transcript.

Do not use a second model call only to shorten the final result.

## 10. Process-level usage state

Maintain:

- current run usage,
- cumulative process usage,
- count of runs,
- last run summary.

No database required. In-memory state is sufficient for v1.

## 11. Logging

All protocol output over stdout must remain MCP-safe.

Use stderr for diagnostic logs.

Never log:

- API keys,
- Authorization headers,
- complete sensitive file contents by default.

Configurable log levels:

- error
- warn
- info
- debug

Default: `info`.

## 12. Shutdown

Handle normal stdio shutdown and process signals cleanly.

No background service is needed.
