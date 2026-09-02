"""Демонстрація 3: MAS на запитах різного типу.

Вимога завдання — щонайменше три запити в різні ролі. Показуємо чотири:
фактолог, дослідник, куратор і аудитор. Куратор обовʼязковий: саме він працює
з грошима і будує план, без нього лишилися б самі читаючі ролі.

Потрібен ключ API.

Запуск: python demo3_mas.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import config
import mas_langgraph as mas
from trajectory_logger import TrajectoryLogger

DEMOS = [
    ("factfinder", "Коли жив Сенека і до якої філософської школи він належав?"),
    ("researcher", "З чого починати вивчення стоїцизму і яких помилок уникати?"),
    ("curator", "Підбери мені два безкоштовні тексти зі стоїцизму і перевір, "
                "скільки лишилось у місячному бюджеті."),
    ("auditor", "Які перевірки безпеки спрацювали на мої запити?"),
]


# Потік унікальний для кожного прогону. З постійним «demo3-1» чекпоінтер
# знаходив стан попереднього запуску і ПРОДОВЖУВАВ його: траєкторія в
# trajectory.json накопичувалася з різних прогонів, і в ній опинялися кроки
# запитів, яких у цьому прогоні вже немає. Демонстрація персистентності —
# завдання demo4, і саме там потік навмисно сталий.
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


async def run_one(app, expected: str, query: str, index: int) -> dict:
    """Прогнати один запит і надрукувати підсумок.

    Args:
        app: Скомпільований граф.
        expected: Очікувана роль — для порівняння з фактичною.
        query: Запит людини.
        index: Порядковий номер для ідентифікатора потоку.

    Returns:
        Підсумковий стан.
    """
    thread = f"demo3-{RUN_ID}-{index}"
    run_config = {"configurable": {"thread_id": thread}}
    state = mas.initial_state(query, session_id="demo3", thread_id=thread)

    print("═" * 78)
    print(f"  ЗАПИТ {index}: {query}")
    print("═" * 78)

    result = await app.ainvoke(state, run_config)

    actual = result.get("current_agent", "—")
    mark = "✅" if actual == expected else "⚠️ "
    print(f"\n  {mark} агент: {actual} (очікувався {expected})")
    print(f"  кроків траєкторії: {len(result.get('trajectory', []))}")
    print(f"  викликів моделі: {result.get('llm_calls', 0)}, "
          f"інструментів: {result.get('tool_calls_count', 0)}")
    agents = sorted({s["agent_name"] for s in result.get("trajectory", [])})
    print(f"  задіяні агенти: {', '.join(agents)}")
    answer = result["messages"][-1].content
    print(f"\n  ВІДПОВІДЬ:\n  {answer[:600]}\n")
    return result


async def main() -> None:
    """Прогнати всі демонстраційні запити і зберегти траєкторію."""
    logger = TrajectoryLogger()
    async with mas.build_mas() as (app, _registry):
        for index, (expected, query) in enumerate(DEMOS, start=1):
            result = await run_one(app, expected, query, index)
            logger.set_canary(result.get("canary", ""))
            for step_num, entry in enumerate(result.get("trajectory", []), start=1):
                logger.log_step(step_num, entry["agent_name"], entry["node"],
                                entry["action"], entry["output"],
                                entry.get("tools", []), entry.get("uid", ""))

    payload = logger.save(str(config.TRAJECTORY_PATH), "completed")
    print("═" * 78)
    print(f"  Траєкторію збережено: {config.TRAJECTORY_PATH}, "
          f"кроків {payload['total_steps']}")
    names = {s["agent_name"] for s in payload["trajectory"]}
    print(f"  Агентів у траєкторії: {', '.join(sorted(names))}")
    print(f"  Поле agent_name присутнє в кожному кроці: "
          f"{all('agent_name' in s for s in payload['trajectory'])}")
    print("═" * 78)


if __name__ == "__main__":
    asyncio.run(main())
