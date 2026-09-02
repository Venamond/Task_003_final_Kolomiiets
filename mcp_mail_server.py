"""Поштова служба — MCP-сервер для надсилання списків літератури.

Винесена окремо від бібліотеки: надсилання листів — не бібліотечна справа, а
MultiServerMCPClient призначений саме для кількох служб. Розділення дає й різні
права: пошта підключена до всієї системи, але користується нею лише куратор.

Реального надсилання немає, лист пишеться в журнал. Перевірка отримувача та
ідемпотентність справжні — вони й захищають від витоку назовні.

Запуск: python mcp_mail_server.py (транспорт stdio).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import Field

import config
from state_store import StateStore

EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")

_STORE = StateStore()


def reset_state_dir() -> None:
    """Скинути кеш шляху. Потрібно тестам, які підміняють STATE_DIR."""
    _STORE.reset()


def _read_json(name: str, default: Any) -> Any:
    """Прочитати файл стану служби.

    Args:
        name: Ім'я файлу в теці стану.
        default: Що повернути, якщо файлу немає.

    Returns:
        Розібраний вміст або default.
    """
    return _STORE.read(name, default)


def _write_json(name: str, data: Any) -> None:
    """Записати файл стану служби атомарно.

    Args:
        name: Ім'я файлу в теці стану.
        data: Дані для запису.
    """
    _STORE.write(name, data)


def read_mail_log() -> list[dict]:
    """Журнал відправлень.

    Returns:
        Перелік записів про надіслані листи.
    """
    return _read_json("mail_log.json", [])


def _allowed(address: str) -> bool:
    """Чи дозволений домен отримувача.

    Args:
        address: Адреса отримувача.

    Returns:
        True, якщо адреса має правильну форму і її домен у списку дозволених.
    """
    match = EMAIL_RE.match(address.strip())
    return bool(match) and match.group(1).casefold() in {
        d.casefold() for d in config.MAIL_ALLOWED_DOMAINS
    }


def send_reading_list_payload(email: str, items: list[str],
                              idempotency_key: str) -> dict[str, Any]:
    """Надіслати список літератури. РИЗИКОВА ДІЯ: дані виходять назовні.

    Відбиток пишеться до спроби надсилання: краще не надіслати, ніж надіслати
    двічі — лист не відкликати, а повтор означає повторний витік.

    Args:
        email: Адреса отримувача.
        items: Перелік позицій списку.
        idempotency_key: Відбиток дії.

    Returns:
        Словник з sent, message_id і message.
    """
    # Критична секція під блокуванням: «прочитати — змінити — записати»
    # має бути неподільною, бо клієнт піднімає окремий процес на кожен
    # виклик, і два одночасні виклики затирали б зміни один одного.
    with _STORE.locked():
        if not idempotency_key:
            return {"sent": False, "message": "Відмовлено: не передано відбиток ідемпотентності"}

        previous = _STORE.idem_lookup("mail_idem.json", idempotency_key)
        if previous is not None:
            if previous["status"] == "done":
                return previous["result"]
            return {"sent": False,
                    "message": ("Відмовлено: попередня спроба з тим самим відбитком "
                                "не завершилась. Повторне надсилання заборонене.")}

        if not _allowed(email):
            return {"sent": False,
                    "message": (f"Відмовлено: домен отримувача не дозволений. "
                                f"Дозволені: {', '.join(config.MAIL_ALLOWED_DOMAINS)}")}
        if not items:
            return {"sent": False, "message": "Відмовлено: порожній список літератури"}

        _STORE.idem_begin("mail_idem.json", idempotency_key)

        message_id = f"MSG-{idempotency_key[:8].upper()}"
        entries = read_mail_log()
        entries.append({"message_id": message_id, "email": email, "items": items,
                        "ts": datetime.now(timezone.utc).isoformat()})
        _write_json("mail_log.json", entries)

        result = {"sent": True, "message_id": message_id,
                  "message": f"Список з {len(items)} позицій надіслано на {email}"}
        _STORE.idem_finish("mail_idem.json", idempotency_key, result)
        return result


# ── MCP протокольний шар ──────────────────────────────────────────────────────

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="philosophy_mail",
    instructions="Поштова служба: надсилання списків літератури читачеві.",
    log_level="WARNING",
)


@mcp.tool()
def send_reading_list(
    email: Annotated[str, Field(
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Адреса отримувача; домен має бути в переліку дозволених.")],
    items: Annotated[list[str], Field(
        min_length=1, max_length=50,
        description="Перелік позицій списку літератури, від 1 до 50.")],
    idempotency_key: Annotated[str, Field(
        default="", max_length=128,
        description=("Відбиток дії, який ПІДСТАВЛЯЄ ХОСТ-ЗАСТОСУНОК. "
                     "Не вигадуйте його: залиште порожнім.")) ] = "",
) -> str:
    """Надіслати читачеві список літератури поштою. РИЗИКОВА, НЕЗВОРОТНА ДІЯ.

    Лист неможливо відкликати, тому виклик зупиняє агента і вимагає
    підтвердження людини. Адресу отримувача редагувати заборонено.

    Приклад: send_reading_list(email="reader@example.com",
    items=["Роздуми — Марк Аврелій"], idempotency_key="c4d8e2f0").

    Args:
        email: Адреса отримувача; домен має бути в переліку дозволених.
        items: Перелік позицій списку літератури.
        idempotency_key: Відбиток дії, який ПІДСТАВЛЯЄ ХОСТ-ЗАСТОСУНОК.
            Не вигадуйте його: залиште порожнім, система підставить свій.

    Returns:
        JSON-рядок з sent, message_id і message.
    """
    return json.dumps(send_reading_list_payload(email, items, idempotency_key),
                      ensure_ascii=False)


@mcp.resource("policy://mail")
def mail_policy() -> str:
    """Регламент надсилання: дозволені домени отримувача і правила.

    Returns:
        Текст регламенту українською.
    """
    return config.mail_policy_text()


if __name__ == "__main__":
    mcp.run(transport="stdio")
