# DeepSeek_MCP — DeepSeek Worker for Claude Code (Python)

A local **Python** MCP server that lets real Claude Code delegate expensive, read-heavy
repository work to **DeepSeek V4 Pro** over DeepSeek's Anthropic-compatible API.
**Claude stays the orchestrator, planner, editor, and final reviewer**; DeepSeek is a
subordinate worker with bounded, budgeted, compacted access to your files.

The parent Claude Code backend is **not** replaced — no `ANTHROPIC_BASE_URL` /
`ANTHROPIC_AUTH_TOKEN` redirection. DeepSeek credentials are scoped to the Python MCP
child process only.

## Architecture

```text
User
  ↓
Claude Code (real Claude — orchestrator / planner / editor / final reviewer)
  ↓ MCP stdio
Python deepseek-mcp child process  (src/deepseek_mcp/)
  │  ├─ deepseek_task / deepseek_review / deepseek_usage   (Claude-facing tools)
  │  ├─ internal worker loop: budget + timeout + iteration caps
  │  ├─ internal tools: repo_list, repo_search, repo_read, repo_stat,
  │  │                  git_diff, git_status, git_show,
  │  │                  fs_read, fs_glob, fs_grep,               (file tools)
  │  │                  fs_write, fs_edit, fs_notebook_edit,     (writes, opt-in)
  │  │                  fs_bash                                  (shell, opt-in)
  │  ├─ AccessPolicy: canonical-root containment, deny globs, .gitignore
  │  └─ 3 compaction layers: tool results → working memory → final response
  ↓ Anthropic-compatible API (api.deepseek.com/anthropic)
DeepSeek V4 Pro
```

- **Claude-facing surface**: exactly 3 MCP tools — `deepseek_task`, `deepseek_review`,
  `deepseek_usage`.
- **Read-only by default**: the original spec made the worker strictly read-only. Per the
  project owner's explicit decision (2026-08-23), the worker's internal toolset also
  includes file tools beyond the repository root and — when enabled in YAML —
  Write/Edit/NotebookEdit/Bash. All of it stays confined to allowed roots and bounded by
  budgets; see “Spec-pack deviation” and “Security/privacy model” below.
- Every worker response ends with a mandatory DeepSeek token-usage footer.

## What gets delegated

Good delegation candidates (read-heavy, repetitive, high-context):

- repository exploration and architecture mapping,
- reading many files / tracing call paths and dependencies,
- code search and evidence collection,
- large diff analysis and first-pass code review,
- log summarization.

What normally stays with Claude: planning, final architectural decisions, edits,
final review, and security-sensitive judgment. Worker output is **advisory** — Claude
verifies material findings (usually the highest-severity/highest-confidence ones)
before acting on them.

## Prerequisites

For DeepSeek_MCP itself:

- **Python 3.11+** (built and tested on 3.12)
- **uv** (recommended package/environment manager; installed at build time)
- **git** on PATH (only for `git_diff`/`git_status`/`git_show`)

Separately, for use with Claude Code:

- **Claude Code** installed by its normal upstream method (its own runtime
  requirements are independent of this Python project)
- a **DeepSeek API key** (for `deepseek_task` / `deepseek_review`;
  `deepseek_usage` and MCP discovery work without one)

## Installation

```bash
git clone <this-repository> DeepSeek_MCP
cd DeepSeek_MCP
uv sync        # creates .venv, installs the package + dev tools
uv build       # optional: builds dist/ sdist + wheel
```

## Configure the DeepSeek key

The key is read from the environment variable named by `provider.api_key_env`
(default `DEEPSEEK_API_KEY`). It is never stored in YAML, `.mcp.json`, or source.

Linux/macOS (persist in `~/.bashrc` / `~/.zshrc`):

```bash
export DEEPSEEK_API_KEY="sk-..."
```

Windows PowerShell:

```powershell
setx DEEPSEEK_API_KEY "sk-..."
```

**Never** export DeepSeek credentials as the parent `ANTHROPIC_AUTH_TOKEN` or
`ANTHROPIC_BASE_URL` — that would replace Claude itself.

## Configure model and budget

Everything lives in **`config/deepseek-worker.yaml`** (set `DEEPSEEK_CONFIG` to point at
another file if you like). Field-by-field:

