"""Safe YAML loading and validation for config/deepseek-worker.yaml."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from deepseek_mcp.config.models import (
    DETAIL_LEVELS,
    BudgetConfig,
    CompactionConfig,
    Config,
    DetailLimits,
    LoggingConfig,
    ModelConfig,
    OutputDetail,
    PricingConfig,
    PricingRates,
    ProviderConfig,
    RepositoryConfig,
    ToolsConfig,
    WorkerConfig,
)

CONFIG_FILE_NAME = "deepseek-worker.yaml"
SUPPORTED_VERSION = 1
DEFAULT_CONFIG_RELATIVE = Path("config") / CONFIG_FILE_NAME
ENV_BASE_URL = "DEEPSEEK_BASE_URL"

_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}


class ConfigError(Exception):
    """Raised when the worker YAML configuration is missing or invalid."""


def _resolve_config_path(env: Mapping[str, str]) -> Path:
    """Resolve the YAML path: env override, then cwd upward search."""
    override = env.get("DEEPSEEK_CONFIG")
    if override:
        return Path(override).expanduser()

    current = Path.cwd()
    for directory in (current, *current.parents):
        candidate = directory / DEFAULT_CONFIG_RELATIVE
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"worker config not found: set DEEPSEEK_CONFIG or place {DEFAULT_CONFIG_RELATIVE} "
        f"under the working directory"
    )


class _Validator:
    """Collects schema errors so a single ConfigError lists them all."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def _err(self, message: str) -> None:
        self.errors.append(message)

    def section(self, data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
        section = data.get(name)
        if not isinstance(section, dict):
            self._err(f"'{name}' must be a mapping")
            return {}
        return section

    def int_field(self, section: Mapping[str, Any], name: str, *, positive: bool = True) -> int:
        value = section.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            self._err(f"'{name}' must be an integer")
            return 0
        if positive and value <= 0:
            self._err(f"'{name}' must be positive")
        return value

    def float_field(self, section: Mapping[str, Any], name: str) -> float:
        value = section.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self._err(f"'{name}' must be a number")
            return 0.0
        return float(value)

    def bool_field(self, section: Mapping[str, Any], name: str) -> bool:
        value = section.get(name)
        if not isinstance(value, bool):
            self._err(f"'{name}' must be a boolean")
            return False
        return value

    def str_field(self, section: Mapping[str, Any], name: str, *, nonempty: bool = True) -> str:
        value = section.get(name)
        if not isinstance(value, str):
            self._err(f"'{name}' must be a string")
            return ""
        if nonempty and not value.strip():
            self._err(f"'{name}' must not be empty")
        return value

    def str_list(self, section: Mapping[str, Any], name: str) -> list[str]:
        value = section.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            self._err(f"'{name}' must be a list of strings")
            return []
        return list(value)

    def finish(self) -> None:
        if self.errors:
            raise ConfigError("invalid worker configuration: " + "; ".join(self.errors))


def _validate_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_config(data: Mapping[str, Any], config_path: Path) -> Config:
    v = _Validator()

    version = v.int_field(data, "version", positive=True)
    if version != SUPPORTED_VERSION:
        v._err(f"unsupported config version {version!r}; expected {SUPPORTED_VERSION}")

    provider = v.section(data, "provider")
    api_format = v.str_field(provider, "api_format")
    if api_format != "anthropic":
        v._err("'provider.api_format' must be 'anthropic'")
    base_url = v.str_field(provider, "base_url")
    if not _validate_url(base_url):
        v._err(f"'provider.base_url' is not a valid URL: {base_url!r}")
    api_key_env = v.str_field(provider, "api_key_env")
    request_timeout_ms = v.int_field(provider, "request_timeout_ms")

    model = v.section(data, "model")
    model_name = v.str_field(model, "name")
    context_window = v.int_field(model, "context_window_tokens")
    max_output = v.int_field(model, "max_output_tokens_per_call")
    temperature = v.float_field(model, "temperature")
    if not 0.0 <= temperature <= 2.0:
        v._err("'model.temperature' must be between 0 and 2")
    if context_window and max_output and max_output >= context_window:
        v._err("'model.max_output_tokens_per_call' must be below the context window")

    worker = v.section(data, "worker")
    max_iterations = v.int_field(worker, "max_agent_iterations")
    max_run_seconds = v.float_field(worker, "max_run_seconds")
    if max_run_seconds <= 0:
        v._err("'worker.max_run_seconds' must be positive")
    default_detail_raw = v.str_field(worker, "default_output_detail")
    if default_detail_raw not in DETAIL_LEVELS:
        v._err(f"'worker.default_output_detail' must be one of {list(DETAIL_LEVELS)}")
    default_detail = cast(OutputDetail, default_detail_raw)

    tools = v.section(data, "tools")
    allow_file_tools = v.bool_field(tools, "allow_file_tools")
    allow_writes = v.bool_field(tools, "allow_writes")
    allow_bash = v.bool_field(tools, "allow_bash")
    extra_roots = v.str_list(tools, "extra_allowed_roots")
    max_bash_output_chars = v.int_field(tools, "max_bash_output_chars")
    bash_timeout_ms = v.int_field(tools, "bash_timeout_ms")

    compaction = v.section(data, "compaction")
    enabled = v.bool_field(compaction, "enabled")
    max_tool_result_chars = v.int_field(compaction, "max_tool_result_chars")
    soft = v.int_field(compaction, "worker_context_soft_limit_tokens")
    hard = v.int_field(compaction, "worker_context_hard_limit_tokens")
    preserve_recent = v.int_field(compaction, "preserve_recent_messages", positive=False)
    target_chars = v.section(compaction, "final_target_chars")
    brief_target = v.int_field(target_chars, "brief")
    normal_target = v.int_field(target_chars, "normal")
    detailed_target = v.int_field(target_chars, "detailed")
    hard_chars = v.int_field(compaction, "final_hard_limit_chars")
    max_findings_raw = v.section(compaction, "max_findings")
    brief_findings = v.int_field(max_findings_raw, "brief")
    normal_findings = v.int_field(max_findings_raw, "normal")
    detailed_findings = v.int_field(max_findings_raw, "detailed")
    max_evidence_raw = v.section(compaction, "max_evidence_items")
    brief_evidence = v.int_field(max_evidence_raw, "brief")
    normal_evidence = v.int_field(max_evidence_raw, "normal")
    detailed_evidence = v.int_field(max_evidence_raw, "detailed")
    include_transcript = v.bool_field(compaction, "include_raw_transcript")

    if soft and hard:
        if soft >= hard:
            v._err("compaction soft limit must be below the hard limit")
        if context_window and hard >= context_window:
            v._err("compaction hard limit must be below the model context window")
    if hard_chars:
        for name, target in (
            ("brief", brief_target),
            ("normal", normal_target),
            ("detailed", detailed_target),
        ):
            if target and target > hard_chars:
                v._err(
                    f"'compaction.final_target_chars.{name}' must not exceed "
                    f"'final_hard_limit_chars' ({hard_chars})"
                )

    repository = v.section(data, "repository")
    root_env = v.str_field(repository, "root_env")
    max_file_bytes = v.int_field(repository, "max_file_bytes")
    max_read_lines = v.int_field(repository, "max_read_lines")
    max_search_matches = v.int_field(repository, "max_search_matches")
    max_list_entries = v.int_field(repository, "max_list_entries")
    max_git_diff_bytes = v.int_field(repository, "max_git_diff_bytes")
    allow_repo_root_argument = v.bool_field(repository, "allow_repo_root_argument")
    respect_gitignore = v.bool_field(repository, "respect_gitignore")
    deny_globs = v.str_list(repository, "deny_globs")

    budget = v.section(data, "budget")
    max_calls = v.int_field(budget, "max_api_calls_per_run")
    max_input = v.int_field(budget, "max_input_tokens_per_run")
    max_output_run = v.int_field(budget, "max_output_tokens_per_run")
    max_total = v.int_field(budget, "max_total_tokens_per_run")
    max_cost = v.float_field(budget, "max_estimated_cost_usd_per_run")
    if max_cost <= 0:
        v._err("'budget.max_estimated_cost_usd_per_run' must be positive")
    on_limit = v.str_field(budget, "on_limit")
    if on_limit != "stop":
        v._err("'budget.on_limit' must be 'stop'")

    pricing = v.section(data, "pricing")
    currency = v.str_field(pricing, "currency")
    source = v.str_field(pricing, "source")
    snapshot_date = v.str_field(pricing, "snapshot_date")
    per_million = v.section(pricing, "per_million_tokens")
    hit = v.float_field(per_million, "input_cache_hit")
    miss = v.float_field(per_million, "input_cache_miss")
    out = v.float_field(per_million, "output")
    if min(hit, miss, out) < 0:
        v._err("'pricing.per_million_tokens' values must not be negative")

    logging_section = v.section(data, "logging")
    log_level = v.str_field(logging_section, "level").lower()
    if log_level not in _LOG_LEVELS:
        v._err(f"'logging.level' must be one of {sorted(_LOG_LEVELS)}")

    v.finish()

    return Config(
        version=version,
        provider=ProviderConfig(
            api_format=api_format,
            base_url=base_url,
            api_key_env=api_key_env,
            request_timeout_ms=request_timeout_ms,
        ),
        model=ModelConfig(
            name=model_name,
            context_window_tokens=context_window,
            max_output_tokens_per_call=max_output,
            temperature=temperature,
        ),
        worker=WorkerConfig(
            max_agent_iterations=max_iterations,
            max_run_seconds=max_run_seconds,
            default_output_detail=default_detail,
        ),
        tools=ToolsConfig(
            allow_file_tools=allow_file_tools,
            allow_writes=allow_writes,
            allow_bash=allow_bash,
            extra_allowed_roots=tuple(extra_roots),
            max_bash_output_chars=max_bash_output_chars,
            bash_timeout_ms=bash_timeout_ms,
        ),
        compaction=CompactionConfig(
            enabled=enabled,
            max_tool_result_chars=max_tool_result_chars,
            worker_context_soft_limit_tokens=soft,
            worker_context_hard_limit_tokens=hard,
            preserve_recent_messages=preserve_recent,
            final_target_chars=DetailLimits(brief_target, normal_target, detailed_target),
            final_hard_limit_chars=hard_chars,
            max_findings=DetailLimits(brief_findings, normal_findings, detailed_findings),
            max_evidence_items=DetailLimits(brief_evidence, normal_evidence, detailed_evidence),
            include_raw_transcript=include_transcript,
        ),
        repository=RepositoryConfig(
            root_env=root_env,
            max_file_bytes=max_file_bytes,
            max_read_lines=max_read_lines,
            max_search_matches=max_search_matches,
            max_list_entries=max_list_entries,
            max_git_diff_bytes=max_git_diff_bytes,
            allow_repo_root_argument=allow_repo_root_argument,
            respect_gitignore=respect_gitignore,
            deny_globs=tuple(deny_globs),
        ),
        budget=BudgetConfig(
            max_api_calls_per_run=max_calls,
            max_input_tokens_per_run=max_input,
            max_output_tokens_per_run=max_output_run,
            max_total_tokens_per_run=max_total,
            max_estimated_cost_usd_per_run=max_cost,
            on_limit=on_limit,
        ),
        pricing=PricingConfig(
            currency=currency,
            source=source,
            snapshot_date=snapshot_date,
            per_million_tokens=PricingRates(hit, miss, out),
        ),
        logging=LoggingConfig(level=log_level),
        api_key=None,
        config_path=config_path,
    )


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    """Load, validate and resolve the worker configuration.

    ``env`` defaults to ``os.environ``. The API key is read from the env var
    named by ``provider.api_key_env`` and never from the YAML file.
    """
    environ = os.environ if env is None else env
    config_path = path or _resolve_config_path(environ)
    if not config_path.is_file():
        raise ConfigError(f"worker config file not found: {config_path}")
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read worker config {config_path}: {exc}") from exc
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in worker config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"worker config {config_path} must contain a YAML mapping")

    config = _parse_config(raw, config_path)

    base_url = environ.get(ENV_BASE_URL)
    if base_url:
        if not _validate_url(base_url):
            raise ConfigError(f"{ENV_BASE_URL} is not a valid URL")
        config = replace(config, provider=replace(config.provider, base_url=base_url))

    api_key = environ.get(config.provider.api_key_env)
    if api_key is not None:
        api_key = api_key.strip() or None
    return replace(config, api_key=api_key)
