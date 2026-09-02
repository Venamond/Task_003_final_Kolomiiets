"""Тести підтвердження людиною.

Головне, що тут перевіряється — неповторність. Вузол підтвердження
перезапускається з початку при відновленні, тому все до interrupt() має бути
чистим, а відбиток — рахуватися заздалегідь і не змінюватися.
"""
from __future__ import annotations

import hitl

ORDER = {"title": "Роздуми", "author": "Марк Аврелій",
         "copies": 2, "price_per_copy": 18.5}


# ── Відбиток ──────────────────────────────────────────────────────────────────

def test_idem_key_is_deterministic():
    """Однакові вхідні дані дають однаковий відбиток."""
    a = hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)
    b = hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)
    assert a == b


def test_idem_key_ignores_argument_order():
    """Порядок ключів не робить дію іншою."""
    reordered = {k: ORDER[k] for k in reversed(list(ORDER))}
    assert (hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)
            == hitl.make_idem_key("library", "place_print_order", reordered, "t1", 3))


def test_idem_key_changes_with_args():
    """Інша кількість примірників — інше замовлення, інший відбиток."""
    other = {**ORDER, "copies": 1}
    assert (hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)
            != hitl.make_idem_key("library", "place_print_order", other, "t1", 3))


def test_idem_key_changes_with_thread():
    """Інший потік — інша дія."""
    assert (hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)
            != hitl.make_idem_key("library", "place_print_order", ORDER, "t2", 3))


def test_idem_key_has_no_time_component():
    """У відбитку немає часу — інакше він змінився б після відновлення."""
    import time
    a = hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)
    time.sleep(0.01)
    assert a == hitl.make_idem_key("library", "place_print_order", ORDER, "t1", 3)


# ── Предʼявлення людині ───────────────────────────────────────────────────────

def test_payload_shows_raw_numbers():
    """Людині показують голі числа, а не переказ моделі."""
    action = {"server": "library", "tool": "place_print_order", "args": ORDER,
              "idem_key": "k", "step": 0}
    payload = hitl.build_interrupt_payload(action, budget={"spent": 12.5, "limit": 60.0})
    assert payload["total_cost"] == 37.0
    assert payload["budget_limit"] == 60.0
    assert payload["budget_spent"] == 12.5
    assert payload["exceeds_limit"] is False


def test_payload_flags_budget_excess():
    """Перевищення бюджету показується явним прапорцем."""
    action = {"server": "library", "tool": "place_print_order",
              "args": {**ORDER, "copies": 3, "price_per_copy": 31.0},
              "idem_key": "k", "step": 0}
    payload = hitl.build_interrupt_payload(action, budget={"spent": 0.0, "limit": 60.0})
    assert payload["exceeds_limit"] is True


def test_payload_for_mail_allows_no_edits():
    """Для надсилання не редагується нічого: підміна адреси це і є атака."""
    action = {"server": "mail", "tool": "send_reading_list",
              "args": {"email": "reader@example.com", "items": ["Роздуми"]},
              "idem_key": "k", "step": 0}
    payload = hitl.build_interrupt_payload(action, budget={"spent": 0.0, "limit": 60.0})
    assert payload["editable"] == []


def test_payload_shows_full_recipient_address():
    """Адреса отримувача показується повністю, а не скорочено."""
    action = {"server": "mail", "tool": "send_reading_list",
              "args": {"email": "reader@example.com", "items": ["Роздуми"]},
              "idem_key": "k", "step": 0}
    payload = hitl.build_interrupt_payload(action, budget={"spent": 0.0, "limit": 60.0})
    assert payload["args"]["email"] == "reader@example.com"


# ── Рішення людини ────────────────────────────────────────────────────────────

def test_approve_keeps_args_unchanged():
    """Згода виконує дію рівно з тими аргументами, які показали."""
    decision, args, note = hitl.apply_human_decision(
        {"decision": "approve"}, ORDER, "place_print_order")
    assert decision == "approve" and args == ORDER


