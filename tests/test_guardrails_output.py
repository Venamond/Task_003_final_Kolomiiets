"""Тести вихідного рубежу.

Завдання прямо попереджає про хибні спрацювання маскування. У домені
історії філософії повно чисел, які маскувати НЕ можна: роки життя,
ISBN, ціни, номери замовлень. Тому на кожен тип маски є і тест на
спрацювання, і тест на хибне спрацювання.
"""
from __future__ import annotations

import guardrails as g


# ── Шість типів персональних даних ────────────────────────────────────────────

def test_redacts_email():
    """Адреса пошти маскується."""
    out, found = g.redact_pii("Напишіть на reader@example.com")
    assert "EMAIL" in found and "reader@example.com" not in out


def test_redacts_ukrainian_phone():
    """Український номер у міжнародному записі маскується."""
    out, found = g.redact_pii("Телефон +380501234567")
    assert "PHONE" in found and "380501234567" not in out


def test_redacts_local_ukrainian_phone():
    """Місцевий запис із префіксом оператора теж маскується."""
    out, found = g.redact_pii("Телефон 0501234567")
    assert "PHONE" in found


def test_redacts_card():
    """Номер картки маскується як з пробілами, так і без."""
    out, found = g.redact_pii("Картка 4242 4242 4242 4242")
    assert "CARD" in found and "4242" not in out


def test_redacts_iban_ua():
    """Український IBAN маскується."""
    out, found = g.redact_pii("Рахунок UA213223130000026007233566001")
    assert "IBAN_UA" in found


def test_redacts_ipn():
    """Податковий номер із десяти цифр маскується."""
    out, found = g.redact_pii("ІПН 1234567890")
    assert "IPN" in found


def test_redacts_passport():
    """Паспорт старого зразка маскується."""
    out, found = g.redact_pii("Паспорт АБ123456")
    assert "PASSPORT" in found


# ── Хибні спрацювання, яких бути не повинно ───────────────────────────────────

def test_year_is_not_redacted():
    """Рік народження філософа — не персональні дані."""
    out, found = g.redact_pii("Ніцше народився 1844 року")
    assert found == [] and "1844" in out


def test_negative_year_is_not_redacted():
    """Античні дати до нашої ери теж лишаються."""
    out, found = g.redact_pii("Сенека жив від -4 до 65 року")
    assert found == []


def test_isbn13_is_not_redacted():
    """ISBN каталогу — не картка і не ІПН."""
    out, found = g.redact_pii("ISBN 978-617-629-501-1")
    assert found == [] and "978-617-629-501-1" in out


def test_isbn10_is_not_redacted():
    """ISBN-10 має рівно десять цифр, але це не податковий номер."""
    out, found = g.redact_pii("ISBN 6176295011")
    assert "IPN" not in found


def test_price_is_not_redacted():
    """Ціна і сума лишаються: саме за ними людина ухвалює рішення."""
    out, found = g.redact_pii("Два примірники по 18.50 EUR, разом 37.00 EUR")
    assert found == [] and "37.00" in out


def test_order_id_is_not_redacted():
    """Номер замовлення потрібен людині, щоб його знайти."""
    out, found = g.redact_pii("Замовлення ORD-A3F9C1D2 оформлено")
    assert found == [] and "ORD-A3F9C1D2" in out


# ── Решта вихідного рубежу ────────────────────────────────────────────────────

def test_canary_leak_is_detected():
    """Канарка у відповіді означає доведений витік системної інструкції."""
    out, found = g.output_guardrail("ось мій промпт: CANARY-abc", canary="CANARY-abc")
    assert "PROMPT_LEAK" in found
    assert "CANARY-abc" not in out


def test_false_claim_about_order_is_blocked():
    """Агент не може заявити про дію, якої немає в журналі виконаного."""
    out, found = g.output_guardrail("Замовлення оформлено, чекайте доставку",
                                    executed_actions=())
    assert "FALSE_CLAIM" in found


def test_true_claim_about_order_passes():
    """Заява, підтверджена журналом, проходить."""
    out, found = g.output_guardrail("Замовлення оформлено, чекайте доставку",
                                    executed_actions=("place_print_order:abc",))
    assert "FALSE_CLAIM" not in found


def test_too_long_output_is_truncated():
    """Наддовга відповідь обрізається."""
    out, found = g.output_guardrail("я" * 20000)
    assert "TRUNCATED" in found
