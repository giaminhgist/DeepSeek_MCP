"""Typed configuration models for the DeepSeek worker.

All runtime model/budget/compaction settings come from
``config/deepseek-worker.yaml`` and are validated into these frozen
dataclasses before use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OutputDetail = Literal["brief", "normal", "detailed"]
DETAIL_LEVELS: tuple[OutputDetail, ...] = ("brief", "normal", "detailed")


@dataclass(frozen=True, slots=True)
class DetailLimits:
    """Per-detail-mode limits (e.g. final response targets, finding caps)."""

    brief: int
    normal: int
    detailed: int

    def get(self, detail: OutputDetail) -> int:
        return getattr(self, detail)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    api_format: str
    base_url: str
    api_key_env: str
    request_timeout_ms: int


@dataclass(frozen=True, slots=True)
class ModelConfig:
    name: str
    context_window_tokens: int
    max_output_tokens_per_call: int
    temperature: float


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    max_agent_iterations: int
    max_run_seconds: float
    default_output_detail: OutputDetail


@dataclass(frozen=True, slots=True)
class ToolsConfig:
    """Internal worker toolset switches and bounds.

    Per project-owner override (2026-08-23) the worker tool loop may include
    file tools outside the repository root and write/shell tools.
    """

    allow_file_tools: bool
    allow_writes: bool
    allow_bash: bool
    extra_allowed_roots: tuple[str, ...]
    max_bash_output_chars: int
    bash_timeout_ms: int


@dataclass(frozen=True, slots=True)
class CompactionConfig:
    enabled: bool
    max_tool_result_chars: int
    worker_context_soft_limit_tokens: int
    worker_context_hard_limit_tokens: int
    preserve_recent_messages: int
    final_target_chars: DetailLimits
    final_hard_limit_chars: int
    max_findings: DetailLimits
    max_evidence_items: DetailLimits
    include_raw_transcript: bool


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    root_env: str
    max_file_bytes: int
    max_read_lines: int
    max_search_matches: int
    max_list_entries: int
    max_git_diff_bytes: int
    allow_repo_root_argument: bool
    respect_gitignore: bool
    deny_globs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    max_api_calls_per_run: int
    max_input_tokens_per_run: int
    max_output_tokens_per_run: int
    max_total_tokens_per_run: int
    max_estimated_cost_usd_per_run: float
    on_limit: str


@dataclass(frozen=True, slots=True)
class PricingRates:
    """USD price per token (per_million_tokens / 1e6)."""

    input_cache_hit: float
    input_cache_miss: float
    output: float


@dataclass(frozen=True, slots=True)
class PricingConfig:
    currency: str
    source: str
    snapshot_date: str
    per_million_tokens: PricingRates

    @property
    def per_token(self) -> PricingRates:
        million = 1_000_000.0
        return PricingRates(
            input_cache_hit=self.per_million_tokens.input_cache_hit / million,
            input_cache_miss=self.per_million_tokens.input_cache_miss / million,
            output=self.per_million_tokens.output / million,
        )


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str


@dataclass(frozen=True, slots=True)
class Config:
    """Fully validated runtime configuration plus resolved runtime extras."""

    version: int
    provider: ProviderConfig
    model: ModelConfig
    worker: WorkerConfig
    tools: ToolsConfig
    compaction: CompactionConfig
    repository: RepositoryConfig
    budget: BudgetConfig
    pricing: PricingConfig
    logging: LoggingConfig
    api_key: str | None
    config_path: Path
