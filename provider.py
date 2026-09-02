"""Вибір провайдера моделі та резерв при вичерпаній квоті.

Основний провайдер — Anthropic, резервний — Google. Перемикання
відбувається ЛИШЕ на помилці квоти: звичайну помилку маскувати підміною
провайдера не можна, інакше справжня поломка виглядатиме як проблема з
квотою і шукатимуть її не там.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import config

# Клієнт Google пише на кожен виклик багаторядкове попередження про AFC, яке
# не стосується нашого коду і забиває вивід демонстрацій до нечитабельності.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

QUOTA_MARKERS = (
    "rate_limit", "rate limit", "429", "quota", "resource_exhausted",
    "resource exhausted", "insufficient_quota", "overloaded",
)


def make_llm(provider: str | None = None) -> Any:
    """Створити об'єкт чат-моделі потрібного провайдера.

    Args:
        provider: "anthropic" або "google"; за умовчанням — з конфігурації.

    Returns:
        Об'єкт чат-моделі LangChain.

    Raises:
        ValueError: Невідомий провайдер.
    """
    name = (provider or config.LLM_PROVIDER).lower()
    if name == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=config.ANTHROPIC_MODEL, temperature=0.1)
    if name == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.GEMINI_MODEL, temperature=0.1)
    if name == "google_lite":
        # Легша модель Gemini має ОКРЕМИЙ кошик квоти, тому годиться як резерв
        # навіть тоді, коли основна модель того самого провайдера вичерпана.
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.GEMINI_LITE_MODEL, temperature=0.1)
    raise ValueError(f"Невідомий провайдер: {name}")


def text_of(message: Any) -> str:
    """Дістати текст із відповіді моделі.

    Anthropic віддає перелік блоків, Google — рядок.

    Args:
        message: Об'єкт відповіді з полем content.

    Returns:
        Текст відповіді.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    return "".join(parts)


def is_quota_error(exc: BaseException) -> bool:
    """Чи є помилка вичерпаною квотою.

    Args:
        exc: Спійманий виняток.

    Returns:
        True, якщо в тексті помилки є ознака вичерпаної квоти.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in QUOTA_MARKERS)


class FallbackModel:
    """Модель, що сама перемикається на резервну при вичерпаній квоті.

    Повторює виклик резервною моделлю і тільки на помилці квоти: звичайну
    помилку не маскує, інакше справжню поломку шукали б не там.

    Підтримує те, що потрібно графу: ainvoke, bind_tools, with_structured_output.
    Два останні повертають нову обгортку, щоб резерв зберігався і для прив'язаних
    інструментів, і для структурованого виходу.
    """

    def __init__(self, primary: Any, make_fallback: Callable[[], Any]) -> None:
        """Створити обгортку.

        Резерв будується ліниво: інакше відсутній ключ резервного провайдера
        ламав би запуск, хоча основна модель працює і може обійтися без резерву.

        Args:
            primary: Основна модель.
            make_fallback: Функція, що створює резервну модель на вимогу.
        """
        self.primary = primary
        self._make_fallback = make_fallback
        self._fallback: Any = None
        self.fallback_used = 0

    def _fallback_model(self) -> Any:
        """Отримати резервну модель, збудувавши її при першому зверненні.

        Returns:
            Резервна модель.
        """
        if self._fallback is None:
            self._fallback = self._make_fallback()
        return self._fallback

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        """Викликати основну модель, при вичерпаній квоті — резервну.

        Args:
            *args: Позиційні аргументи виклику моделі.
            **kwargs: Іменовані аргументи виклику моделі.

        Returns:
            Відповідь моделі.
        """
        try:
            return await self.primary.ainvoke(*args, **kwargs)
        except BaseException as exc:
            if not is_quota_error(exc):
                raise
            try:
                model = self._fallback_model()
            except Exception:
                # Резерву немає (немає ключа) — піднімаємо первісну помилку
                # квоти, а не помилку побудови резерву.
                raise exc from None
            self.fallback_used += 1
            return await model.ainvoke(*args, **kwargs)

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FallbackModel":
        """Прив'язати інструменти; резерв лишається лінивим.

        Args:
            tools: Інструменти для прив'язки.
            **kwargs: Додаткові параметри прив'язки.

        Returns:
            Нова обгортка з прив'язаними інструментами.
        """
        return FallbackModel(
            self.primary.bind_tools(tools, **kwargs),
            lambda: self._fallback_model().bind_tools(tools, **kwargs))

    def with_structured_output(self, schema: Any, **kwargs: Any) -> "FallbackModel":
        """Задати структурований вихід; резерв лишається лінивим.

        Args:
            schema: Схема відповіді.
            **kwargs: Додаткові параметри.

        Returns:
            Нова обгортка зі структурованим виходом.
        """
        return FallbackModel(
            self.primary.with_structured_output(schema, **kwargs),
            lambda: self._fallback_model().with_structured_output(schema, **kwargs))


def make_llm_with_fallback(primary: str | None = None,
                           fallback: str | None = None) -> FallbackModel:
    """Створити модель із автоматичним резервом за квотою.

    Args:
        primary: Основний провайдер; за умовчанням — з конфігурації.
        fallback: Резервний провайдер; за умовчанням — з конфігурації.

    Returns:
        Обгортка, яку граф використовує як звичайну модель.
    """
    name = fallback or config.LLM_FALLBACK_PROVIDER
    return FallbackModel(make_llm(primary), lambda: make_llm(name))


async def run_with_fallback(fn: Callable[[str], Awaitable[Any]],
                            *args: Any, **kwargs: Any) -> Any:
    """Виконати виклик основним провайдером, при вичерпаній квоті — резервним.

    Args:
        fn: Асинхронна функція, першим аргументом якої є ім'я провайдера.
        *args: Додаткові позиційні аргументи для fn.
        **kwargs: Додаткові іменовані аргументи для fn.

    Returns:
        Результат fn.

    Raises:
        BaseException: Будь-яка помилка, крім вичерпаної квоти, — без підміни.
    """
    try:
        return await fn(config.LLM_PROVIDER, *args, **kwargs)
    except BaseException as exc:
        if not is_quota_error(exc):
            raise
        return await fn(config.LLM_FALLBACK_PROVIDER, *args, **kwargs)
