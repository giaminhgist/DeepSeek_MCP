# Claude Code Build Prompt

You are implementing this repository from scratch.

Your job is to build a production-quality but intentionally small **DeepSeek MCP Worker for Claude Code**.

## Mission

Claude Code must remain powered by real Claude and act as the orchestrator. It should delegate high-context, token-expensive, read-heavy work to a DeepSeek worker through MCP.

The worker must use:

- Provider API format: Anthropic-compatible
- Default base URL: `https://api.deepseek.com/anthropic`
- Default model: `deepseek-v4-pro`
- Transport to Claude Code: local MCP `stdio`
- Implementation language: TypeScript on Node.js
- MCP SDK: official Model Context Protocol TypeScript SDK
- DeepSeek client: Anthropic SDK configured with a custom base URL, or an equivalently small standards-compatible client if SDK behavior requires it

## Execution rules

1. Read all specification files before writing code.
2. State a short implementation plan and explicit acceptance checks.
3. Implement the smallest architecture that satisfies all acceptance criteria.
4. Do not add a web UI, database, daemon, telemetry backend, authentication service, vector database, or unrelated framework.
5. Keep the worker read-only with respect to the target repository.
6. Do not expose arbitrary shell execution to DeepSeek.
7. Use mocks/fixtures in automated tests. Tests must not require a paid DeepSeek API call.
8. Perform one optional documented manual smoke test path for users who have a real `DEEPSEEK_API_KEY`.
9. Generate `README.md` only after the implementation and tests are complete, so the README describes the actual repository rather than an imagined one.
10. Before declaring completion, run formatting, type checking, tests, build, and a local MCP startup smoke test.

## Recommended target repository tree

You may adjust names slightly if required by the chosen SDK, but keep responsibilities separated.

```text
deepseek-mcp/
├─ src/
│  ├─ index.ts
│  ├─ server.ts
│  ├─ config/
│  │  ├─ load-config.ts
│  │  └─ schema.ts
│  ├─ deepseek/
│  │  ├─ client.ts
│  │  ├─ worker-loop.ts
│  │  └─ system-prompt.ts
│  ├─ repo/
│  │  ├─ guard.ts
│  │  ├─ list.ts
│  │  ├─ read.ts
│  │  ├─ search.ts
│  │  └─ git.ts
│  ├─ usage/
│  │  ├─ budget.ts
│  │  ├─ tracker.ts
│  │  └─ footer.ts
│  ├─ compaction/
│  │  ├─ working-memory.ts
│  │  ├─ compact-tool-result.ts
│  │  └─ compact-final-result.ts
│  └─ tools/
│     ├─ deepseek-task.ts
│     ├─ deepseek-review.ts
│     └─ deepseek-usage.ts
├─ config/
│  └─ deepseek-worker.yaml
├─ tests/
├─ .mcp.json.example
├─ .env.example
├─ .gitignore
├─ package.json
├─ tsconfig.json
├─ README.md
├─ LICENSE
└─ GLOBAL_CLAUDE.md
```

## Required implementation behavior

A normal flow should look like this:

```text
User
  ↓
Claude Code (real Claude)
  ↓  decides delegation is useful
MCP tool: deepseek_task / deepseek_review
  ↓
DeepSeek MCP server
  ↓
DeepSeek V4 Pro
  ↕ internal read-only tool loop
repo_list / repo_search / repo_read / git_diff
  ↓
DeepSeek produces concise findings
  ↓
MCP server appends exact usage footer
  ↓
Claude validates findings, reasons, edits code, and makes final decision
```

The MCP server must not require Claude to paste entire files into the request. The purpose is to move large-context reading to the worker.

The worker must also prevent the reverse problem: DeepSeek must not return an enormous summary/transcript to Claude. Use the compaction rules in `09_CONTEXT_COMPACTION.md` for bounded tool results, rolling worker working-memory compaction, and deterministic final-response compaction.

## Definition of done

Do not stop at scaffolding. Done means:

- MCP server starts cleanly over stdio.
- Claude Code can register it using `.mcp.json`.
- DeepSeek can inspect a configured repository root through internal read-only tools.
- Path traversal and symlink escape are blocked.
- Repository ignore rules are respected.
- `deepseek_task` works with a mock API in tests.
- `deepseek_review` works with a mock API in tests.
- Every completed/aborted worker run includes a usage footer.
- Long internal message history is compacted into structured working memory.
- Claude-facing responses respect configured target/hard limits and omit raw worker transcripts.
- Run budgets stop additional model calls after limits are reached.
- Runtime model/budget values come from `config/deepseek-worker.yaml`.
- `GLOBAL_CLAUDE.md` exists and contains explicit visible delegation-notice rules.
- `README.md` is written last and documents install, usage, configuration, token accounting, security model, troubleshooting, and uninstall.
