"""``deepseek_usage`` — usage/budget reporting. Never makes a model call."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from deepseek_mcp.config.models import Config
from deepseek_mcp.usage.footer import format_footer, format_process_usage
from deepseek_mcp.usage.tracker import UsageTracker


class DeepSeekUsageInput(BaseModel):
    scope: Literal["last_run", "process"] = "process"


def usage_report(
    *,
    config: Config,
    tracker: UsageTracker,
    scope: Literal["last_run", "process"],
) -> str:
    """Render usage statistics without contacting the provider."""
    if scope == "last_run":
        last = tracker.last_run
        if last is None:
            return "No completed DeepSeek worker runs yet in this process."
        text = format_footer(last)
        if last.stopped_reason:
            text += f"\nstop_reason: {last.stopped_reason}"
        return text
    return format_process_usage(
        tracker.process_summary(config.model.name),
        model=config.model.name,
        config=config,
    )
