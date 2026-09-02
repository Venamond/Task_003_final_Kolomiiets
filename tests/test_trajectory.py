"""Тести журналу траєкторії.

Головне, що тут перевіряється, крім самого запису — що канарковий токен
у файл не потрапляє. Інакше ми публікуємо його самі, і перевірка на витік
системної інструкції перестає щось означати.
"""
from __future__ import annotations

import json

import trajectory_logger as tl


def test_every_step_has_agent_name():
    """Поле agent_name присутнє в КОЖНОМУ кроці — вимога завдання."""
    log = tl.TrajectoryLogger()
    log.log_step(1, "supervisor", "route", "запит", "→ curator")
    log.log_step(2, "curator", "planner", "мета", "план з 3 кроків")
    assert all("agent_name" in s for s in log.steps)
    assert [s["agent_name"] for s in log.steps] == ["supervisor", "curator"]


def test_fields_are_truncated():
    """Довгі поля обрізаються, щоб файл лишався читабельним."""
    log = tl.TrajectoryLogger()
    log.log_step(1, "curator", "act", "я" * 5000, "б" * 5000)
    assert len(log.steps[0]["input"]) == tl.MAX_FIELD_LENGTH
    assert len(log.steps[0]["output"]) == tl.MAX_FIELD_LENGTH


def test_canary_is_stripped_from_step():
    """Канарка не потрапляє в запис кроку."""
    log = tl.TrajectoryLogger()
    log.set_canary("CANARY-abc123")
    log.log_step(1, "curator", "act", "текст із CANARY-abc123 усередині", "ок")
    assert "CANARY-abc123" not in json.dumps(log.steps, ensure_ascii=False)


def test_canary_is_stripped_from_saved_file(tmp_path):
    """Канарка не потрапляє й у збережений файл."""
    log = tl.TrajectoryLogger()
    log.set_canary("CANARY-xyz")
    log.log_step(1, "curator", "act", "вхід", "вихід із CANARY-xyz")
    path = tmp_path / "t.json"
    log.save(str(path), "completed")
    assert "CANARY-xyz" not in path.read_text(encoding="utf-8")


def test_save_writes_summary(tmp_path):
    """У файлі є підсумок: кількість кроків, час і причина зупинки."""
    log = tl.TrajectoryLogger()
    log.log_step(1, "general", "answer", "привіт", "вітаю")
    path = tmp_path / "t.json"
    payload = log.save(str(path), "completed")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["total_steps"] == 1
    assert saved["stop_reason"] == "completed"
    assert payload["trajectory"][0]["agent_name"] == "general"


def test_reset_clears_steps():
    """Скидання очищає накопичене перед новим запитом."""
    log = tl.TrajectoryLogger()
    log.log_step(1, "general", "answer", "а", "б")
    log.reset()
    assert log.steps == []


def test_every_step_has_a_unique_id():
    """Кожен крок має власний uid — на ньому тримається merge_by_uid."""
    log = tl.TrajectoryLogger()
    for i in range(5):
        log.log_step(i, "curator", "act", "той самий вхід", "той самий вихід")
    uids = [s["uid"] for s in log.steps]
    assert len(set(uids)) == 5


def test_module_level_entry_also_has_an_id():
    """Запис, зроблений без накопичувача, теж отримує ідентифікатор."""
    entry = tl.log_entry("supervisor", "route", "запит", "→ curator")
    assert "uid" in entry and len(entry["uid"]) == 12


def test_log_step_keeps_the_uid_from_the_graph():
    """Ідентифікатор кроку переноситься з траєкторії графа, а не вигадується."""
    logger = tl.TrajectoryLogger()
    logger.log_step(1, "supervisor", "route", "запит", "→ куратор", [], "abc123")
    assert logger.steps[0]["uid"] == "abc123"


def test_log_step_generates_uid_when_absent():
    """Без переданого ідентифікатора крок отримує власний."""
    logger = tl.TrajectoryLogger()
    logger.log_step(1, "supervisor", "route", "запит", "→ куратор")
    assert len(logger.steps[0]["uid"]) == 12