| Section | Field | Meaning |
|---|---|---|
| `provider` | `base_url` | DeepSeek Anthropic-compatible endpoint (overridable via `DEEPSEEK_BASE_URL`) |
| | `api_key_env` | env var holding the key |
| | `request_timeout_ms` | per provider request timeout |
| `model` | `name` | worker model (default `deepseek-v4-pro`) |
| | `context_window_tokens` | model context window (compaction limits must stay below it) |
| | `max_output_tokens_per_call` | per-API-call output cap |
| | `temperature` | sampling temperature |
| `worker` | `max_agent_iterations` | max DeepSeek API calls per run loop |
| | `max_run_seconds` | whole-run wall-clock limit |
| | `default_output_detail` | `brief` / `normal` / `detailed` when not specified |
| `tools` | `allow_file_tools` | register `fs_read` / `fs_glob` / `fs_grep` (paths beyond repo root) |
| | `allow_writes` | register `fs_write` / `fs_edit` / `fs_notebook_edit` |
| | `allow_bash` | register `fs_bash` (bounded subprocess shell) |
| | `extra_allowed_roots` | additional absolute roots `fs_*` tools may touch (read and, if enabled, write) |
| | `max_bash_output_chars` / `bash_timeout_ms` | bash output/time bounds |
| `compaction` | `max_tool_result_chars` | bound on each tool result before it re-enters DeepSeek context |
| | `worker_context_soft_limit_tokens` / `hard_...` | rolling working-memory compaction triggers |
| | `preserve_recent_messages` | recent messages kept verbatim during compaction |
| | `final_target_chars` / `final_hard_limit_chars` | Claude-facing response targets and absolute cap |
| | `max_findings` / `max_evidence_items` | per-detail-mode caps |
| | `include_raw_transcript` | debug-only transcript inclusion (default `false`) |
| `repository` | `max_file_bytes`, `max_read_lines`, `max_search_matches`, `max_list_entries`, `max_git_diff_bytes` | tool bounds |
| | `allow_repo_root_argument` | allow a per-call `repo_root` override (default `false`) |
| | `respect_gitignore` | honor the repo's `.gitignore` |
| | `deny_globs` | sensitive-file patterns — applied to reads **and** writes |
| `budget` | `max_api_calls_per_run`, `max_input_tokens_per_run`, `max_output_tokens_per_run`, `max_total_tokens_per_run`, `max_estimated_cost_usd_per_run` | per-run budgets; `on_limit: stop` |
| `pricing` | `per_million_tokens` snapshot | used for `estimated_cost_usd` (informational; provider billing is authoritative — DeepSeek pricing changes over time, update the snapshot when it does) |
| `logging` | `level` | stderr log level |

Repository root resolution order (canonicalized before use): per-call `repo_root` arg
(only if `allow_repo_root_argument: true`) → MCP roots when unambiguous → `DEEPSEEK_REPO_ROOT`
→ the MCP process working directory.

## Connect to Claude Code

### Option A — project `.mcp.json`

Copy the example and restart Claude Code:

```bash
cp .mcp.json.example .mcp.json
```

Example content (matches the built paths):

```json
{
  "mcpServers": {
    "deepseek-worker": {
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "${CLAUDE_PROJECT_DIR:-.}", "run", "deepseek-mcp"],
      "env": {
        "DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}",
        "DEEPSEEK_REPO_ROOT": "${CLAUDE_PROJECT_DIR:-.}",
        "DEEPSEEK_CONFIG": "${CLAUDE_PROJECT_DIR:-.}/config/deepseek-worker.yaml"
      }
    }
  }
}
```

### Option B — `claude mcp add`

claude mcp add deepseek-worker \
  --scope user \
  --env DEEPSEEK_API_KEY \
  --env DEEPSEEK_CONFIG=/path/to/DeepSeek_MCP/config/deepseek-worker.yaml \
  -- uv run --directory /path/to/DeepSeek_MCP deepseek-mcp
  
(--env DEEPSEEK_API_KEY without a value passes the variable through from your shell.) 
(--env DEEPSEEK_REPO_ROOT=/path/to/your/repo  for specific project)

### Verify registration

```bash
claude mcp list
claude mcp get deepseek-worker
```

and inside Claude Code run `/mcp` — `deepseek-worker` should be listed as connected.
The server also starts standalone: `uv run deepseek-mcp` (or `uv run python -m deepseek_mcp`).

## Install global Claude instructions

`GLOBAL_CLAUDE.md` in this repository is a **template** of behavioral guidelines for
Claude Code (delegation discipline: visible delegation notices, worker-return notices,
treat-worker-output-as-evidence, budget awareness, …). For user-global behavior, merge
the relevant content into your Claude Code user instructions file (commonly
`~/.claude/CLAUDE.md`, subject to current Claude Code behavior).

**Merge, don't overwrite** — copy only the sections you want; never replace your
existing personal instructions wholesale.

## Usage examples

```text
Read this repo and map the request lifecycle. Delegate the repository scan to
DeepSeek Worker.
```

```text
Review my working-tree diff. Use DeepSeek for the first-pass large diff review,
then validate the high-confidence findings yourself.
```

```text
Find all call sites affected by changing FooConfig and summarize them before
making edits.
```

Per `GLOBAL_CLAUDE.md`, delegation is never silent. Before each call Claude shows a
notice like:

