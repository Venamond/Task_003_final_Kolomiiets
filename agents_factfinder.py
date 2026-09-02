"""Фактолог — ReAct-цикл з ДЗ1, загорнутий у підграф MAS.

Цикл і межі ті самі (кроки, таймаут, детектор зациклення); нове — асинхронність,
виклики через рубежі та спільна траєкторія з agent_name. Ключі стану збігаються
з головним графом, тому підграф стає вузлом напряму.
"""
from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

import mcp_tools
import safety
from trajectory_logger import log_entry

FACTFINDER_TOOLS = ("find_philosopher", "get_influences", "find_texts",
                    "check_lifespan_overlap")

class FactAnswer(BaseModel):
    """Підсумок роботи фактолога.

    out_of_scope дає право повернути питання нагору: без нього агент брався
    за методичні питання, не маючи ні даних, ні інструментів, і вигадував.
    """

    answer: str = Field(
        min_length=1,
        description="Відповідь українською за знайденими фактами.")
    out_of_scope: bool = Field(
        default=False,
        description=("true, якщо питання про методику самоосвіти чи регламент "
                     "— це не факти про людей, ними займається дослідник."))


FACTFINDER_PROMPT = """Ти — фактолог системи самоосвіти з історії філософії.

Відповідаєш на питання про конкретних людей: роки життя, філософська школа,
хто на кого вплинув, які твори доступні безкоштовно, чи могли двоє зустрітися.

Правила:
- Не вигадуй роки життя, школу і граф впливів. Якщо не знайшов через
  інструмент — так і скажи.
- Інструменти ланцюжкові, і одного виклику зазвичай НЕ ДОСИТЬ:
  * «хто вплинув на X» → спершу find_philosopher(X), щоб дістати його qid,
    потім get_influences(qid). Без другого виклику відповіді немає;
  * «чи могли X і Y зустрітися» → find_philosopher для обох, потім
    check_lifespan_overlap з їхніми роками;
  * «хто вплинув на X і чи могли вони зустрітися» → це ТРИ кроки:
    find_philosopher, get_influences, далі роки і перетин.
- Про фізичну можливість зустрічі кажи саме як про можливість, а не як про факт.
- Методичні питання («з чого починати вивчення») — не твої, поверни їх назад.

Коли даних достатньо, дай відповідь українською без виклику інструментів."""


def without_dangling_calls(messages: list) -> list:
    """Прибрати з історії виклики інструментів, на які немає відповіді.

    Anthropic відхиляє історію, де `tool_use` не має `tool_result`, і падає
    ВЕСЬ наступний виклик моделі. Незакритий виклик може потрапити сюди не
    лише з цього циклу: стан спільний, і `completed` здатна виставити інша
    роль просто після того, як модель попросила інструмент.

    Args:
        messages: Історія повідомлень зі стану.

    Returns:
        Історія, у якій лишилися тільки закриті виклики.
    """
    answered = {getattr(m, "tool_call_id", None) for m in messages}
    kept = []
    for message in messages:
        calls = getattr(message, "tool_calls", None) or []
        if calls and not all(call["id"] in answered for call in calls):
            continue
        kept.append(message)
    return kept


def unanswered_calls(state: dict, reason: str) -> list[ToolMessage]:
    """Закрити виклики інструментів, на які цикл уже не відповість.

    Зупинка за межею лишає в історії виклик без результату. Anthropic таку
    історію відхиляє з 400 (`tool_use` без `tool_result`), і падає вже
    НАСТУПНИЙ виклик моделі — на цьому самому запиті або після передачі
    іншій ролі. Тому кожен незакритий виклик отримує відповідь із причиною
    зупинки.

    Args:
        state: Стан графа.
        reason: Причина зупинки, яку побачить модель.

    Returns:
        Відповіді на незакриті виклики; порожній перелік, якщо їх немає.
    """
    messages = state.get("messages") or []
    if not messages:
        return []
    calls = getattr(messages[-1], "tool_calls", None) or []
    return [ToolMessage(content=f"Не виконано: {reason}.",
                        tool_call_id=call["id"]) for call in calls]


