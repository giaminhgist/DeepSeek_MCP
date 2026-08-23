"""Claude-facing MCP tool handlers: deepseek_task, deepseek_review, deepseek_usage."""

from deepseek_mcp.tools.review import DeepSeekReviewInput, run_review
from deepseek_mcp.tools.task import DeepSeekTaskInput, run_task
from deepseek_mcp.tools.usage import DeepSeekUsageInput, usage_report

__all__ = [
    "DeepSeekReviewInput",
    "DeepSeekTaskInput",
    "DeepSeekUsageInput",
    "run_review",
    "run_task",
    "usage_report",
]
