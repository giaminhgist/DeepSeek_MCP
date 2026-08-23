"""DeepSeek provider client behind a small mockable protocol.

The production implementation uses the Python ``anthropic`` SDK pointed at
the DeepSeek Anthropic-compatible endpoint. Tests inject fake clients that
implement :class:`DeepSeekClient` — no network, no paid calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam

from deepseek_mcp.usage.tracker import ProviderUsage


class DeepSeekProviderError(Exception):
    """Base class for provider-level failures."""


class ProviderAuthError(DeepSeekProviderError):
    """401/403: bad or unauthorized API key."""


class ProviderRateLimitError(DeepSeekProviderError):
    """429: rate limited by the provider."""


class ProviderTimeoutError(DeepSeekProviderError):
    """The provider request timed out."""


class ProviderConnectionError(DeepSeekProviderError):
    """Network-level connection failure."""


class ProviderError(DeepSeekProviderError):
    """Any other provider API error."""


@dataclass(slots=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkerTurnRequest:
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_tokens: int
    temperature: float
    timeout_s: float


@dataclass(slots=True)
class WorkerTurnResponse:
    text: str
    tool_calls: list[ToolCallRequest]
    stop_reason: str
    usage: ProviderUsage


class DeepSeekClient(Protocol):
    async def run_turn(self, request: WorkerTurnRequest) -> WorkerTurnResponse: ...


class AnthropicDeepSeekClient:
    """Async Anthropic SDK client configured for the DeepSeek base URL."""

    def __init__(self, *, api_key: str, base_url: str, timeout_ms: int) -> None:
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_ms / 1000,
            max_retries=0,
        )

    async def run_turn(self, request: WorkerTurnRequest) -> WorkerTurnResponse:
        try:
            response = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                # Messages/tools are built dynamically in Anthropic wire format.
                messages=cast(list[MessageParam], request.messages),
                tools=cast(list[ToolParam], request.tools),
                temperature=request.temperature,
            )
        except anthropic.AuthenticationError as exc:
            raise ProviderAuthError(f"DeepSeek API authentication failed: {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderAuthError(f"DeepSeek API authorization failed: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderRateLimitError(f"DeepSeek API rate limit: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(f"DeepSeek API request timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderConnectionError(f"DeepSeek API connection error: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"DeepSeek API error (status {exc.status_code}): {exc}") from exc

        if response.usage is None or not isinstance(response.usage, anthropic.types.Usage):
            raise ProviderError("provider response did not include usage data")
        usage = ProviderUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", None),
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )
        return WorkerTurnResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            usage=usage,
        )
