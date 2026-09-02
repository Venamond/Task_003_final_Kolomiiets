"""Тести обмежувача частоти.

Головна відмінність від прикладу завдання: стан лежить на диску, а не в
пам'яті. Демонстрація персистентності навмисно вбиває процес між
перериванням і відновленням, а атака «втома підтверджувача» рахує
підтвердження через кілька переривань. Лічильник у пам'яті обнулився б
рівно тоді, коли він потрібен.
"""
from __future__ import annotations

import pytest

import config
import guardrails as g


@pytest.fixture(autouse=True)
def isolated_limits(tmp_path, monkeypatch):
    """Кожен тест веде власний файл лічильників.

    Args:
        tmp_path: Тимчасова тека тесту.
        monkeypatch: Підмінник змінних оточення.
    """
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    yield


def test_allows_within_window():
    """До межі виклики проходять."""
    rl = g.RateLimiter(max_calls=3, window_sec=60)
    for _ in range(3):
        assert rl.check("s1")[0] is True


def test_blocks_over_window():
    """Понад межу — відмова."""
    rl = g.RateLimiter(max_calls=3, window_sec=60)
    for _ in range(3):
        rl.check("s1")
    ok, reason = rl.check("s1")
    assert ok is False and "3" in reason


def test_sessions_are_independent():
    """Інша сесія має власний лічильник."""
    rl = g.RateLimiter(max_calls=1, window_sec=60)
    rl.check("s1")
    assert rl.check("s2")[0] is True


def test_window_slides():
    """Старі виклики випадають із вікна."""
    import time as _t
    rl = g.RateLimiter(max_calls=2, window_sec=0.05)
    rl.check("s1")
    rl.check("s1")
    _t.sleep(0.1)
    assert rl.check("s1")[0] is True


def test_state_survives_new_instance():
    """Новий об'єкт бачить лічильник попереднього — стан на диску."""
    first = g.RateLimiter(max_calls=2, window_sec=60)
    first.check("s1")
    first.check("s1")
    second = g.RateLimiter(max_calls=2, window_sec=60)
    assert second.check("s1")[0] is False


def test_approval_limit_survives_new_instance():
    """Лічильник підтверджень теж переживає перезапуск — це атака 15."""
    first = g.RateLimiter()
    for _ in range(config.MAX_APPROVALS_PER_SESSION):
        assert first.record_approval("s1")[0] is True
    second = g.RateLimiter()
    assert second.record_approval("s1")[0] is False


def test_reset_clears_session():
    """Скидання очищає лічильники сесії."""
    rl = g.RateLimiter(max_calls=1, window_sec=60)
    rl.check("s1")
    rl.reset("s1")
    assert rl.check("s1")[0] is True


def test_corrupted_counter_file_does_not_break_the_request(tmp_path):
    """Пошкоджений файл лічильників не валить запит, а починає рахунок з нуля."""
    (tmp_path / g.LIMITS_FILE).write_text("{обірваний рядок", encoding="utf-8")
    allowed, reason = g.RateLimiter().check("сесія")
    assert allowed is True
    assert "1/" in reason
