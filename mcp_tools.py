"""Підключення служб MCP і виклик інструментів через рубежі.

Кожен виклик проходить три перевірки: права агента, аргументи, відповідь
служби. Адаптери 0.3.2 відкривають сесію на кожен виклик, тому реєстр
інструментів живе поза контекстом клієнта.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

import audit
import guardrails as g
import logs
import mcp_manifest

log = logs.get_logger("mcp_tools")

# Шлях від файлу, а не від робочого каталогу: інакше запуск не з кореня
# репозиторію ламає підняття служб, а помилка виглядає як збій служби.
_HERE = Path(__file__).resolve().parent

# Без явного env клієнт MCP передає підпроцесу лише HOME, PATH, SHELL, USER,
# LOGNAME. Ані STATE_DIR, ані межі з config не доходять — служби тихо
# працюють з умовчаннями, а тести пишуть у робочу теку замість тимчасової.
# Ключі API не передаємо: службам вони не потрібні.
_FORWARDED_VARS = (
    "STATE_DIR",
    "BUDGET_MONTHLY_EUR",
    "SESSION_SPEND_LIMIT_EUR",
    "MAX_COPIES_PER_ORDER",
    "MAIL_ALLOWED_DOMAINS",
)


def server_env() -> dict[str, str]:
    """Оточення для підпроцесів служб: мінімум плюс наша конфігурація.

    Returns:
        Змінні, які отримає підпроцес служби.
    """
    from mcp.client.stdio import get_default_environment

    env = dict(get_default_environment())
    for name in _FORWARDED_VARS:
        value = os.getenv(name)
        if value is not None:
            env[name] = value
    return env


MCP_CONFIG: dict[str, dict[str, Any]] = {
    "library": {
        "command": sys.executable,
        "args": [str(_HERE / "mcp_server.py")],
        "transport": "stdio",
        "env": server_env(),
    },
    "mail": {
        "command": sys.executable,
        "args": [str(_HERE / "mcp_mail_server.py")],
        "transport": "stdio",
        "env": server_env(),
    },
}


def refresh_config() -> None:
    """Перечитати оточення для підпроцесів.

    Потрібно тестам: MCP_CONFIG будується при імпорті, а тест підміняє
    STATE_DIR уже після нього.
    """
    for server in MCP_CONFIG.values():
        server["env"] = server_env()


class ToolDenied(RuntimeError):
    """Виклик інструмента відхилено рубежем."""


async def load_mcp_tools(*, verify: bool = True) -> dict[str, BaseTool]:
    """Підключитися до служб MCP і отримати їхні інструменти.

    Args:
        verify: Чи звіряти схеми служб зі зліпком перед підключенням.

    Returns:
        Відображення «ім'я інструмента → інструмент».

    Raises:
        mcp_manifest.ManifestMismatch: Схеми розійшлися зі зліпком.
    """
    # Тест міг підмінити STATE_DIR після імпорту — перечитуємо оточення.
    refresh_config()

    if verify:
        ok, problems = await mcp_manifest.verify_manifest()
        if not ok:
            audit.write("manifest", "verify", "block", session_id="startup",
                        detail="; ".join(problems))
            raise mcp_manifest.ManifestMismatch(
                "Схеми служб розійшлися зі зліпком:\n  " + "\n  ".join(problems))
        audit.write("manifest", "verify", "allow", session_id="startup")

    client = MultiServerMCPClient(MCP_CONFIG)
    return {tool.name: tool for tool in await client.get_tools()}


async def load_all_tools(*, verify: bool = True) -> dict[str, BaseTool]:
    """Зібрати повний реєстр: інструменти MCP плюс локальні з ДЗ1 і ДЗ2.

    Локальні лишаються в процесі: вони лише читають. У службах — тільки те,
    що змінює стан або коштує грошей.

    Args:
        verify: Чи звіряти зліпок.

    Returns:
        Відображення «ім'я інструмента → інструмент».
    """
    import tools_legacy as legacy

    registry = await load_mcp_tools(verify=verify)
    for tool in (legacy.find_philosopher, legacy.get_influences,
                 legacy.find_texts, legacy.check_lifespan_overlap,
                 legacy.read_audit, legacy.search_knowledge):
        registry[tool.name] = tool
    return registry


async def call_tool(registry: dict[str, BaseTool], name: str, args: dict, *,
                    agent: str, session_id: str,
                    thread_id: str = "") -> tuple[bool, str]:
    """Викликати інструмент через усі рубежі.

    Args:
        registry: Реєстр доступних інструментів.
        name: Ім'я інструмента.
        args: Аргументи виклику.
        agent: Який агент викликає — за ним перевіряються права.
        session_id: Сесія для журналу.
        thread_id: Потік для журналу.

    Returns:
        Пара (чи успішно, текст результату або пояснення відмови).
    """
    if not g.tool_guardrail(agent, name):
        audit.write("guardrail", f"tool_permission:{name}", "block",
                    session_id=session_id, thread_id=thread_id, agent=agent)
        return False, (f"Відмовлено: агент '{agent}' не має права викликати "
                       f"'{name}'.")

    ok_args, reason = g.check_tool_args(args)
    if not ok_args:
        audit.write("guardrail", f"tool_args:{name}", "block",
                    session_id=session_id, thread_id=thread_id, agent=agent,
                    detail=reason)
        return False, f"Відмовлено: {reason}"

    tool = registry.get(name)
    if tool is None:
        return False, f"Інструмент '{name}' не зареєстровано."

    try:
        raw = await tool.ainvoke(args)
    except Exception as exc:  # noqa: BLE001 — відмову має побачити агент, а не впасти
        # Агенту — текст, щоб перепланував; у журнал — тип винятку, інакше
        # збій схеми не відрізнити від збою мережі.
        log.warning("Інструмент %s впав: %s: %s", name, type(exc).__name__, exc)
        audit.write("guardrail", f"tool_error:{name}", "block",
                    session_id=session_id, thread_id=thread_id, agent=agent,
                    detail=f"{type(exc).__name__}: {exc}"[:300])
        return False, f"Помилка виклику '{name}': {type(exc).__name__}: {exc}"

    ok_out, cleaned, found = g.tool_output_guardrail(name, _as_text(raw))
    if not ok_out:
        audit.write("guardrail", f"tool_output:{name}", "block",
                    session_id=session_id, thread_id=thread_id, agent=agent,
                    detail=", ".join(found))
        return False, cleaned
    if found:
        audit.write("guardrail", f"tool_output:{name}", "redact",
                    session_id=session_id, thread_id=thread_id, agent=agent,
                    detail=", ".join(found))
    return True, cleaned


def _as_text(raw: Any) -> str:
    """Звести відповідь інструмента до тексту.

    Адаптери повертають перелік блоків, локальні інструменти — рядок.

    Args:
        raw: Відповідь інструмента.

    Returns:
        Текстове подання.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                parts.append(block.get("text", str(block)))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(raw)
