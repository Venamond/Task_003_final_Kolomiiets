"""Захисні механізми агента: ліміт кроків, таймаут, детекція зациклення.

Перенесено з ДЗ1 (hw1_react_agent/safety.py). Умовчання тепер беруться з
конфігурації, а не з локальних констант; додано перевірку бюджету всього
прогону і перевірку форми плану.

Ліміти можна передати параметрами: так в одному прогоні тестів уживаються
бойові кейси і демонстраційні з навмисно малими межами.
"""
from __future__ import annotations

import hashlib
import time

import config


class StopReason:
    """Причини завершення роботи."""

    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    LOOP_DETECTED = "loop_detected"
    LLM_BUDGET = "llm_budget"
    TOOL_BUDGET = "tool_budget"
    MAX_HOPS = "max_hops"
    RUN_TIMEOUT = "run_timeout"


class LoopDetector:
    """Детекція зациклення — однакових викликів інструмента ПОСПІЛЬ.

    Захищає від ситуації, коли модель наполегливо повторює той самий виклик,
    очікуючи іншого результату.
    """

    def __init__(self, max_repeats: int | None = None) -> None:
        """Створити детектор.

        Args:
            max_repeats: Скільки однакових викликів поспіль вважати
                зацикленням; за умовчанням — з конфігурації.
        """
        self.max_repeats = max_repeats or config.LOOP_DETECTOR_MAX_REPEATS
        self.recent_calls: list[str] = []

    def _hash(self, tool_name: str, args: dict) -> str:
        """Відбиток виклику. Порядок ключів не має значення.

        Args:
            tool_name: Ім'я інструмента.
            args: Аргументи виклику.

        Returns:
            Відбиток виклику.
        """
        raw = f"{tool_name}:{sorted(args.items())}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def check(self, tool_name: str, args: dict) -> bool:
        """Зареєструвати виклик і повернути True, якщо виявлено зациклення.

        Args:
            tool_name: Ім'я інструмента.
            args: Аргументи виклику.

        Returns:
            True, якщо останні max_repeats викликів однакові.
        """
        self.recent_calls.append(self._hash(tool_name, args))
        if len(self.recent_calls) < self.max_repeats:
            return False
        last_n = self.recent_calls[-self.max_repeats:]
        return len(set(last_n)) == 1

    def reset(self) -> None:
        """Очистити історію — перед новим запитом."""
        self.recent_calls.clear()


def check_limits(step_count: int, started_at: float,
                 max_steps: int | None = None,
                 timeout_seconds: float | None = None) -> str | None:
    """Перевірити ліміти кроків і часу одного агента.

    Кроки перевіряємо першими: це конкретніша причина, ніж час.

    Args:
        step_count: Скільки кроків уже зроблено.
        started_at: Момент старту за time.monotonic().
        max_steps: Межа кроків; за умовчанням — з конфігурації.
        timeout_seconds: Межа часу; за умовчанням — з конфігурації.

    Returns:
        Причина зупинки або None.
    """
    limit_steps = max_steps if max_steps is not None else config.MAX_STEPS_PER_AGENT
    limit_time = (timeout_seconds if timeout_seconds is not None
                  else config.AGENT_TIMEOUT_SEC)
    if step_count >= limit_steps:
        return StopReason.MAX_STEPS
    if time.monotonic() - started_at >= limit_time:
        return StopReason.TIMEOUT
    return None


def check_run_budget(state: dict) -> str | None:
    """Перевірити бюджет усього прогону, а не окремого агента.

    Args:
        state: Стан графа з полями llm_calls, tool_calls_count, hops,
            started_at.

    Returns:
        Причина зупинки або None.
    """
    if state.get("llm_calls", 0) >= config.MAX_LLM_CALLS_PER_RUN:
        return StopReason.LLM_BUDGET
    if state.get("tool_calls_count", 0) >= config.MAX_TOOL_CALLS_PER_RUN:
        return StopReason.TOOL_BUDGET
    if state.get("hops", 0) >= config.MAX_HOPS:
        return StopReason.MAX_HOPS
    started = state.get("started_at")
    if started and time.monotonic() - started >= config.RUN_TIMEOUT_SEC:
        return StopReason.RUN_TIMEOUT
    return None


def plan_is_sane(plan: list[str]) -> tuple[bool, str]:
    """Перевірити форму плану від планувальника.

    План на десятки кроків — це не план, а ознака того, що модель пішла
    врозніс. Порожній план теж не план.

    Args:
        plan: Перелік кроків.

    Returns:
        Пара (чи прийнятний, пояснення).
    """
    if not plan:
        return False, "План порожній"
    if len(plan) > config.MAX_PLAN_STEPS:
        return False, (f"План з {len(plan)} кроків перевищує межу "
                       f"{config.MAX_PLAN_STEPS}")
    return True, ""
