"""Тести спільного сховища стану.

Головне тут — атомарність запису. Без неї падіння посеред запису лишало б
обрізаний файл, а для журналу відбитків це означало б втрату гарантії,
що ризикова дія не виконається двічі.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Сховище у власній тимчасовій теці.

    Args:
        tmp_path: Тимчасова тека тесту.
        monkeypatch: Підмінник змінних оточення.

    Returns:
        Сховище у власній тимчасовій теці.
    """
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    s = state_store.StateStore()
    s.reset()
    return s


def test_read_returns_default_when_missing(store):
    """Відсутній файл дає значення за умовчанням, а не падіння."""
    assert store.read("немає.json", {"порожньо": True}) == {"порожньо": True}


def test_write_then_read_roundtrip(store):
    """Записане читається без втрат, кирилиця не перетворюється на \\uXXXX."""
    store.write("дані.json", {"назва": "Роздуми", "ціна": 18.5})
    assert store.read("дані.json", {}) == {"назва": "Роздуми", "ціна": 18.5}
    raw = (store.directory() / "дані.json").read_text(encoding="utf-8")
    assert "Роздуми" in raw


def test_write_leaves_no_temporary_files(store):
    """Після запису тимчасових файлів не лишається."""
    store.write("дані.json", {"a": 1})
    assert not list(store.directory().glob("*.tmp"))


def test_failed_write_does_not_corrupt_existing_file(store, monkeypatch):
    """Падіння посеред запису лишає старий вміст цілим."""
    store.write("дані.json", {"версія": "стара"})

    def boom(*args, **kwargs):
        """Підміна os.replace, що падає посеред запису.

        Args:
            *args: Аргументи справжнього os.replace.
            **kwargs: Іменовані аргументи справжнього os.replace.

        Raises:
            OSError: Завжди.
        """
        raise OSError("диск закінчився")

    monkeypatch.setattr(state_store.os, "replace", boom)
    with pytest.raises(OSError):
        store.write("дані.json", {"версія": "нова"})

    assert store.read("дані.json", {}) == {"версія": "стара"}
    assert not list(store.directory().glob("*.tmp"))


def test_idempotency_marks_before_execution(store):
    """Відбиток позначається розпочатим до виконання."""
    store.idem_begin("idem.json", "k1")
    assert store.idem_lookup("idem.json", "k1")["status"] == "in_progress"


def test_idempotency_finish_replaces_mark(store):
    """Підсумок записується поверх позначки про початок."""
    store.idem_begin("idem.json", "k1")
    store.idem_finish("idem.json", "k1", {"ordered": True})
    entry = store.idem_lookup("idem.json", "k1")
    assert entry["status"] == "done" and entry["result"] == {"ordered": True}


def test_unknown_key_has_no_entry(store):
    """Невідомий відбиток нічого не повертає."""
    assert store.idem_lookup("idem.json", "невідомий") is None


def test_reset_picks_up_new_directory(tmp_path, monkeypatch):
    """Після скидання сховище бачить нову теку."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "перша"))
    s = state_store.StateStore()
    first = s.directory()
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "друга"))
    s.reset()
    assert s.directory() != first


WORKER = """
import os, sys
os.environ["STATE_DIR"] = sys.argv[1]
sys.path.insert(0, sys.argv[2])
import state_store
store = state_store.StateStore()
for _ in range(60):
    with store.locked():
        data = store.read("лічильник.json", {"n": 0})
        data["n"] += 1
        store.write("лічильник.json", data)
"""


def test_lock_serialises_read_modify_write(tmp_path, monkeypatch):
    """Блокування не дає паралельним процесам загубити оновлення."""
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    running = [subprocess.Popen([sys.executable, "-c", WORKER, str(tmp_path), root])
               for _ in range(4)]
    for process in running:
        assert process.wait(timeout=120) == 0

    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    assert state_store.StateStore().read("лічильник.json", {"n": 0})["n"] == 240
