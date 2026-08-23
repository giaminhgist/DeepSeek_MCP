"""fs_* tool tests: Read/Glob/Grep beyond the repo, Write/Edit/Bash/Notebook."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepseek_mcp.repo.bash import fs_bash
from deepseek_mcp.repo.fs import fs_glob, fs_grep, fs_read
from deepseek_mcp.repo.guard import (
    AccessPolicy,
    FileTooLargeError,
    PathDeniedError,
    PathTraversalError,
    RepoAccessError,
)
from deepseek_mcp.repo.reader import read_file
from deepseek_mcp.repo.writes import fs_edit, fs_notebook_edit, fs_write
from tests.conftest import make_test_config


@pytest.fixture
def extra_root(tmp_path: Path) -> Path:
    root = tmp_path / "extra"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.txt").write_text("guide line one\nguide line two\n", encoding="utf-8")
    (root / "note.py").write_text("print('hello')\n", encoding="utf-8")
    return root


@pytest.fixture
def policy(sample_repo: Path, extra_root: Path, tmp_path: Path) -> AccessPolicy:
    policy = AccessPolicy(sample_repo, make_test_config(tmp_path).repository)
    policy.add_extra_roots([str(extra_root)])
    return policy


@pytest.fixture
def config(tmp_path: Path):
    return make_test_config(tmp_path)


# --- read/glob/grep --------------------------------------------------------


def test_fs_read_outside_repo(policy: AccessPolicy, extra_root: Path, config) -> None:
    # Relative paths resolve against the repo root, so extra-root files need
    # absolute paths (this is the documented contract).
    result = fs_read(policy, config.repository, str(extra_root / "docs" / "guide.txt"))
    assert result.total_lines == 2


def test_fs_read_absolute_in_extra_root(policy: AccessPolicy, extra_root: Path, config) -> None:
    result = fs_read(policy, config.repository, str(extra_root / "note.py"))
    assert result.lines[0] == (1, "print('hello')")


def test_fs_read_absolute_outside_rejected(policy: AccessPolicy, config) -> None:
    with pytest.raises(PathTraversalError):
        fs_read(policy, config.repository, "/etc/passwd")


def test_fs_glob(policy: AccessPolicy, extra_root: Path, config) -> None:
    result = fs_glob(policy, config.repository, "**/*.py", path=str(extra_root))
    assert result.paths == ["note.py"]


def test_fs_glob_bounded(policy: AccessPolicy, extra_root: Path, tmp_path: Path, config) -> None:
    for i in range(20):
        (extra_root / f"f{i}.txt").write_text("x", encoding="utf-8")
    result = fs_glob(policy, config.repository, "*.txt", path=str(extra_root), max_results=5)
    assert len(result.paths) == 5
    assert result.has_more


def test_fs_glob_deny_filtered(policy: AccessPolicy, extra_root: Path, config) -> None:
    (extra_root / ".env").write_text("SECRET=1", encoding="utf-8")
    result = fs_glob(policy, config.repository, "*", path=str(extra_root))
    assert ".env" not in result.paths
    assert result.skipped >= 1


def test_fs_grep(policy: AccessPolicy, extra_root: Path, config) -> None:
    result = fs_grep(policy, config.repository, "guide", path=str(extra_root))
    assert result.total == 2
    assert all(m[0] == "docs/guide.txt" for m in result.matches)
    assert [m[1] for m in result.matches] == [1, 2]


def test_fs_grep_single_file(policy: AccessPolicy, extra_root: Path, config) -> None:
    result = fs_grep(
        policy,
        config.repository,
        "hello",
        path=str(extra_root / "note.py"),
    )
    assert result.total == 1
    assert result.matches[0][0] == "note.py"


def test_fs_grep_regex(policy: AccessPolicy, extra_root: Path, config) -> None:
    result = fs_grep(
        policy, config.repository, r"guide line (one|two)", path=str(extra_root), regex=True
    )
    assert result.total == 2


# --- write/edit/notebook ---------------------------------------------------


def test_fs_write_within_extra_root(policy: AccessPolicy, extra_root: Path, config) -> None:
    outcome = fs_write(
        policy,
        config.repository,
        str(extra_root / "new.py"),
        "x = 1\n",
        writes_enabled=True,
    )
    assert outcome["bytes_written"] == 6
    assert (extra_root / "new.py").read_text(encoding="utf-8") == "x = 1\n"


def test_fs_write_outside_rejected(policy: AccessPolicy, tmp_path: Path, config) -> None:
    with pytest.raises(PathTraversalError):
        fs_write(
            policy,
            config.repository,
            str(tmp_path / "nope.txt"),
            "x",
            writes_enabled=True,
        )


def test_fs_write_denied_path_rejected(policy: AccessPolicy, extra_root: Path, config) -> None:
    with pytest.raises(PathDeniedError):
        fs_write(
            policy,
            config.repository,
            str(extra_root / ".env"),
            "SECRET=1",
            writes_enabled=True,
        )


def test_fs_write_disabled(policy: AccessPolicy, extra_root: Path, config) -> None:
    with pytest.raises(RepoAccessError, match="disabled"):
        fs_write(
            policy,
            config.repository,
            str(extra_root / "x.txt"),
            "x",
            writes_enabled=False,
        )


def test_fs_write_oversized_rejected(
    policy: AccessPolicy, extra_root: Path, tmp_path: Path
) -> None:
    config = make_test_config(tmp_path, {"repository": {"max_file_bytes": 10}})
    with pytest.raises(FileTooLargeError):
        fs_write(
            policy,
            config.repository,
            str(extra_root / "big.txt"),
            "x" * 100,
            writes_enabled=True,
        )


def test_fs_edit_unique(policy: AccessPolicy, extra_root: Path, config) -> None:
    outcome = fs_edit(
        policy,
        config.repository,
        str(extra_root / "note.py"),
        "print('hello')",
        "print('world')",
        writes_enabled=True,
    )
    assert outcome["occurrences_replaced"] == 1
    assert "world" in (extra_root / "note.py").read_text(encoding="utf-8")


def test_fs_edit_ambiguous_requires_replace_all(
    policy: AccessPolicy, extra_root: Path, config
) -> None:
    (extra_root / "dup.txt").write_text("x x x", encoding="utf-8")
    with pytest.raises(RepoAccessError, match="occurs 3 times"):
        fs_edit(
            policy,
            config.repository,
            str(extra_root / "dup.txt"),
            "x",
            "y",
            writes_enabled=True,
        )
    outcome = fs_edit(
        policy,
        config.repository,
        str(extra_root / "dup.txt"),
        "x",
        "y",
        replace_all=True,
        writes_enabled=True,
    )
    assert outcome["occurrences_replaced"] == 3


def test_fs_edit_missing_string(policy: AccessPolicy, extra_root: Path, config) -> None:
    with pytest.raises(RepoAccessError, match="not found"):
        fs_edit(
            policy,
            config.repository,
            str(extra_root / "note.py"),
            "does not exist",
            "y",
            writes_enabled=True,
        )


def test_fs_edit_missing_file(policy: AccessPolicy, extra_root: Path, config) -> None:
    with pytest.raises(RepoAccessError, match="not found"):
        fs_edit(
            policy,
            config.repository,
            str(extra_root / "nope.py"),
            "a",
            "b",
            writes_enabled=True,
        )


def test_fs_notebook_edit_ops(policy: AccessPolicy, extra_root: Path, config) -> None:
    notebook = extra_root / "nb.ipynb"
    cells = [
        {"cell_type": "code", "id": "c1", "source": "x = 1", "metadata": {}},
        {"cell_type": "markdown", "id": "c2", "source": "# Title", "metadata": {}},
    ]
    notebook.write_text(json.dumps({"cells": cells, "metadata": {}}), encoding="utf-8")
    fs_notebook_edit(
        policy,
        config.repository,
        str(notebook),
        "replace",
        "x = 2",
        cell_id="c1",
        writes_enabled=True,
    )
    fs_notebook_edit(
        policy,
        config.repository,
        str(notebook),
        "insert",
        "y = 3",
        cell_id="c2",
        writes_enabled=True,
    )
    fs_notebook_edit(
        policy,
        config.repository,
        str(notebook),
        "delete",
        "",
        cell_id="c1",
        writes_enabled=True,
    )
    data = json.loads(notebook.read_text(encoding="utf-8"))
    assert len(data["cells"]) == 2
    assert data["cells"][0]["id"] == "c2"
    assert data["cells"][1]["source"] == "y = 3"


def test_fs_notebook_edit_bad_json(policy: AccessPolicy, extra_root: Path, config) -> None:
    bad = extra_root / "bad.ipynb"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(RepoAccessError, match="notebook"):
        fs_notebook_edit(
            policy,
            config.repository,
            str(bad),
            "insert",
            "x = 1",
            writes_enabled=True,
        )


# --- bash ------------------------------------------------------------------


async def test_fs_bash_runs_command(policy: AccessPolicy, sample_repo: Path) -> None:
    result = await fs_bash(policy, "echo hello", bash_enabled=True)
    assert result.exit_code == 0
    assert "hello" in result.output


async def test_fs_bash_output_bounded(policy: AccessPolicy, tmp_path: Path) -> None:
    result = await fs_bash(
        policy,
        "python3 -c \"print('x' * 10000)\"",
        max_output_chars=1000,
        bash_enabled=True,
    )
    assert result.truncated
    assert len(result.output) <= 1500
    assert "truncated" in result.output


async def test_fs_bash_timeout(policy: AccessPolicy) -> None:
    result = await fs_bash(policy, "sleep 5", timeout_ms=1000, bash_enabled=True)
    assert result.timed_out
    assert "timed out" in result.output


async def test_fs_bash_cwd_validation(policy: AccessPolicy, tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError):
        await fs_bash(policy, "pwd", cwd=str(tmp_path), bash_enabled=True)


async def test_fs_bash_api_key_stripped(
    policy: AccessPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-super-secret")
    result = await fs_bash(policy, "echo KEY=$DEEPSEEK_API_KEY", bash_enabled=True)
    assert "sk-super-secret" not in result.output


async def test_fs_bash_disabled(policy: AccessPolicy) -> None:
    with pytest.raises(RepoAccessError, match="disabled"):
        await fs_bash(policy, "echo x", bash_enabled=False)


async def test_fs_bash_workdir_is_repo_root(policy: AccessPolicy, sample_repo: Path) -> None:
    result = await fs_bash(policy, "pwd", bash_enabled=True)
    assert str(sample_repo.resolve()) in result.output


def test_read_file_extra_root_via_read_file(policy: AccessPolicy, extra_root: Path, config) -> None:
    result = read_file(policy, config.repository, str(extra_root / "note.py"), allow_absolute=True)
    assert result.lines[0][1] == "print('hello')"
