"""The provider seam for the AI service.

The service talks to one small interface, :class:`ModelClient`, so another provider can be added
without touching a caller. This module is the only place a provider's key and wire format live.

The trial's provider is DeepSeek through its OpenAI-compatible chat API. No SDK is imported: the
request is one JSON document over HTTPS, and the answer is read back as text. The model gets no
tools and no function calling.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_ESCALATION_MODEL = "deepseek-v4-pro"

# USD per 1,000,000 tokens, at DeepSeek's peak rates: (cache-miss input, output). Off-peak is half.
# The ledger stores the cost this computes; the report states it is an estimate, not a bill.
PRICING_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "deepseek-flash": (Decimal("0.30"), Decimal("1.20")),
    "deepseek-v4-pro": (Decimal("1.32"), Decimal("3.96")),
}


class ModelCallError(RuntimeError):
    """The provider could not be reached or answered with something that is not a reply."""


@dataclass(frozen=True)
class ModelReply:
    """One model answer, with the usage the provider reported (or an estimate when it did not)."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None


class ModelClient(Protocol):
    """The one interface every provider implements. No tools, no free-form actions."""

    provider: str

    def complete(self, *, model: str, system: str, user: str) -> ModelReply:
        """Return one text answer for one system/user pair."""
        ...


def estimate_tokens(text: str) -> int:
    """A conservative token estimate for a provider that did not report usage."""
    return max(1, (len(text) + 3) // 4)


def cost_of(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """The estimated USD cost of one call at the model's peak rates."""
    rates = PRICING_PER_MILLION.get(model)
    if rates is None:
        return Decimal("0")
    input_rate, output_rate = rates
    cost = (Decimal(tokens_in) * input_rate + Decimal(tokens_out) * output_rate) / Decimal(
        1_000_000
    )
    return cost.quantize(Decimal("0.000001"))


class DeepSeekClient:
    """DeepSeek's OpenAI-compatible chat API, with a JSON-only response."""

    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def complete(self, *, model: str, system: str, user: str) -> ModelReply:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # The whole pipeline is JSON in, JSON out. Thinking is off: finding one element on one
            # page is a lookup, and the trial measures the cost per row.
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": 512,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ModelCallError(f"DeepSeek answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelCallError(f"DeepSeek could not be reached: {exc}") from exc

        try:
            payload = json.loads(raw)
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelCallError("DeepSeek answered with something that is not a reply") from exc
        if not isinstance(content, str):
            raise ModelCallError("DeepSeek answered without a message")

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
        tokens_in = int(tokens_in) if isinstance(tokens_in, int) else estimate_tokens(system + user)
        tokens_out = int(tokens_out) if isinstance(tokens_out, int) else estimate_tokens(content)
        return ModelReply(
            text=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_of(model, tokens_in, tokens_out),
        )
