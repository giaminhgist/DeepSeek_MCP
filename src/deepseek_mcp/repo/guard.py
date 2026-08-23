"""Path security for all repository and filesystem tool access.

Every path a worker touches goes through :class:`AccessPolicy`, which:

- canonicalizes roots with ``Path.resolve()`` (follows symlinks),
- verifies containment with ``Path.is_relative_to`` (never string prefixes),
- blocks ``..`` traversal and symlink escapes,
- enforces deny globs everywhere and ``.gitignore`` inside the repo root.
"""

from __future__ import annotations

from pathlib import Path

from pathspec import PathSpec
from pathspec.gitignore import GitIgnoreSpec

from deepseek_mcp.config.models import RepositoryConfig


class RepoAccessError(Exception):
    """Base class for repository access failures."""


class PathTraversalError(RepoAccessError):
    """The requested path escapes an allowed root."""


class PathDeniedError(RepoAccessError):
    """The path matches a sensitive-file deny glob."""


class PathIgnoredError(RepoAccessError):
    """The path is excluded by .gitignore rules."""


class PathNotFoundError(RepoAccessError):
    """The requested path does not exist."""


class BinaryFileError(RepoAccessError):
    """The file looks binary and cannot be returned as text."""


class FileTooLargeError(RepoAccessError):
    """The file exceeds the configured byte limit."""


class NotAFileError(RepoAccessError):
    """The path is not a regular file."""


def _canonical(path: Path) -> Path:
    """Resolve symlinks/.. without requiring the path to exist."""
    return Path(path).expanduser().resolve()


def _contained(resolved: Path, root: Path) -> bool:
    return resolved == root or resolved.is_relative_to(root)


def _clean_rel(resolved: Path, root: Path) -> str:
    return resolved.relative_to(root).as_posix()


class AccessPolicy:
    """Multi-root path guard: repo root plus optional extra allowed roots."""

    def __init__(self, repo_root: Path, config: RepositoryConfig) -> None:
        self.repo_root = _canonical(repo_root)
        self.config = config
        self.extra_roots: tuple[Path, ...] = ()
        # An invalid deny glob raises here; config loading is expected to
        # surface configuration problems at startup.
        self.deny_spec: PathSpec = PathSpec.from_lines("gitignore", config.deny_globs)
        self.gitignore: GitIgnoreSpec | None = None
        if config.respect_gitignore:
            gitignore_file = self.repo_root / ".gitignore"
            if gitignore_file.is_file():
                try:
                    lines = gitignore_file.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                self.gitignore = GitIgnoreSpec.from_lines(lines)

    def add_extra_roots(self, roots: tuple[str, ...] | list[str]) -> None:
        canonical = tuple(_canonical(Path(r).expanduser()) for r in roots)
        # Exclude duplicates of the repo root and keep order stable.
        self.extra_roots = tuple(dict.fromkeys(r for r in canonical if r != self.repo_root))

    @property
    def all_roots(self) -> tuple[Path, ...]:
        return (self.repo_root, *self.extra_roots)

    def _find_root(self, resolved: Path) -> Path | None:
        for root in self.all_roots:
            if _contained(resolved, root):
                return root
        return None

    def _deny(self, rel_posix: str, containing_root: Path) -> None:
        if self.deny_spec.match_file(rel_posix):
            raise PathDeniedError(f"path denied by deny rules: {rel_posix}")
        if (
            containing_root == self.repo_root
            and self.config.respect_gitignore
            and self.gitignore is not None
            and self.gitignore.match_file(rel_posix)
        ):
            raise PathIgnoredError(f"path excluded by .gitignore: {rel_posix}")

    def check_repo(self, path: str) -> Path:
        """Resolve a repository-relative path. Absolute paths are rejected."""
        if not path or "\x00" in path:
            raise PathTraversalError("empty or invalid path")
        raw = Path(path)
        if raw.is_absolute():
            raise PathTraversalError(f"absolute paths are not allowed for repo tools: {path!r}")
        candidate = self.repo_root / raw
        resolved = _canonical(candidate)
        if not _contained(resolved, self.repo_root):
            raise PathTraversalError(f"path escapes the repository root: {path!r}")
        rel = _clean_rel(resolved, self.repo_root)
        self._deny(rel, self.repo_root)
        return resolved

    def check_any(self, path: str) -> Path:
        """Resolve an absolute path or repo-relative path within any allowed root."""
        if not path or "\x00" in path:
            raise PathTraversalError("empty or invalid path")
        raw = Path(path)
        base = raw if raw.is_absolute() else self.repo_root / raw
        resolved = _canonical(base)
        root = self._find_root(resolved)
        if root is None:
            raise PathTraversalError(f"path is outside allowed roots: {path!r}")
        rel = _clean_rel(resolved, root)
        self._deny(rel, root)
        return resolved

    def matches_deny(self, rel_posix: str) -> bool:
        return bool(self.deny_spec.match_file(rel_posix))

    def matches_ignore(self, rel_posix: str) -> bool:
        return bool(
            self.config.respect_gitignore
            and self.gitignore is not None
            and self.gitignore.match_file(rel_posix)
        )

    def dir_is_excluded(self, rel_posix: str) -> bool:
        """True when a directory should be pruned from listing/search walks."""
        return (
            self.matches_deny(rel_posix)
            or self.matches_deny(rel_posix + "/")
            or self.matches_ignore(rel_posix)
            or self.matches_ignore(rel_posix + "/")
        )
