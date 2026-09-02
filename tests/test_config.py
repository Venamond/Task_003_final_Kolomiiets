"""Тести шару конфігурації: межі читаються з оточення, а не зашиті в код."""
from __future__ import annotations

import importlib

import pytest

import config


@pytest.fixture(autouse=True)
def restore_config():
    """Після кожного тесту повертаємо модуль до стану з чистого оточення."""
    yield
    importlib.reload(config)


def test_defaults_match_spec():
    """Умовчання зі специфікації: 60 EUR на місяць, 3 примірники."""
    assert config.BUDGET_MONTHLY_EUR == 60.0
    assert config.MAX_COPIES_PER_ORDER == 3


def test_env_overrides_float(monkeypatch):
    """Значення з оточення перекриває умовчання."""
    monkeypatch.setenv("BUDGET_MONTHLY_EUR", "99.5")
    importlib.reload(config)
    assert config.BUDGET_MONTHLY_EUR == 99.5


def test_env_overrides_int(monkeypatch):
    """Цілочисельна межа теж читається з оточення."""
    monkeypatch.setenv("MAX_COPIES_PER_ORDER", "7")
    importlib.reload(config)
    assert config.MAX_COPIES_PER_ORDER == 7


def test_env_overrides_list(monkeypatch):
    """Списки задаються через кому."""
    monkeypatch.setenv("MAIL_ALLOWED_DOMAINS", "a.org, b.net")
    importlib.reload(config)
    assert config.MAIL_ALLOWED_DOMAINS == ["a.org", "b.net"]


def test_nonsense_value_is_rejected(monkeypatch):
    """Від'ємний бюджет — не конфігурація, а помилка. Падаємо одразу."""
    monkeypatch.setenv("BUDGET_MONTHLY_EUR", "-5")
    with pytest.raises(ValueError, match="BUDGET_MONTHLY_EUR"):
        importlib.reload(config)


def test_unparsable_value_is_rejected(monkeypatch):
    """Нечислове значення там, де очікується число, — теж помилка."""
    monkeypatch.setenv("MAX_COPIES_PER_ORDER", "три")
    with pytest.raises(ValueError, match="MAX_COPIES_PER_ORDER"):
        importlib.reload(config)


def test_budget_policy_text_contains_the_numbers():
    """Текст регламенту будується з констант — розійтися вони не можуть."""
    text = config.budget_policy_text()
    assert "60" in text
    assert "3" in text


def test_mail_policy_text_lists_allowed_domains():
    """Текст поштового регламенту перелічує саме дозволені домени."""
    text = config.mail_policy_text()
    for domain in config.MAIL_ALLOWED_DOMAINS:
        assert domain in text


def test_state_dir_is_created():
    """Каталог стану існує після імпорту — далі пишуть без перевірок."""
    assert config.STATE_DIR.is_dir()


def test_all_limits_are_positive():
    """Жодна межа не може бути нулем чи від'ємною."""
    for name in ("MAX_INPUT_LEN", "MAX_OUTPUT_LEN", "MAX_STEPS_PER_AGENT",
                 "MAX_HOPS", "MAX_LLM_CALLS_PER_RUN", "RATE_LIMIT_MAX_CALLS"):
        assert getattr(config, name) > 0, name
