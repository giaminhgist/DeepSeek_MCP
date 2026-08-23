"""Bounded directory listing with deny/gitignore filtering."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from deepseek_mcp.config.models import RepositoryConfig
from deepseek_mcp.repo.guard import AccessPolicy, PathNotFoundError, RepoAccessError

DEFAULT_LIST_LIMIT = 200


@dataclass(slots=True)
class ListEntry:
    path: str
    kind: Literal["file", "dir", "symlink"]
    size: int | None


@dataclass(slots=True)
class ListResult:
    entries: list[ListEntry]
    total: int
    has_more: bool
    next_offset: int
    truncated: bool


def list_directory(
    policy: AccessPolicy,
    config: RepositoryConfig,
    rel_dir: str = "",
    *,
    offset: int = 0,
    limit: int | None = None,
) -> ListResult:
    """List a directory tree with bounds and continuation metadata.

    Traversal is a deterministic lexicographic DFS that never follows
    symlinks and prunes denied/ignored directories.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit is None:
        limit = DEFAULT_LIST_LIMIT
    if limit < 1:
        raise ValueError("limit must be >= 1")

    target = policy.check_repo(rel_dir or ".")
    if not target.is_dir():
        raise PathNotFoundError(f"not a directory: {rel_dir}")

    def rel_of(path: Path) -> str:
        return path.relative_to(policy.repo_root).as_posix()

    # Walk at most `scan_cap` entries so paging past the cap stays bounded.
    scan_cap = offset + max(limit, config.max_list_entries)
    entries: list[ListEntry] = []
    scanned = 0
    truncated = False

    stack = [target]
    while stack and not truncated:
        directory = stack.pop()
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda e: e.name)
        except OSError as exc:
            raise RepoAccessError(f"cannot read directory {directory}: {exc}") from exc
        for child in children:
            if truncated:
                break
            rel_posix = rel_of(Path(child.path))
            try:
                if child.is_symlink():
                    scanned += 1
                    entries.append(ListEntry(path=rel_posix, kind="symlink", size=None))
                elif child.is_dir(follow_symlinks=False):
                    if policy.dir_is_excluded(rel_posix):
                        continue
                    scanned += 1
                    entries.append(ListEntry(path=rel_posix, kind="dir", size=None))
                    stack.append(Path(child.path))
                elif child.is_file(follow_symlinks=False):
                    if policy.matches_deny(rel_posix) or policy.matches_ignore(rel_posix):
                        continue
                    scanned += 1
                    entries.append(
                        ListEntry(
                            path=rel_posix,
                            kind="file",
                            size=child.stat(follow_symlinks=False).st_size,
                        )
                    )
            except OSError:
                continue
            if scanned >= scan_cap:
                truncated = True

    page = entries[offset : offset + limit]
    next_offset = offset + len(page)
    has_more = truncated or next_offset < len(entries)
    return ListResult(
        entries=page,
        total=len(entries),
        has_more=has_more,
        next_offset=next_offset,
        truncated=truncated,
    )
