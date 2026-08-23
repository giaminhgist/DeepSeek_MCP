"""Stage C: deterministic server-side final response compaction.

Pure functions only — never a paid model call. Parses the worker's known
section structure when possible, keeps the highest-priority content, marks
omissions explicitly, and enforces the configured hard character limit.

The usage footer is appended by the caller AFTER compaction and is therefore
never truncated by this layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from deepseek_mcp.config.models import CompactionConfig, OutputDetail

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_RE = re.compile(r"\b(critical|high|medium|low)\b", re.IGNORECASE)
_CONFIDENCE_RE = re.compile(r"\b(high|medium|low)\b(?:\s+confidence)?", re.IGNORECASE)
_ITEM_START_RE = re.compile(r"^\s*([-*]|\d+\.)\s+")


# Canonical section names, in retention priority order.
_SECTION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("result", ("worker result", "result", "conclusion", "summary", "answer")),
    ("findings", ("key findings", "findings")),
    ("evidence", ("evidence", "key evidence")),
    ("uncertainties", ("uncertainties", "open questions", "uncertainty")),
    ("next_checks", ("suggested next checks", "next checks", "next steps")),
)
_CANONICAL_ORDER = ("result", "findings", "evidence", "uncertainties", "next_checks")
# Fraction of the detail target allocated to each canonical section.
_BUDGET_SHARE = {
    "result": 0.45,
    "findings": 0.25,
    "evidence": 0.20,
    "uncertainties": 0.05,
    "next_checks": 0.05,
    "other": 0.0,
}


@dataclass(slots=True)
class _Section:
    canonical: str
    title: str
    body: str


@dataclass(slots=True)
class _CompactionStats:
    dropped_chars: int = 0
    dropped_findings: int = 0
    dropped_evidence: int = 0
    markers: list[str] = field(default_factory=list)


def _normalize_heading(heading: str) -> str:
    # Strip trailing decorations like "(14 findings)" so headings with counts
    # still match canonical section names.
    plain = re.sub(r"\s*\([^)]*\)\s*$", "", heading.strip())
    normalized = plain.lower().rstrip(":")
    for canonical, aliases in _SECTION_ALIASES:
        if normalized in aliases or normalized.startswith(tuple(f"{alias} " for alias in aliases)):
            return canonical
    return "other"


def _parse_sections(text: str) -> list[_Section]:
    """Split text into canonical sections; pre-heading text becomes result."""
    lines = text.splitlines()
    sections: list[_Section] = []
    current: _Section | None = None
    preamble: list[str] = []
    for line in lines:
        match = _HEADING_RE.match(line)
        if match and len(line.strip()) <= 64:
            if current is not None:
                sections.append(current)
            current = _Section(
                canonical=_normalize_heading(match.group(2)),
                title=line.strip(),
                body="",
            )
        elif current is not None:
            current.body += line + "\n"
        else:
            preamble.append(line)
    if current is not None:
        sections.append(current)
    if preamble:
        sections.insert(
            0,
            _Section(canonical="result", title="", body="\n".join(preamble).strip()),
        )
    # Multiple sections with the same canonical name keep their first
    # occurrence position for stability.
    return sections


def _split_items(body: str) -> list[str]:
    """Split a section body into bullet/numbered items, preserving prose."""
    items: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if _ITEM_START_RE.match(line) and current:
            items.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current and any(line.strip() for line in current):
        items.append("\n".join(current).strip())
    return [item for item in items if item]


def _finding_rank(item: str) -> tuple[int, int, str]:
    severity = _SEVERITY_RE.search(item)
    confidence = _CONFIDENCE_RE.search(item)
    sev = _SEVERITY_RANK.get(severity.group(1).lower(), 99) if severity else 99
    con = _CONFIDENCE_RANK.get(confidence.group(1).lower(), 99) if confidence else 99
    return (sev, con, item)


def _dedupe_lines(body: str) -> tuple[str, int]:
    """Drop exact duplicate non-empty lines (keeps first occurrence)."""
    seen: set[str] = set()
    kept: list[str] = []
    dropped = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped in seen:
            dropped += 1
            continue
        seen.add(stripped)
        kept.append(line)
    return "\n".join(kept), dropped


def _cap_section(body: str, budget: int, stats: _CompactionStats, label: str) -> str:
    if len(body) <= budget:
        return body
    keep = max(budget - 200, budget // 2)
    stats.dropped_chars += len(body) - keep
    marker = f"[{label} truncated by MCP server: {len(body) - keep} chars omitted]"
    return body[:keep] + "\n" + marker


def compact_final_response(
    text: str,
    detail: OutputDetail,
    config: CompactionConfig,
    *,
    sort_findings: bool = False,
) -> str:
    """Deterministically compact a final worker response.

    Enforces per-detail finding/evidence caps and the configured character
    target; the hard limit always wins. Pure function of its inputs.
    """
    if not text.strip():
        return text
    max_findings = config.max_findings.get(detail)
    max_evidence = config.max_evidence_items.get(detail)
    target = config.final_target_chars.get(detail)
    hard = config.final_hard_limit_chars

    sections = _parse_sections(text)
    by_canonical: dict[str, list[_Section]] = {}
    for section in sections:
        by_canonical.setdefault(section.canonical, []).append(section)

    stats = _CompactionStats()
    processed: dict[str, str] = {}
    for canonical in _CANONICAL_ORDER:
        group = by_canonical.get(canonical)
        if not group:
            continue
        body = "\n".join(section.body.strip() for section in group if section.body.strip())
        if canonical == "evidence":
            body, dropped = _dedupe_lines(body)
            stats.dropped_evidence += dropped
            items = _split_items(body)
            if len(items) > max_evidence:
                omitted = len(items) - max_evidence
                items = items[:max_evidence]
                stats.dropped_evidence += omitted
                stats.markers.append(
                    f"[{omitted} evidence items omitted by MCP server; "
                    f"request detail=detailed or a targeted follow-up read]"
                )
            body = "\n".join(items)
        elif canonical == "findings":
            items = _split_items(body)
            if sort_findings:
                items.sort(key=_finding_rank)
            if len(items) > max_findings:
                omitted = len(items) - max_findings
                items = items[:max_findings]
                stats.dropped_findings += omitted
                stats.markers.append(f"[{omitted} lower-priority findings omitted by MCP server]")
            body = "\n".join(items)
        processed[canonical] = body

    # Assemble in retention priority order.
    ordered_bodies = [
        (canonical, processed[canonical])
        for canonical in _CANONICAL_ORDER
        if processed.get(canonical)
    ]
    other = by_canonical.get("other")
    if other:
        other_body = "\n".join(s.body.strip() for s in other if s.body.strip())
        if other_body:
            ordered_bodies.append(("other", other_body))

    full = "\n\n".join(body for _, body in ordered_bodies)
    if len(full) <= target and not stats.markers:
        return full

    # Structural compaction to the detail target. Reserve room for the
    # omission markers appended at the end.
    marker_block = "\n".join(stats.markers) if stats.markers else ""
    marker_reserve = len(marker_block) + 4 if marker_block else 0
    out_parts: list[str] = []
    used = 0
    for canonical, body in ordered_bodies:
        remaining = target - used - marker_reserve
        if remaining <= 0:
            stats.dropped_chars += len(body)
            continue
        budget = min(int(target * _BUDGET_SHARE.get(canonical, 0.0)), remaining)
        if budget <= 0:
            stats.dropped_chars += len(body)
            continue
        out_parts.append(_cap_section(body, budget, stats, canonical))
        used += len(out_parts[-1])
        if used >= target - marker_reserve:
            break

    if marker_block:
        out_parts.append(marker_block)
    result = "\n\n".join(part for part in out_parts if part)

    if len(result) > hard:
        stats.dropped_chars += len(result) - hard
        marker = (
            f"\n[response compacted by MCP server: "
            f"{stats.dropped_chars} chars of lower-priority content omitted]"
        )
        result = result[: hard - len(marker)] + marker
    elif stats.dropped_chars:
        result += (
            f"\n\n[response compacted by MCP server: "
            f"{stats.dropped_chars} chars of lower-priority content omitted]"
        )
    return result
