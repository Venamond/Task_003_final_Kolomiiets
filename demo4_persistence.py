"""Демонстрація 4: запуск → обрив → відновлення.

Показує вимогу завдання: робота обривається посеред плану, а повторний запуск
з тим самим thread_id продовжує з того самого місця, а не починає заново.

Обрив справжній: вузол act підіймає виняток на заданому кроці (crash_at).

Потрібен ключ API.

Запуск: python demo4_persistence.py
"""
from __future__ import annotations

import asyncio

import mas_langgraph as mas

# Запит навмисно з трьох дій: супервізор віддасть його кураторові, бо саме
# куратор будує план. Одноактне питання пішло б до дослідника, плану не виникло
# б — і обривати не було б чого. Обрив спрацьовує на другому кроці, до
# ризикової дії.
QUERY = "Замов друковане видання «Роздумів» Марка Аврелія, один примірник."
THREAD = "demo4-persistence"
DB = "state/agent_state_demo4.db"


def _show(title: str, state) -> None:
    """Надрукувати зріз стану.

    Args:
        title: Заголовок.
        state: Знімок стану графа або сам стан.
    """
    # У звичайного dict є МЕТОД .values, тому hasattr тут не годиться:
    # розрізняємо знімок стану і сам стан за типом.
    values = state if isinstance(state, dict) else state.values
    print(f"\n  {title}")
    print(f"    план: {values.get('plan') or '(немає)'}")
    print(f"    поточний крок: {values.get('current_step')}")
    print(f"    зібрано результатів: {len(values.get('results', []))}")
    print(f"    виконані ризикові дії: {values.get('executed_actions')}")
    print(f"    викликів моделі: {values.get('llm_calls')}")


async def main() -> None:
    """Прогнати обрив і відновлення."""
    cfg = {"configurable": {"thread_id": THREAD}}

    print("═" * 78)
    print("  ЕТАП 1 — запуск із навмисним обривом на другому кроці плану")
    print("═" * 78)

    async with mas.build_mas(db_path=DB) as (app, _):
        state = mas.initial_state(QUERY, session_id="demo4", thread_id=THREAD,
                                  crash_at=1)
        try:
            await app.ainvoke(state, cfg)
            print("\n  ⚠️  обриву не сталося — план виявився коротшим за очікуваний")
        except RuntimeError as exc:
            print(f"\n  💥 {exc}")
        snapshot = await app.aget_state(cfg)
        _show("СТАН НА ДИСКУ ПІСЛЯ ОБРИВУ:", snapshot)

    print("\n" + "═" * 78)
    print("  ЕТАП 2 — новий процес графа, той самий thread_id")
    print("═" * 78)
    print(f"  (між етапами немає нічого спільного, крім файлу {DB})")

    async with mas.build_mas(db_path=DB) as (app, _):
        snapshot = await app.aget_state(cfg)
        _show("ВІДНОВЛЕНО З ДИСКУ:", snapshot)
        calls_before = snapshot.values.get("llm_calls", 0)

        # Продовжуємо з того самого місця: crash_at уже пройдено.
        await app.aupdate_state(cfg, {"crash_at": None})
        result = await app.ainvoke(None, cfg)
        _show("ПІСЛЯ ЗАВЕРШЕННЯ:", result)
        print(f"\n  ВІДПОВІДЬ:\n  {result['messages'][-1].content[:500]}\n")

    print("═" * 78)
    print("  ЩО САМЕ ПЕРЕЖИЛО ОБРИВ")
    print("═" * 78)
    print(f"""
  Лічильник викликів моделі продовжився з {calls_before}, а не з нуля.
  Це і є доказ: другий процес НЕ переграв крок супервізора, який уже був
  виконаний до обриву. Він продовжив з того місця, де зупинився перший.

  Але план на диску після обриву був порожній — і це не помилка, а
  справжня межа зернистості. Підграф куратора — це ОДИН суперкрок
  батьківського графа, і стан фіксується на межі суперкроку. Виняток
  усередині підграфа відкочує все, що підграф устиг зробити за цей захід:
  план ще не був зафіксований, тому його й не видно.

  Практичний висновок для підтвердження людиною: вузол approval_gate
  живе в ГОЛОВНОМУ графі, а не в підграфі, саме тому його переривання
  фіксується на диску надійно. Якби він був усередині куратора, стан
  очікування підтвердження ділив би долю решти роботи підграфа.""")
    print("═" * 78)


if __name__ == "__main__":
    asyncio.run(main())
