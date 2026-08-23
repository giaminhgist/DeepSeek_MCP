"""Write/Edit/NotebookEdit tools for the worker.

Registered only when ``tools.allow_writes`` is enabled. All writes are
confined to allowed roots by the same AccessPolicy used for reads, and the
sensitive-file deny globs apply to writes as well.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from deepseek_mcp.config.models import RepositoryConfig
from deepseek_mcp.repo.guard import (
    AccessPolicy,
    BinaryFileError,
    FileTooLargeError,
    NotAFileError,
    RepoAccessError,
)

logger = logging.getLogger("deepseek_mcp.writes")

EditMode = Literal["replace", "insert", "delete"]


def _require_writes_enabled(allowed: bool) -> None:
    if not allowed:
        raise RepoAccessError("write tools are disabled (tools.allow_writes: false)")


def _resolve_writable_path(policy: AccessPolicy, path: str, *, must_exist: bool = False) -> Path:
    resolved = policy.check_any(path)
    if must_exist and not resolved.exists():
        raise RepoAccessError(f"path not found: {path}")
    if resolved.exists() and resolved.is_dir():
        raise NotAFileError(f"is a directory: {path}")
    return resolved


def fs_write(
    policy: AccessPolicy,
    config: RepositoryConfig,
    path: str,
    content: str,
    *,
    writes_enabled: bool,
) -> dict[str, str | int]:
    """Create or overwrite a file within allowed roots (Write tool)."""
    _require_writes_enabled(writes_enabled)
    raw = content.encode("utf-8")
    if len(raw) > config.max_file_bytes:
        raise FileTooLargeError(
            f"content is {len(raw)} bytes; limit is {config.max_file_bytes} bytes"
        )
    target = _resolve_writable_path(policy, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    logger.info("fs_write wrote %d bytes to %s", len(raw), target)
    return {"path": str(target), "bytes_written": len(raw)}


def fs_edit(
    policy: AccessPolicy,
    config: RepositoryConfig,
    path: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
    writes_enabled: bool,
) -> dict[str, str | int]:
    """Replace a unique string in an existing file (Edit tool)."""
    _require_writes_enabled(writes_enabled)
    if old_string == "":
        raise RepoAccessError("old_string must not be empty")
    target = _resolve_writable_path(policy, path, must_exist=True)
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BinaryFileError(f"file is not valid UTF-8 text: {path}") from exc
    if len(text.encode("utf-8")) > config.max_file_bytes:
        raise FileTooLargeError("file exceeds the configured byte limit")
    occurrences = text.count(old_string)
    if occurrences == 0:
        raise RepoAccessError(f"old_string not found in {path}")
    if occurrences > 1 and not replace_all:
        raise RepoAccessError(
            f"old_string occurs {occurrences} times in {path}; "
            f"make it unique or pass replace_all=true"
        )
    new_text = text.replace(old_string, new_string)
    target.write_text(new_text, encoding="utf-8")
    logger.info("fs_edit replaced %d occurrence(s) in %s", occurrences, target)
    return {"path": str(target), "occurrences_replaced": occurrences}


def fs_notebook_edit(
    policy: AccessPolicy,
    config: RepositoryConfig,
    path: str,
    edit_mode: EditMode,
    new_source: str,
    *,
    cell_id: str | None = None,
    cell_type: Literal["code", "markdown"] = "code",
    writes_enabled: bool,
) -> dict[str, str | int]:
    """Edit a single cell of a Jupyter notebook (NotebookEdit tool)."""
    _require_writes_enabled(writes_enabled)
    target = _resolve_writable_path(policy, path, must_exist=True)
    try:
        notebook = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoAccessError(f"cannot parse notebook {path}: {exc}") from exc
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise RepoAccessError(f"not a valid notebook: {path}")
    cells = notebook["cells"]

    def find_index(cid: str) -> int:
        for index, cell in enumerate(cells):
            if cell.get("id") == cid:
                return index
        raise RepoAccessError(f"cell id not found: {cid!r}")

    if edit_mode == "replace":
        if cell_id is None:
            raise RepoAccessError("cell_id is required for replace")
        cells[find_index(cell_id)]["source"] = new_source
    elif edit_mode == "insert":
        if cell_id is not None:
            cells.insert(find_index(cell_id) + 1, _make_cell(cell_type, new_source))
        else:
            cells.insert(0, _make_cell(cell_type, new_source))
    elif edit_mode == "delete":
        if cell_id is None:
            raise RepoAccessError("cell_id is required for delete")
        del cells[find_index(cell_id)]

    target.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("fs_notebook_edit %s on %s", edit_mode, target)
    return {"path": str(target), "cells": len(cells)}


def _make_cell(cell_type: Literal["code", "markdown"], source: str) -> dict[str, object]:
    return {
        "cell_type": cell_type,
        "metadata": {},
        "source": source,
    }
