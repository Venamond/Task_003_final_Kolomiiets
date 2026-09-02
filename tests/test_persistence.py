"""Тести збереження і відновлення стану.

Модель підставна: перевіряється, що чекпоінтер справді зберігає стан і що
відновлення не переграє вже зроблене. Здатність моделі щось вирішити тут ні
до чого.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class S(TypedDict):
    """Мінімальний стан для перевірки самого механізму."""

    results: Annotated[list, operator.add]
    step: int


async def _build(saver):
    """Зібрати граф із перериванням посередині.

    Args:
        saver: Чекпоінтер для графа.

    Returns:
        Скомпільований граф.
    """
    async def first(state: S) -> dict:
        """Перший вузол: дописує результат і збільшує лічильник кроків.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану.
        """
        return {"results": ["перший"], "step": state["step"] + 1}

    async def gate(state: S) -> dict:
        """Вузол переривання: чекає відповіді людини.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану з відповіддю людини.
        """
        answer = interrupt({"ask": "далі?"})
        return {"results": [f"gate:{answer}"]}

    async def last(state: S) -> dict:
        """Останній вузол після відновлення.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану.
        """
        return {"results": ["останній"]}

    g = StateGraph(S)
    g.add_node("first", first)
    g.add_node("gate", gate)
    g.add_node("last", last)
    g.add_edge(START, "first")
    g.add_edge("first", "gate")
    g.add_edge("gate", "last")
    g.add_edge("last", END)
    return g.compile(checkpointer=saver)


async def test_state_survives_interrupt(tmp_path):
    """Стан зберігається на диск при перериванні."""
    db = str(tmp_path / "s.db")
    cfg = {"configurable": {"thread_id": "t1"}}
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        app = await _build(saver)
        out = await app.ainvoke({"results": [], "step": 0}, cfg)
        assert "__interrupt__" in out
        assert out["results"] == ["перший"]


async def test_resume_does_not_replay_completed_steps(tmp_path):
    """Відновлення не переграє виконані кроки: інакше дія сталася б двічі."""
    db = str(tmp_path / "s.db")
    cfg = {"configurable": {"thread_id": "t1"}}
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        app = await _build(saver)
        await app.ainvoke({"results": [], "step": 0}, cfg)
        out = await app.ainvoke(Command(resume="так"), cfg)

    assert out["results"] == ["перший", "gate:так", "останній"]
    assert out["results"].count("перший") == 1


async def test_state_survives_new_process(tmp_path):
    """Новий об'єкт графа з тим самим файлом бачить збережений стан."""
    db = str(tmp_path / "s.db")
    cfg = {"configurable": {"thread_id": "t1"}}

    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        app = await _build(saver)
        await app.ainvoke({"results": [], "step": 0}, cfg)

    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        app = await _build(saver)
        state = await app.aget_state(cfg)
        assert state.values["results"] == ["перший"]
        assert state.values["step"] == 1
        out = await app.ainvoke(Command(resume="так"), cfg)

    assert out["results"] == ["перший", "gate:так", "останній"]


async def test_different_threads_are_independent(tmp_path):
    """Різні потоки не бачать стану один одного."""
    db = str(tmp_path / "s.db")
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        app = await _build(saver)
        await app.ainvoke({"results": [], "step": 0},
                          {"configurable": {"thread_id": "a"}})
        state_b = await app.aget_state({"configurable": {"thread_id": "b"}})
        assert not state_b.values


async def test_crash_leaves_recoverable_state(tmp_path):
    """Виняток посеред роботи не знищує вже збережене."""
    db = str(tmp_path / "s.db")
    cfg = {"configurable": {"thread_id": "t1"}}

    async def first(state: S) -> dict:
        """Вузол, що встигає відпрацювати до обриву.

        Args:
            state: Стан графа.

        Returns:
            Оновлення стану.
        """
        return {"results": ["перший"], "step": 1}

    async def boom(state: S) -> dict:
        """Вузол, що навмисно падає — імітація обриву роботи.

        Args:
            state: Стан графа.

        Raises:
            RuntimeError: Завжди.
        """
        raise RuntimeError("НАВМИСНИЙ ОБРИВ")

    g = StateGraph(S)
    g.add_node("first", first)
    g.add_node("boom", boom)
    g.add_edge(START, "first")
    g.add_edge("first", "boom")
    g.add_edge("boom", END)

    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        app = g.compile(checkpointer=saver)
        with pytest.raises(RuntimeError, match="НАВМИСНИЙ ОБРИВ"):
            await app.ainvoke({"results": [], "step": 0}, cfg)
        state = await app.aget_state(cfg)
        assert state.values["results"] == ["перший"]
