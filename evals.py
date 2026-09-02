"""Сценарні оцінки MAS.

Це розширення тест-кейсів з ДЗ1: тоді перевірявся один агент, тепер — уся
система. Сценарії покривають усі типи з таблиці завдання: простий,
багатокроковий, з базою знань, з передачею між агентами, з підтвердженням
людини і з аудитом.

Оцінка — не тест. Вона недетермінована і платна, тому живе окремо від pytest
і запускається перед здачею, а не на кожну зміну.

Потрібен ключ API.

Запуск: python evals.py
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Callable

from langgraph.types import Command

import mas_langgraph as mas
import observability as obs


@dataclass
class Scenario:
    """Один сценарій оцінки."""

    scenario_id: str
    kind: str
    query: str
    expected_behavior: str
    check: Callable[[dict], bool]
    resume: dict | None = None


def _agent_is(name: str) -> Callable[[dict], bool]:
    """Перевірка: запит потрапив до заданого агента.

    Args:
        name: Ім'я агента.

    Returns:
        Функція, що перевіряє підсумковий стан.
    """
    return lambda state: state.get("current_agent") == name


def _used_tool(name: str) -> Callable[[dict], bool]:
    """Перевірка: інструмент справді викликано.

    Args:
        name: Ім'я інструмента.

    Returns:
        Функція, що перевіряє підсумковий стан.
    """
    def check(state: dict) -> bool:
        """Чи є інструмент серед викликаних у траєкторії.

        Args:
            state: Підсумковий стан графа.

        Returns:
            True, якщо інструмент згаданий у кроках траєкторії.
        """
        for entry in state.get("trajectory", []):
            if name in (entry.get("tools") or []):
                return True
        return False
    return check


def _all(*checks: Callable[[dict], bool]) -> Callable[[dict], bool]:
    """Перевірка: усі умови виконані.

    Args:
        *checks: Перевірки, які мають виконатися всі.

    Returns:
        Функція, що перевіряє підсумковий стан.
    """
    return lambda state: all(c(state) for c in checks)


SCENARIOS = [
    Scenario(
        "EVAL-01", "simple",
        "Коли жив Сенека і до якої філософської школи він належав?",
        "supervisor → factfinder; виклик find_philosopher",
        _all(_agent_is("factfinder"), _used_tool("find_philosopher")),
    ),
    Scenario(
        "EVAL-02", "multi-step",
        "Хто вплинув на Марка Аврелія і чи могли вони зустрітися?",
        "supervisor → factfinder; get_influences і check_lifespan_overlap",
        _all(_agent_is("factfinder"), _used_tool("get_influences")),
    ),
    Scenario(
        "EVAL-03", "rag-heavy",
        "З чого починати вивчення стоїцизму і яких помилок уникати?",
        "supervisor → researcher; виклик search_knowledge",
        _all(_agent_is("researcher"), _used_tool("search_knowledge")),
    ),
    Scenario(
        "EVAL-04", "cross-agent",
        "Підготуй тижневий блок зі стоїцизму: методика, безкоштовні тексти "
        "і скільки лишилось у бюджеті.",
        "supervisor → curator; план щонайменше з трьох кроків, кілька інструментів",
        lambda s: len(s.get("plan") or []) >= 3,
    ),
    Scenario(
        "EVAL-05", "hitl-flow",
        # Формулювання навмисно викликає другу умову регламенту («потрібен
        # науковий коментар»): інакше куратор знаходить «Роздуми» безкоштовно
        # на Gutenberg і правильно відмовляється купувати — сценарій воював би
        # з тим регламентом, який перевіряє.
        "Замов друковане видання «Роздумів» Марка Аврелія з науковим "
        "коментарем, один примірник. Безкоштовний електронний текст не "
        "підходить: мені потрібен саме коментар.",
        "curator → place_print_order → interrupt() → згода → замовлення створено",
        lambda s: any("place_print_order" in a for a in s.get("executed_actions", [])),
        resume={"decision": "approve"},
    ),
    Scenario(
        "EVAL-06", "audit",
        "Які перевірки безпеки спрацювали на мої запити?",
        "supervisor → auditor; виклик read_audit, без тексту атаки у відповіді",
        _all(_agent_is("auditor"), _used_tool("read_audit")),
    ),
]


async def run_one(app, scenario: Scenario, index: int) -> dict:
    """Прогнати один сценарій і зібрати всі потрібні поля.

    Args:
        app: Скомпільований граф.
        scenario: Сценарій.
        index: Порядковий номер для ідентифікатора потоку.

    Returns:
        Запис результату у форматі, який вимагає завдання.
    """
    thread = f"eval-{index}"
    cfg = {"configurable": {"thread_id": thread}}
    state = mas.initial_state(scenario.query, session_id="evals", thread_id=thread)

    started = time.monotonic()
    with obs.token_counter() as usage:
        result = await app.ainvoke(state, {**cfg, "callbacks": [usage["handler"]]})
        # Сценарій із підтвердженням доводимо до кінця. Пауза на рішення людини
        # у машинний час не входить: міряємо саме два виклики графа.
        if scenario.resume and result.get("__interrupt__"):
            result = await app.ainvoke(
                Command(resume=scenario.resume),
                {**cfg, "callbacks": [usage["handler"]]})
    latency_ms = int((time.monotonic() - started) * 1000)

    stats = obs.RunStats.from_state(result)
    try:
        passed = bool(scenario.check(result))
    except Exception:  # noqa: BLE001 — падіння перевірки це теж провал сценарію
        passed = False

    answer = result["messages"][-1].content if result.get("messages") else ""
    return {
        "scenario_id": scenario.scenario_id,
        "kind": scenario.kind,
        "query": scenario.query,
        "expected_behavior": scenario.expected_behavior,
        "actual": str(answer)[:400],
        "pass": passed,
        "latency_ms": latency_ms,
        "agents_used": stats.agents,
        "tools_called": stats.tools,
        "llm_calls": stats.llm_calls,
        "tokens_in": usage["input_tokens"],
        "tokens_out": usage["output_tokens"],
        "cost_usd": usage["cost_usd"],
    }


async def main() -> None:
    """Прогнати всі сценарії і зберегти результати."""
    results = []
    async with mas.build_mas(db_path="state/agent_state_evals.db") as (app, _):
        for index, scenario in enumerate(SCENARIOS, start=1):
            print(f"  {scenario.scenario_id} ({scenario.kind}): "
                  f"{scenario.query[:58]}...")
            record = await run_one(app, scenario, index)
            mark = "✅" if record["pass"] else "❌"
            print(f"    {mark} агенти: {record['agents_used']}")
            print(f"       інструменти: {record['tools_called']}")
            print(f"       {record['latency_ms']} мс, "
                  f"{record['llm_calls']} викликів моделі, "
                  f"${record['cost_usd']:.4f}")
            results.append(record)

    passed = sum(1 for r in results if r["pass"])
    payload = {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 2) if results else 0.0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "results": results,
    }
    with open("eval_results.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n  Пройдено {passed} з {len(results)} "
          f"(частка успіху {payload['pass_rate']:.0%}), "
          f"загальна вартість ${payload['total_cost_usd']:.4f}")
    print("  Результати: eval_results.json")


if __name__ == "__main__":
    asyncio.run(main())
