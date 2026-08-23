"""Bounded, line-numbered text file reading with binary detection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from deepseek_mcp.config.models import RepositoryConfig
from deepseek_mcp.repo.guard import (
    AccessPolicy,
    BinaryFileError,
    FileTooLargeError,
    NotAFileError,
    PathNotFoundError,
)

_BINARY_PROBE_BYTES = 8192


@dataclass(slots=True)
class ReadResult:
    path: str
    start_line: int
    end_line: int
    total_lines: int
    lines: list[tuple[int, str]]
    truncated_bytes: bool
    has_more_before: bool
    has_more_after: bool


@dataclass(slots=True)
class StatResult:
    path: str
    kind: Literal["file", "dir", "symlink", "other"]
    size_bytes: int
    mtime: float


def _detect_binary(first_bytes: bytes) -> bool:
    if b"\x00" in first_bytes:
        return True
    try:
        first_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _read_text_lines(path: Path, max_bytes: int) -> tuple[list[str], bool]:
    """Return (lines, truncated_bytes). Raises on binary/oversized input."""
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise PathNotFoundError(f"path not found: {path}") from exc
    if not path.is_file():
        raise NotAFileError(f"not a regular file: {path}")
    if stat.st_size > max_bytes:
        raise FileTooLargeError(f"file is {stat.st_size} bytes; limit is {max_bytes} bytes")
    with path.open("rb") as handle:
        head = handle.read(_BINARY_PROBE_BYTES)
        if _detect_binary(head):
            raise BinaryFileError(f"binary file rejected: {path}")
        handle.seek(0)
        raw = handle.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BinaryFileError(f"file is not valid UTF-8 text: {path}") from exc
    return text.splitlines(), False


def read_file(
    policy: AccessPolicy,
    config: RepositoryConfig,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
    allow_absolute: bool = False,
) -> ReadResult:
    """Read a bounded window of a text file with line numbers.

    ``path`` is repo-relative unless ``allow_absolute`` is set, in which case
    absolute paths within any allowed root are accepted as well.
    """
    if start_line < 1:
        raise ValueError("start_line must be >= 1")
    if end_line is not None and end_line < start_line:
        raise ValueError("end_line must be >= start_line")
    resolved = policy.check_any(path) if allow_absolute else policy.check_repo(path)
    if not resolved.exists():
        raise PathNotFoundError(f"path not found: {path}")

    lines, truncated_bytes = _read_text_lines(resolved, config.max_file_bytes)
    total = len(lines)
    window_end = min(
        end_line if end_line is not None else total,
        start_line + config.max_read_lines - 1,
    )
    window_end = max(window_end, start_line)
    window = lines[start_line - 1 : window_end]
    return ReadResult(
        path=str(resolved),
        start_line=start_line,
        end_line=window_end if window else start_line,
        total_lines=total,
        lines=[(start_line + i, line) for i, line in enumerate(window)],
        truncated_bytes=truncated_bytes,
        has_more_before=start_line > 1,
        has_more_after=window_end < total,
    )


def stat_file(
    policy: AccessPolicy,
    path: str,
    *,
    allow_absolute: bool = False,
) -> StatResult:
    """Return basic metadata for a path (no content)."""
    resolved = policy.check_any(path) if allow_absolute else policy.check_repo(path)
    try:
        stat = os.lstat(resolved)
    except FileNotFoundError as exc:
        raise PathNotFoundError(f"path not found: {path}") from exc
    if resolved.is_symlink():
        kind: Literal["file", "dir", "symlink", "other"] = "symlink"
    elif resolved.is_dir():
        kind = "dir"
    elif resolved.is_file():
        kind = "file"
    else:
        kind = "other"
    return StatResult(path=str(resolved), kind=kind, size_bytes=stat.st_size, mtime=stat.st_mtime)
