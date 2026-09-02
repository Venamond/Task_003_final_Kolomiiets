"""Спостережуваність: токени, вартість, підсумок прогону, посилання на трейс.

Перенесено підхід з hw4. Замір токенів локальний: UsageMetadataCallbackHandler
бере їх з відповідей моделі, без мережі, тому облік працює і без ключа
LangSmith — трасування є другим джерелом, а не умовою роботи.

Ціни беруться з конфігурації, щоб не рахувати за вчорашніми.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler

import config

# Ціна вказана за мільйон токенів — так її публікують провайдери.
TOKENS_PER_PRICE_UNIT = 1_000_000

PRICES: dict[str, tuple[float, float]] = {
    "anthropic": (config.PRICE_ANTHROPIC_INPUT, config.PRICE_ANTHROPIC_OUTPUT),
    "google": (config.PRICE_GOOGLE_INPUT, config.PRICE_GOOGLE_OUTPUT),
    "google_lite": (config.PRICE_GOOGLE_INPUT, config.PRICE_GOOGLE_OUTPUT),
}


def cost_of(input_tokens: int, output_tokens: int,
            provider: str | None = None) -> float:
    """Порахувати вартість прогону в доларах.

    Args:
        input_tokens: Скільки токенів пішло на вхід.
        output_tokens: Скільки токенів повернула модель.
        provider: Ім'я провайдера; за умовчанням — з конфігурації.

    Returns:
        Вартість у доларах. Невідомий провайдер дає нуль, а не помилку:
        облік не має валити прогін.
    """
    name = (provider or config.LLM_PROVIDER).lower()
    if name not in PRICES:
        return 0.0
    price_in, price_out = PRICES[name]
    return round(
        input_tokens / TOKENS_PER_PRICE_UNIT * price_in
        + output_tokens / TOKENS_PER_PRICE_UNIT * price_out, 6)


@contextmanager
def token_counter():
    """Лічильник токенів прогону. Працює без мережі й без ключа.

    Використання:
        with token_counter() as usage:
            await app.ainvoke(state, config={"callbacks": [usage["handler"]]})
        print(usage["input_tokens"], usage["output_tokens"], usage["cost_usd"])

    Yields:
        Словник, який заповнюється після виходу з контексту.
    """
    handler = UsageMetadataCallbackHandler()
    usage: dict[str, Any] = {"handler": handler, "input_tokens": 0,
                             "output_tokens": 0, "cost_usd": 0.0,
                             "elapsed_ms": 0}
    started = time.monotonic()
    try:
        yield usage
    finally:
        totals = getattr(handler, "usage_metadata", {}) or {}
        for data in totals.values():
            usage["input_tokens"] += data.get("input_tokens", 0)
            usage["output_tokens"] += data.get("output_tokens", 0)
        usage["cost_usd"] = cost_of(usage["input_tokens"], usage["output_tokens"])
        usage["elapsed_ms"] = int((time.monotonic() - started) * 1000)


def tracing_on() -> bool:
    """Чи ввімкнене трасування LangSmith.

    Returns:
        True, якщо є ключ і трасування не вимкнене явно.
    """
    enabled = os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
    return bool(enabled and os.getenv("LANGSMITH_API_KEY"))


def share_trace(run_id: str) -> str | None:
    """Створити публічне посилання на трейс.

    Args:
        run_id: Ідентифікатор прогону в LangSmith.

    Returns:
        Публічна адреса або None, якщо трасування вимкнене чи щось пішло не так.
    """
    if not tracing_on():
        return None
    try:
        from langsmith import Client
        return Client().share_run(run_id)
    except Exception:  # noqa: BLE001 — відсутнє посилання не має валити прогін
        return None


@dataclass
class RunStats:
    """Підсумок одного прогону графа."""

    llm_calls: int = 0
    tool_calls: int = 0
    steps: int = 0
    agents: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

    @classmethod
    def from_state(cls, state: dict) -> "RunStats":
        """Зібрати підсумок зі стану графа.

        Args:
            state: Підсумковий стан після прогону.

        Returns:
            Заповнений підсумок.
        """
        trajectory = state.get("trajectory") or []
        tools: list[str] = []
        for entry in trajectory:
            for name in entry.get("tools", []) or []:
                if name not in tools:
                    tools.append(name)
        return cls(
            llm_calls=state.get("llm_calls", 0),
            tool_calls=state.get("tool_calls_count", 0),
            steps=len(trajectory),
            agents=sorted({e["agent_name"] for e in trajectory if "agent_name" in e}),
            tools=tools,
        )
