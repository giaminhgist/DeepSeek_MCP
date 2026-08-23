"""Compaction tests: bounded tool results, working memory, final responses."""

from __future__ import annotations

from pathlib import Path

from deepseek_mcp.compaction.final_response import compact_final_response
from deepseek_mcp.compaction.tool_results import (
    bound_text,
    format_list_result,
    format_read_result,
    format_search_result,
)
from deepseek_mcp.compaction.working_memory import (
    WorkingMemory,
    estimate_tokens,
    extract_path_line_refs,
)
from deepseek_mcp.repo.listing import ListEntry, ListResult
from deepseek_mcp.repo.reader import ReadResult
from deepseek_mcp.repo.search import SearchMatch, SearchResult
from tests.conftest import make_test_config


def _config(tmp_path: Path, **overrides):
    return make_test_config(tmp_path, overrides)


# --- tool result bounding --------------------------------------------------


def test_bound_text_truncates_with_marker() -> None:
    text = "x" * 5000
    result = bound_text(text, 1000)
    assert len(result) <= 1000
    assert "truncated" in result


def test_bound_text_passes_through_small_text() -> None:
    assert bound_text("short", 1000) == "short"


def test_search_result_is_bounded() -> None:
    matches = [SearchMatch(path="src/a.py", line=i, text=f"match {i}") for i in range(1, 100)]
    result = SearchResult(
        query="q",
        case_sensitive=True,
        regex=False,
        matches=matches,
        total=100,
        searched_files=5,
        skipped_files=0,
        has_more=False,
    )
    text = format_search_result(result, 500)
    assert len(text) <= 500
    assert "match 1" in text


def test_read_result_is_bounded() -> None:
    lines = [(i, f"line content {i} " * 5) for i in range(1, 400)]
    result = ReadResult(
        path="big.py",
        start_line=1,
        end_line=400,
        total_lines=400,
        lines=lines,
        truncated_bytes=False,
        has_more_before=False,
        has_more_after=False,
    )
    text = format_read_result(result, 800)
    assert len(text) <= 800
    assert "[repo_read]" in text


def test_list_result_is_bounded() -> None:
    entries = [ListEntry(path=f"dir/f{i}.py", kind="file", size=10) for i in range(300)]
    result = ListResult(
        entries=entries, total=300, has_more=False, next_offset=300, truncated=False
    )
    text = format_list_result(result, 600)
    assert len(text) <= 600


# --- working memory --------------------------------------------------------


def test_memory_renders_all_sections() -> None:
    memory = WorkingMemory(objective="map auth")
    memory.note_evidence("token parsed here", path="src/auth.py", start_line=31, end_line=55)
    memory.findings.append("finding one")
    memory.uncertainties.append("unclear how refresh works")
    memory.inspected_paths.append("src/auth.py")
    memory.discarded.append("H1 rejected")
    memory.next_checks.append("src/middleware/session.py")
    rendered = memory.render()
    assert "map auth" in rendered
    assert "src/auth.py:31-55" in rendered
    assert "finding one" in rendered
    assert "unclear how refresh works" in rendered


def test_memory_dedupes_evidence_locations() -> None:
    memory = WorkingMemory(objective="x")
    memory.note_evidence("a", path="src/a.py", start_line=1)
    memory.note_evidence("b", path="src/a.py", start_line=1)
    assert len(memory.evidence) == 1


def test_memory_bounded_render_drops_low_priority() -> None:
    memory = WorkingMemory(objective="keep me")
    for i in range(50):
        memory.discarded.append(f"hyp {i}")
    rendered = memory.bounded_render(400)
    assert len(rendered) <= 400 + 80
    assert "keep me" in rendered