```text
↳ Delegating to DeepSeek Worker: repository-wide scan of auth flow to save Claude context.
```

and after it returns:

```text
↳ DeepSeek Worker returned: 4 candidate call sites; I will verify the high-impact
ones before editing. Worker usage: 183,421 input / 12,874 output tokens.
```

## Context and response compaction

The worker saves context in both directions:

```text
Claude → delegates large read-heavy work → DeepSeek
Claude ← receives compact findings + evidence ← DeepSeek MCP
```

Three deterministic layers:

1. **Bounded tool results** — every repo/fs/git/basher tool result is truncated to
   `compaction.max_tool_result_chars` with an explicit omission marker *before* it
   re-enters DeepSeek's context.
2. **Rolling working memory** — when the message history estimate reaches
   `worker_context_soft_limit_tokens`, older messages are folded into one structured
   `# Worker Working Memory` message (objective, confirmed evidence with
   `path:line-range`, findings, open questions, files inspected, next reads). The most
   recent `preserve_recent_messages` stay verbatim. The hard limit blocks further API
   calls. Objectives and evidence identifiers survive repeated compactions.
3. **Deterministic final compaction** — the final DeepSeek answer is compacted
   server-side (a pure Python pass — **no extra paid model call**) to the
   `final_target_chars` for `brief | normal | detailed`, with per-mode finding/evidence
   caps, evidence deduplication, severity/confidence sorting for reviews, and explicit
   omission markers. `final_hard_limit_chars` always wins.

Raw DeepSeek transcripts are **not** returned to Claude by default. Example compact
result:

```markdown
## Worker result
Authentication is parsed in src/auth.py and verified by middleware before lookup.

## Key findings
1. [severity: high] [confidence: high] `src/auth.py:31-55` — token parsed without length check.

## Evidence
- `src/auth.py:31-55` — Bearer token split on first space; no bounds validation.
- `src/user.py:80-103` — user lookup occurs after middleware.

## Uncertainties
- Refresh-token path not traced.

## Suggested next checks
- Inspect `src/middleware/session.py` for the second validation site.

---
DeepSeek Worker Usage
run_id: ds_20260823_010203_abcd1234
model: deepseek-v4-pro
api_calls: 7
input_tokens: 183421
output_tokens: 12874
cache_read_tokens: 52210
cache_write_tokens: n/a
total_tokens: 196295
estimated_cost_usd: 0.091384
budget_status: ok
```

If a compaction marker says details were omitted, ask Claude to inspect the specific
`path:line` evidence you actually need — don't raise every worker response size.

## Token usage footer

Every `deepseek_task` / `deepseek_review` response ends with the footer above:

- `run_id` — one id per tool invocation; it covers all DeepSeek API calls inside that run,
- `api_calls` — successful provider responses that returned usage,
- `input_tokens` / `output_tokens` — aggregated **provider-reported** usage across the run,
- `cache_read_tokens` / `cache_write_tokens` — shown when the provider reports them (`n/a` otherwise),
- `total_tokens` — `input_tokens + output_tokens` (cache counters are not counted twice),
- `estimated_cost_usd` — from the YAML pricing snapshot; **not authoritative billing**,
- `budget_status` — `ok`, or `stopped` when a budget/timeout/error stopped the run.

`deepseek_usage(scope="process" | "last_run")` reports run/process totals plus the
configured limits and pricing snapshot, and never calls DeepSeek.

## Security / privacy model

- Repository content the worker reads is **sent to the configured DeepSeek API**. Review
  your organization's policies before using this on code you are not permitted to send
  to that provider.
- Local containment: every path passes a canonical-root check (`Path.resolve()` +
  containment — not string prefixes). `..` traversal, absolute escape, and symlink
  escape are rejected. Access is confined to the repository root plus
  `tools.extra_allowed_roots`.
- Deny globs (`.env*`, `*.pem`, `*.key`, `*credentials*`, `*secret*`, `.git/**`,
  `node_modules/**`, `dist/**`, `build/**`) apply to reads **and** writes. This is a
  sensible default, **not a perfect DLP system** — the worker can still see other files
  you have not classified.
- Binary and oversized files are rejected/skipped.
- Git runs only through fixed argument arrays (`asyncio.create_subprocess_exec`, never a
  shell); `fs_bash` is the only arbitrary-command surface, and only when
  `tools.allow_bash: true`. Bash children get a validated cwd, bounded output, a
  timeout, and an environment with `DEEPSEEK_API_KEY`/`ANTHROPIC_*` stripped.
- Prompt injection: the worker system prompt instructs DeepSeek to treat repository
  text as data, never to follow instructions found in source files, and never to
  request secrets. Claude still treats worker output as untrusted evidence.
