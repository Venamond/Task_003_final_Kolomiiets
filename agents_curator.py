"""Куратор — план-і-виконання з ДЗ2, загорнутий у підграф MAS.

Той самий ланцюг planner → executor → act → replanner. Ключова зміна проти
ДЗ2: ризикову дію вузол act НЕ ВИКОНУЄ. Він відкладає її в pending_action і
віддає управління нагору, у вузол підтвердження головного графа.

Так interrupt() лишається в головному графі: просте відновлення через Command,
єдина точка аудиту і неможливість обійти підтвердження зсередини підграфа.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

import config
import guardrails as g
import mcp_tools
import safety
from hitl import make_idem_key
# Імпорт на рівні модуля, а не у функції: LangGraph розрішує анотації
# функцій-гілок через get_type_hints, і локального імпорту вони не бачать.
# Кільця немає — mas_langgraph підключає підграфи ліниво, всередині build_mas.
from mas_langgraph import MASState, user_text
from trajectory_logger import log_entry

CURATOR_TOOLS = ("search_knowledge", "find_texts", "search_catalog",
                 "get_order", "get_budget_status", "place_print_order",
                 "cancel_order", "send_reading_list")


class Plan(BaseModel):
    """План виконання складеної цілі.

    Межа довжини оголошена В СХЕМІ, а не лише в описі поля. Опис — це
    прохання, а min_length/max_length — заборона: модель фізично не поверне
    план на сорок кроків. Перевірка plan_is_sane лишається як друга лінія
    на випадок, якщо структурований вихід колись обійдуть.
    """

    steps: list[str] = Field(
        min_length=1,
        max_length=config.MAX_PLAN_STEPS,
        description=(
            f"Від 1 до {config.MAX_PLAN_STEPS} кроків українською. Кожен крок — "
            f"одна дія, яку можна виконати одним інструментом."
        ),
    )

    @field_validator("steps")
    @classmethod
    def steps_are_meaningful(cls, v: list[str]) -> list[str]:
        """Крок має бути осмисленим рядком, а не порожнім чи однослівним.

        Args:
            v: Перелік кроків плану.

        Returns:
            Той самий перелік, якщо кожен крок осмислений.

        Raises:
            ValueError: Крок коротший за 5 символів.
        """
        for step in v:
            if len(step.strip()) < 5:
                raise ValueError(f"Крок '{step}' занадто короткий, щоб бути дією")
        return v


class ReplanDecision(BaseModel):
    """Рішення після виконання кроку."""

    action: Literal["continue", "finish"] = Field(
        description="continue — виконувати наступний крок; finish — завершити."
    )
    answer: str = Field(
        default="",
        max_length=config.MAX_OUTPUT_LEN,
        description="Підсумкова відповідь українською, якщо action=finish.",
    )


PLANNER_PROMPT = f"""Ти — куратор самоосвіти з історії філософії. Склади план
виконання цілі людини.

Доступні дії:
- search_knowledge: методика, регламент, суть течій;
- find_texts: безкоштовні тексти автора;
- search_catalog: друковані видання і ціни;
- get_budget_status: залишок місячного бюджету;
- place_print_order: замовити видання (потребує підтвердження людини);
- cancel_order: скасувати замовлення (потребує підтвердження);
- send_reading_list: надіслати список поштою (потребує підтвердження).

Правила:
- Не більше {config.MAX_PLAN_STEPS} кроків.
- Перед замовленням обов'язково перевір безкоштовні джерела і залишок бюджету.
- Один крок — одна дія.
- Якщо людина просить ЗАМОВИТИ видання, СКАСУВАТИ замовлення або НАДІСЛАТИ
  список — ця дія обовʼязково має бути ОСТАННІМ кроком плану. План, який
  зупиняється на підготовці й не містить самої дії, не виконує ціль людини."""

EXECUTOR_PROMPT = """Ти виконуєш ОДИН крок плану. Обери рівно один інструмент
і виклич його з потрібними аргументами. Не виконуй кроки наперед.

