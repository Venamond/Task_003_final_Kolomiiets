"""Демонстрація 6: підтвердження людиною — згода, відмова, правка.

Три сценарії на замовленні книжки плюс дві спроби, які мають не пройти:
правка ціни й надсилання на недозволений домен.

Потрібен ключ API.

Запуск: python demo6_hitl.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib

from langgraph.types import Command

import config
import mas_langgraph as mas
from hitl import make_idem_key


def reset_state() -> None:
    """Повернути бюджет і журнали до чистого перед КОЖНИМ сценарієм.

    Без цього пʼять замовлень підряд вичерпують місячний ліміт, служба
    починає відмовляти — правильно, але це заважає показати саме
    підтвердження людиною. Кожен сценарій має бути незалежним.
    """
    state_dir = pathlib.Path(config.STATE_DIR)
    for name in ("orders.json", "order_idem.json", "mail_log.json",
                 "mail_idem.json", "session_limits.json"):
        (state_dir / name).unlink(missing_ok=True)
    (state_dir / "budget.json").write_text(
        json.dumps({"2026-09": {"spent": 12.50}}, ensure_ascii=False, indent=2),
        encoding="utf-8")

# Формулювання викликає другу умову регламенту закупівлі («потрібен науковий
# коментар»), інакше куратор правильно відмовляється купувати те, що є
# безкоштовно.
#
# Кількість різна навмисно: видання коштує 34 EUR, ліміт 60, витрачено 12.50 —
# один примірник у ліміт вкладається, два ні. Так видно і успішне підтвердження,
# і те, що бюджетний рубіж тримає навіть після згоди людини.
ORDER_ONE = ("Замов друковане видання «Роздумів» Марка Аврелія з науковим "
             "коментарем, один примірник. Безкоштовний текст не підходить.")
ORDER_TWO = ("Замов друковане видання «Роздумів» Марка Аврелія з науковим "
             "коментарем, два примірники. Безкоштовний текст не підходить.")
# Адреса ДОЗВОЛЕНОГО домену: тут показуємо, що підтвердження надсилання
# працює наскрізь. Відмову на чужому домені перевіряє атака RT-06 і
# модульний тест test_send_to_foreign_domain_is_refused.
MAIL_QUERY = ("Надішли список літератури зі стоїцизму на пошту "
              "reader@example.com")
DB = "state/agent_state_demo6.db"


def show_payload(payload: dict) -> None:
    """Надрукувати те, що бачить людина.

    Args:
        payload: Предʼявлення з вузла підтвердження.
    """
    print("\n  ┌─ СИСТЕМА ПИТАЄ ЛЮДИНУ " + "─" * 45)
    print(f"  │ дія:             {payload['action']} (служба {payload['server']})")
    print(f"  │ аргументи:       {json.dumps(payload['args'], ensure_ascii=False)}")
    if payload["total_cost"]:
        print(f"  │ сума:            {payload['total_cost']:.2f} EUR")
        if payload["budget_known"]:
            print(f"  │ вже витрачено:   {payload['budget_spent']:.2f} EUR")
            print(f"  │ місячний ліміт:  {payload['budget_limit']:.2f} EUR")
            print(f"  │ перевищення:     "
                  f"{'ТАК' if payload['exceeds_limit'] else 'ні'}")
        else:
            # Служба бюджету не відповіла. Показуємо це прямо: вигаданий нуль
            # схилив би людину схвалити покупку понад ліміт.
            print("  │ залишок бюджету: НЕВІДОМИЙ — служба не відповіла")
    print(f"  │ можна правити:   {payload['editable'] or '(нічого)'}")
    print(f"  │ чому ризиково:   {payload['why_risky']}")
    print("  └" + "─" * 68)


# Аргументи ризикової дії для прямого показу. Беруться з каталогу, а не
# вигадуються: CAT-009, «Роздуми» з науковим коментарем, 34 EUR.
DIRECT_ORDER = {"title": "Роздуми (з науковим коментарем)",
                "author": "Марк Аврелій", "price_per_copy": 34.0}


def direct_action(tool: str, args: dict, thread: str) -> dict:
    """Скласти відкладену дію напряму, без участі планувальника.

    Предмет демонстрації — механізм підтвердження, а не везіння планувальника:
    той є моделлю і від прогону до прогону то доходить до ризикової дії, то ні.
    Тому спершу пробуємо чесний наскрізний прохід, а якщо планувальник до дії
    не дійшов — показуємо той самий вузол напряму і кажемо про це прямо.

    Args:
        tool: Ім'я ризикової дії.
        args: Аргументи виклику.
        thread: Ідентифікатор потоку — входить у відбиток.

    Returns:
        Значення для pending_action.
    """
    server = "mail" if tool == "send_reading_list" else "library"
    return {"server": server, "tool": tool, "args": args,
            "idem_key": make_idem_key(server, tool, args, thread, 0), "step": 0}


async def scenario(app, name: str, query: str, thread: str,
                   resume: dict, direct: tuple[str, dict] | None = None) -> None:
    """Прогнати один сценарій підтвердження.

    Args:
        app: Скомпільований граф.
        name: Назва сценарію для виводу.
        query: Запит людини.
        thread: Ідентифікатор потоку.
        resume: Відповідь людини для Command(resume=...).
    """
    reset_state()
    print("\n" + "═" * 78)
    print(f"  СЦЕНАРІЙ: {name}")
    print("═" * 78)
    print(f"  запит: {query}")

    # Зрідка модель складає план, який не доходить до дії, і переривання не
    # настає. Це мінливість моделі, а не помилка коду — пробуємо в іншому потоці.
    for attempt in (1, 2):
        cfg = {"configurable": {"thread_id": f"{thread}-{attempt}"}}
        state = mas.initial_state(query, session_id="demo6",
                                  thread_id=f"{thread}-{attempt}")
        result = await app.ainvoke(state, cfg)
        plan = result.get("plan") or []
        if plan:
            print(f"\n  план куратора ({len(plan)} кроків):")
            for i, step in enumerate(plan, start=1):
                print(f"    {i}. {step}")
        interrupts = result.get("__interrupt__")
        if interrupts:
            break
        print(f"\n  спроба {attempt}: переривання не настало — план не дійшов "
              f"до ризикової дії")

    if not interrupts and direct:
        tool, args = direct
        print("\n  ⓘ  планувальник до ризикової дії не дійшов — це мінливість")
        print("     моделі, а не помилка коду. Показуємо той самий вузол")
        print("     підтвердження напряму, з тими самими аргументами.")
        cfg = {"configurable": {"thread_id": f"{thread}-direct"}}
        state = mas.initial_state(query, session_id="demo6",
                                  thread_id=f"{thread}-direct")
        state["pending_approval"] = True
        state["pending_action"] = direct_action(tool, args, f"{thread}-direct")
        result = await app.ainvoke(state, cfg)
        interrupts = result.get("__interrupt__")

    if not interrupts:
        print("  ⚠️  підтвердження не настало")
        print(f"  відповідь: {result['messages'][-1].content[:200]}")
        return

    show_payload(interrupts[0].value)
    print(f"\n  ЛЮДИНА ВІДПОВІДАЄ: {json.dumps(resume, ensure_ascii=False)}")

    final = await app.ainvoke(Command(resume=resume), cfg)
    print(f"\n  виконані ризикові дії: {final.get('executed_actions')}")
    print(f"  ВІДПОВІДЬ:\n  {final['messages'][-1].content[:400]}")


async def main() -> None:
    """Прогнати всі сценарії."""
    async with mas.build_mas(db_path=DB) as (app, _):
        one = ("place_print_order", {**DIRECT_ORDER, "copies": 1})
        two = ("place_print_order", {**DIRECT_ORDER, "copies": 2})
        mail = ("send_reading_list",
                {"email": "reader@example.com",
                 "items": ["Роздуми — Марк Аврелій", "Листи до Луцілія — Сенека"]})

        await scenario(app, "ЗГОДА — замовлення в межах бюджету", ORDER_ONE,
                       "demo6-approve", {"decision": "approve"}, direct=one)
        await scenario(app, "ВІДМОВА", ORDER_ONE, "demo6-reject",
                       {"decision": "reject",
                        "reason": "знайшов безкоштовний текст"}, direct=one)
        await scenario(app, "ПРАВКА — один примірник замість двох", ORDER_TWO,
                       "demo6-edit", {"decision": "edit", "args": {"copies": 1}},
                       direct=two)
        await scenario(app, "ПРАВКА ЦІНИ — має бути відхилена", ORDER_TWO,
                       "demo6-price",
                       {"decision": "edit", "args": {"price_per_copy": 0.01}},
                       direct=two)
        await scenario(app, "НАДСИЛАННЯ СПИСКУ НА ПОШТУ", MAIL_QUERY,
                       "demo6-mail", {"decision": "approve"}, direct=mail)

    print("\n" + "═" * 78)
    print("  Згода на один примірник (34 EUR) вкладається в ліміт — замовлення")
    print("  створено. Два примірники (68 EUR) перевищують залишок 47.50, і")
    print("  служба відмовляє НАВІТЬ ПІСЛЯ ЗГОДИ людини: підтвердження це")
    print("  друга лінія, а не заміна перевірці. Правка кількості рятує")
    print("  замовлення, правка ціни — ні: ціну задає каталог.")
    print("  Для надсилання перелік дозволених правок порожній: адресу")
    print("  отримувача змінити не можна взагалі — підміна адреси це і є атака.")
    print("  Відмову на недозволеному домені перевіряє атака RT-06 і")
    print("  модульний тест test_send_to_foreign_domain_is_refused.")
    print("═" * 78 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
