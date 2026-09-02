"""Журнал діагностики: збої і деградації, які не є подіями безпеки.

Відрізняється від audit.jsonl за призначенням. Аудит відповідає на питання
«що система заборонила»; цей журнал — на питання «де вона працювала гірше,
ніж мала». Пошкоджений файл лічильників, відмова служби бюджету, збій запису
стану — усе це не інциденти безпеки, але мовчати про них не можна.

Рівень береться з LOG_LEVEL, за умовчанням WARNING: у звичайному прогоні
журнал мовчить і не заважає читати вивід демонстрацій.
"""
from __future__ import annotations

import logging
import sys

import config

_ROOT = "mas"
_configured = False


def get_logger(name: str) -> logging.Logger:
    """Отримати логер модуля, налаштувавши кореневий при першому зверненні.

    Args:
        name: Ім'я модуля без спільного префікса.

    Returns:
        Логер, який пише у stderr.
    """
    global _configured
    if not _configured:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger(_ROOT)
        root.addHandler(handler)
        root.setLevel(config.LOG_LEVEL)
        root.propagate = False
        _configured = True
    return logging.getLogger(f"{_ROOT}.{name}")
