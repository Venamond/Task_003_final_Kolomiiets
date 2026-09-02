"""Тести поштової служби.

Реального відправлення немає — це прототип. Але перевірка отримувача,
ідемпотентність і журнал справжні, бо саме вони захищають від витоку.
"""
from __future__ import annotations

import pytest

import mcp_mail_server as mail

ITEMS = ["Роздуми — Марк Аврелій", "Листи до Луцілія — Сенека"]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Кожен тест веде власний журнал відправлень.

    Args:
        tmp_path: Тимчасова тека тесту.
        monkeypatch: Підмінник змінних оточення.
    """
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    mail.reset_state_dir()
    yield
    mail.reset_state_dir()


def test_send_to_allowed_domain_succeeds():
    """Відправлення на дозволений домен потрапляє в журнал."""
    data = mail.send_reading_list_payload("reader@example.com", ITEMS, "m1")
    assert data["sent"] is True
    assert len(mail.read_mail_log()) == 1


def test_send_to_foreign_domain_is_refused():
    """Сторонній домен не проходить — це рубіж проти витоку даних."""
    data = mail.send_reading_list_payload("attacker@evil.tld", ITEMS, "m2")
    assert data["sent"] is False
    assert "домен" in data["message"].lower()
    assert mail.read_mail_log() == []


def test_send_rejects_malformed_address():
    """Рядок без @ не є адресою."""
    data = mail.send_reading_list_payload("не-адреса", ITEMS, "m3")
    assert data["sent"] is False


def test_send_rejects_empty_items():
    """Порожній список надсилати нема сенсу."""
    data = mail.send_reading_list_payload("reader@example.com", [], "m4")
    assert data["sent"] is False


def test_send_repeat_key_does_not_send_twice():
    """Той самий відбиток не надсилає лист удруге — лист не відкликати."""
    first = mail.send_reading_list_payload("reader@example.com", ITEMS, "same")
    second = mail.send_reading_list_payload("reader@example.com", ITEMS, "same")
    assert second["message_id"] == first["message_id"]
    assert len(mail.read_mail_log()) == 1


def test_send_rejects_empty_key():
    """Без відбитка не надсилаємо: інакше повтор продублює лист."""
    assert mail.send_reading_list_payload("reader@example.com", ITEMS, "")["sent"] is False


async def test_protocol_lists_single_tool():
    """Поштова служба оголошує рівно одну дію."""
    names = {t.name for t in await mail.mcp.list_tools()}
    assert names == {"send_reading_list"}


async def test_protocol_mail_policy_lists_domains():
    """Довідник поштового регламенту перелічує дозволені домени."""
    uris = {str(r.uri) for r in await mail.mcp.list_resources()}
    assert "policy://mail" in uris