def build_factfinder(llm: Any, registry: dict) -> Any:
    """Зібрати підграф фактолога.

    Args:
        llm: Модель.
        registry: Реєстр інструментів.

    Returns:
        Скомпільований підграф, готовий стати вузлом головного графа.
    """
    tools = [registry[name] for name in FACTFINDER_TOOLS if name in registry]
    bound = llm.bind_tools(tools)

    async def agent_node(state: dict) -> dict[str, Any]:
        """Крок міркування: модель обирає інструмент або дає відповідь.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану: відповідь моделі, лічильники, крок траєкторії.
        """
        started = state.get("started_at", time.monotonic())
        stop = safety.check_limits(state.get("step_count", 0), started)
        if stop:
            return {
                "messages": unanswered_calls(state, stop),
                "results": [f"Фактолог зупинився: {stop}."],
                "completed": True,
                "trajectory": [log_entry("factfinder", "limit", "перевірка меж",
                                         stop, canary=state.get("canary", ""))],
            }

        messages = [("system", FACTFINDER_PROMPT),
                    *without_dangling_calls(state["messages"])]
        reply = await bound.ainvoke(messages)
        calls = [c["name"] for c in getattr(reply, "tool_calls", [])]

        return {
            "messages": [reply],
            "llm_calls": state.get("llm_calls", 0) + 1,
            "step_count": state.get("step_count", 0) + 1,
            "trajectory": [log_entry(
                "factfinder", "reason",
                str(state["messages"][-1].content)[:200],
                str(reply.content)[:200] or f"викликає {calls}",
                tools=calls, canary=state.get("canary", ""))],
        }

    async def tools_node(state: dict) -> dict[str, Any]:
        """Виконати обрані моделлю інструменти через рубежі.

        Args:
            state: Стан графа; виклики беруться з останнього повідомлення.

        Returns:
            Оновлення стану: результати інструментів і крок траєкторії.
        """
        last = state["messages"][-1]
        detector = safety.LoopDetector()
        messages: list = []
        names: list[str] = []

        for call in getattr(last, "tool_calls", []):
            names.append(call["name"])
            if detector.check(call["name"], call["args"]):
                text = (f"Зупинено: інструмент '{call['name']}' викликано "
                        f"з тими самими аргументами поспіль.")
            else:
                ok, text = await mcp_tools.call_tool(
                    registry, call["name"], call["args"],
                    agent="factfinder", session_id=state["session_id"],
                    thread_id=state.get("thread_id", ""))
            messages.append(ToolMessage(content=text, tool_call_id=call["id"]))

        return {
            "messages": messages,
            "tool_calls_count": state.get("tool_calls_count", 0) + len(names),
            "trajectory": [log_entry("factfinder", "act", str(names),
                                     "виконано", tools=names,
                                     canary=state.get("canary", ""))],
        }

    async def finish_node(state: dict) -> dict[str, Any]:
        """Скласти підсумок або повернути питання супервізору.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану: відповідь і completed, або needs_handoff.
        """
        draft = str(state["messages"][-1].content)
        summary = await llm.with_structured_output(FactAnswer).ainvoke([
            ("system", FACTFINDER_PROMPT),
            ("user", f"Питання людини і зібрані факти:\n{draft}\n\n"
                     f"Склади підсумок. Якщо питання про методику самоосвіти "
                     f"чи регламент, а не про факти — постав out_of_scope."),
        ])

        if summary.out_of_scope:
            reason = ("фактолог: питання про методику чи регламент, "
                      "а не про факти")
            return {
                "needs_handoff": True,
                "llm_calls": state.get("llm_calls", 0) + 1,
                "handoff_reason": reason,
                "results": [f"[передача] {reason}"],
                "trajectory": [log_entry("factfinder", "handoff", "підсумок",
                                         reason,
                                         canary=state.get("canary", ""))],
            }

        return {
            "results": [summary.answer],
            "completed": True,
            "llm_calls": state.get("llm_calls", 0) + 1,
            "trajectory": [log_entry("factfinder", "answer", "підсумок",
                                     summary.answer,
                                     canary=state.get("canary", ""))],
        }

    def should_continue(state: dict) -> str:
        """Обрати наступний вузол циклу.

        Args:
            state: Стан графа.

        Returns:
            'tools', якщо модель викликала інструмент, інакше 'finish'.
        """
        if state.get("completed"):
            return "__end__"
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "finish"

    # MASState, а не dict: зі словником губляться редьюсери, і записи підграфа
    # в results та trajectory затирають одне одного. Імпорт тут — проти кільця.
    from mas_langgraph import MASState

    graph = StateGraph(MASState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue,
                                {"tools": "tools", "finish": "finish",
                                 "__end__": END})
    graph.add_edge("tools", "agent")
    graph.add_edge("finish", END)
    return graph.compile()
