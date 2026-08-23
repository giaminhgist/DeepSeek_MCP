"""Filesystem Read/Glob/Grep tools for the worker.

These extend access beyond the repository root: paths may be absolute when
they fall under an allowed root (repository root or ``extra_allowed_roots``).
All reads are still bounded, deny-filtered, and never follow symlink escapes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from deepseek_mcp.config.models import RepositoryConfig
from deepseek_mcp.repo.guard import AccessPolicy, PathNotFoundError
from deepseek_mcp.repo.reader import ReadResult, read_file
from deepseek_mcp.repo.search import _MAX_MATCH_LINE_CHARS, _is_binary_file

_MAX_GLOB_RESULTS_DEFAULT = 500


@dataclass(slots=True)
class GlobResult:
    pattern: str
    paths: list[str]
    has_more: bool
    skipped: int


@dataclass(slots=True)
class GrepResult:
    pattern: str
    regex: bool
    matches: list[tuple[str, int, str]]
    total: int
    has_more: bool
    searched_files: int
    skipped_files: int


def fs_read(
    policy: AccessPolicy,
    config: RepositoryConfig,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
) -> ReadResult:
    """Read a text file anywhere within allowed roots (Read tool)."""
    return read_file(
        policy, config, path, start_line=start_line, end_line=end_line, allow_absolute=True
    )


def _resolve_base_dir(policy: AccessPolicy, base: str) -> Path:
    resolved = policy.check_any(base) if base else policy.repo_root
    if not resolved.is_dir():
        raise PathNotFoundError(f"not a directory: {base or '.'}")
    return resolved


def fs_glob(
    policy: AccessPolicy,
    config: RepositoryConfig,
    pattern: str,
    *,
    path: str = "",
    max_results: int | None = None,
) -> GlobResult:
    """Glob files under an allowed root (Glob tool). Pattern is relative."""
    if not pattern:
        raise ValueError("pattern must not be empty")
    if Path(pattern).is_absolute():
        raise ValueError("pattern must be relative")
    if max_results is None:
        max_results = _MAX_GLOB_RESULTS_DEFAULT
    if max_results < 1:
        raise ValueError("max_results must be >= 1")

    base = _resolve_base_dir(policy, path)
    results: list[str] = []
    skipped = 0
    limit_hit = False
    try:
        iterator = base.glob(pattern)
        for match in iterator:
            if match.is_symlink() or match.is_dir():
                skipped += 1
                continue
            rel_posix = match.relative_to(base).as_posix()
            if policy.matches_deny(rel_posix) or policy.matches_ignore(rel_posix):
                skipped += 1
                continue
            if len(results) >= max_results:
                limit_hit = True
                break
            results.append(rel_posix)
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid glob pattern {pattern!r}: {exc}") from exc
    return GlobResult(pattern=pattern, paths=results, has_more=limit_hit, skipped=skipped)


def _grep_text(
    text: str,
    *,
    regex_pattern: re.Pattern[str] | None,
    needle: str | None,
    case_sensitive: bool,
    rel_posix: str,
    matches: list[tuple[str, int, str]],
    max_matches: int,
) -> int:
    """Scan one file's lines, appending up to the remaining match budget."""
    found = 0
    remaining = max_matches - len(matches)
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if regex_pattern is not None:
            hit = regex_pattern.search(raw_line) is not None
        elif needle is not None:
            haystack = raw_line if case_sensitive else raw_line.lower()
            hit = needle in haystack
        else:  # pragma: no cover - unreachable by construction
            hit = False
        if hit:
            found += 1
            if remaining > 0:
                excerpt = raw_line.strip()
                if len(excerpt) > _MAX_MATCH_LINE_CHARS:
                    excerpt = excerpt[: _MAX_MATCH_LINE_CHARS - 3] + "..."
                matches.append((rel_posix, line_no, excerpt))
                remaining -= 1
    return found


def fs_grep(
    policy: AccessPolicy,
    config: RepositoryConfig,
    pattern: str,
    *,
    path: str = "",
    regex: bool = False,
    case_sensitive: bool = True,
    max_matches: int | None = None,
) -> GrepResult:
    """Search files under an allowed root or single file (Grep tool)."""
    if not pattern:
        raise ValueError("pattern must not be empty")
    if max_matches is None:
        max_matches = config.max_search_matches
    if max_matches < 1:
        raise ValueError("max_matches must be >= 1")

    compiled: re.Pattern[str] | None = None
    if regex:
        try:
            compiled = re.compile(pattern if case_sensitive else f"(?i:{pattern})")
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
    compiled_pattern = compiled
    needle: str | None = None if compiled_pattern is not None else pattern
    if needle is not None and not case_sensitive:
        needle = needle.lower()

    resolved = policy.check_any(path) if path else policy.repo_root
    if resolved.is_file():
        files = [resolved]
    elif resolved.is_dir():
        files = _collect_text_files(policy, config, resolved)
    else:
        raise PathNotFoundError(f"path not found: {path or '.'}")

    matches: list[tuple[str, int, str]] = []
    total = 0
    limit_hit = False
    skipped = 0
    base = resolved.parent if resolved.is_file() else resolved
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        rel_posix = file_path.relative_to(base).as_posix()
        found = _grep_text(
            text,
            regex_pattern=compiled_pattern,
            needle=needle,
            case_sensitive=case_sensitive,
            rel_posix=rel_posix,
            matches=matches,
            max_matches=max_matches,
        )
        total += found
        if len(matches) >= max_matches and total > len(matches):
            limit_hit = True
            break

    return GrepResult(
        pattern=pattern,
        regex=regex,
        matches=matches,
        total=total,
        has_more=limit_hit,
        searched_files=len(files),
        skipped_files=skipped,
    )


def _deny_rel(policy: AccessPolicy, path: Path, walk_root: Path) -> str:
    """Return the deny-relevant relative path for a file under a walk root.

    Deny globs are repo-root-relative patterns. For files inside the repo
    root use the repo-relative path; for files under an extra root use the
    path relative to the walked root (basename-style patterns still apply).
    """
    if path.is_relative_to(policy.repo_root):
        return path.relative_to(policy.repo_root).as_posix()
    return path.relative_to(walk_root).as_posix()


def _collect_text_files(policy: AccessPolicy, config: RepositoryConfig, root: Path) -> list[Path]:
    """Bounded walk of searchable text files under an allowed root."""
    files: list[Path] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda e: e.name)
        except OSError:
            continue
        for child in children:
            path = Path(child.path)
            try:
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    rel = _deny_rel(policy, path, root)
                    if policy.dir_is_excluded(rel):
                        continue
                    stack.append(path)
                elif child.is_file(follow_symlinks=False):
                    rel = _deny_rel(policy, path, root)
                    if policy.matches_deny(rel) or (
                        root == policy.repo_root and policy.matches_ignore(rel)
                    ):
                        continue
                    try:
                        size = child.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    if size > config.max_file_bytes:
                        continue
                    if _is_binary_file(path):
                        continue
                    files.append(path)
            except OSError:
                continue
    return files
