"""Constrained read-only Git operations via fixed-argument subprocess calls.

DeepSeek never supplies an arbitrary Git command string: only the safe modes
below exist, all with validated path arguments and bounded output.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal

from deepseek_mcp.config.models import RepositoryConfig
from deepseek_mcp.repo.guard import AccessPolicy, RepoAccessError

DiffMode = Literal["working", "staged", "head"]

_SAFE_REV_RE = re.compile(
    r"^(?:[0-9a-fA-F]{7,40}|HEAD(?:~[0-9]{1,3})?|[A-Za-z][A-Za-z0-9._-]{0,63})$"
)
_GIT_TIMEOUT_S = 30.0
_TRUNCATION_MARKER = "\n[git output truncated: {n} bytes omitted; narrow the scope]\n"


class GitError(RepoAccessError):
    """Git execution failed or produced no usable output."""


class GitTools:
    """Fixed-argv git wrapper. Read-only modes only; no shell involved."""

    def __init__(self, policy: AccessPolicy, config: RepositoryConfig) -> None:
        self.policy = policy
        self.config = config
        self.timeout_s = _GIT_TIMEOUT_S

    def _base_args(self) -> list[str]:
        # -c overrides neutralize local paging/color config; env below
        # disables system/global gitconfig (aliases, filters) entirely.
        return [
            "git",
            "-c",
            "core.pager=cat",
            "-c",
            "color.ui=false",
            "-c",
            "diff.external=",
            "-C",
            str(self.policy.repo_root),
        ]

    @staticmethod
    def _clean_env() -> dict[str, str]:
        import os

        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    async def _run(self, args: list[str]) -> str:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._clean_env(),
            )
        except OSError as exc:
            raise GitError(f"failed to launch git: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise GitError(f"git timed out after {self.timeout_s:.0f}s") from None
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise GitError(f"git failed ({process.returncode}): {message}")
        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    def _bound(text: str, max_bytes: int) -> str:
        raw = text.encode("utf-8", errors="replace")
        if len(raw) <= max_bytes:
            return text
        kept = raw[:max_bytes].decode("utf-8", errors="replace")
        omitted = len(raw) - max_bytes
        return kept + _TRUNCATION_MARKER.format(n=omitted)

    def _validate_paths(self, paths: list[str] | None) -> list[str]:
        if not paths:
            return []
        validated: list[str] = []
        for rel in paths:
            resolved = self.policy.check_repo(rel)
            if not resolved.exists():
                raise RepoAccessError(f"git path not found: {rel}")
            validated.append(str(resolved))
        return validated

    async def diff(self, mode: DiffMode, paths: list[str] | None = None) -> str:
        """Return a bounded unified diff for working/staged/head scope."""
        base = ["diff", "--no-ext-diff", "--unified=3"]
        if mode == "staged":
            base.append("--cached")
        elif mode == "head":
            base.append("HEAD")
        base.append("--")
        args = [*self._base_args(), *base, *self._validate_paths(paths)]
        output = await self._run(args)
        return self._bound(output, self.config.max_git_diff_bytes)

    async def status(self) -> str:
        """Return short porcelain status."""
        args = [*self._base_args(), "status", "--porcelain"]
        output = await self._run(args)
        return self._bound(output, self.config.max_git_diff_bytes)

    async def show(self, rev: str, rel_path: str) -> str:
        """Return the content of a file at a safe revision or ref."""
        if not _SAFE_REV_RE.match(rev):
            raise RepoAccessError(f"unsafe revision specifier: {rev!r}")
        resolved = self.policy.check_repo(rel_path)
        rel_posix = resolved.relative_to(self.policy.repo_root).as_posix()
        args = [
            *self._base_args(),
            "show",
            f"{rev}:{rel_posix}",
        ]
        output = await self._run(args)
        return self._bound(output, self.config.max_git_diff_bytes)