def test_edit_applies_allowed_field():
    """Кількість примірників редагувати можна."""
    decision, args, note = hitl.apply_human_decision(
        {"decision": "edit", "args": {"copies": 1}}, ORDER, "place_print_order")
    assert decision == "edit" and args["copies"] == 1


def test_edit_refuses_price_change():
    """Ціну редагувати не можна: її задає каталог, зміна була б підробкою."""
    decision, args, note = hitl.apply_human_decision(
        {"decision": "edit", "args": {"price_per_copy": 1.0}}, ORDER,
        "place_print_order")
    assert args["price_per_copy"] == 18.5
    assert "price_per_copy" in note


def test_edit_refuses_email_change():
    """Адресу отримувача редагувати не можна взагалі."""
    args_in = {"email": "reader@example.com", "items": ["Роздуми"]}
    decision, args, note = hitl.apply_human_decision(
        {"decision": "edit", "args": {"email": "attacker@evil.tld"}}, args_in,
        "send_reading_list")
    assert args["email"] == "reader@example.com"


def test_reject_returns_reason():
    """Відмова несе причину для журналу."""
    decision, args, note = hitl.apply_human_decision(
        {"decision": "reject", "reason": "дорого"}, ORDER, "place_print_order")
    assert decision == "reject" and "дорого" in note


def test_unknown_decision_is_treated_as_refusal():
    """Невідоме рішення трактується як відмова."""
    decision, args, note = hitl.apply_human_decision(
        {"decision": "може"}, ORDER, "place_print_order")
    assert decision == "reject"


def test_missing_decision_is_treated_as_refusal():
    """Порожня відповідь — теж відмова."""
    assert hitl.apply_human_decision({}, ORDER, "place_print_order")[0] == "reject"


# ── Відмова служби ≠ виконана дія ─────────────────────────────────────────────

def test_refused_order_is_not_recorded_as_done():
    """Відмова служби не потрапляє в журнал виконаного."""
    refused = ('{"ordered": false, "total_cost": 68.0, '
               '"message": "Відмовлено: перевищує місячний ліміт"}')
    assert hitl.action_succeeded("place_print_order", refused) is False


def test_successful_order_is_recorded_as_done():
    """Підтверджене замовлення зараховується."""
    done = '{"ordered": true, "order_id": "ORD-1", "total_cost": 18.5}'
    assert hitl.action_succeeded("place_print_order", done) is True


def test_refused_mail_is_not_recorded_as_done():
    """Відмова надсилання теж не зараховується."""
    refused = '{"sent": false, "message": "Відмовлено: домен не дозволений"}'
    assert hitl.action_succeeded("send_reading_list", refused) is False


def test_successful_mail_is_recorded_as_done():
    """Успішне надсилання зараховується."""
    assert hitl.action_succeeded("send_reading_list",
                                 '{"sent": true, "message_id": "MSG-1"}') is True


def test_cancel_uses_its_own_flag():
    """Скасування має власне поле підтвердження."""
    assert hitl.action_succeeded("cancel_order", '{"cancelled": true}') is True
    assert hitl.action_succeeded("cancel_order", '{"cancelled": false}') is False


def test_unparsable_answer_counts_as_not_done():
    """Нерозбірлива відповідь: краще недорахувати, ніж збрехати."""
    assert hitl.action_succeeded("place_print_order", "не JSON зовсім") is False


def test_unknown_budget_is_not_shown_as_zero():
    """Невідомий залишок показується як невідомий, а не як нуль."""
    action = {"server": "library", "tool": "place_print_order", "args": ORDER,
              "idem_key": "k", "step": 0}
    payload = hitl.build_interrupt_payload(action, budget=None)
    assert payload["budget_known"] is False
    assert payload["budget_spent"] is None
    assert payload["budget_limit"] is None
    assert payload["exceeds_limit"] is None
    assert payload["total_cost"] == 37.0
