"""Pure-Python bounded text search over the repository."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from deepseek_mcp.config.models import RepositoryConfig
from deepseek_mcp.repo.guard import AccessPolicy

_MAX_MATCH_LINE_CHARS = 200
_BINARY_PROBE_BYTES = 8192


@dataclass(slots=True)
class SearchMatch:
    path: str
    line: int
    text: str


@dataclass(slots=True)
class SearchResult:
    query: str
    case_sensitive: bool
    regex: bool
    matches: list[SearchMatch]
    total: int
    searched_files: int
    skipped_files: int
    has_more: bool


def _is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(_BINARY_PROBE_BYTES)
    except OSError:
        return True
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _iter_text_files(
    policy: AccessPolicy,
    config: RepositoryConfig,
    root: Path,
) -> tuple[list[Path], int]:
    """Collect searchable text files (bounded), returning (files, skipped)."""
    files: list[Path] = []
    skipped = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda e: e.name)
        except OSError:
            skipped += 1
            continue
        for child in children:
            path = Path(child.path)
            rel_posix = path.relative_to(policy.repo_root).as_posix()
            try:
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    if policy.dir_is_excluded(rel_posix):
                        continue
                    stack.append(path)
                elif child.is_file(follow_symlinks=False):
                    if policy.matches_deny(rel_posix) or policy.matches_ignore(rel_posix):
                        skipped += 1
                        continue
                    try:
                        size = child.stat(follow_symlinks=False).st_size
                    except OSError:
                        skipped += 1
                        continue
                    if size > config.max_file_bytes:
                        skipped += 1
                        continue
                    if _is_binary_file(path):
                        skipped += 1
                        continue
                    files.append(path)
            except OSError:
                skipped += 1
    return files, skipped


def search_repo(
    policy: AccessPolicy,
    config: RepositoryConfig,
    query: str,
    *,
    max_matches: int | None = None,
    case_sensitive: bool = True,
    regex: bool = False,
) -> SearchResult:
    """Search text files under the repository root (literal or regex)."""
    if not query:
        raise ValueError("query must not be empty")
    if max_matches is None:
        max_matches = config.max_search_matches
    if max_matches < 1:
        raise ValueError("max_matches must be >= 1")

    pattern: re.Pattern[str] | None = None
    if regex:
        try:
            pattern = re.compile(query if case_sensitive else f"(?i:{query})")
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
    needle = None if pattern is not None else (query if case_sensitive else query.lower())

    files, skipped = _iter_text_files(policy, config, policy.repo_root)
    matches: list[SearchMatch] = []
    total = 0
    limit_hit = False

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        rel_posix = path.relative_to(policy.repo_root).as_posix()
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line if case_sensitive else raw_line.lower()
            if pattern is not None:
                hit = pattern.search(raw_line) is not None
            elif needle is not None:
                hit = needle in line
            else:  # pragma: no cover - unreachable by construction
                hit = False
            if hit:
                total += 1
                if len(matches) < max_matches:
                    excerpt = raw_line.strip()
                    if len(excerpt) > _MAX_MATCH_LINE_CHARS:
                        excerpt = excerpt[: _MAX_MATCH_LINE_CHARS - 3] + "..."
                    matches.append(SearchMatch(path=rel_posix, line=line_no, text=excerpt))
                else:
                    limit_hit = True

    return SearchResult(
        query=query,
        case_sensitive=case_sensitive,
        regex=regex,
        matches=matches,
        total=total,
        searched_files=len(files),
        skipped_files=skipped,
        has_more=limit_hit,
    )
