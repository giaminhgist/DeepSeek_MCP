# MCP Tool Contract — Python MCP Server

The MCP surface should be small and stable.

Implement the Claude-facing tools with the official MCP Python SDK. Prefer Python type annotations and validated input models rather than untyped manual dictionary parsing.

## 1. `deepseek_task`

### Purpose

Delegate a high-context, read-heavy codebase task to DeepSeek.

### Input schema

```json
{
  "task": "string, required",
  "focus_paths": ["optional/relative/path"],
  "output_detail": "brief | normal | detailed",
  "repo_root": "optional, only if allowed by config"
}
```

Recommended validation:

- `task`: 1–20,000 characters
- `focus_paths`: max 100 entries
- paths must be repository-relative unless an explicit safe root mode is enabled
- default `output_detail`: `normal`

### Behavior

1. Start a new usage/budget run.
2. Resolve and secure the repo root.
3. Construct DeepSeek system/task messages.
4. Let DeepSeek use internal repo tools.
5. Stop on a final answer, timeout, loop cap, or budget.
6. Format the result.
7. Apply Claude-facing structured final compaction.
8. Append/preserve the usage footer.
9. Return the bounded result to Claude.

### Best suited for

- repo exploration,
- architecture tracing,
- many-file reading,
- large text/log summarization,
- finding affected call sites,
- collecting evidence.

## 2. `deepseek_review`

### Purpose

Perform a first-pass review of code or diffs.

### Input schema

```json
{
  "scope": "working | staged | head | paths",
  "paths": ["optional/path"],
  "review_focus": ["correctness", "security", "performance", "tests", "maintainability"],
  "task": "optional additional review instruction"
}
```

### Review output requirements

DeepSeek must prioritize findings over generic praise.

Each finding should include where possible:

```text
severity: critical | high | medium | low
confidence: high | medium | low
location: path:line-range
finding: ...
evidence: ...
suggested_fix: ...
```

Then include:

- open questions,
- coverage gaps,
- concise summary,
- bounded evidence list,
- explicit omission count when lower-priority findings are compacted,
- usage footer.

Do not require a finding if there is no evidence. "No concrete issue found" is valid.

## 3. `deepseek_usage`

### Purpose

Inspect usage without making a model call.

### Input schema

```json
{
  "scope": "last_run | process"
}
```

### Output

Include:

- model
- config path
- runs
- API calls
- input tokens
- output tokens
- cache read tokens if provider reports them
- cache write tokens if provider reports them
- total tokens
- estimated cost
- current configured run limits
- pricing snapshot values

Do not expose the API key or raw headers.

## 4. Response compaction contract

`deepseek_task` and `deepseek_review` must honor `output_detail`/configured response targets.

The Claude-facing return value must not contain:

- the raw DeepSeek message transcript,
- the full internal tool-call transcript,
- large copied file bodies,
- repeated search/list/read output.

When the DeepSeek final text exceeds the selected target, compact it structurally.

When it exceeds the configured hard limit, deterministic server-side compaction is mandatory.

Prioritize retention in this order:

1. final conclusion/status,
2. critical/high-severity findings,
3. high-confidence evidence with `path:line`,
4. material uncertainties/contradictions,
5. suggested next checks,
6. lower-priority findings,
7. explanatory prose.

The usage footer is outside the text budget for truncation purposes and must always survive intact.

See `09_CONTEXT_COMPACTION.md`.

## 5. Mandatory usage footer

Every `deepseek_task` and `deepseek_review` response must end with a machine-readable-enough plain-text footer.

Example:

```text
---
DeepSeek Worker Usage
run_id: ds_20260822_01H...
model: deepseek-v4-pro
api_calls: 7
input_tokens: 183421
output_tokens: 12874
cache_read_tokens: 52210
cache_write_tokens: 0
total_tokens: 196295
estimated_cost_usd: 0.091384
budget_status: ok
```

If cache metrics are unavailable, use:

```text
cache_read_tokens: n/a
cache_write_tokens: n/a
```

`total_tokens` means `input_tokens + output_tokens`, not including cache counters a second time.

## 6. Run identity

Generate one unique `run_id` per Claude-facing worker call.

A UUID or sortable ID is acceptable.

The same `run_id` covers all DeepSeek API calls made inside that tool invocation.

## 7. Timeout semantics

Use two limits:

- per-provider-request timeout
- whole-worker-run timeout

On timeout:

- cancel further work where possible,
- preserve usage already recorded,
- append `budget_status: stopped`,
- return a clear reason.

## 8. Partial results

If a budget or timeout stops the run after useful evidence was gathered, ask DeepSeek for no extra call if the budget disallows it.

Return a deterministic server-generated partial summary containing:

- reason stopped,
- files/tools already inspected if tracked,
- last usable worker text if any,
- usage footer.

Never exceed budget just to create a prettier "budget exceeded" message.

## 9. Tool annotations/descriptions

Tool descriptions presented to Claude should clearly say:

- the operation is delegated to DeepSeek,
- it is best for large-context read-heavy work,
- DeepSeek output is advisory,
- the worker has read-only repository access,
- the response includes DeepSeek token usage.

This improves Claude's tool-selection behavior.
