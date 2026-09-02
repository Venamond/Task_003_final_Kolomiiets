"""Зліпок схем служб MCP і його звірка.

Захист від rug-pull: служба тихо змінює опис дії після того, як клієнт її
схвалив, а опис читає модель. При підключенні звіряємо імена дій, довідників,
заготовок і хеші схем із закріпленим зліпком.

Шлях до зліпка береться з config.MCP_MANIFEST_PATH, тобто зі змінної оточення.
Саме цим підміняється зліпок у тестах і в перевірці атаками.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import config


class ManifestMismatch(RuntimeError):
    """Схеми служби розійшлися зі зліпком — підключатися не можна."""


def _schema_hash(payload: Any) -> str:
    """Хеш схеми дії: опис і параметри разом.

    Args:
        payload: Будь-яка серіалізовна структура.

    Returns:
        sha256 у шістнадцятковому вигляді.
    """
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def build_snapshot(server_name: str, server: Any) -> dict[str, Any]:
    """Зняти зліпок однієї служби.

    Args:
        server_name: Ім'я служби у зліпку.
        server: Об'єкт FastMCP.

    Returns:
        Словник з tools (ім'я → хеш), resources і prompts (списки імен).
    """
    tools = {
        tool.name: _schema_hash({"description": tool.description,
                                 "schema": tool.inputSchema})
        for tool in await server.list_tools()
    }
    resources = sorted(str(r.uri) for r in await server.list_resources())
    prompts = sorted(p.name for p in await server.list_prompts())
    return {"name": server_name, "tools": tools,
            "resources": resources, "prompts": prompts}


async def build_manifest() -> dict[str, Any]:
    """Зняти зліпок обох служб.

    Returns:
        Словник {"library": ..., "mail": ...}.
    """
    import mcp_mail_server
    import mcp_server
    return {
        "library": await build_snapshot("library", mcp_server.mcp),
        "mail": await build_snapshot("mail", mcp_mail_server.mcp),
    }


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Прочитати збережений зліпок.

    Args:
        path: Шлях до файлу; за умовчанням — з конфігурації.

    Returns:
        Розібраний зліпок.

    Raises:
        ManifestMismatch: Файл зліпка відсутній.
    """
    target = path or config.MCP_MANIFEST_PATH
    if not Path(target).exists():
        raise ManifestMismatch(f"Зліпок {target} не знайдено")
    return json.loads(Path(target).read_text(encoding="utf-8"))


async def verify_manifest(path: Path | None = None) -> tuple[bool, list[str]]:
    """Звірити поточні служби зі збереженим зліпком.

    Args:
        path: Шлях до файлу зліпка; за умовчанням — з конфігурації.

    Returns:
        Пара (чи збіглося, перелік розходжень українською).
    """
    saved = load_manifest(path)
    current = await build_manifest()
    problems: list[str] = []

    for server in sorted(set(saved) | set(current)):
        if server not in saved:
            problems.append(f"{server}: служба відсутня у зліпку")
            continue
        if server not in current:
            problems.append(f"{server}: служба зникла")
            continue

        saved_tools, current_tools = saved[server]["tools"], current[server]["tools"]
        for name in sorted(set(saved_tools) | set(current_tools)):
            if name not in saved_tools:
                problems.append(f"{server}.{name}: дія відсутня у зліпку")
            elif name not in current_tools:
                problems.append(f"{server}.{name}: дія зникла зі служби")
            elif saved_tools[name] != current_tools[name]:
                problems.append(f"{server}.{name}: змінено опис або схему дії")

        for kind in ("resources", "prompts"):
            if saved[server][kind] != current[server][kind]:
                problems.append(f"{server}: перелік {kind} розійшовся зі зліпком")

    return (not problems), problems


if __name__ == "__main__":
    import asyncio

    manifest = asyncio.run(build_manifest())
    Path(config.MCP_MANIFEST_PATH).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Зліпок записано у {config.MCP_MANIFEST_PATH}")