- Logs go to stderr only; API keys and headers are never logged.

**Spec-pack deviation (2026-08-23):** the project owner explicitly chose “Full toolset +
spec override”. The worker therefore includes file tools beyond the repository root and,
when enabled, Write/Edit/NotebookEdit/Bash tools. This means the original acceptance
items “Worker cannot write repository files” and “Arbitrary shell is unavailable” are
**not satisfied by design**. Set `tools.allow_writes: false` and `tools.allow_bash: false`
to restore a strictly read-only worker; `allow_file_tools: false` restricts access to the
repository root only.

## Troubleshooting

- **Worker not visible in `/mcp`** — check `claude mcp list` / `claude mcp get
  deepseek-worker`; confirm the `.mcp.json` paths and that `uv run deepseek-mcp` starts
  from the project directory.
- **Missing `DEEPSEEK_API_KEY`** — `deepseek_task`/`deepseek_review` return a typed error
  naming the variable; `deepseek_usage` still works. Export the key (see above).
- **Invalid YAML** — the server refuses to start and prints the specific validation
  problem to stderr (unsupported version, bad URL, limit ordering, …).
- **API 401/403** — wrong/unauthorized key, or key missing from the MCP server env.
- **API 429 / rate limit** — the run stops with a `provider error` result and a footer;
  wait and retry later rather than spamming retries.
- **Request timeout** — raise `provider.request_timeout_ms`, or split the task.
- **Budget stop** — the result is partial by design (`budget_status: stopped` + reason +
  working memory). Use the partial evidence; don't silently retry with a bigger budget.
- **Path denied** — the file matches a deny glob, is git-ignored, or lies outside
  allowed roots; adjust `deny_globs` / `extra_allowed_roots` deliberately.
- **MCP output too large** — worker results are capped by `compaction` limits; keep them
  concise rather than raising Claude Code's `MAX_MCP_OUTPUT_TOKENS`.
- **Windows path issues** — use `setx` for env vars; the server uses `pathlib` and fixed
  subprocess argv, but deny globs are case-sensitive.
- **Debug logs** — set `logging.level: debug` in the YAML (stderr only, never stdout).

## Development

```bash
uv run pytest                             # full suite (fake DeepSeek client, no paid calls)
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

MCP smoke tests:

```bash
uv run pytest tests/test_mcp.py           # tool list/schemas + in-memory MCP calls
uv run pytest tests/test_mcp.py::test_stdio_startup_smoke   # real stdio subprocess
```

Optional **real-API smoke test** (only when a key is set; a few API calls, ~cents):

```bash
DEEPSEEK_API_KEY=sk-... uv run python scripts/real_api_smoke.py
DEEPSEEK_API_KEY=sk-... uv run python scripts/real_api_smoke.py /path/to/your/repo
```

## Updating the model or pricing

Edit `config/deepseek-worker.yaml` (model name, budgets, compaction, pricing snapshot)
and restart the MCP server / Claude Code session — there is no live config reload in v1.
Pricing changes over time; update `pricing.per_million_tokens` and `snapshot_date` from
[DeepSeek's pricing page](https://api-docs.deepseek.com/quick_start/pricing/).

## Uninstall

1. Remove the MCP registration: `claude mcp remove deepseek-worker`, or delete the
   `deepseek-worker` entry from your project `.mcp.json` (whichever you used).
2. Remove/merge back any global `CLAUDE.md` instructions you copied from
   `GLOBAL_CLAUDE.md`.
3. Delete the repository directory if desired.
4. Remove any shell/profile `DEEPSEEK_API_KEY` export dedicated to this project.
5. Optionally revoke/delete the DeepSeek API key in the provider console.

Do not uninstall Claude Code itself unless you explicitly want to.

## Limitations

- Worker output can be wrong — Claude must validate material claims (especially
  `path:line` citations) before acting on them.
- Token/cost budgets are per worker run; a single final provider response may slightly
  cross a cumulative threshold (no further calls are allowed afterward).
- `estimated_cost_usd` is a snapshot-based estimate; provider billing is authoritative.
- Very large, binary, denied, or git-ignored files are skipped or rejected by design.
- The default config enables write/bash worker tools (spec-pack deviation above);
  disable them in YAML if you want the strictly read-only behavior.
- DeepSeek context compaction uses a conservative local char-based token *estimate* for
  triggering only; displayed token counts always come from provider usage.

## References

- DeepSeek API docs — https://api-docs.deepseek.com/
- DeepSeek Anthropic-compatible API — https://api-docs.deepseek.com/guides/anthropic_api/
- Claude Code MCP docs — https://code.claude.com/docs/en/mcp
- MCP Python SDK — https://github.com/modelcontextprotocol/python-sdk
- Behavioral inspiration — https://github.com/multica-ai/andrej-karpathy-skills