def test_objective_survives_repeated_compactions() -> None:
    memory = WorkingMemory(objective="OBJ: trace call graph")
    for _ in range(3):
        memory.absorb_tool_result("repo_read", "- `src/a.py:5-9` — step one\n")
        memory.absorb_tool_result("repo_search", "- `src/b.py:3` — step two\n")
        text = memory.render()
        assert "OBJ: trace call graph" in text
        assert "src/a.py:5-9" in text
        assert "src/b.py:3" in text


def test_extract_path_line_refs() -> None:
    refs = extract_path_line_refs(
        "see `src/auth.py:31-55` and `tests/test_x.py:12`; no ref here 3:4"
    )
    assert ("src/auth.py", 31, 55) in refs
    assert ("tests/test_x.py", 12, None) in refs


def test_estimate_tokens_is_conservative() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 300) == 100


# --- final response compaction ---------------------------------------------


LONG_RESPONSE = (
    "## Worker result\n"
    + ("The auth flow works like this.\n" * 50)
    + "## Key findings\n"
    + "- [severity: high] [confidence: high] src/a.py:10-20 — bug one\n"
    + "- [severity: low] src/b.py:3 — nit\n"
    + "- [severity: critical] src/c.py:7 — serious\n"
    + "## Evidence\n"
    + "- `src/a.py:10-20` — evidence for bug one\n"
    + "- `src/a.py:10-20` — evidence for bug one\n"
    + "- `src/c.py:7-9` — serious evidence\n"
    + "## Uncertainties\n- whether fix A contradicts fix B\n"
    + "## Suggested next checks\n- rerun tests\n"
)


def _config_detail(tmp_path: Path):
    return make_test_config(tmp_path).compaction


def test_final_compaction_dedupes_evidence(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    result = compact_final_response(LONG_RESPONSE, "detailed", comp)
    assert result.count("evidence for bug one") == 1


def test_final_compaction_sorts_findings_by_severity(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    result = compact_final_response(LONG_RESPONSE, "detailed", comp, sort_findings=True)
    critical = result.find("critical")
    high = result.find("high")
    low = result.find("low] src/b.py")
    assert critical != -1 and high != -1 and low != -1
    assert critical < high < low


def test_final_compaction_keeps_contradictions(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    result = compact_final_response(LONG_RESPONSE, "normal", comp)
    assert "whether fix A contradicts fix B" in result


def test_detail_modes_have_distinct_targets(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    brief = compact_final_response(LONG_RESPONSE, "brief", comp)
    normal = compact_final_response(LONG_RESPONSE, "normal", comp)
    detailed = compact_final_response(LONG_RESPONSE, "detailed", comp)
    assert len(brief) <= len(normal) <= len(detailed)
    assert brief != normal


def test_hard_limit_always_enforced(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    huge = "## Worker result\n" + "lots of content.\n" * 4000
    result = compact_final_response(huge, "detailed", comp)
    assert len(result) <= comp.final_hard_limit_chars
    assert "compacted" in result  # omission marker present


def test_final_compaction_is_deterministic(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    first = compact_final_response(LONG_RESPONSE, "normal", comp)
    second = compact_final_response(LONG_RESPONSE, "normal", comp)
    assert first == second


def test_final_compaction_omission_marker_for_findings(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    many_findings = "## Key findings\n" + "".join(
        f"- [severity: low] src/f{i}.py:1 — minor issue {i}\n" for i in range(20)
    )
    result = compact_final_response(many_findings, "brief", comp)
    assert "findings omitted" in result


def test_evidence_line_refs_survive_compaction(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    result = compact_final_response(LONG_RESPONSE, "brief", comp)
    assert "src/c.py:7-9" in result
    assert "src/a.py:10-20" in result


def test_unparseable_text_still_bounded(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    blob = "no headings here at all.\n" * 3000
    result = compact_final_response(blob, "brief", comp)
    assert len(result) <= comp.final_hard_limit_chars


def test_empty_text_passes_through(tmp_path: Path) -> None:
    comp = _config_detail(tmp_path)
    assert compact_final_response("   ", "normal", comp) == "   "
