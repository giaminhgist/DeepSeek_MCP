# Configuration and Budget — Python

## 1. Single source of truth

Create this runtime configuration file.

Load it using safe Python YAML parsing and validate it into typed Python models before use:

`config/deepseek-worker.yaml`

All normal model and budget changes should be possible by editing this file without code changes.

The server may allow `DEEPSEEK_CONFIG` to point at another YAML file.

## 2. Required example configuration

Use this as the initial shape. Values may be tuned slightly during implementation if tests or SDK constraints require it, but keep the semantics.

```yaml
version: 1

provider:
  api_format: anthropic
  base_url: "https://api.deepseek.com/anthropic"
  api_key_env: "DEEPSEEK_API_KEY"
  request_timeout_ms: 120000

model:
  name: "deepseek-v4-pro"
  context_window_tokens: 1000000
  max_output_tokens_per_call: 32768
  temperature: 0.2

worker:
  max_agent_iterations: 24
  max_run_seconds: 300
  default_output_detail: "normal"

compaction:
  enabled: true
  max_tool_result_chars: 24000
  worker_context_soft_limit_tokens: 320000
  worker_context_hard_limit_tokens: 520000
  preserve_recent_messages: 8
  final_target_chars:
    brief: 4000
    normal: 8000
    detailed: 12000
  final_hard_limit_chars: 16000
  max_findings:
    brief: 8
    normal: 15
    detailed: 24
  max_evidence_items:
    brief: 12
    normal: 24
    detailed: 40
  include_raw_transcript: false

repository:
  root_env: "DEEPSEEK_REPO_ROOT"
  max_file_bytes: 1048576
  max_read_lines: 12000
  max_search_matches: 500
  max_list_entries: 5000
  allow_repo_root_argument: false
  respect_gitignore: true
  deny_globs:
    - "**/.env"
    - "**/.env.*"
    - "**/*.pem"
    - "**/*.key"
    - "**/*credentials*"
    - "**/*secret*"
    - "**/.git/**"
    - "**/node_modules/**"
    - "**/dist/**"
    - "**/build/**"

budget:
  max_api_calls_per_run: 24
  max_input_tokens_per_run: 600000
  max_output_tokens_per_run: 60000
  max_total_tokens_per_run: 660000
  max_estimated_cost_usd_per_run: 0.35
  on_limit: "stop"

pricing:
  currency: "USD"
  source: "https://api-docs.deepseek.com/quick_start/pricing/"
  snapshot_date: "2026-08-22"
  per_million_tokens:
    input_cache_hit: 0.003625
    input_cache_miss: 0.435
    output: 0.87

logging:
  level: "info"
```

## 3. Why these defaults?

They intentionally leave headroom below the documented 1M context window and place a practical per-run cap around a few tenths of a US dollar using the 2026-08-22 price snapshot.

The values are defaults, not promises about future DeepSeek pricing.

## 4. Budget enforcement

Track actual usage returned by the provider after each API call.

Before making another call, stop if any already-consumed limit has been reached:

```text
api_calls >= max_api_calls_per_run
input_tokens >= max_input_tokens_per_run
output_tokens >= max_output_tokens_per_run
total_tokens >= max_total_tokens_per_run
estimated_cost >= max_estimated_cost_usd_per_run
```

Also cap `max_tokens`/output on each individual API request.

Because exact input usage for the next call is not always knowable before sending it, enforcement may be:

- exact after each API response,
- conservative before the next call.

Document that a single final provider response can cause a run to slightly cross a cumulative token/cost threshold, while no further calls are allowed afterward.

## 5. Cost estimation

Prefer provider-reported cache metrics if available.

Conceptually:

```text
estimated_cost =
  cache_hit_input_tokens * cache_hit_rate
+ cache_miss_input_tokens * cache_miss_rate
+ output_tokens * output_rate
```

Rates are per token after dividing the per-million price by 1,000,000.

If the Anthropic-compatible response does not expose enough cache detail:

- treat all input tokens as cache miss for a conservative estimate,
- mark the calculation mode in code/tests,
- do not invent cache token counts.

## 6. Configuration validation

Fail fast at startup for:

- unsupported config version,
- missing required values,
- non-positive limits,
- context limit lower than output limit,
- impossible enum values,
- invalid URL,
- duplicate/conflicting budget semantics,
- compaction soft limit greater than/equal to hard limit,
- compaction hard limit greater than/equal to model context window,
- final target greater than hard final limit,
- non-positive compaction limits.

Do not fail merely because the API key is missing until a DeepSeek-calling tool is invoked, unless startup validation is intentionally documented otherwise. This lets `deepseek_usage` and MCP discovery work without a key.

## 7. Compaction configuration

Compaction settings belong in the same YAML file so future model/context changes do not require code edits.

Important invariants:

```text
worker_context_soft_limit_tokens
  < worker_context_hard_limit_tokens
  < model.context_window_tokens
```

`final_target_chars` is a target per detail mode. `final_hard_limit_chars` is an absolute Claude-facing safety cap.

`include_raw_transcript` defaults to `false`. The normal product must not send the raw worker transcript back to Claude.

Compaction thresholds are independent from billed token accounting. Provider-reported usage remains authoritative.

See `09_CONTEXT_COMPACTION.md`.

## Python configuration implementation

Recommended package layout:

```text
src/deepseek_mcp/config/
├─ loader.py
└─ models.py
```

`models.py` defines validated Python models for:

- provider
- model
- worker
- repository
- budget
- pricing
- logging
- compaction

`loader.py` should:

1. resolve `DEEPSEEK_CONFIG` or the default YAML path,
2. load YAML safely,
3. validate all values,
4. apply the small documented environment override set,
5. return a typed config object.

Do not use unsafe YAML object construction, `eval`, or arbitrary dynamic imports.

## 8. Environment overrides

Keep overrides limited and explicit.

Required:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_CONFIG`
- `DEEPSEEK_REPO_ROOT`

Optional:

- `DEEPSEEK_BASE_URL` for compatible gateways/testing

If `DEEPSEEK_BASE_URL` is supported, it overrides `provider.base_url`.

Do not create environment overrides for every YAML field unless there is a strong reason.

## 9. No secret in shared `.mcp.json`

A project example may reference an environment variable, but must not contain a real value.

Example:

```json
{
  "mcpServers": {
    "deepseek-worker": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "${CLAUDE_PROJECT_DIR:-.}",
        "run",
        "deepseek-mcp"
      ],
      "env": {
        "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}",
        "DEEPSEEK_REPO_ROOT": "${CLAUDE_PROJECT_DIR:-.}",
        "DEEPSEEK_CONFIG": "${CLAUDE_PROJECT_DIR:-.}/config/deepseek-worker.yaml"
      }
    }
  }
}
```

If the final build's location differs, make the README/example match the real path.
