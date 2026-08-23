"""Repository tool tests: listing, reading, search, git (bounded, safe)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deepseek_mcp.repo.git import GitTools
from deepseek_mcp.repo.guard import (
    AccessPolicy,
    BinaryFileError,
    FileTooLargeError,
    PathDeniedError,
    PathNotFoundError,
    RepoAccessError,
)
from deepseek_mcp.repo.listing import list_directory
from deepseek_mcp.repo.reader import read_file, stat_file
from deepseek_mcp.repo.search import search_repo
from tests.conftest import make_test_config


@pytest.fixture
def config(tmp_path: Path):
    return make_test_config(tmp_path)


@pytest.fixture
def policy(sample_repo: Path, config) -> AccessPolicy:
    return AccessPolicy(sample_repo, config.repository)


# --- listing ---------------------------------------------------------------


def test_list_is_bounded(sample_repo: Path, tmp_path: Path) -> None:
    for i in range(50):
        (sample_repo / f"bulk_{i:03d}.txt").write_text("x", encoding="utf-8")
    config = make_test_config(tmp_path, {"repository": {"max_list_entries": 30}})
    policy = AccessPolicy(sample_repo, config.repository)
    result = list_directory(policy, config.repository, limit=20)
    assert len(result.entries) <= 20
    assert result.has_more
    assert result.truncated or result.next_offset < result.total


def test_list_respects_deny_and_ignore(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = list_directory(policy, config.repository)
    paths = [entry.path for entry in result.entries]
    assert "secrets.env" not in paths
    assert "id_rsa.pem" not in paths
    assert "debug.log" not in paths
    assert "src/main.py" in paths
    assert "src" in paths


def test_list_continuation(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    first = list_directory(policy, config.repository, limit=3)
    second = list_directory(policy, config.repository, offset=3, limit=3)
    assert [e.path for e in first.entries] != [e.path for e in second.entries]
    assert first.next_offset == 3


def test_list_missing_dir_raises(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    with pytest.raises(PathNotFoundError):
        list_directory(policy, config.repository, "nope")


def test_list_does_not_follow_symlinks(sample_repo: Path, tmp_path: Path, config) -> None:
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.py").write_text("secret", encoding="utf-8")
    (sample_repo / "dir_link").symlink_to(outside)
    policy = AccessPolicy(sample_repo, config.repository)
    result = list_directory(policy, config.repository)
    entries = {e.path: e.kind for e in result.entries}
    assert entries.get("dir_link") == "symlink"
    assert "outside_dir/secret.py" not in entries


# --- reading ---------------------------------------------------------------


def test_read_returns_line_numbers(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = read_file(policy, config.repository, "src/main.py")
    assert result.total_lines == 2
    assert result.lines[0] == (1, "def main() -> None:")
    assert result.start_line == 1 and result.end_line == 2


def test_read_truncates_at_configured_limits(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, {"repository": {"max_read_lines": 10}})
    policy = AccessPolicy(tmp_path, config.repository)
    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
    result = read_file(policy, config.repository, "big.txt")
    assert len(result.lines) == 10
    assert result.has_more_after
    assert result.has_more_before is False


def test_read_line_range(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = read_file(policy, config.repository, "src/auth.py", start_line=5, end_line=8)
    assert [line_no for line_no, _ in result.lines] == [5, 6, 7, 8]
    assert result.has_more_before and result.has_more_after


def test_read_rejects_binary(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    with pytest.raises(BinaryFileError):
        read_file(policy, config.repository, "data.bin")


def test_read_rejects_oversized(tmp_path: Path) -> None:
    config = make_test_config(tmp_path, {"repository": {"max_file_bytes": 100}})
    policy = AccessPolicy(tmp_path, config.repository)
    big = tmp_path / "big.txt"
    big.write_text("x" * 1000, encoding="utf-8")
    with pytest.raises(FileTooLargeError):
        read_file(policy, config.repository, "big.txt")


def test_read_rejects_denied(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    with pytest.raises(PathDeniedError):
        read_file(policy, config.repository, "secrets.env")


def test_read_missing_file(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    with pytest.raises(PathNotFoundError):
        read_file(policy, config.repository, "nope.py")


def test_stat(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = stat_file(policy, "src/main.py")
    assert result.kind == "file"
    assert result.size_bytes > 0


# --- search ----------------------------------------------------------------


def test_search_bounded_matches(sample_repo: Path, tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    policy = AccessPolicy(sample_repo, config.repository)
    result = search_repo(policy, config.repository, "auth line", max_matches=10)
    assert result.total == 50
    assert len(result.matches) == 10
    assert result.has_more
    assert all(m.path == "src/auth.py" for m in result.matches)


def test_search_regex(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = search_repo(policy, config.repository, r"auth line [0-9]+", regex=True)
    assert result.total == 50


def test_search_case_insensitive(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = search_repo(policy, config.repository, "PRINT", case_sensitive=False)
    assert result.total == 1
    assert result.matches[0].path == "src/main.py"


def test_search_skips_binary_and_denied(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    result = search_repo(policy, config.repository, "TOKEN")
    assert result.total == 0  # secrets.env is denied, data.bin is binary


def test_search_invalid_regex(sample_repo: Path, config) -> None:
    policy = AccessPolicy(sample_repo, config.repository)
    with pytest.raises(ValueError):
        search_repo(policy, config.repository, "([", regex=True)


# --- git -------------------------------------------------------------------


@pytest.fixture
def git_repo(sample_repo: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(sample_repo)], check=True)
    subprocess.run(
        ["git", "-C", str(sample_repo), "config", "user.email", "t@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(sample_repo), "config", "user.name", "Tester"],
        check=True,
    )
    subprocess.run(["git", "-C", str(sample_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(sample_repo), "commit", "-qm", "init"], check=True)
    return sample_repo


async def test_git_diff_working(git_repo: Path, config) -> None:
    (git_repo / "src" / "main.py").write_text("changed\n", encoding="utf-8")
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    text = await git.diff("working")
    assert "diff --git" in text
    assert "+changed" in text


async def test_git_diff_staged(git_repo: Path, config) -> None:
    (git_repo / "src" / "main.py").write_text("staged change\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "src/main.py"], check=True)
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    staged = await git.diff("staged")
    working = await git.diff("working")
    assert "+staged change" in staged
    assert "staged change" not in working


async def test_git_diff_head(git_repo: Path, config) -> None:
    # Tracked modification: untracked files do not appear in `git diff HEAD`.
    (git_repo / "src" / "main.py").write_text("changed for head diff\n", encoding="utf-8")
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    text = await git.diff("head")
    assert "diff --git" in text
    assert "changed for head diff" in text


async def test_git_diff_bounded(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / "src" / "main.py").write_text(
        "".join(f"line {i}\n" for i in range(2000)), encoding="utf-8"
    )
    config = make_test_config(tmp_path, {"repository": {"max_git_diff_bytes": 2000}})
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    text = await git.diff("working")
    assert len(text.encode("utf-8")) <= 2200
    assert "truncated" in text


async def test_git_diff_path_validation(git_repo: Path, config) -> None:
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    with pytest.raises(RepoAccessError):
        await git.diff("working", ["../outside"])


async def test_git_status(git_repo: Path, config) -> None:
    (git_repo / "new.txt").write_text("x", encoding="utf-8")
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    status = await git.status()
    assert "new.txt" in status


async def test_git_show(git_repo: Path, config) -> None:
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    text = await git.show("HEAD", "src/main.py")
    assert "def main" in text


async def test_git_show_rejects_unsafe_rev(git_repo: Path, config) -> None:
    git = GitTools(AccessPolicy(git_repo, config.repository), config.repository)
    for rev in ("--help", "HEAD..HEAD~2", "a;rm -rf /", "HEAD --", "refs/../x"):
        with pytest.raises(RepoAccessError):
            await git.show(rev, "src/main.py")
