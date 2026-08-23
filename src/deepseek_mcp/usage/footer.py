"""The single usage footer formatter used by every worker tool."""

from __future__ import annotations

from deepseek_mcp.config.models import BudgetConfig, Config
from deepseek_mcp.usage.tracker import ProcessUsage, RunUsage


def _cache_value(tokens: int | None) -> str:
    return str(tokens) if tokens is not None else "n/a"


def format_footer(usage: RunUsage) -> str:
    """Render the mandatory end-of-run usage footer.

    ``total_tokens`` is ``input_tokens + output_tokens`` and never counts
    cache counters a second time.
    """
    return (
        "---\n"
        "DeepSeek Worker Usage\n"
        f"run_id: {usage.run_id}\n"
        f"model: {usage.model}\n"
        f"api_calls: {usage.api_calls}\n"
        f"input_tokens: {usage.input_tokens}\n"
        f"output_tokens: {usage.output_tokens}\n"
        f"cache_read_tokens: {_cache_value(usage.cache_read_tokens)}\n"
        f"cache_write_tokens: {_cache_value(usage.cache_write_tokens)}\n"
        f"total_tokens: {usage.total_tokens}\n"
        f"estimated_cost_usd: {usage.estimated_cost_usd:.6f}\n"
        f"budget_status: {usage.budget_status}"
    )


def format_process_usage(
    summary: ProcessUsage,
    *,
    model: str,
    config: Config,
) -> str:
    """Render ``deepseek_usage(scope="process")`` output. Makes no API call."""
    budget: BudgetConfig = config.budget
    pricing = config.pricing
    rates = pricing.per_million_tokens
    return (
        "DeepSeek Worker Process Usage\n"
        f"model: {model}\n"
        f"config: {config.config_path}\n"
        f"runs: {summary.runs}\n"
        f"stopped_runs: {summary.stopped_runs}\n"
        f"api_calls: {summary.api_calls}\n"
        f"input_tokens: {summary.input_tokens}\n"
        f"output_tokens: {summary.output_tokens}\n"
        f"cache_read_tokens: {_cache_value(summary.cache_read_tokens)}\n"
        f"cache_write_tokens: {_cache_value(summary.cache_write_tokens)}\n"
        f"total_tokens: {summary.total_tokens}\n"
        f"estimated_cost_usd: {summary.estimated_cost_usd:.6f}\n"
        f"cost_mode: {summary.cost_mode}\n"
        "\n"
        "Configured run limits\n"
        f"max_api_calls_per_run: {budget.max_api_calls_per_run}\n"
        f"max_input_tokens_per_run: {budget.max_input_tokens_per_run}\n"
        f"max_output_tokens_per_run: {budget.max_output_tokens_per_run}\n"
        f"max_total_tokens_per_run: {budget.max_total_tokens_per_run}\n"
        f"max_estimated_cost_usd_per_run: {budget.max_estimated_cost_usd_per_run}\n"
        f"on_limit: {budget.on_limit}\n"
        f"max_agent_iterations: {config.worker.max_agent_iterations}\n"
        f"max_run_seconds: {config.worker.max_run_seconds}\n"
        "\n"
        "Pricing snapshot\n"
        f"currency: {pricing.currency}\n"
        f"source: {pricing.source}\n"
        f"snapshot_date: {pricing.snapshot_date}\n"
        f"input_cache_hit_per_million: {rates.input_cache_hit}\n"
        f"input_cache_miss_per_million: {rates.input_cache_miss}\n"
        f"output_per_million: {rates.output}"
    )
