"""Compaction: working memory, bounded tool results, final responses."""

from deepseek_mcp.compaction.final_response import compact_final_response
from deepseek_mcp.compaction.tool_results import bound_text
from deepseek_mcp.compaction.working_memory import (
    EvidenceItem,
    WorkingMemory,
    estimate_tokens,
    extract_path_line_refs,
)

__all__ = [
    "EvidenceItem",
    "WorkingMemory",
    "bound_text",
    "compact_final_response",
    "estimate_tokens",
    "extract_path_line_refs",
]
