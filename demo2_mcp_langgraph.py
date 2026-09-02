"""Демонстрація 2: інструменти MCP, підключені до LangGraph.

Це окрема вимога завдання: показати, що агент LangGraph користується
інструментами служби MCP через стандартизований протокол, щонайменше на
двох запитах.

Потрібен ключ API.

Запуск: python demo2_mcp_langgraph.py
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage, ToolMessage

import mcp_tools
from provider import make_llm, text_of

QUERIES = [
    "Скільки коштує друковане видання «Роздумів» Марка Аврелія і чи є воно в каталозі?",
    "Скільки я вже витратив з місячного бюджету на самоосвіту у 2026-09?",
]

MCP_NAMES = ["search_catalog", "get_order", "get_budget_status"]


async def answer(llm, registry, query: str) -> None:
    """Відповісти на один запит, користуючись інструментами MCP.

    Args:
        llm: Модель.
        registry: Реєстр інструментів.
        query: Запит людини.
    """
    print("═" * 78)
    print(f"  ЗАПИТ: {query}")
    print("═" * 78)

    bound = llm.bind_tools([registry[n] for n in MCP_NAMES])
    messages = [HumanMessage(content=query)]

    reply = await bound.ainvoke(messages)
    messages.append(reply)

    for call in getattr(reply, "tool_calls", []):
        print(f"\n  → викликає MCP-інструмент: {call['name']}({call['args']})")
        ok, result = await mcp_tools.call_tool(
            registry, call["name"], call["args"],
            agent="curator", session_id="demo2")
        print(f"    служба відповіла: {result[:120]}")
        messages.append(ToolMessage(content=result, tool_call_id=call["id"]))

    final = await bound.ainvoke(messages)
    print(f"\n  ВІДПОВІДЬ: {text_of(final)}\n")


async def main() -> None:
    """Прогнати обидва запити."""
    registry = await mcp_tools.load_all_tools()
    print(f"\nПідключено служби MCP, інструментів у реєстрі: {len(registry)}")
    print("Від служб MCP: search_catalog, get_order, get_budget_status, "
          "place_print_order, cancel_order, send_reading_list\n")

    llm = make_llm()
    for query in QUERIES:
        await answer(llm, registry, query)


if __name__ == "__main__":
    asyncio.run(main())
