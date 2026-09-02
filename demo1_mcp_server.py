"""Демонстрація 1: MCP зсередини. Ключ API не потрібен, токени не витрачаються.

Показує те, що бачить агент при підключенні: перелік дій з описами і схемами,
довідники, заготовки — і результат звірки зі зліпком.

Запуск: python demo1_mcp_server.py
"""
from __future__ import annotations

import asyncio
import json

import mcp_mail_server
import mcp_manifest
import mcp_server


def _line(char: str = "─", width: int = 78) -> None:
    """Надрукувати роздільник.

    Args:
        char: Символ роздільника.
        width: Довжина рядка.
    """
    print(char * width)


async def show_server(title: str, server) -> None:
    """Надрукувати вміст однієї служби.

    Args:
        title: Заголовок для виводу.
        server: Об'єкт FastMCP.
    """
    _line("═")
    print(f"  {title}")
    _line("═")

    tools = await server.list_tools()
    print(f"\nДІЇ ({len(tools)}):\n")
    for tool in tools:
        first_line = (tool.description or "").strip().splitlines()[0]
        params = ", ".join(tool.inputSchema.get("properties", {}))
        print(f"  • {tool.name}({params})")
        print(f"    {first_line}")
        required = tool.inputSchema.get("required", [])
        if required:
            print(f"    обов'язкові: {', '.join(required)}")
        print()

    resources = await server.list_resources()
    print(f"ДОВІДНИКИ ({len(resources)}):\n")
    for res in resources:
        print(f"  • {res.uri}")
        print(f"    {(res.description or '').strip()}")
    print()

    prompts = await server.list_prompts()
    print(f"ЗАГОТОВКИ ({len(prompts)}):\n")
    for prompt in prompts:
        args = ", ".join(a.name for a in (prompt.arguments or []))
        # Беремо лише перший рядок опису: далі йдуть Args і Returns,
        # потрібні моделі, але зайві у вигляді для людини.
        summary = (prompt.description or "").strip().splitlines()[0] if prompt.description else ""
        print(f"  • {prompt.name}({args})")
        print(f"    {summary}")
    print()


async def main() -> None:
    """Показати обидві служби і звірити зліпок."""
    await show_server("СЛУЖБА-БІБЛІОТЕКА (mcp_server.py)", mcp_server.mcp)
    await show_server("ПОШТОВА СЛУЖБА (mcp_mail_server.py)", mcp_mail_server.mcp)

    _line("═")
    print("  ЗВІРКА ЗЛІПКА")
    _line("═")
    ok, problems = await mcp_manifest.verify_manifest()
    if ok:
        print("\n  ✓ Схеми служб збігаються зі зліпком mcp_manifest.json\n")
    else:
        print("\n  ✗ Розходження зі зліпком:")
        for problem in problems:
            print(f"    - {problem}")
        print()

    _line("═")
    print("  ПРИКЛАД ВИКЛИКУ ЧЕРЕЗ ПРОТОКОЛ")
    _line("═")
    result = await mcp_server.mcp.call_tool(
        "search_catalog", {"query": "Марк Аврелій", "lang": "uk", "limit": 2})
    blocks = result[0] if isinstance(result, tuple) else result
    payload = json.loads(blocks[0].text)
    print(f"\n  search_catalog(query='Марк Аврелій') → знайдено {payload['found']}")
    for item in payload["items"]:
        print(f"    {item['id']}  {item['title']} — {item['price_eur']} EUR")
    print()


if __name__ == "__main__":
    asyncio.run(main())
