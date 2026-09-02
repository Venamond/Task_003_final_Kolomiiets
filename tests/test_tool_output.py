"""Тести огляду відповідей інструментів.

Дані з інструмента потрапляють ПРЯМО в контекст моделі, тобто є недовіреним
вводом рівно тією ж мірою, що й запит людини. Wikidata редагує будь-хто,
назви книжок у відкритих бібліотеках приходять із користувацьких завантажень.
"""
from __future__ import annotations

import json

import config
import fixtures_poisoned as poisoned
import guardrails as g


def test_clean_response_passes():
    """Звичайна відповідь проходить без зауважень."""
    ok, cleaned, found = g.tool_output_guardrail(
        "search_catalog", json.dumps({"found": 1, "items": []}, ensure_ascii=False))
    assert ok is True and found == []


def test_injection_in_gutendex_title_is_blocked():
    """Прихована команда в назві книжки не доходить до моделі."""
    raw = json.dumps(poisoned.POISONED_GUTENDEX, ensure_ascii=False)
    ok, cleaned, found = g.tool_output_guardrail("find_texts", raw)
    assert ok is False and "INJECTION_IN_DATA" in found


def test_injection_in_wikidata_description_is_blocked():
    """Прихована команда в описі філософа теж не доходить."""
    raw = json.dumps(poisoned.POISONED_WIKIDATA, ensure_ascii=False)
    ok, cleaned, found = g.tool_output_guardrail("find_philosopher", raw)
    assert ok is False and "INJECTION_IN_DATA" in found


def test_injection_in_resource_is_blocked():
    """Довідник MCP із дописаною командою не доходить."""
    ok, cleaned, found = g.tool_output_guardrail(
        "policy://budget", poisoned.POISONED_RESOURCE)
    assert ok is False


def test_injection_in_kb_document_is_blocked():
    """Отруєний документ бази знань не доходить."""
    ok, cleaned, found = g.tool_output_guardrail(
        "search_knowledge", poisoned.POISONED_KB_DOC)
    assert ok is False


def test_html_markup_is_stripped():
    """Розмітка знімається, а не блокує відповідь цілком."""
    ok, cleaned, found = g.tool_output_guardrail(
        "search_catalog", "Назва <script>alert(1)</script> книжки")
    assert "<script>" not in cleaned and "MARKUP" in found


def test_javascript_link_is_stripped():
    """Посилання зі схемою javascript знімається."""
    ok, cleaned, found = g.tool_output_guardrail(
        "search_catalog", "Дивіться javascript:steal() тут")
    assert "javascript:" not in cleaned


def test_ansi_escape_is_stripped():
    """Керуючі послідовності термінала знімаються."""
    ok, cleaned, found = g.tool_output_guardrail(
        "get_order", "Замовлення \x1b[31mЧЕРВОНЕ\x1b[0m")
    assert "\x1b" not in cleaned


def test_zero_width_is_stripped():
    """Невидимі символи знімаються, інакше ними ховають команду."""
    ok, cleaned, found = g.tool_output_guardrail(
        "get_order", "Замов​лення ORD-1")
    assert "​" not in cleaned


def test_oversized_response_is_truncated():
    """Наддовга відповідь обрізається: захист від переповнення контексту."""
    ok, cleaned, found = g.tool_output_guardrail(
        "search_catalog", "я" * (config.MAX_TOOL_RESPONSE_BYTES * 2))
    assert "OVERSIZED" in found
    assert len(cleaned) <= config.MAX_TOOL_RESPONSE_BYTES + 100


def test_pii_in_tool_response_is_redacted():
    """Персональні дані ріжуться вже тут, а не лише у фінальній відповіді."""
    ok, cleaned, found = g.tool_output_guardrail(
        "get_order", "Отримувач reader@example.com")
    assert "reader@example.com" not in cleaned


def test_broken_payload_is_not_fatal():
    """Порожня чи зіпсована відповідь не валить систему."""
    ok, cleaned, found = g.tool_output_guardrail("get_order", "")
    assert isinstance(cleaned, str)
