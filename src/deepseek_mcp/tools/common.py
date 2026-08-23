"""Shared helpers for the Claude-facing MCP tool handlers."""

from __future__ import annotations

from deepseek_mcp.compaction.final_response import compact_final_response
from deepseek_mcp.compaction.tool_results import bound_text
from deepseek_mcp.config.models import Config, OutputDetail
from deepseek_mcp.deepseek.worker_loop import WorkerResult
from deepseek_mcp.usage.footer import format_footer


class WorkerInputError(Exception):
    """Bad tool input or missing runtime prerequisites (mapped to MCP errors)."""


def compose_result(
    result: WorkerResult,
    config: Config,
    detail: OutputDetail,
    *,
    sort_findings: bool = False,
) -> str:
    """Deterministic final compaction + debug transcript + mandatory footer.

    The footer is appended last and is never truncated by the compactor.
    """
    body = compact_final_response(
        result.text, detail, config.compaction, sort_findings=sort_findings
    )
    if config.compaction.include_raw_transcript and result.transcript:
        budget = max(0, config.compaction.final_hard_limit_chars - len(body) - 1200)
        if budget > 400:
            transcript_text = bound_text("\n".join(result.transcript), min(budget, 6000))
            body += "\n\n## Worker transcript (debug)\n" + transcript_text
    return body + "\n\n" + format_footer(result.usage)
