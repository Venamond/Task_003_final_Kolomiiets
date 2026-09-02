"""Тести списку дозволених вузлів.

Якщо ін'єкція змусить агента піти на чужий вузол, дані втечуть саме там.
Тому перевіряється не тільки те, що дозволене проходить, а передусім те,
що недозволене не проходить — і що воно падає ДО мережевого виклику.
"""
from __future__ import annotations

import pytest

import cache
import config


def test_allowed_hosts_from_config_pass():
    """Кожен вузол зі списку конфігурації дозволений."""
    for host in config.EGRESS_ALLOWED_HOSTS:
        assert cache.host_allowed(f"https://{host}/api")


def test_foreign_host_is_denied():
    """Сторонній вузол не дозволений."""
    assert not cache.host_allowed("https://attacker.evil.tld/collect")


def test_subdomain_of_allowed_is_denied():
    """Піддомен дозволеного вузла не дозволений: збіг імені повний."""
    assert not cache.host_allowed("https://gutendex.com.attacker.tld/x")


def test_http_scheme_is_denied():
    """Незашифрований протокол не дозволений навіть для дозволеного вузла."""
    assert not cache.host_allowed("http://gutendex.com/books")


def test_get_json_refuses_foreign_host_without_network(monkeypatch):
    """Заборонений вузол падає ДО мережевого виклику, а не після."""
    called = []
    monkeypatch.setattr(cache.HttpClient, "_fetch",
                        lambda self, url, params: called.append(url))
    client = cache.HttpClient(use_cache=False)
    with pytest.raises(cache.EgressDenied):
        client.get_json("https://attacker.evil.tld/collect", {})
    assert called == []


def test_get_json_allows_permitted_host(monkeypatch, tmp_path):
    """Дозволений вузол доходить до виклику."""
    monkeypatch.setattr(cache.HttpClient, "_fetch",
                        lambda self, url, params: {"ok": True})
    client = cache.HttpClient(cache_dir=str(tmp_path), use_cache=False)
    assert client.get_json("https://gutendex.com/books", {}) == {"ok": True}


def test_mcp_paths_do_not_depend_on_current_directory(tmp_path, monkeypatch):
    """Шляхи до служб рахуються від файлу модуля, а не від каталогу запуску."""
    import mcp_tools
    monkeypatch.chdir(tmp_path)
    for server in mcp_tools.MCP_CONFIG.values():
        path = server["args"][0]
        assert __import__("pathlib").Path(path).is_file(), path
