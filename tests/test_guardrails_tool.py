"""Тести прав інструментів за агентами.

Ключове твердження: супервізор не має ЖОДНОГО інструмента. Найризикованіші
дії не належать агенту, який першим читає текст людини.
"""
from __future__ import annotations

import guardrails as g


def test_supervisor_has_no_tools_at_all():
    """Супервізор не має жодного інструмента — його нема чим умовити."""
    assert g.TOOL_PERMISSIONS["supervisor"] == set()


def test_supervisor_cannot_place_order():
    """Найризикованіша дія супервізору недоступна."""
    assert g.tool_guardrail("supervisor", "place_print_order") is False


def test_only_curator_can_place_order():
    """Замовляти має право лише куратор."""
    allowed = [a for a in g.TOOL_PERMISSIONS if g.tool_guardrail(a, "place_print_order")]
    assert allowed == ["curator"]


def test_only_curator_can_send_mail():
    """Надсилати список має право лише куратор."""
    allowed = [a for a in g.TOOL_PERMISSIONS if g.tool_guardrail(a, "send_reading_list")]
    assert allowed == ["curator"]


def test_researcher_has_rag_only():
    """Дослідник має лише пошук у базі знань."""
    assert g.tool_guardrail("researcher", "search_knowledge") is True
    assert g.tool_guardrail("researcher", "search_catalog") is False


def test_factfinder_has_external_lookups_only():
    """Фактолог має лише довідкові інструменти, без дій зі станом."""
    assert g.tool_guardrail("factfinder", "find_philosopher") is True
    assert g.tool_guardrail("factfinder", "cancel_order") is False


def test_auditor_has_read_audit_only():
    """Аудитор має рівно один інструмент — читання журналу."""
    assert g.TOOL_PERMISSIONS["auditor"] == {"read_audit"}


def test_unknown_agent_gets_nothing():
    """Невідомий агент не отримує нічого — за умовчанням заборонено."""
    assert g.tool_guardrail("хтось", "search_knowledge") is False


def test_risky_tools_listed():
    """Перелік ризикових дій збігається з тим, що вимагає підтвердження."""
    assert g.RISKY_TOOLS == {"place_print_order", "cancel_order", "send_reading_list"}


def test_dangerous_argument_is_refused():
    """Спроба обходу шляху в аргументі відхиляється."""
    ok, reason = g.check_tool_args({"order_id": "../../etc/passwd"})
    assert ok is False and "../" in reason


def test_script_in_argument_is_refused():
    """Розмітка в аргументі відхиляється."""
    assert g.check_tool_args({"title": "<script>alert(1)</script>"})[0] is False


def test_normal_arguments_pass():
    """Звичайні аргументи домену проходять."""
    assert g.check_tool_args({"title": "Роздуми", "author": "Марк Аврелій",
                              "copies": 2})[0] is True


# ── Вкладені структури ────────────────────────────────────────────────────────

def test_dangerous_string_inside_list_is_refused():
    """Небезпечний фрагмент усередині списку не проходить перевірку."""
    ok, reason = g.check_tool_args({"items": ["Роздуми", "<script>alert(1)</script>"]})
    assert ok is False and "items[1]" in reason


def test_dangerous_string_inside_dict_is_refused():
    """Небезпечний фрагмент усередині словника не проходить."""
    ok, reason = g.check_tool_args({"meta": {"path": "../../etc/passwd"}})
    assert ok is False and "meta.path" in reason


def test_deeply_nested_dangerous_string_is_refused():
    """Глибока вкладеність не рятує зловмисника."""
    args = {"a": {"b": {"c": ["ok", {"d": "drop table users"}]}}}
    assert g.check_tool_args(args)[0] is False


def test_excessive_nesting_is_refused():
    """Надмірна вкладеність відхиляється: рекурсія обмежена MAX_ARG_DEPTH."""
    value: object = "дно"
    for _ in range(g.MAX_ARG_DEPTH + 3):
        value = {"вкладено": value}
    assert g.check_tool_args({"корінь": value})[0] is False


def test_normal_nested_arguments_pass():
    """Звичайні вкладені аргументи домену проходять."""
    ok, _ = g.check_tool_args({
        "email": "reader@example.com",
        "items": ["Роздуми — Марк Аврелій", "Листи до Луцілія — Сенека"],
    })
    assert ok is True


def test_non_string_values_are_ignored():
    """Числа й булеві значення не заважають перевірці."""
    assert g.check_tool_args({"copies": 2, "ok": True, "price": 18.5})[0] is True
