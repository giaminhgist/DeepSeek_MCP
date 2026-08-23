# DeepSeek MCP for Claude Code — Spec Pack

This folder is a build specification for Claude Code. The goal is to implement a new repository from scratch where:

- Real Claude remains the **Orchestrator / Planner / Final Reviewer**.
- A local MCP server exposes a **DeepSeek Worker** to Claude Code.
- The worker calls the Anthropic-compatible DeepSeek API with model `deepseek-v4-pro`.
- Token-heavy repository work is delegated to DeepSeek: repository exploration, reading files, code review, summarization, large diff analysis, log analysis, and other high-context read-heavy tasks.
- DeepSeek usage is reported at the end of every worker run.
- Long tool outputs, internal message history, summaries, and final responses are compacted so DeepSeek does not flood Claude's context.
- Model, context, token, cost, and runtime budgets live in one dedicated configuration file.
- The worker is read-only by default. Claude owns edits and final decisions.
- The finished implementation must include a comprehensive `README.md` with install, configuration, usage, troubleshooting, upgrade, and uninstall instructions.

## Read order for Claude Code

Read and follow these files in order:

1. `01_BUILD_PROMPT.md`
2. `02_PROJECT_REQUIREMENTS.md`
3. `03_ARCHITECTURE.md`
4. `04_MCP_TOOL_CONTRACT.md`
5. `05_CONFIG_AND_BUDGET.md`
6. `06_TOKEN_ACCOUNTING.md`
7. `07_SECURITY_TESTING_ACCEPTANCE.md`
8. `09_CONTEXT_COMPACTION.md`
9. `08_README_REQUIREMENTS.md`
10. `GLOBAL_CLAUDE.md`

Do not skip files. If two requirements conflict, use this priority:

1. Security and hard constraints
2. Project requirements
3. Tool contract
4. Architecture
5. Implementation convenience

## Important architecture invariant

**Never point the parent Claude Code process at the DeepSeek Anthropic endpoint.**

Do not set these variables globally for Claude Code:

- `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`
- `ANTHROPIC_AUTH_TOKEN=<DeepSeek key>`
- `ANTHROPIC_MODEL=deepseek-v4-pro`

Doing so would replace Claude itself with DeepSeek, violating the purpose of this project.

The DeepSeek endpoint and API key belong only to the MCP worker process, using worker-specific environment variable names such as:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_CONFIG`

## Current reference facts

As of 2026-08-22:

- DeepSeek documents an Anthropic-compatible API at `https://api.deepseek.com/anthropic`.
- The model ID is `deepseek-v4-pro`.
- DeepSeek V4 uses a 1M context window according to current official documentation.
- Claude Code supports project-scoped MCP servers in `.mcp.json`.
- Claude Code project-scoped `.mcp.json` supports environment-variable expansion.

Treat pricing and API behavior as configurable and updateable rather than permanent constants.

Compaction behavior is specified in `09_CONTEXT_COMPACTION.md`. The default design never returns a raw DeepSeek agent transcript to Claude.

## References

- DeepSeek API docs: https://api-docs.deepseek.com/
- DeepSeek + Claude Code: https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/
- DeepSeek pricing: https://api-docs.deepseek.com/quick_start/pricing/
- Claude Code MCP docs: https://code.claude.com/docs/en/mcp
- Behavioral inspiration: https://github.com/multica-ai/andrej-karpathy-skills
