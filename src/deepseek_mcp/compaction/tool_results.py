"""Stage A compaction: bounded, deduplicated repository tool results.

Applied BEFORE tool output enters the next DeepSeek model request. Every
formatter enforces ``max_tool_result_chars`` and marks omissions explicitly.
"""

from __future__ import annotations

from deepseek_mcp.repo.bash import BashResult
from deepseek_mcp.repo.fs import GlobResult, GrepResult
from deepseek_mcp.repo.listing import ListResult
from deepseek_mcp.repo.reader import ReadResult, StatResult
from deepseek_mcp.repo.search import SearchResult

_TRUNCATION_MARKER = "[truncated: {n} chars omitted; narrow the request]"


def bound_text(text: str, max_chars: int) -> str:
    """Hard-bound arbitrary text with an explicit truncation marker."""
    if len(text) <= max_chars:
        return text
    keep = max(max_chars - 200, max_chars // 2)
    marker = _TRUNCATION_MARKER.format(n=len(text) - keep)
    result = text[:keep] + "\n" + marker
    return result[:max_chars]


def format_list_result(result: ListResult, max_chars: int) -> str:
    lines = [f"[repo_list] entries: {len(result.entries)} returned"]
    for entry in result.entries:
        size = f" ({entry.size} bytes)" if entry.size is not None else ""
        lines.append(f"- {entry.path} [{entry.kind}]{size}")
    if result.has_more:
        lines.append(
            f"[{result.total - result.next_offset} additional entries omitted; "
            f"continue with offset={result.next_offset}]"
        )
    if result.truncated:
        lines.append("[listing truncated by scan cap]")
    return bound_text("\n".join(lines), max_chars)


def format_search_result(result: SearchResult, max_chars: int) -> str:
    lines = [
        f"[repo_search] query: {result.query!r}",
        f"matches: {result.total} total, {len(result.matches)} returned "
        f"({result.searched_files} files searched, {result.skipped_files} skipped)",
    ]
    for match in result.matches:
        lines.append(f"- {match.path}:{match.line} — {match.text}")
    omitted = result.total - len(result.matches)
    if omitted > 0:
        lines.append(
            f"[{omitted} additional matches omitted; narrow the query or use "
            f"case_sensitive/regex to refine]"
        )
    return bound_text("\n".join(lines), max_chars)


def format_grep_result(result: GrepResult, max_chars: int) -> str:
    lines = [
        f"[fs_grep] pattern: {result.pattern!r} (regex={result.regex})",
        f"matches: {result.total} total, {len(result.matches)} returned "
        f"({result.searched_files} files searched, {result.skipped_files} skipped)",
    ]
    for path, line_no, text in result.matches:
        lines.append(f"- {path}:{line_no} — {text}")
    omitted = result.total - len(result.matches)
    if omitted > 0:
        lines.append(f"[{omitted} additional matches omitted; narrow the pattern]")
    return bound_text("\n".join(lines), max_chars)


def format_read_result(result: ReadResult, max_chars: int) -> str:
    header = (
        f"[repo_read] {result.path} lines {result.start_line}-{result.end_line} "
        f"of {result.total_lines}"
    )
    if result.truncated_bytes:
        header += " [byte-truncated]"
    lines = [header]
    for line_no, content in result.lines:
        lines.append(f"{line_no:>6}\t{content}")
    if result.has_more_before:
        lines.append("[earlier lines exist; read with a smaller start_line]")
    if result.has_more_after:
        lines.append("[later lines exist; read with a larger end_line]")
    return bound_text("\n".join(lines), max_chars)


def format_stat_result(result: StatResult) -> str:
    return (
        f"[repo_stat] {result.path}: kind={result.kind} "
        f"size={result.size_bytes} bytes mtime={result.mtime:.0f}"
    )


def format_diff_result(diff_text: str, max_chars: int) -> str:
    if not diff_text.strip():
        return "[git_diff] empty diff"
    # Keep the per-file headers when truncating so the model sees what files
    # the diff touched even after char bounding.
    file_lines = [line for line in diff_text.splitlines() if line.startswith("diff --git")]
    prefix = "\n".join(file_lines) + "\n" if file_lines else ""
    body = bound_text(diff_text, max_chars)
    if len(prefix) + len(body) <= max_chars:
        return "[git_diff]\n" + prefix + body
    return bound_text("[git_diff]\n" + prefix + body, max_chars)


def format_bash_result(result: BashResult, max_chars: int) -> str:
    head = f"[fs_bash] exit_code={result.exit_code} timed_out={result.timed_out}\n"
    return bound_text(head + result.output, max_chars)


def format_glob_result(result: GlobResult, max_chars: int) -> str:
    lines = [
        f"[fs_glob] pattern: {result.pattern!r}",
        f"paths: {len(result.paths)} returned, {result.skipped} skipped",
    ]
    lines.extend(f"- {path}" for path in result.paths)
    if result.has_more:
        lines.append("[additional matches omitted; narrow the pattern]")
    return bound_text("\n".join(lines), max_chars)
