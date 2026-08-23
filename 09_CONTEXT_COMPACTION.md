# Context and Response Compaction

## 1. Goal

DeepSeek is the high-context worker, but its output must not become a new context problem for Claude.

The MCP server must compact both:

1. **worker-side message/context history** sent back to DeepSeek during long agent loops, and
2. **Claude-facing final responses** returned by `deepseek_task` and `deepseek_review`.

The default behavior is:

```text
large repository evidence
        ↓
DeepSeek internal analysis
        ↓
structured compact working memory
        ↓
compact final answer + evidence index
        ↓
Claude selectively verifies only what matters
```

Do not return raw agent transcripts, full tool-call history, or large copied file contents to Claude.

## 2. Compaction principles

Compaction must preserve decision-relevant information:

- task/objective,
- confirmed facts,
- file paths,
- line ranges,
- high-confidence findings,
- unresolved questions,
- assumptions/uncertainties,
- suggested next checks,
- budget/token state.

Compaction should remove or collapse:

- duplicate observations,
- repeated file excerpts,
- verbose reasoning,
- stale intermediate hypotheses,
- successful tool chatter,
- low-value prose,
- large raw search/list/read results,
- repeated summaries of the same evidence.

Never compact away a material contradiction. Preserve both sides and mark the conflict.

## 3. No raw chain-of-thought requirement

The worker must not rely on returning hidden/private reasoning or a complete reasoning transcript.

The Claude-facing result should contain concise conclusions and evidence, not step-by-step private reasoning.

Internal compaction should summarize state in terms of facts/evidence/tasks, not preserve verbose reasoning traces.

## 4. Two-stage compaction

### Stage A — tool-result compaction

Before repository tool output is appended to the next DeepSeek model request:

- enforce per-tool result limits,
- strip irrelevant metadata,
- collapse repeated matches,
- prefer path + line-range + short excerpts,
- truncate safely with an explicit marker,
- allow DeepSeek to request a narrower follow-up read.

Example:

```text
[repo_search compact result]
query: "FooConfig"
matches: 84 total, 25 returned
- src/config.ts:18 — export interface FooConfig
- src/server.ts:42 — function createServer(config: FooConfig)
...
[59 additional matches omitted; narrow the query or request another page if needed]
```

Do not inject multi-megabyte tool results into the model history.

### Stage B — rolling message-history compaction

When the DeepSeek agent loop approaches the configured soft context threshold, compact older worker messages/tool results into one structured **working-memory message**.

Preserve:

- original task,
- current scope,
- evidence index,
- confirmed findings,
- unresolved items,
- files already inspected,
- constraints,
- latest relevant tool results.

Keep a configurable number of the most recent messages verbatim.

Do not repeatedly summarize a summary without retaining stable evidence identifiers. The compactor should merge/update the existing working memory.

Conceptual compact working memory:

```markdown
# Worker Working Memory

## Objective
...

## Confirmed evidence
- E01 `src/auth.ts:31-55` — token parsed here.
- E02 `src/user.ts:80-103` — user lookup occurs here.

## Current findings
- ...

## Open questions
- ...

## Files inspected
- `src/auth.ts`
- `src/user.ts`

## Discarded/stale hypotheses
- Hypothesis H1 rejected because E05 contradicts it.

## Next useful reads
- `src/middleware/session.ts`
```

## 5. Claude-facing final compaction

DeepSeek should be prompted to produce a compact structured answer first.

Then the MCP server must enforce a final response budget before returning to Claude.

Preferred final structure:

```markdown
## Worker result
<short answer; prioritize conclusions>

## Key findings
1. ...
2. ...

## Evidence
- `path/file.ts:10-32` — compact evidence statement
- ...

## Uncertainties
- ...

## Suggested next checks
- ...

---
DeepSeek Worker Usage
...
```

Rules:

- no raw transcript,
- no full source files,
- no giant code blocks,
- deduplicate findings,
- cap findings/evidence items,
- use file references instead of repeating code,
- omit empty sections,
- preserve the mandatory usage footer.

## 6. Deterministic server-side final compactor

The server must have a deterministic compaction layer that does **not require another model call**.

This layer is the last safety net when the model returns too much text.

It should:

1. Parse known worker sections when possible.
2. Keep the highest-priority findings.
3. Deduplicate evidence entries.
4. Keep file paths and line ranges.
5. Truncate oversized excerpts.
6. Remove low-priority/repeated prose.
7. Add an explicit omission marker.
8. Always preserve the usage footer.

