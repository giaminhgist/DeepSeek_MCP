# Token Accounting — Python

## 1. Requirement

The DeepSeek worker must show how many DeepSeek tokens were used at the end of **every Claude-facing worker run**.

A run means one invocation of:

- `deepseek_task`, or
- `deepseek_review`.

A run may contain many DeepSeek API requests due to internal tool calls.

## 2. Counters

At minimum track:

```text
api_calls
input_tokens
output_tokens
total_tokens
estimated_cost_usd
```

Track these when exposed by the provider:

```text
cache_read_tokens
cache_write_tokens
```

Also maintain process totals.

## 3. Source of truth

Use usage fields returned by the DeepSeek Anthropic-compatible API.

Do not estimate token totals from character length when provider usage is available.

Character-based estimates may only be used for a preflight safety heuristic, never as the final displayed billed usage.

## 4. Multi-call aggregation

For each provider response:

```text
run.api_calls += 1
run.input_tokens += response.usage.input_tokens
run.output_tokens += response.usage.output_tokens
...
```

The final run footer shows the aggregate.

## 5. Tool-loop accounting

Internal repository tool execution itself does not count as model tokens.

However, when tool results are sent back to DeepSeek on a subsequent model request, the resulting provider-reported input tokens count normally.

## 6. Error accounting

If an API request fails before returning usage, do not invent usage.

Record:

- call attempt if useful internally,
- billed API calls only according to a clearly documented convention,
- known token usage from successful responses.

The footer should still be emitted.

## 7. Required footer formatter

Implement a single formatter function used by all worker tools. Do not duplicate footer formatting.

Conceptual Python type:

```python
from dataclasses import dataclass

@dataclass(slots=True)
class RunUsage:
    run_id: str
    model: str
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated_cost_usd: float = 0.0
    budget_status: str = "ok"
```

## 8. Footer invariants

Automated tests must verify:

- footer is always last,
- exact model is present,
- aggregate input/output are correct across several fake calls,
- `total_tokens = input_tokens + output_tokens`,
- missing cache fields render as `n/a`,
- cost formatting is deterministic,
- budget stop still has a footer,
- provider error still has a footer when the MCP tool can return a normal error result.

## 9. Process usage

`deepseek_usage(scope="process")` should aggregate prior runs since server startup.

It should not call the model.

Example:

```text
DeepSeek Worker Process Usage
model: deepseek-v4-pro
runs: 12
api_calls: 63
input_tokens: 1240441
output_tokens: 84772
total_tokens: 1325213
estimated_cost_usd: 0.612345
```

If the model changes during process lifetime through config reload (if config reload is implemented), totals may be grouped by model. Config reload is optional for v1; restart-to-reload is acceptable.

## 10. Compaction and accounting

Compaction must never alter the usage numbers already reported by DeepSeek.

- Tool-result compaction before a future provider call may reduce future input tokens.
- Rolling message-history compaction may reduce future input tokens.
- Deterministic final response compaction occurs after the provider response and does not change that response's billed usage.
- Never subtract omitted/compacted text from provider-reported token totals.
- Token estimators used to trigger compaction are not billing counters.

Tests must verify that the final usage footer still reports the aggregate provider usage even when the visible Claude-facing answer is heavily compacted.

## 11. Cost disclaimer

README must say estimated cost is informational and provider billing is authoritative.

Pricing is deliberately configured in YAML so it can be updated without a code release.
