"""Per-run budget enforcement.

Exact after each API response; conservative before the next call. A single
final provider response may slightly cross a cumulative threshold, but no
further calls are allowed afterward.
"""

from __future__ import annotations

from dataclasses import dataclass

from deepseek_mcp.config.models import BudgetConfig
from deepseek_mcp.usage.tracker import RunUsage


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str | None


class Budget:
    def __init__(self, config: BudgetConfig) -> None:
        self.config = config

    def check(self, usage: RunUsage) -> BudgetDecision:
        """Decide whether another API call is allowed."""
        checks = (
            (
                usage.api_calls >= self.config.max_api_calls_per_run,
                f"api_calls limit reached ({self.config.max_api_calls_per_run})",
            ),
            (
                usage.input_tokens >= self.config.max_input_tokens_per_run,
                f"input token limit reached ({self.config.max_input_tokens_per_run})",
            ),
            (
                usage.output_tokens >= self.config.max_output_tokens_per_run,
                f"output token limit reached ({self.config.max_output_tokens_per_run})",
            ),
            (
                usage.total_tokens >= self.config.max_total_tokens_per_run,
                f"total token limit reached ({self.config.max_total_tokens_per_run})",
            ),
            (
                usage.estimated_cost_usd >= self.config.max_estimated_cost_usd_per_run,
                "estimated cost limit reached "
                f"({self.config.max_estimated_cost_usd_per_run:.2f} USD)",
            ),
        )
        for blocked, reason in checks:
            if blocked:
                return BudgetDecision(allowed=False, reason=reason)
        return BudgetDecision(allowed=True, reason=None)
