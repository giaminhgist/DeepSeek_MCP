"""Usage accounting tests: aggregation, footer invariants, cost math."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepseek_mcp.compaction.working_memory import WorkingMemory
from deepseek_mcp.deepseek.worker_loop import WorkerResult
from deepseek_mcp.tools.common import compose_result
from deepseek_mcp.usage.budget import Budget
from deepseek_mcp.usage.footer import format_footer, format_process_usage
from deepseek_mcp.usage.tracker import (
    ProviderUsage,
    UsageTracker,
    estimate_call_cost,
)
from tests.conftest import make_test_config


def _tracker(tmp_path: Path) -> UsageTracker:
    return UsageTracker(make_test_config(tmp_path).pricing)


def test_aggregates_multiple_provider_responses(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(
        run,
        ProviderUsage(input_tokens=100, output_tokens=50, cache_read_tokens=30),
    )
    tracker.record(
        run,
        ProviderUsage(input_tokens=200, output_tokens=70, cache_read_tokens=None),
    )
    assert run.api_calls == 2
    assert run.input_tokens == 300
    assert run.output_tokens == 120
    assert run.total_tokens == 420
    assert run.cache_read_tokens == 30  # second call reported none: not added


def test_cost_math_uses_config(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    usage = ProviderUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = estimate_call_cost(config.pricing, usage)
    # all input as cache miss (conservative): 0.435 + 0.87 = 1.305
    assert cost == pytest.approx(0.435 + 0.87)
    usage_cache = ProviderUsage(
        input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=400_000
    )
    cost_cache = estimate_call_cost(config.pricing, usage_cache)
    assert cost_cache == pytest.approx(0.4 * 0.003625 + 0.6 * 0.435 + 0.87)


def test_conservative_mode_without_cache_details(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    tracker = UsageTracker(config.pricing)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(run, ProviderUsage(input_tokens=1000, output_tokens=0))
    assert tracker.cost_mode == "conservative"
    tracker.finish(run, "ok")
    summary = tracker.process_summary("deepseek-v4-pro")
    assert summary.cost_mode == "conservative"
    assert summary.cache_read_tokens is None


def test_footer_invariants(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    run = tracker.start_run("ds_20260822_010203_abcd1234", "deepseek-v4-pro")
    tracker.record(
        run,
        ProviderUsage(
            input_tokens=1000,
            output_tokens=200,
            cache_read_tokens=None,
            cache_write_tokens=None,
        ),
    )
    tracker.finish(run, "ok")
    footer = format_footer(run)
    lines = footer.splitlines()
    assert lines[0] == "---"
    assert lines[1] == "DeepSeek Worker Usage"
    assert "run_id: ds_20260822_010203_abcd1234" in footer
    assert "model: deepseek-v4-pro" in footer
    assert "input_tokens: 1000" in footer
    assert "output_tokens: 200" in footer
    assert "total_tokens: 1200" in footer
    assert "cache_read_tokens: n/a" in footer
    assert "cache_write_tokens: n/a" in footer
    assert "budget_status: ok" in footer


def test_footer_deterministic_cost_format(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(run, ProviderUsage(input_tokens=777, output_tokens=333))
    tracker.finish(run, "ok")
    footer = format_footer(run)
    assert "estimated_cost_usd: 0.000628" in footer  # 777*0.435/1e6 + 333*0.87/1e6


def test_footer_on_budget_stop(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(run, ProviderUsage(input_tokens=100, output_tokens=10))
    tracker.finish(run, "stopped", "total token limit reached")
    footer = format_footer(run)
    assert "budget_status: stopped" in footer


def test_budget_decision(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    budget = Budget(config.budget)
    tracker = _tracker(tmp_path)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    assert budget.check(run).allowed
    tracker.record(run, ProviderUsage(input_tokens=5000, output_tokens=100))
    decision = budget.check(run)
    assert not decision.allowed
    assert "input token limit" in (decision.reason or "")


def test_process_totals_aggregate_runs(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    tracker = UsageTracker(config.pricing)
    for run_id in ("ds_run_1", "ds_run_2"):
        run = tracker.start_run(run_id, "deepseek-v4-pro")
        tracker.record(run, ProviderUsage(input_tokens=100, output_tokens=25))
        tracker.finish(run, "ok" if run_id == "ds_run_1" else "stopped", None)
    summary = tracker.process_summary("deepseek-v4-pro")
    assert summary.runs == 2
    assert summary.stopped_runs == 1
    assert summary.api_calls == 2
    assert summary.input_tokens == 200
    assert summary.output_tokens == 50
    assert summary.total_tokens == 250
    report = format_process_usage(summary, model="deepseek-v4-pro", config=config)
    assert "DeepSeek Worker Process Usage" in report
    assert "runs: 2" in report
    assert "max_total_tokens_per_run: 3000" in report


def test_process_usage_with_cache_totals(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(
        run,
        ProviderUsage(input_tokens=100, output_tokens=10, cache_read_tokens=40),
    )
    tracker.finish(run, "ok")
    summary = tracker.process_summary("deepseek-v4-pro")
    assert summary.cache_read_tokens == 40
    assert summary.cost_mode == "detailed"


def test_compaction_does_not_rewrite_usage(tmp_path: Path) -> None:
    """Compaction changes visible text, never provider-reported totals."""
    config = make_test_config(tmp_path)
    tracker = UsageTracker(config.pricing)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(run, ProviderUsage(input_tokens=1234, output_tokens=567))
    tracker.finish(run, "ok")
    result = WorkerResult(
        status="ok",
        text="## Worker result\n" + ("x" * 6000) + "\n",
        reason=None,
        usage=run,
        memory=WorkingMemory(objective="t"),
        transcript=[],
    )
    composed = compose_result(result, config, "brief")
    footer = composed.split("---", 1)[1]
    assert "input_tokens: 1234" in footer
    assert "output_tokens: 567" in footer
    assert "total_tokens: 1801" in footer
    # Footer is always last.
    assert composed.rstrip().endswith("budget_status: ok")


def test_footer_survives_heavy_compaction(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    tracker = UsageTracker(config.pricing)
    run = tracker.start_run("ds_test", "deepseek-v4-pro")
    tracker.record(run, ProviderUsage(input_tokens=10, output_tokens=10))
    tracker.finish(run, "ok")
    result = WorkerResult(
        status="ok",
        text="## Worker result\n" + ("blah " * 3000) + "\n",
        reason=None,
        usage=run,
        memory=WorkingMemory(objective="t"),
        transcript=[],
    )
    composed = compose_result(result, config, "brief")
    assert len(composed) <= config.compaction.final_hard_limit_chars + 600
    assert composed.endswith("budget_status: ok")
    assert "DeepSeek Worker Usage" in composed
