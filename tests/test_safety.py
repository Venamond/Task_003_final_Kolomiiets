"""Тести обмежувачів: кроки, час, зациклення, бюджет прогону, форма плану."""
from __future__ import annotations

import time

import config
import safety


def test_max_steps_stops():
    """Досягнення межі кроків зупиняє роботу."""
    assert safety.check_limits(10, time.monotonic(), max_steps=10) == safety.StopReason.MAX_STEPS


def test_below_max_steps_continues():
    """До межі кроків робота триває."""
    assert safety.check_limits(3, time.monotonic(), max_steps=10) is None


def test_timeout_stops():
    """Вичерпаний час зупиняє роботу."""
    started = time.monotonic() - 100
    assert safety.check_limits(1, started, max_steps=10,
                               timeout_seconds=10) == safety.StopReason.TIMEOUT


def test_steps_checked_before_time():
    """Кроки перевіряються першими: це конкретніша причина, ніж час."""
    started = time.monotonic() - 100
    assert safety.check_limits(10, started, max_steps=10,
                               timeout_seconds=10) == safety.StopReason.MAX_STEPS


def test_limits_default_to_config():
    """Без явних аргументів межі беруться з конфігурації."""
    assert safety.check_limits(config.MAX_STEPS_PER_AGENT,
                               time.monotonic()) == safety.StopReason.MAX_STEPS


def test_loop_detected_on_identical_calls():
    """Три однакові виклики поспіль — це зациклення."""
    det = safety.LoopDetector(max_repeats=3)
    args = {"qid": "Q1430"}
    assert det.check("get_influences", args) is False
    assert det.check("get_influences", args) is False
    assert det.check("get_influences", args) is True


def test_loop_not_detected_on_varied_calls():
    """Різні аргументи — не зациклення."""
    det = safety.LoopDetector(max_repeats=3)
    for qid in ("Q1", "Q2", "Q3"):
        assert det.check("get_influences", {"qid": qid}) is False


def test_loop_detector_ignores_argument_order():
    """Порядок ключів не робить виклик іншим."""
    det = safety.LoopDetector(max_repeats=2)
    det.check("t", {"a": 1, "b": 2})
    assert det.check("t", {"b": 2, "a": 1}) is True


def test_run_budget_stops_on_llm_calls():
    """Стеля викликів моделі за прогін зупиняє роботу."""
    state = {"llm_calls": config.MAX_LLM_CALLS_PER_RUN, "tool_calls_count": 0,
             "hops": 0, "started_at": time.monotonic()}
    assert safety.check_run_budget(state) == safety.StopReason.LLM_BUDGET


def test_run_budget_stops_on_hops():
    """Стеля переходів між агентами зупиняє перемаршрутизацію по колу."""
    state = {"llm_calls": 0, "tool_calls_count": 0,
             "hops": config.MAX_HOPS, "started_at": time.monotonic()}
    assert safety.check_run_budget(state) == safety.StopReason.MAX_HOPS


def test_plan_too_long_is_rejected():
    """План на десятки кроків — аномалія, а не план."""
    ok, reason = safety.plan_is_sane(["крок"] * (config.MAX_PLAN_STEPS + 1))
    assert ok is False and str(config.MAX_PLAN_STEPS) in reason


def test_empty_plan_is_rejected():
    """Порожній план теж не план."""
    assert safety.plan_is_sane([])[0] is False


def test_reasonable_plan_passes():
    """Звичайний план з трьох кроків проходить."""
    assert safety.plan_is_sane(["знайти автора", "перевірити тексти", "замовити"])[0] is True
