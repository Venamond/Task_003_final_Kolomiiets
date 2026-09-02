"""Спільний механізм зберігання стану для служб MCP.

Тут лише механізм: як покласти JSON на диск і не втратити його при падінні.
Домен — каталог, бюджет, правила замовлення — лишається в кожній службі власний.

Ізоляція не страждає: це бібліотека, а не спільний рантайм, у кожної служби свій
процес і свої файли. Спільний модуль потрібен, щоб не писати атомарний запис
двічі й не виправляти потім лише одну копію.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import config


class StateStore:
    """Файлове сховище стану однієї служби MCP."""

    def __init__(self, env_var: str = "STATE_DIR") -> None:
        """Створити сховище.

        Args:
            env_var: Змінна оточення зі шляхом до теки стану.
        """
        self._env_var = env_var
        self._dir: Path | None = None

    def reset(self) -> None:
        """Скинути кеш шляху. Потрібно тестам, які підміняють теку стану."""
        self._dir = None

    def directory(self) -> Path:
        """Тека стану. Створює її при першому зверненні й кешує шлях.

        Returns:
            Тека стану.
        """
        if self._dir is None:
            self._dir = Path(os.getenv(self._env_var, str(config.STATE_DIR)))
            self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Взяти виключне блокування на теку стану.

        Атомарний запис рятує від обрізаного файлу, але не від втраченого
        оновлення: клієнт піднімає окремий процес на кожен виклик, тому два
        одночасні замовлення читають однаковий бюджет і друге затирає перше.
        Тим самим обходився б і журнал відбитків. Блокування робить
        «прочитати — змінити — записати» неподільним між процесами.

        Yields:
            Керування, поки блокування утримується.
        """
        path = self.directory() / ".lock"
        with path.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self, name: str, default: Any) -> Any:
        """Прочитати файл стану.

        Args:
            name: Ім'я файлу всередині теки стану.
            default: Що повернути, якщо файлу немає.

        Returns:
            Розібраний вміст або default.
        """
        path = self.directory() / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, name: str, data: Any) -> None:
        """Записати файл стану АТОМАРНО.

        Пишемо в тимчасовий файл поруч і перейменовуємо: у межах однієї
        файлової системи це атомарно, тому читач бачить або старий вміст, або
        новий, але не обрізаний. Інакше падіння посеред запису псувало б журнал
        відбитків — той, що не дає виконати ризикову дію двічі.

        Args:
            name: Ім'я файлу всередині теки стану.
            data: Дані для запису.
        """
        directory = self.directory()
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        handle, temp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, directory / name)
        except BaseException:
            Path(temp_path).unlink(missing_ok=True)
            raise

    # ── Журнал відбитків ─────────────────────────────────────────────────────
    # Відбиток пишеться до спроби, а не після: подвійне списання гірше за
    # несписання, а лист узагалі не відкликати.

    def idem_lookup(self, name: str, key: str) -> dict[str, Any] | None:
        """Знайти результат попереднього виконання за відбитком.

        Args:
            name: Ім'я файлу журналу відбитків.
            key: Відбиток дії.

        Returns:
            Збережений результат, запис про незавершену спробу, або None.
        """
        return self.read(name, {}).get(key)

    def idem_begin(self, name: str, key: str) -> None:
        """Позначити відбиток як розпочатий ДО спроби виконання.

        Args:
            name: Ім'я файлу журналу відбитків.
            key: Відбиток дії.
        """
        log = self.read(name, {})
        log[key] = {"status": "in_progress",
                    "ts": datetime.now(timezone.utc).isoformat()}
        self.write(name, log)

    def idem_finish(self, name: str, key: str, result: dict[str, Any]) -> None:
        """Записати підсумок виконання поверх позначки про початок.

        Args:
            name: Ім'я файлу журналу відбитків.
            key: Відбиток дії.
            result: Результат, який повернеться при повторному зверненні.
        """
        log = self.read(name, {})
        log[key] = {"status": "done", "result": result,
                    "ts": datetime.now(timezone.utc).isoformat()}
        self.write(name, log)