Example marker:

```text
[response compacted by MCP server: 14 lower-priority items omitted]
```

Do not spend additional DeepSeek tokens merely to make an overlong final response shorter.

## 7. Detail modes

`output_detail` controls the target, not whether hard limits apply.

Suggested semantics:

### `brief`

- target final response: ~4,000 chars
- max findings: 8
- max evidence items: 12

### `normal`

- target final response: ~8,000 chars
- max findings: 15
- max evidence items: 24

### `detailed`

- target final response: ~12,000 chars
- max findings: 24
- max evidence items: 40

The configured hard final limit always wins.

A `detailed` response is still compact; it is not permission to return a transcript.

## 8. Configuration

Add a dedicated section to `config/deepseek-worker.yaml`:

```yaml
compaction:
  enabled: true

  # Internal tool results before they are sent back to DeepSeek.
  max_tool_result_chars: 24000

  # Rolling DeepSeek message-history compaction.
  worker_context_soft_limit_tokens: 320000
  worker_context_hard_limit_tokens: 520000
  preserve_recent_messages: 8

  # Claude-facing result limits.
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
```

The hard context threshold must be below the configured model context window.

`include_raw_transcript` must default to `false` and should remain a debugging-only setting if implemented at all. Do not document it as normal usage.

## 9. Token estimation for soft thresholds

Provider-returned usage remains authoritative for billing/accounting.

For deciding when to compact *before the next model call*, the implementation may use a conservative token estimator when exact next-request tokenization is unavailable.

The estimator is only for compaction/preflight decisions.

It must not replace provider usage in the final token footer.

## 10. Compaction algorithm requirements

The implementation may be simple, but must be predictable.

Recommended internal representation:

```ts
type EvidenceItem = {
  id: string;
  path?: string;
  startLine?: number;
  endLine?: number;
  statement: string;
  priority: "high" | "medium" | "low";
};

type WorkingMemory = {
  objective: string;
  facts: string[];
  findings: string[];
  evidence: EvidenceItem[];
  uncertainties: string[];
  inspectedPaths: string[];
  nextChecks: string[];
};
```

Prefer structured state over repeatedly summarizing free-form text.

## 11. Paging instead of flooding

Repository tools should support bounded results and, where practical, offset/cursor-style continuation.

DeepSeek should request another page or narrower range only if the omitted material is needed.

Examples:

- search: return first N ranked matches + `has_more`
- list: bounded entries + continuation metadata
- read: bounded line range + `has_more_before` / `has_more_after`
- diff: bounded chunks or file-scoped follow-up

This is preferable to a huge one-shot response.

## 12. Interaction with review findings

For `deepseek_review`:

- sort findings by severity, then confidence,
- compact duplicates that share one root cause,
- preserve at least the best evidence location for each retained finding,
- omit generic praise before omitting concrete findings,
- if findings exceed the configured limit, say how many lower-priority findings were omitted.

## 13. Interaction with budget stops

If a run stops because of token/cost/time budget:

- do not make another model call for summarization,
- return the latest structured working memory,
- run deterministic final compaction,
- state that the result is partial,
- preserve usage footer.

## 14. Required automated tests

Add tests for:

- oversized repo-read result is compacted before model history insertion,
- repeated search matches are deduplicated,
- rolling history compaction triggers at the soft threshold,
- original objective survives multiple compactions,
- evidence path/line references survive compaction,
- contradictions are not silently discarded,
- recent messages are preserved,
- hard context limit prevents another provider call,
- `brief`, `normal`, and `detailed` use different targets,
- final oversized DeepSeek response is deterministically compacted,
- raw transcript is absent by default,
- final usage footer is never truncated,
- compaction does not change token accounting totals,
- budget-stop partial result is compacted without another model call.

## 15. Acceptance criteria

- [ ] Tool/file outputs are bounded before being added to DeepSeek messages.
- [ ] Long DeepSeek message history is compacted into structured working memory.
- [ ] Claude never receives the raw worker transcript by default.
- [ ] Claude-facing response has target and hard size limits.
- [ ] Evidence file paths and line ranges survive compaction.
- [ ] Final compaction does not require another paid model call.
- [ ] Usage footer is always preserved.
- [ ] `output_detail` changes compactness without disabling safety limits.
