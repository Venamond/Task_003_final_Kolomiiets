"""Підтвердження людиною для ризикових дій. Перенесено з ДЗ2.

Три правила:
- людині показуємо сирі числа, а не переказ моделі — переконливий текст
  схиляє до автоматичного «згоден» (rubber stamping, ASI09);
- редагувати можна лише поля з EDITABLE_ARGS, фільтр у коді, не в промпті;
- вузол чистий до interrupt(): при відновленні LangGraph перезапускає його
  з початку, і будь-який побічний ефект вище спрацював би вдруге.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

import audit
import guardrails as g
import logs
import mcp_tools

log = logs.get_logger("hitl")

# Єдині поля, які рецензент має право змінити перед виконанням.
EDITABLE_ARGS: dict[str, frozenset[str]] = {
    "place_print_order": frozenset({"copies"}),
    "cancel_order": frozenset({"reason"}),
    # Нічого: єдине, що тут можна «поправити», — адреса, а це і є атака.
    "send_reading_list": frozenset(),
}

WHY_RISKY = {
    "place_print_order": "Незворотна дія: витрата коштів на друковане видання",
    "cancel_order": "Незворотна дія: скасування вже оформленого замовлення",
    "send_reading_list": ("Незворотна дія: дані виходять за межі системи, "
                          "лист не відкликати"),
}


def make_idem_key(server: str, tool: str, args: dict, thread_id: str,
                  step: int) -> str:
    """Порахувати відбиток ризикової дії.

    Тільки постійні величини — ні часу, ні випадкових чисел: інакше відбиток
    змінився б між перериванням і відновленням, і захист від повтору зник би.

    Args:
        server: Ім'я служби.
        tool: Ім'я дії.
        args: Аргументи виклику.
        thread_id: Потік розмови.
        step: Номер кроку плану.

    Returns:
        Відбиток у шістнадцятковому вигляді.
    """
    # Модель бачить idempotency_key у схемі і вигадує йому значення. У хеш
    # його не беремо: та сама дія з іншим ключем вважалася б новою.
    payload = {k: v for k, v in args.items() if k != "idempotency_key"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raw = f"{server}|{tool}|{canonical}|{thread_id}|{step}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_interrupt_payload(action: dict, *,
                            budget: dict | None) -> dict[str, Any]:
    """Скласти опис ризикової дії для людини.

    Args:
        action: Відкладена дія з полями server, tool, args, idem_key, step.
        budget: Стан бюджету з полями spent і limit, або None, якщо служба
            бюджету не відповіла.

    Returns:
        Предʼявлення з голими числами; при невідомому бюджеті поля витрат
        дорівнюють None, а budget_known — False.
    """
    # Вигаданий моделлю ключ не показуємо: виконається наш власний відбиток.
    args = {k: v for k, v in action["args"].items() if k != "idempotency_key"}
    tool = action["tool"]
    total = round(args.get("copies", 1) * args.get("price_per_copy", 0.0), 2)

    # Невідомий залишок НЕ показуємо нулем. Уся суть предʼявлення в тому, що
    # числа справжні; вигаданий нуль тут гірший за відсутність числа, бо
    # людина схвалить покупку, яка насправді виходить за ліміт.
    known = budget is not None
    spent = round(float(budget["spent"]), 2) if known else None
    limit = round(float(budget["limit"]), 2) if known else None

    return {
        "action": tool,
        "server": action["server"],
        "args": args,
        "total_cost": total,
        "budget_known": known,
        "budget_spent": spent,
        "budget_limit": limit,
        "exceeds_limit": ((spent + total) > limit) if (known and total) else None,
        "editable": sorted(EDITABLE_ARGS.get(tool, frozenset())),
        "why_risky": WHY_RISKY.get(tool, "Незворотна дія"),
        "idem_key": action["idem_key"],
    }


def apply_human_decision(answer: dict, args: dict,
                         tool: str) -> tuple[str, dict, str]:
    """Розібрати відповідь людини і застосувати дозволені правки.

    Args:
        answer: Відповідь із Command(resume=...) з ключами decision, reason, args.
        args: Аргументи, які показали людині.
        tool: Ім'я дії — за ним визначається перелік дозволених правок.

    Returns:
        Трійка (рішення, підсумкові аргументи, примітка для журналу).
    """
    decision = answer.get("decision")
    allowed = EDITABLE_ARGS.get(tool, frozenset())

    if decision == "approve":
        return "approve", args, ""

    if decision == "edit":
        proposed = answer.get("args", {}) or {}
        applied = {k: v for k, v in proposed.items() if k in allowed}
        ignored = sorted(set(proposed) - allowed)
        note = (f"Поза переліком дозволених, не застосовано: {', '.join(ignored)}"
                if ignored else "")
        return "edit", {**args, **applied}, note

    if decision == "reject":
        return "reject", args, f"причина: {answer.get('reason', 'без пояснення')}"

    # Невідоме рішення — відмова: за умовчанням ризикову дію не виконуємо.
    return "reject", args, f"невідоме рішення '{decision}', трактовано як відмову"


async def _read_budget(registry: dict, session_id: str,
                       thread_id: str) -> dict[str, float] | None:
    """Прочитати стан бюджету для предʼявлення людині.

    Саме читання зі служби, а не переказ моделі: людина бачить справжні числа.

    Args:
        registry: Реєстр інструментів.
        session_id: Сесія для журналу.
        thread_id: Потік для журналу.

    Returns:
        Словник з spent і limit, або None, якщо служба не відповіла або
        відповіла нерозбірливо.
    """
    from datetime import datetime, timezone

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ok, text = await mcp_tools.call_tool(
        registry, "get_budget_status", {"month": month}, agent="curator",
        session_id=session_id, thread_id=thread_id)
    if not ok:
        log.warning("Служба бюджету не відповіла (%s), людині показуємо "
                    "«залишок невідомий»", text[:80])
        return None
    try:
        data = json.loads(text)
        return {"spent": float(data["spent"]), "limit": float(data["limit"])}
    except (ValueError, TypeError, KeyError) as exc:
        log.warning("Відповідь служби бюджету нерозбірлива (%s)",
                    type(exc).__name__)
        return None


# Виклик може пройти, а дія — ні: служба відмовляє за власними правилами,
# наприклад при перевищенні бюджету. Ці поля показують, що сталося насправді.
SUCCESS_FLAG = {
    "place_print_order": "ordered",
    "cancel_order": "cancelled",
    "send_reading_list": "sent",
}


def action_succeeded(tool: str, raw: str) -> bool:
    """Чи виконала служба дію насправді, а не просто відповіла без помилки.

    Без цієї перевірки агент рапортує про неоформлене замовлення, аудит пише
    approve на відмову, а відбиток блокує законну повторну спробу.

    Args:
        tool: Ім'я дії.
        raw: Відповідь служби (JSON-рядок).

    Returns:
        True, якщо служба підтвердила виконання.
    """
    flag = SUCCESS_FLAG.get(tool)
    if flag is None:
        return True
    try:
        return bool(json.loads(raw).get(flag))
    except (ValueError, TypeError, AttributeError):
        # Нерозбірлива відповідь — вважаємо, що дія не відбулася: краще
        # недорахувати виконане, ніж збрехати про нього.
        return False


def make_approval_gate(registry: dict) -> Callable:
    """Створити вузол підтвердження поверх реєстру інструментів.

    Args:
        registry: Реєстр доступних інструментів.

    Returns:
        Асинхронний вузол графа.
    """
    from langgraph.types import interrupt

    from trajectory_logger import log_entry

    limiter = g.RateLimiter()

    async def approval_gate(state: dict) -> dict[str, Any]:
        """Спитати людину і виконати схвалену дію.

        До interrupt() — лише читання стану і складання предʼявлення:
        при відновленні LangGraph перезапускає вузол з початку.

        Args:
            state: Стан графа з відкладеною дією в pending_action.

        Returns:
            Оновлення стану.
        """
        action = state.get("pending_action")
        if not action:
            return {"pending_approval": False}

        session = state["session_id"]

        # Дію з таким відбитком уже виконано — повторно не виконуємо.
        if action["idem_key"] in state.get("executed_actions", []):
            return {
                "pending_approval": False, "pending_action": None,
                "results": [f"Дію {action['tool']} вже виконано раніше."],
                "trajectory": [log_entry("approval_gate", "idempotent",
                                         action["tool"], "повтор пропущено")],
            }

        budget = await _read_budget(registry, session, state.get("thread_id", ""))
        payload = build_interrupt_payload(action, budget=budget)

        # ── З цього місця починаються побічні ефекти ──
        answer = interrupt(payload)

        decision, final_args, note = apply_human_decision(
            answer if isinstance(answer, dict) else {}, action["args"],
            action["tool"])

        audit.write("hitl", action["tool"], decision, session_id=session,
                    thread_id=state.get("thread_id", ""), agent="curator",
                    detail=note, idem_key=action["idem_key"])

        if decision == "reject":
            return {
                "pending_approval": False, "pending_action": None,
                "current_step": state.get("current_step", 0) + 1,
                "results": [f"Дію {action['tool']} відхилено людиною. {note}"],
                "trajectory": [log_entry("approval_gate", "reject",
                                         action["tool"], note)],
            }

        allowed, limit_note = limiter.record_approval(session)
        if not allowed:
            return {
                "pending_approval": False, "pending_action": None,
                "current_step": state.get("current_step", 0) + 1,
                "results": [f"Дію не виконано: {limit_note}."],
                "trajectory": [log_entry("approval_gate", "limit",
                                         action["tool"], limit_note)],
            }

        final_args = {**final_args, "idempotency_key": action["idem_key"]}
        ok, text = await mcp_tools.call_tool(
            registry, action["tool"], final_args, agent="curator",
            session_id=session, thread_id=state.get("thread_id", ""))

        # Виклик пройшов ≠ дія відбулася: служба могла відмовити за лімітом.
        performed = ok and action_succeeded(action["tool"], text)

        audit.write("risky_action", action["tool"],
                    "approve" if performed else "block",
                    session_id=session, thread_id=state.get("thread_id", ""),
                    agent="curator", detail=text[:200],
                    idem_key=action["idem_key"])

        return {
            "pending_approval": False,
            "pending_action": None,
            "current_step": state.get("current_step", 0) + 1,
            "executed_actions": ([f"{action['tool']}:{action['idem_key']}"]
                                 if performed else []),
            "results": [f"[{action['tool']}] {text}"],
            "tool_calls_count": state.get("tool_calls_count", 0) + 1,
            "trajectory": [log_entry("approval_gate", decision, action["tool"],
                                     text[:200], tools=[action["tool"]])],
        }

    return approval_gate
