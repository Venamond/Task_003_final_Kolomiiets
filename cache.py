"""Спільний HTTP-клієнт для зовнішніх API з файловим кешем.

Перенесено з ДЗ1 (hw1_react_agent/cache.py). Додано список дозволених вузлів.

Кеш: Wikidata SPARQL відповідає 1-3 секунди, і без кешу прогін упирається у
власний таймаут, а результати змінюються від запуску до запуску.

Таймаут на кожен запит: загальний таймаут агента перевіряється лише між кроками
графа, і завислий HTTP-запит його обійшов би.

Список дозволених вузлів: перевірка стоїть перед мережевим викликом, бо саме на
чужому вузлі ін'єкція вивела б дані назовні.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import urlparse

import httpx

import config

# Wikidata вимагає змістовний User-Agent.
USER_AGENT = "hw3-mas-philosophy/1.0 (educational project)"


class EgressDenied(RuntimeError):
    """Спроба звернутися до вузла поза списком дозволених."""


def host_allowed(url: str) -> bool:
    """Чи дозволено звертатися за цією адресою.

    Порівнюємо ім'я вузла повністю, а не підрядком: gutendex.com.attacker.tld
    містить дозволене ім'я, але веде не туди. Схема має бути https.

    Args:
        url: Повна адреса запиту.

    Returns:
        True, якщо схема https і ім'я вузла є у списку дозволених.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    return parsed.hostname in set(config.EGRESS_ALLOWED_HOSTS)


class HttpClient:
    """GET-клієнт із файловим кешем відповідей і перевіркою вузла."""

    def __init__(self, cache_dir: str = ".cache", use_cache: bool = True,
                 timeout: float = 10.0) -> None:
        """Створити клієнт.

        Args:
            cache_dir: Тека файлового кешу.
            use_cache: Чи користуватися кешем.
            timeout: Обмеження на ОДИН запит, у секундах.
        """
        self.cache_dir = cache_dir
        self.use_cache = use_cache
        self.timeout = timeout
        if self.use_cache:
            os.makedirs(self.cache_dir, exist_ok=True)

    def _cache_path(self, url: str, params: dict) -> str:
        """Шлях до файлу кешу: хеш від адреси та відсортованих параметрів.

        Args:
            url: Повна адреса запиту.
            params: Параметри рядка запиту.

        Returns:
            Шлях до файлу кешу.
        """
        raw = f"{url}?{sorted(params.items())}"
        digest = hashlib.md5(raw.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{digest}.json")

    def _fetch(self, url: str, params: dict) -> dict[str, Any]:
        """Реальний мережевий виклик. У тестах підміняється.

        Args:
            url: Повна адреса запиту.
            params: Параметри рядка запиту.

        Returns:
            Розібрана відповідь служби.
        """
        response = httpx.get(
            url, params=params, timeout=self.timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=False,
        )
        response.raise_for_status()
        return response.json()

    def get_json(self, url: str, params: dict) -> dict[str, Any]:
        """Отримати JSON за адресою, за можливості — з кешу.

        Args:
            url: Повна адреса запиту.
            params: Параметри рядка запиту.

        Returns:
            Розібрана відповідь.

        Raises:
            EgressDenied: Вузол поза списком дозволених.
        """
        if not host_allowed(url):
            raise EgressDenied(f"Вузол поза списком дозволених: {url}")

        if not self.use_cache:
            return self._fetch(url, params)

        path = self._cache_path(url, params)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)

        data = self._fetch(url, params)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
