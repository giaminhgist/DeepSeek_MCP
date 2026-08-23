# README Requirements

Create the final repository `README.md` **after implementation is complete**.

It must describe the code that actually exists.

## Required sections

## 1. Title and one-paragraph explanation

Explain:

> Claude stays the orchestrator; DeepSeek V4 Pro is a read-heavy MCP worker.

## 2. Architecture diagram

Use a small Mermaid or ASCII diagram showing:

```text
User → Claude Code (Claude) → MCP → DeepSeek Worker → DeepSeek Anthropic-compatible API
                                ↕
                         read-only repository tools
```

Explicitly say the parent Claude Code backend is not replaced.

## 3. What gets delegated

Examples:

- read many files,
- summarize repository architecture,
- trace call paths,
- analyze large diffs,
- code review,
- summarize logs.

Also list what should normally stay with Claude:

- planning,
- final architectural decisions,
- edits,
- final review,
- security-sensitive judgment.

## 4. Prerequisites

Document exact supported versions based on the finished `package.json`.

At minimum:

- Node.js
- Claude Code
- DeepSeek API key

## 5. Installation

Provide copy/paste commands from fresh clone through build.

Example shape, adjusted to real scripts:

```bash
git clone ...
cd ...
npm install
npm run build
cp .env.example .env   # only if the implementation actually uses dotenv
```

Do not invent steps.

## 6. Configure DeepSeek key

Prefer normal shell environment setup and explain persistence options carefully.

Examples may use:

```bash
export DEEPSEEK_API_KEY="..."
```

and PowerShell equivalent.

Never instruct the user to export DeepSeek as the parent `ANTHROPIC_AUTH_TOKEN`.

## 7. Configure model and budget

Explain `config/deepseek-worker.yaml` field-by-field:

- model name,
- base URL,
- context,
- per-call output limit,
- max iterations,
- token limits,
- max estimated cost,
- pricing snapshot,
- repository read limits,
- deny globs.

Clearly state pricing changes over time.

## 8. Connect to Claude Code

Document both:

### Option A: `.mcp.json`

Show a correct project-scoped example for the final built path.

### Option B: `claude mcp add`

Show a correct `stdio` command if practical.

Use current Claude Code MCP syntax.

Explain:

```bash
claude mcp list
claude mcp get deepseek-worker
```

and `/mcp` in Claude Code.

## 9. Install global Claude instructions

Explain that `GLOBAL_CLAUDE.md` is a template.

For user-global behavior, the user can merge/copy the relevant content into their Claude Code user instructions file (commonly `~/.claude/CLAUDE.md`, subject to current Claude Code behavior).

Warn users to merge rather than blindly overwrite existing personal instructions.

## 10. Usage examples

Include realistic prompts such as:

```text
Read this repo and map the request lifecycle. Delegate the repository scan to DeepSeek Worker.
```

```text
Review my working-tree diff. Use DeepSeek for the first-pass large diff review, then validate the high-confidence findings yourself.
```

```text
Find all call sites affected by changing FooConfig and summarize them before making edits.
```

Show the visible delegation notice behavior from `GLOBAL_CLAUDE.md`.

## 11. Context and response compaction

Explain that the worker has two context-saving directions:

```text
Claude → delegates large read-heavy work → DeepSeek
Claude ← receives compact findings/evidence ← DeepSeek MCP
```

Document:

- bounded repository tool results,
- rolling DeepSeek message-history compaction,
- structured working memory,
- `brief | normal | detailed`,
- final target/hard limits from YAML,
- no raw agent transcript by default,
- evidence references survive compaction,
- deterministic final compaction does not spend another model call,
- omitted lower-priority details are marked explicitly.

Show a compact example result.

Explain that users should ask Claude to inspect specific `path:line` evidence when more detail is needed rather than increasing every worker response size.

## 12. Token usage footer

Show an example footer and explain every field.

Explain that:

- token count comes from DeepSeek API usage,
- it aggregates all API calls in a worker run,
- estimated cost is not authoritative billing.

## 13. Security/privacy model

Clearly state:

- repository content selected by the worker is sent to the configured DeepSeek API,
- worker is read-only locally,
- sensitive globs are denied by default but are not a perfect DLP system,
- users should not use it on code they are not permitted to send to that provider.

## 14. Troubleshooting

Cover at minimum:

- worker not visible in `/mcp`,
- missing `DEEPSEEK_API_KEY`,
- invalid YAML,
- API 401/403,
- API 429/rate limit,
- request timeout,
- budget stop,
- path denied,
- MCP output too large,
- Windows path issues if relevant,
- how to enable debug logs safely.

Mention Claude Code's `MAX_MCP_OUTPUT_TOKENS` only if needed, and prefer keeping worker results concise.

## 15. Development

List exact real commands:

- test,
- typecheck,
- build,
- formatting/lint,
- mock MCP smoke test.

## 16. Updating the model/pricing

Explain that users normally edit:

```text
config/deepseek-worker.yaml
```

and restart the MCP server/Claude Code session if config reload is not implemented.

## 17. Uninstall

Give precise steps.

At minimum:

1. Remove the MCP registration:
   ```bash
   claude mcp remove deepseek-worker
   ```
   or remove the `deepseek-worker` entry from project `.mcp.json`, depending on how it was installed.
2. Remove/merge back any global `CLAUDE.md` instructions the user added.
3. Remove the repository directory if desired.
4. Remove any shell/profile `DEEPSEEK_API_KEY` export if it was dedicated to this project.
5. Optionally revoke/delete the DeepSeek API key from the provider console.

Do not tell users to uninstall Claude Code itself unless they explicitly want to.

## 18. Limitations

Include:

- worker output can be wrong,
- Claude must validate it,
- token budget is per worker run,
- exact billing can differ from local estimate,
- very large/binary/sensitive files may be skipped,
- v1 is read-only and intentionally cannot autonomously modify files.

## 19. References

Include:

- DeepSeek API docs
- DeepSeek Anthropic-compatible integration docs
- Claude Code MCP docs
- MCP SDK
- Andrej Karpathy skills inspiration