Ніколи не питай людину про те, що можеш дізнатися сам або що тобі вже дали.
Поточну дату наведено нижче — бери місяць звідти, а не вгадуй і не перепитуй."""

REPLANNER_PROMPT = """Оціни, чи виконано ціль людини. Якщо всі потрібні дані
зібрані — поверни finish і повну відповідь українською. Якщо ні — continue."""


def build_curator(llm: Any, registry: dict) -> Any:
    """Зібрати підграф куратора.

    Args:
        llm: Модель.
        registry: Реєстр інструментів.

    Returns:
        Скомпільований підграф.
    """
    planner = llm.with_structured_output(Plan)
    replanner = llm.with_structured_output(ReplanDecision)
    tools = [registry[name] for name in CURATOR_TOOLS if name in registry]
    # tool_choice="any": робота виконавця — обрати рівно один інструмент. З
    # типовим "auto" модель час від часу відповідала текстом замість виклику
    # («перевірю бюджет, яким місяцем цікавитесь?»), і крок не виконувався —
    # зокрема крок замовлення, тому підтвердження людини не наставало.
    executor_llm = llm.bind_tools(tools, tool_choice="any")

    async def planner_node(state: MASState) -> dict[str, Any]:
        """Скласти план, якщо його ще немає.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану з планом; порожній словник, якщо план уже є.
        """
        if state.get("plan"):
            return {}

        goal = user_text(state)
        today = datetime.now(timezone.utc)
        plan = await planner.ainvoke([
            ("system", PLANNER_PROMPT),
            ("user", f"Сьогодні: {today:%Y-%m-%d}.\n{goal}"),
        ])
        sane, reason = safety.plan_is_sane(plan.steps)
        if not sane:
            return {
                "results": [f"Не вдалося скласти прийнятний план: {reason}."],
                "completed": True,
                "llm_calls": state.get("llm_calls", 0) + 1,
                "trajectory": [log_entry("curator", "planner", goal, reason,
                                         canary=state.get("canary", ""))],
            }

        return {
            "plan": plan.steps,
            "current_step": 0,
            "llm_calls": state.get("llm_calls", 0) + 1,
            "step_count": state.get("step_count", 0) + 1,
            "trajectory": [log_entry(
                "curator", "planner", goal,
                " | ".join(f"{i + 1}. {s}" for i, s in enumerate(plan.steps)),
                canary=state.get("canary", ""))],
        }

    async def executor_node(state: MASState) -> dict[str, Any]:
        """Обрати інструмент для поточного кроку. Нічого не виконує.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану з обраним інструментом.
        """
        index = state.get("current_step", 0)
        plan = state.get("plan", [])
        if index >= len(plan):
            return {}

        step = plan[index]
        context = "\n".join(str(r) for r in state.get("results", [])[-3:])
        # Поточна дата обовʼязкова: get_budget_status вимагає місяць у форматі
        # YYYY-MM, і без неї модель або перепитує людину, або вгадує місяць —
        # тоді людина ухвалює рішення за числами чужого місяця.
        today = datetime.now(timezone.utc)
        reply = await executor_llm.ainvoke([
            ("system", EXECUTOR_PROMPT),
            ("user", f"Сьогодні: {today:%Y-%m-%d}. Поточний місяць: {today:%Y-%m}.\n"
                     f"Ціль: {user_text(state)}\n"
                     f"Зроблено раніше:\n{context or '(нічого)'}\n\n"
                     f"Поточний крок: {step}"),
        ])

        return {
            "messages": [reply],
            "llm_calls": state.get("llm_calls", 0) + 1,
            "step_count": state.get("step_count", 0) + 1,
            "trajectory": [log_entry(
                "curator", "executor", step,
                str(reply.content)[:200] or "обрано інструмент",
                tools=[c["name"] for c in getattr(reply, "tool_calls", [])],
                canary=state.get("canary", ""))],
        }

    async def act_node(state: MASState) -> dict[str, Any]:
        """Виконати обраний інструмент — або відкласти, якщо він ризиковий.

        Ризикову дію цей вузол НЕ ВИКОНУЄ ніколи. Він кладе її в
        pending_action і віддає управління нагору.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану: результат кроку або відкладена ризикова дія.
        """
        index = state.get("current_step", 0)
        plan = state.get("plan", [])

        # План вичерпано — діяти нема за чим. Без перевірки вузол читав останнє
        # повідомлення і знову пропонував ту саму ризикову дію: виходило кільце
        # «підтвердив → знову питають». Знайдено наскрізним тестом графа.
        if index >= len(plan):
            return {"trajectory": [log_entry("curator", "act", "план вичерпано",
                                             "нічого виконувати",
                                             canary=state.get("canary", ""))]}

        # Демонстрація персистентності: навмисний обрив на заданому кроці.
        crash_at = state.get("crash_at")
        if crash_at is not None and index == crash_at:
            raise RuntimeError(
                f"НАВМИСНИЙ ОБРИВ на кроці {index} — демонстрація персистентності")

        last = state["messages"][-1] if state.get("messages") else None
        calls = list(getattr(last, "tool_calls", []) or [])
        if not calls:
            # Модель відповіла текстом замість виклику — найчастіше це відмова
            # виконувати крок. Показуємо її причину, а не «крок пропущено».
            said = str(getattr(last, "content", "") or "").strip()
            note = said[:400] if said else "інструмент не обрано і пояснення немає"
            return {
                "current_step": index + 1,
                "results": [f"[крок {index + 1}] {note}"],
                "trajectory": [log_entry("curator", "act", "крок без виклику",
                                         note[:200],
                                         canary=state.get("canary", ""))],
            }

        call = calls[0]
        if call["name"] in g.RISKY_TOOLS:
            server = "mail" if call["name"] == "send_reading_list" else "library"
            key = make_idem_key(server, call["name"], call["args"],
                                state["thread_id"], index)
            return {
                "pending_approval": True,
                "pending_action": {
                    "server": server,
                    "tool": call["name"],
                    "args": dict(call["args"]),
                    "idem_key": key,
                    "step": index,
                },
                "trajectory": [log_entry(
                    "curator", "act", call["name"],
                    "відкладено на підтвердження людини",
                    tools=[call["name"]], canary=state.get("canary", ""))],
            }

        ok, text = await mcp_tools.call_tool(
            registry, call["name"], call["args"], agent="curator",
            session_id=state["session_id"], thread_id=state["thread_id"])

        return {
            "current_step": index + 1,
            "results": [f"[{call['name']}] {text}"],
            "tool_calls_count": state.get("tool_calls_count", 0) + 1,
            "trajectory": [log_entry("curator", "act", str(call["args"]),
                                     text[:200], tools=[call["name"]],
                                     canary=state.get("canary", ""))],
        }

    async def replanner_node(state: MASState) -> dict[str, Any]:
        """Вирішити, продовжувати план чи завершувати.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану: наступний крок або ознака завершення.
        """
        if state.get("pending_approval"):
            return {}

        stop = safety.check_run_budget(state)
        if stop or state.get("current_step", 0) >= len(state.get("plan", [])):
            gathered = "\n".join(str(r) for r in state.get("results", []))
            decision = await replanner.ainvoke([
                ("system", REPLANNER_PROMPT),
                ("user", f"Ціль: {user_text(state)}\n\nЗібрано:\n{gathered}"),
            ])
            return {
                "results": [decision.answer] if decision.answer else [],
                "completed": True,
                "llm_calls": state.get("llm_calls", 0) + 1,
                "trajectory": [log_entry("curator", "replanner", "підсумок",
                                         decision.answer[:200] or stop or "finish",
                                         canary=state.get("canary", ""))],
            }
        return {"trajectory": [log_entry("curator", "replanner", "продовжуємо",
                                         f"крок {state.get('current_step')}",
                                         canary=state.get("canary", ""))]}

    def after_replanner(state: MASState) -> str:
        """Куди йти після переоцінки плану.

        Args:
            state: Стан графа.

        Returns:
            Ім'я наступного вузла підграфа.
        """
        if state.get("pending_approval") or state.get("completed"):
            return "__end__"
        return "executor"

    graph = StateGraph(MASState)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("act", act_node)
    graph.add_node("replanner", replanner_node)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        lambda s: "__end__" if s.get("completed") else "executor",
        {"executor": "executor", "__end__": END})
    graph.add_edge("executor", "act")
    graph.add_conditional_edges(
        "act",
        lambda s: "__end__" if s.get("pending_approval") else "replanner",
        {"replanner": "replanner", "__end__": END})
    graph.add_conditional_edges("replanner", after_replanner,
                                {"executor": "executor", "__end__": END})
    return graph.compile()
