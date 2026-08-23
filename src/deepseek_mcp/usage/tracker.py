"""Per-run and process-wide usage aggregation from provider-reported numbers.

Provider-reported usage is authoritative; compaction never rewrites these
totals. Estimated cost uses the YAML pricing snapshot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from deepseek_mcp.config.models import PricingConfig


@dataclass(slots=True)
class ProviderUsage:
    """Usage reported by one DeepSeek API response."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass(slots=True)
class RunUsage:
    """Aggregated usage for one worker run (one MCP tool invocation)."""

    run_id: str
    model: str
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated_cost_usd: float = 0.0
    budget_status: str = "ok"
    stopped_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class ProcessUsage:
    """Cumulative usage since the MCP server process started."""

    runs: int = 0
    stopped_runs: int = 0
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated_cost_usd: float = 0.0
    cost_mode: str = "conservative"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def new_run_id() -> str:
    """Sortable run id, e.g. ``ds_20260822_143205_a1b2c3d4``."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"ds_{stamp}_{uuid.uuid4().hex[:8]}"


def estimate_call_cost(pricing: PricingConfig, usage: ProviderUsage) -> float:
    """Cost of one call: cache-hit/miss input split plus output.

    Without cache metrics all input tokens count as cache miss
    (conservative). Provider billing remains authoritative.
    """
    rates = pricing.per_token
    cache_hit = usage.cache_read_tokens if usage.cache_read_tokens is not None else 0
    cache_miss = max(usage.input_tokens - cache_hit, 0)
    return (
        cache_hit * rates.input_cache_hit
        + cache_miss * rates.input_cache_miss
        + usage.output_tokens * rates.output
    )


class UsageTracker:
    """Aggregates usage across API calls and runs; owns cost estimation."""

    def __init__(self, pricing: PricingConfig) -> None:
        self.pricing = pricing
        self._runs: list[RunUsage] = []
        self.cost_mode = "conservative"

    def start_run(self, run_id: str, model: str) -> RunUsage:
        return RunUsage(run_id=run_id, model=model)

    def record(self, run: RunUsage, usage: ProviderUsage) -> None:
        """Fold one provider response into the run aggregates."""
        run.api_calls += 1
        run.input_tokens += usage.input_tokens
        run.output_tokens += usage.output_tokens
        if usage.cache_read_tokens is not None:
            run.cache_read_tokens = (run.cache_read_tokens or 0) + usage.cache_read_tokens
            self.cost_mode = "detailed"
        if usage.cache_write_tokens is not None:
            run.cache_write_tokens = (run.cache_write_tokens or 0) + usage.cache_write_tokens
        run.estimated_cost_usd += estimate_call_cost(self.pricing, usage)

    def finish(self, run: RunUsage, status: str, reason: str | None = None) -> None:
        """Close a run into process history."""
        run.budget_status = status
        run.stopped_reason = reason
        self._runs.append(run)

    @property
    def last_run(self) -> RunUsage | None:
        return self._runs[-1] if self._runs else None

    def process_summary(self, model: str) -> ProcessUsage:
        summary = ProcessUsage(cost_mode=self.cost_mode)
        for run in self._runs:
            summary.runs += 1
            if run.budget_status != "ok":
                summary.stopped_runs += 1
            summary.api_calls += run.api_calls
            summary.input_tokens += run.input_tokens
            summary.output_tokens += run.output_tokens
            if run.cache_read_tokens is not None:
                summary.cache_read_tokens = (summary.cache_read_tokens or 0) + run.cache_read_tokens
            if run.cache_write_tokens is not None:
                summary.cache_write_tokens = (
                    summary.cache_write_tokens or 0
                ) + run.cache_write_tokens
            summary.estimated_cost_usd += run.estimated_cost_usd
        summary.estimated_cost_usd = round(summary.estimated_cost_usd, 6)
        return summary
