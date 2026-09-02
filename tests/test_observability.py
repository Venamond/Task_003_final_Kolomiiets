"""Тести спостережуваності.

Замір токенів локальний: він читає дані прямо з відповідей моделі. Тому
працює без мережі й без ключа LangSmith — і саме це тут перевіряється.
Трасування має бути другим, необовʼязковим джерелом, а не умовою роботи.
"""
from __future__ import annotations

import config
import observability as obs


def test_cost_is_zero_without_tokens():
    """Нуль токенів — нуль вартості."""
    assert obs.cost_of(0, 0) == 0.0


def test_cost_uses_configured_prices():
    """Вартість рахується за цінами з конфігурації, а не за зашитими."""
    assert obs.cost_of(1_000_000, 0, provider="anthropic") == config.PRICE_ANTHROPIC_INPUT


def test_cost_separates_input_and_output():
    """Вихідні токени дорожчі за вхідні — ціни різні."""
    assert (obs.cost_of(0, 1_000_000, provider="anthropic")
            == config.PRICE_ANTHROPIC_OUTPUT)


def test_cost_for_google_provider():
    """Для іншого провайдера беруться його ціни."""
    assert obs.cost_of(1_000_000, 0, provider="google") == config.PRICE_GOOGLE_INPUT


def test_unknown_provider_falls_back_to_zero():
    """Невідомий провайдер дає нуль, а не падіння: облік не валить прогін."""
    assert obs.cost_of(1000, 1000, provider="невідомий") == 0.0


def test_token_counter_works_without_network():
    """Лічильник токенів піднімається без ключа і без мережі."""
    with obs.token_counter() as usage:
        assert "handler" in usage
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0


def test_tracing_off_without_key(monkeypatch):
    """Без ключа LangSmith трасування вимкнене, але це не помилка."""
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert obs.tracing_on() is False


def test_run_stats_accumulates():
    """Підсумок прогону збирає лічильники зі стану графа."""
    stats = obs.RunStats.from_state({
        "llm_calls": 3, "tool_calls_count": 5,
        "trajectory": [{"agent_name": "supervisor"}, {"agent_name": "curator"}],
    })
    assert stats.llm_calls == 3
    assert stats.tool_calls == 5
    assert stats.agents == ["curator", "supervisor"]


def test_run_stats_survives_empty_state():
    """Порожній стан не валить облік."""
    stats = obs.RunStats.from_state({})
    assert stats.llm_calls == 0 and stats.agents == []
