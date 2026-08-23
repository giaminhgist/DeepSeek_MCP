"""Path guard tests: containment, traversal, symlink escape, deny/ignore."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepseek_mcp.repo.guard import (
    AccessPolicy,
    PathDeniedError,
    PathIgnoredError,
    PathTraversalError,
)
from tests.conftest import make_test_config


@pytest.fixture
def policy(sample_repo: Path, tmp_path: Path) -> AccessPolicy:
    return AccessPolicy(sample_repo, make_test_config(tmp_path).repository)


def test_accepts_valid_nested_file(policy: AccessPolicy, sample_repo: Path) -> None:
    resolved = policy.check_repo("src/main.py")
    assert resolved == (sample_repo / "src" / "main.py").resolve()


def test_accepts_dot_path(policy: AccessPolicy, sample_repo: Path) -> None:
    assert policy.check_repo(".") == sample_repo.resolve()


def test_rejects_dotdot_traversal(policy: AccessPolicy) -> None:
    with pytest.raises(PathTraversalError):
        policy.check_repo("../outside.py")
    with pytest.raises(PathTraversalError):
        policy.check_repo("src/../../etc/passwd")


def test_rejects_absolute_outside_path(policy: AccessPolicy) -> None:
    with pytest.raises(PathTraversalError):
        policy.check_repo("/etc/passwd")


def test_rejects_absolute_path_even_inside(policy: AccessPolicy, sample_repo: Path) -> None:
    # Repo tools accept relative paths only.
    with pytest.raises(PathTraversalError):
        policy.check_repo(str((sample_repo / "src" / "main.py").resolve()))


def test_rejects_empty_and_nul(policy: AccessPolicy) -> None:
    with pytest.raises(PathTraversalError):
        policy.check_repo("")
    with pytest.raises(PathTraversalError):
        policy.check_repo("a\x00b")


def test_rejects_symlink_escape(policy: AccessPolicy, sample_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    (sample_repo / "escape.py").symlink_to(outside)
    with pytest.raises(PathTraversalError):
        policy.check_repo("escape.py")


def test_accepts_symlink_inside(policy: AccessPolicy, sample_repo: Path) -> None:
    (sample_repo / "link.py").symlink_to(sample_repo / "src" / "main.py")
    resolved = policy.check_repo("link.py")
    assert resolved == (sample_repo / "src" / "main.py").resolve()


def test_nonexistent_path_resolves_safely(policy: AccessPolicy, sample_repo: Path) -> None:
    resolved = policy.check_repo("not/there.py")
    assert resolved == (sample_repo / "not" / "there.py").resolve()
    assert not resolved.exists()


def test_deny_globs_always_respected(policy: AccessPolicy) -> None:
    with pytest.raises(PathDeniedError):
        policy.check_repo("secrets.env")
    with pytest.raises(PathDeniedError):
        policy.check_repo("src/../secrets.env")
    with pytest.raises(PathDeniedError):
        policy.check_repo("id_rsa.pem")
    with pytest.raises(PathDeniedError):
        policy.check_repo(".git/config")


def test_gitignore_respected(policy: AccessPolicy) -> None:
    with pytest.raises(PathIgnoredError):
        policy.check_repo("debug.log")
    with pytest.raises(PathIgnoredError):
        policy.check_repo("ignored/file.txt")


def test_extra_roots_extend_access(sample_repo: Path, tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "file.txt").write_text("hi", encoding="utf-8")
    policy = AccessPolicy(sample_repo, make_test_config(tmp_path).repository)
    policy.add_extra_roots([str(extra)])
    assert policy.check_any(str(extra / "file.txt")).exists()
    # Relative paths resolve against the repo root.
    assert policy.check_any("src/main.py").exists()
    # Outside everything is still blocked.
    with pytest.raises(PathTraversalError):
        policy.check_any("/etc/passwd")


def test_extra_root_deny_globs_apply(sample_repo: Path, tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / ".env").write_text("SECRET=1", encoding="utf-8")
    policy = AccessPolicy(sample_repo, make_test_config(tmp_path).repository)
    policy.add_extra_roots([str(extra)])
    with pytest.raises(PathDeniedError):
        policy.check_any(str(extra / ".env"))


def test_check_any_rejects_relative_escape(sample_repo: Path, tmp_path: Path) -> None:
    policy = AccessPolicy(sample_repo, make_test_config(tmp_path).repository)
    with pytest.raises(PathTraversalError):
        policy.check_any("../../etc/passwd")
