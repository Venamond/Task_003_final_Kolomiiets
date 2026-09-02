"""Тести бази знань.

Перевірка узгодженості регламенту працює БЕЗ моделі ембедингів — вона
читає лише тексти документів. Пошук потребує моделі (~470 МБ качається при
першому зверненні), тому позначений маркером needs_model і не входить у
швидкий прогін.
"""
from __future__ import annotations

import re

import pytest

import config
import knowledge as kb


# ── Узгодженість регламенту: без моделі ───────────────────────────────────────

def _budget_doc() -> str:
    """Текст документа регламенту витрат.

    Returns:
        Текст документа policy_budget.
    """
    return next(d["text"] for d in kb.KNOWLEDGE_DOCUMENTS if d["id"] == "policy_budget")


def test_budget_document_states_the_configured_limit():
    """Ліміт у тексті документа збігається з числом у конфігурації."""
    assert str(int(config.BUDGET_MONTHLY_EUR)) in _budget_doc()


def test_budget_document_states_the_configured_copies_limit():
    """Межа примірників у документі теж збігається з конфігурацією."""
    assert str(config.MAX_COPIES_PER_ORDER) in _budget_doc()


def test_no_life_dates_in_knowledge_base():
    """У базі знань немає дат життя: це дані Wikidata, а не бази."""
    for doc in kb.KNOWLEDGE_DOCUMENTS:
        assert not re.search(r"\b1[0-9]{3}\b", doc["text"]), doc["id"]


def test_documents_have_unique_ids():
    """Ідентифікатори документів унікальні — інакше upsert затре один одним."""
    ids = [d["id"] for d in kb.KNOWLEDGE_DOCUMENTS]
    assert len(ids) == len(set(ids))


def test_documents_are_split_into_two_categories():
    """Документи поділені на течії та регламент."""
    categories = {d["category"] for d in kb.KNOWLEDGE_DOCUMENTS}
    assert categories == {"school", "policy"}


# ── Пошук: потребує моделі ембедингів ─────────────────────────────────────────

@pytest.mark.needs_model
def test_search_finds_stoicism(tmp_path, monkeypatch):
    """Україномовний запит піднімає документ про стоїцизм."""
    monkeypatch.setattr(kb, "CHROMA_PATH", str(tmp_path))
    kb.seed_knowledge_base(reset=True)
    out = kb.search_knowledge.invoke({"query": "з чого починати стоїцизм",
                                      "n_results": 2})
    assert "стоїцизм" in out.lower()


@pytest.mark.needs_model
def test_search_finds_budget_policy(tmp_path, monkeypatch):
    """Запит про ліміт піднімає документ регламенту."""
    monkeypatch.setattr(kb, "CHROMA_PATH", str(tmp_path))
    kb.seed_knowledge_base(reset=True)
    out = kb.search_knowledge.invoke({"query": "ліміт бюджету на друковані видання",
                                      "n_results": 2})
    assert "60" in out
