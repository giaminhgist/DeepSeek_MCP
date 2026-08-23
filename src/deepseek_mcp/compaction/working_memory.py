"""Structured working memory for rolling message-history compaction.

The worker loop periodically collapses older DeepSeek messages into a single
working-memory message. Memory is structured state (facts/evidence/tasks),
merged and re-rendered on each compaction, so objective and evidence
identifiers survive repeated compactions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATH_LINE_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z0-9_./\\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|go|rs|"
    r"java|kt|kts|c|h|cc|cpp|hpp|cs|rb|php|swift|m|mm|scala|sh|bash|zsh|ps1|bat|"
    r"sql|html|css|scss|json|ya?ml|toml|ini|cfg|conf|md|rst|txt|mdx|vue|svelte|"
    r"proto|graphql|tf|hcl|dockerfile|makefile|gradle|xml|lua|r|pl|ex|exs|erl|hs|"
    r"dart|nim|zig|v|fs|fsx|sol|vy|move|jl|ipynb)):(\d+)(?:-(\d+))?"
)

_TOKEN_CHARS_PER_TOKEN = 3
_MEMORY_OMISSION = "[{n} lower-priority working-memory items omitted]"
_READ_HEADER_RE = re.compile(r"\[(?:repo_read|fs_read)\] (\S+) lines")


def estimate_tokens(text: str) -> int:
    """Conservative token estimate used ONLY for compaction/preflight decisions.

    Billing always uses provider-reported usage, never this estimate.
    """
    return max(1, len(text) // _TOKEN_CHARS_PER_TOKEN)


def extract_path_line_refs(text: str) -> list[tuple[str, int | None, int | None]]:
    """Extract ``path.ext:line`` or ``path.ext:line-end`` references from text."""
    refs: list[tuple[str, int | None, int | None]] = []
    for match in _PATH_LINE_RE.finditer(text):
        path, start, end = match.group(1), int(match.group(2)), match.group(3)
        refs.append((path, start, int(end) if end else None))
    return refs


@dataclass(slots=True)
class EvidenceItem:
    id: str
    statement: str
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(slots=True)
class WorkingMemory:
    objective: str
    facts: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    inspected_paths: list[str] = field(default_factory=list)
    next_checks: list[str] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def note_evidence(
        self,
        statement: str,
        *,
        path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> None:
        key = (path, start_line)
        if path is not None and any((e.path, e.start_line) == key for e in self.evidence):
            return  # duplicate evidence location already recorded
        if len(self.evidence) >= 60:
            self.evidence.pop(0)
        self.evidence.append(
            EvidenceItem(
                id=f"E{len(self.evidence) + 1:02d}",
                statement=statement[:300],
                path=path,
                start_line=start_line,
                end_line=end_line,
            )
        )

    def note_inspected(self, path: str) -> None:
        if path not in self.inspected_paths:
            self.inspected_paths.append(path)

    def absorb_tool_result(self, tool_name: str, result_text: str) -> None:
        """Update memory from a compacted tool result (bounded side effects)."""
        first_line = (
            result_text.strip().splitlines()[0][:200]
            if result_text.strip()
            else f"reference in {tool_name}"
        )
        for path, start, end in extract_path_line_refs(result_text):
            self.note_inspected(path)
            if start is not None and len(self.evidence) < 60:
                self.note_evidence(first_line, path=path, start_line=start, end_line=end)
        # Read-style results put the file path in the header without line refs.
        for match in _READ_HEADER_RE.finditer(result_text):
            self.note_inspected(match.group(1))

    def render(self) -> str:
        parts = ["# Worker Working Memory", f"## Objective\n{self.objective}"]
        if self.evidence:
            lines = [
                f"- {item.id} `{item.path}:{item.start_line}"
                + (f"-{item.end_line}" if item.end_line else "")
                + f"` — {item.statement}"
                for item in self.evidence
            ]
            parts.append("## Confirmed evidence\n" + "\n".join(lines))
        if self.findings:
            parts.append("## Current findings\n" + "\n".join(f"- {f}" for f in self.findings))
        if self.uncertainties:
            parts.append("## Open questions\n" + "\n".join(f"- {u}" for u in self.uncertainties))
        if self.inspected_paths:
            parts.append(
                "## Files inspected\n" + "\n".join(f"- `{p}`" for p in self.inspected_paths)
            )
        if self.discarded:
            parts.append(
                "## Discarded/stale hypotheses\n" + "\n".join(f"- {d}" for d in self.discarded)
            )
        if self.next_checks:
            parts.append("## Next useful reads\n" + "\n".join(f"- `{p}`" for p in self.next_checks))
        return "\n\n".join(parts)

    def bounded_render(self, max_chars: int) -> str:
        """Render within a char budget, dropping lowest-priority items."""
        text = self.render()
        if len(text) <= max_chars:
            return text
        dropped = 0
        # Drop in ascending priority: discarded, facts, next_checks, then
        # evidence tails. Objective, findings, uncertainties are kept.
        while len(text) > max_chars:
            if self.discarded:
                self.discarded.pop()
                dropped += 1
            elif self.next_checks:
                self.next_checks.pop()
                dropped += 1
            elif self.evidence:
                self.evidence.pop()
                dropped += 1
            elif self.inspected_paths:
                self.inspected_paths.pop()
                dropped += 1
            elif self.facts:
                self.facts.pop()
                dropped += 1
            else:
                break
            text = self.render()
        if dropped:
            text += "\n\n" + _MEMORY_OMISSION.format(n=dropped)
        if len(text) > max_chars:
            text = text[: max_chars - 60] + "\n[working memory truncated]"
        return text
