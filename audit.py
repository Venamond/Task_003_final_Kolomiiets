"""Журнал безпеки: що система заборонила і що підтвердила людина.

Ведеться окремо від траєкторії: та потрібна для розбору помилок, цей — для
розбору інцидентів; разом події безпеки потонули б у шумі.

Читання не віддає текст заблокованої атаки: за відповідями аудитора зловмисник
з'ясував би, які прийоми ловляться, і підібрав обхід.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
import logs

log = logs.get_logger("audit")

SAFE_KINDS = frozenset({"guardrail", "hitl", "risky_action", "manifest"})

_PATH: Path | None = None


def reset_path() -> None:
    """Скинути кеш шляху. Потрібно тестам, які підміняють AUDIT_PATH."""
    global _PATH
    _PATH = None


def audit_path() -> Path:
    """Шлях до журналу безпеки. Створює теку при першому зверненні.

    Returns:
        Шлях до файлу журналу.
    """
    global _PATH
    if _PATH is None:
        _PATH = Path(os.getenv("AUDIT_PATH", str(config.AUDIT_PATH)))
        _PATH.parent.mkdir(parents=True, exist_ok=True)
    return _PATH


def write(kind: str, name: str, verdict: str, *, session_id: str,
          thread_id: str = "", agent: str = "", detail: str = "",
          idem_key: str | None = None, canary: str = "") -> dict[str, Any]:
    """Записати подію безпеки.

    Args:
        kind: Рід події з SAFE_KINDS.
        name: Правило або дія, якої стосується подія.
        verdict: allow | block | redact | approve | reject | edit.
        session_id: Сесія, до якої належить подія.
        thread_id: Потік розмови.
        agent: Який агент задіяний.
        detail: Пояснення для людини.
        idem_key: Відбиток ризикової дії, якщо він є.
        canary: Токен прогону — вирізається з усіх текстових полів.

    Returns:
        Записана подія.

    Raises:
        ValueError: Невідомий рід події.
    """
    if kind not in SAFE_KINDS:
        raise ValueError(
            f"Невідомий kind '{kind}', очікується одне з {sorted(SAFE_KINDS)}")

    def clean(value: str) -> str:
        """Прибрати канарку з текстового поля.

        Args:
            value: Текстове поле запису.

        Returns:
            Те саме поле з вирізаною канаркою.
        """
        return value.replace(canary, "[CANARY]") if canary else value

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "thread_id": thread_id,
        "kind": kind,
        "name": clean(name),
        "verdict": verdict,
        "agent": agent,
        "detail": clean(detail),
        "idem_key": idem_key,
    }
    with audit_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read(session_id: str, kind: str = "all", limit: int = 20) -> list[dict[str, Any]]:
    """Прочитати події своєї сесії у безпечному вигляді.

    Для вердикту block пояснення не віддається: людина бачить, що спрацювало,
    але не чим атакували.

    Args:
        session_id: Чия сесія.
        kind: Рід події або "all".
        limit: Скільки останніх записів повернути.

    Returns:
        Перелік подій, найновіші останніми.
    """
    path = audit_path()
    if not path.exists():
        return []

    out: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        # Журнал дописують кілька процесів, тому обірваний рядок можливий.
        # Одна пошкоджена подія не повинна робити недоступним увесь журнал:
        # аудитор має показати решту, а про втрату сказати в діагностику.
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Пошкоджений рядок %d у журналі безпеки, пропущено", number)
            continue
        if record.get("session_id") != session_id:
            continue
        if kind != "all" and record.get("kind") != kind:
            continue
        if record.get("verdict") == "block":
            record = {**record, "detail": ""}
        out.append(record)
    return out[-limit:]
