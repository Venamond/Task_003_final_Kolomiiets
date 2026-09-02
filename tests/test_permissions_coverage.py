"""Тести повноти матриці прав і крихких місць збірки.

Дві речі, які легко зламати непомітно: додати дію до служби й забути про
матрицю прав, або перенести імпорт MASState усередину функції й зламати
розбір анотацій у LangGraph.
"""
from __future__ import annotations

import inspect

import pytest

import guardrails as g
import mcp_mail_server
import mcp_server

# Дії, які навмисно не призначені жодному агенту. Порожньо: якщо колись
# зʼявиться така дія, її треба вписати сюди СВІДОМО, а не забути.
INTENTIONALLY_UNASSIGNED: set[str] = set()


async def _declared_tools() -> set[str]:
    """Усі дії, які оголошують служби MCP.

    Returns:
        Імена всіх дій, які оголошують обидві служби.
    """
    names = set()
    for server in (mcp_server.mcp, mcp_mail_server.mcp):
        names |= {t.name for t in await server.list_tools()}
    return names


async def test_every_declared_tool_is_assigned_to_someone():
    """Кожна дія служби призначена хоча б одному агенту."""
    assigned = set().union(*g.TOOL_PERMISSIONS.values())
    orphans = await _declared_tools() - assigned - INTENTIONALLY_UNASSIGNED
    assert not orphans, (
        f"Дії служб, не призначені жодному агенту: {sorted(orphans)}. "
        f"Додайте їх у TOOL_PERMISSIONS або в INTENTIONALLY_UNASSIGNED.")


async def test_permissions_do_not_mention_nonexistent_tools():
    """У матриці прав немає дій, яких не існує у службах."""
    import tools_legacy as legacy

    local = {legacy.find_philosopher.name, legacy.get_influences.name,
             legacy.find_texts.name, legacy.check_lifespan_overlap.name,
             legacy.read_audit.name, legacy.search_knowledge.name}
    known = await _declared_tools() | local
    assigned = set().union(*g.TOOL_PERMISSIONS.values())
    ghosts = assigned - known
    assert not ghosts, f"Права вказують на неіснуючі дії: {sorted(ghosts)}"


async def test_every_risky_tool_is_declared_by_a_server():
    """Кожна ризикова дія справді існує у службі."""
    assert g.RISKY_TOOLS <= await _declared_tools()


async def test_risky_tools_belong_to_curator_only():
    """Ризикові дії має лише куратор, і це перевіряється явно."""
    for tool in g.RISKY_TOOLS:
        owners = [a for a in g.TOOL_PERMISSIONS if g.tool_guardrail(a, tool)]
        assert owners == ["curator"], f"{tool}: власники {owners}"


def test_curator_imports_state_at_module_level():
    """MASState імпортується на рівні модуля: get_type_hints у гілках
    LangGraph не бачить локального імпорту."""
    import agents_curator as ac

    source = inspect.getsource(ac)
    header = source[:source.index("def build_curator")]
    assert "from mas_langgraph import MASState" in header, (
        "MASState має імпортуватися на рівні модуля: LangGraph розрішує "
        "анотації гілок через get_type_hints і локальний імпорт не бачить")


def test_no_module_level_cycle_between_graph_and_subgraphs():
    """Головний граф підключає підграфи ліниво — інакше імпорт зациклиться."""
    import mas_langgraph as mas

    source = inspect.getsource(mas)
    header = source[:source.index("async def build_mas")]
    for name in ("agents_curator", "agents_factfinder"):
        assert f"import {name}" not in header, (
            f"{name} має підключатися лише всередині build_mas: "
            f"модульний імпорт замкне кільце")
