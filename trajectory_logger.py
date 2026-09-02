"""Логування траєкторії виконання MAS у JSON.

Перенесено з ДЗ1 (hw1_react_agent/logger.py) і розширено полем agent_name:
при розборі помилки видно, ЯКИЙ САМЕ агент її зробив.

Канарковий токен вирізається перед записом: потрапивши у файл, він зробив би
перевірку на витік системної інструкції безглуздою.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

# Обмеження довжини полів, щоб файл лишався читабельним.
MAX_FIELD_LENGTH = 500

CANARY_PLACEHOLDER = "[CANARY]"


def _strip_canary(text: str, canary: str) -> str:
    """Прибрати канарковий токен з тексту.

    Args:
        text: Текст, який готується до запису.
        canary: Токен поточного прогону; порожній рядок — нічого не робити.

    Returns:
        Текст без токена.
    """
    if not canary:
        return text
    return text.replace(canary, CANARY_PLACEHOLDER)


def log_entry(agent_name: str, node: str, action: str, output: str,
              tools: list | None = None, canary: str = "") -> dict:
    """Скласти один запис траєкторії без накопичувача.

    Потрібно вузлам, які повертають крок через редьюсер, а не тримають об'єкт
    журналу. Кожен запис отримує uid: підграф повертає накопичене батьком разом
    зі своїм, і без ідентифікатора батьківські записи дописувалися б удруге —
    той самий прийом, що add_messages застосовує до повідомлень.

    Args:
        agent_name: Який агент виконував крок.
        node: Ім'я вузла графа.
        action: Що подали на вхід.
        output: Що вийшло.
        tools: Перелік викликаних інструментів.
        canary: Токен прогону для вирізання.

    Returns:
        Запис кроку.
    """
    return {
        "uid": uuid.uuid4().hex[:12],
        "agent_name": agent_name,
        "node": node,
        "action": _strip_canary(str(action), canary)[:MAX_FIELD_LENGTH],
        "output": _strip_canary(str(output), canary)[:MAX_FIELD_LENGTH],
        "tools": tools or [],
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


class TrajectoryLogger:
    """Накопичує кроки виконання та зберігає їх у JSON-файл."""

    def __init__(self) -> None:
        """Створити порожній журнал."""
        self.steps: list[dict] = []
        self.start_time = time.monotonic()
        self.canary = ""

    def set_canary(self, token: str) -> None:
        """Задати токен прогону, який треба вирізати із записів.

        Args:
            token: Канарковий токен.
        """
        self.canary = token

    def log_step(self, step_num: int, agent_name: str, node: str,
                 input_data: str, output_data: str,
                 tool_calls: list | None = None, uid: str = "") -> None:
        """Записати один крок виконання графа.

        Args:
            step_num: Порядковий номер кроку.
            agent_name: Який агент виконував крок — розширення з ДЗ1.
            node: Ім'я вузла графа.
            input_data: Що подали на вхід.
            output_data: Що вийшло.
            tool_calls: Перелік викликаних інструментів.
            uid: Ідентифікатор кроку з траєкторії графа. Порожній рядок —
                згенерувати новий. Переносити наявний важливо: за ним запис
                у файлі звіряється з кроком у стані графа, а новий щоразу
                ідентифікатор робив би таку звірку неможливою.
        """
        self.steps.append({
            "uid": uid or uuid.uuid4().hex[:12],
            "step": step_num,
            "agent_name": agent_name,
            "node": node,
            "input": _strip_canary(str(input_data), self.canary)[:MAX_FIELD_LENGTH],
            "output": _strip_canary(str(output_data), self.canary)[:MAX_FIELD_LENGTH],
            "tool_calls": tool_calls or [],
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "elapsed_ms": int((time.monotonic() - self.start_time) * 1000),
        })

    def save(self, filepath: str, stop_reason: str) -> dict:
        """Зберегти траєкторію у файл і повернути збережену структуру.

        Args:
            filepath: Куди писати.
            stop_reason: Чому робота завершилася.

        Returns:
            Записана структура.
        """
        payload = {
            "total_steps": len(self.steps),
            "total_time_ms": int((time.monotonic() - self.start_time) * 1000),
            "stop_reason": stop_reason,
            "trajectory": self.steps,
        }
        # ensure_ascii=False — інакше кирилиця перетвориться на \uXXXX.
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(_strip_canary(raw, self.canary))
        return payload

    def reset(self) -> None:
        """Очистити накопичені кроки перед новим запитом."""
        self.steps.clear()
        self.start_time = time.monotonic()
