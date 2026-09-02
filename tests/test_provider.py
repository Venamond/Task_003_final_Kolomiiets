"""Тести провайдера моделі. Жодного мережевого виклику."""
from __future__ import annotations

import pytest

import provider


def test_is_quota_error_detects_rate_limit():
    """Помилка вичерпаної квоти розпізнається за текстом."""
    assert provider.is_quota_error(Exception("429 rate_limit_error: quota exceeded"))


def test_is_quota_error_detects_resource_exhausted():
    """Формулювання Google теж розпізнається."""
    assert provider.is_quota_error(Exception("RESOURCE_EXHAUSTED"))


def test_is_quota_error_ignores_other_errors():
    """Звичайна помилка не приймається за вичерпану квоту."""
    assert not provider.is_quota_error(ValueError("щось інше пішло не так"))


def test_text_of_plain_string_content():
    """Відповідь із рядковим вмістом віддає сам рядок."""
    class Msg:
        """Заглушка відповіді моделі з рядковим вмістом."""

        content = "відповідь"
    assert provider.text_of(Msg()) == "відповідь"


def test_text_of_block_list_content():
    """Відповідь із переліком блоків склеюється в текст."""
    class Msg:
        """Заглушка відповіді моделі з переліком блоків."""

        content = [{"type": "text", "text": "а"}, {"type": "text", "text": "б"}]
    assert provider.text_of(Msg()) == "аб"


async def test_run_with_fallback_switches_on_quota():
    """При вичерпаній квоті виклик повторюється резервним провайдером."""
    calls = []

    async def flaky(provider_name: str) -> str:
        """Падає на основному провайдері, спрацьовує на резервному.

        Args:
            provider_name: Ім'я провайдера, з яким прийшов виклик.

        Returns:
            Рядок «готово» для резервного провайдера.

        Raises:
            Exception: Помилка квоти для основного провайдера.
        """
        calls.append(provider_name)
        if provider_name == "anthropic":
            raise Exception("429 rate_limit_error")
        return "готово"

    result = await provider.run_with_fallback(flaky)
    assert result == "готово"
    assert calls == ["anthropic", "google"]


async def test_run_with_fallback_reraises_other_errors():
    """Звичайна помилка не маскується підміною провайдера."""
    async def broken(provider_name: str) -> str:
        """Падає звичайною помилкою, не пов'язаною з квотою.

        Args:
            provider_name: Ім'я провайдера.

        Raises:
            ValueError: Завжди.
        """
        raise ValueError("справжня помилка")

    with pytest.raises(ValueError, match="справжня помилка"):
        await provider.run_with_fallback(broken)


# ── Обгортка з резервом ───────────────────────────────────────────────────────

class _Stub:
    """Підставна модель, що падає задану кількість разів."""

    def __init__(self, fail_times: int = 0, error: Exception | None = None,
                 label: str = "ok") -> None:
        """Створити заглушку.

        Args:
            fail_times: Скільки разів упасти перед успіхом.
            error: Виняток, яким падати.
            label: Мітка, яку повертати при успіху.
        """
        self.fail_times = fail_times
        self.error = error or Exception("429 rate_limit_error")
        self.label = label
        self.calls = 0

    async def ainvoke(self, *args, **kwargs):
        """Повернути мітку або впасти, якщо ще лишилися падіння.

        Args:
            *args: Позиційні аргументи.
            **kwargs: Іменовані аргументи.

        Returns:
            Мітка заглушки.

        Raises:
            Exception: Поки лишаються заплановані падіння.
        """
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.error
        return self.label

    def bind_tools(self, tools, **kwargs):
        """Повернути себе — заглушці нема що прив'язувати.

        Args:
            tools: Інструменти — заглушка їх не використовує.
            **kwargs: Решта аргументів.

        Returns:
            Сама заглушка.
        """
        return self

    def with_structured_output(self, schema, **kwargs):
        """Повернути себе.

        Args:
            schema: Схема — заглушка її не використовує.
            **kwargs: Решта аргументів.

        Returns:
            Сама заглушка.
        """
        return self


async def test_fallback_model_uses_primary_when_it_works():
    """Поки основна модель відповідає, резерв не задіюється."""
    primary, fallback = _Stub(label="основна"), _Stub(label="резервна")
    model = provider.FallbackModel(primary, lambda: fallback)
    assert await model.ainvoke("що завгодно") == "основна"
    assert fallback.calls == 0


async def test_fallback_model_switches_on_quota():
    """Вичерпана квота перемикає на резервну модель."""
    primary, fallback = _Stub(fail_times=1), _Stub(label="резервна")
    model = provider.FallbackModel(primary, lambda: fallback)
    assert await model.ainvoke("що завгодно") == "резервна"
    assert model.fallback_used == 1


async def test_fallback_model_does_not_mask_other_errors():
    """Звичайна помилка не маскується резервом."""
    primary = _Stub(fail_times=1, error=ValueError("справжня помилка"))
    fallback = _Stub(label="резервна")
    model = provider.FallbackModel(primary, lambda: fallback)
    with pytest.raises(ValueError, match="справжня помилка"):
        await model.ainvoke("що завгодно")
    assert fallback.calls == 0


def test_fallback_survives_bind_tools():
    """Резерв зберігається і для моделі з прив'язаними інструментами."""
    model = provider.FallbackModel(_Stub(), lambda: _Stub())
    assert isinstance(model.bind_tools([]), provider.FallbackModel)


def test_fallback_survives_structured_output():
    """Резерв зберігається і для структурованого виходу."""
    model = provider.FallbackModel(_Stub(), lambda: _Stub())
    assert isinstance(model.with_structured_output(dict), provider.FallbackModel)


async def test_fallback_is_built_lazily():
    """Резервна модель не будується, поки основна працює."""
    built = []

    def make_fallback():
        """Фабрика резерву, що падає; факт виклику фіксується у built.

        Raises:
            RuntimeError: Завжди — імітує відсутній ключ.
        """
        built.append(1)
        raise RuntimeError("немає ключа резервного провайдера")

    model = provider.FallbackModel(_Stub(label="основна"), make_fallback)
    assert await model.ainvoke("x") == "основна"
    assert built == []


async def test_missing_fallback_reraises_original_quota_error():
    """Якщо резерв недоступний, піднімається первісна помилка квоти."""
    def make_fallback():
        """Фабрика резерву, що падає через відсутній ключ.

        Raises:
            RuntimeError: Завжди.
        """
        raise RuntimeError("немає ключа резервного провайдера")

    model = provider.FallbackModel(_Stub(fail_times=1), make_fallback)
    with pytest.raises(Exception, match="rate_limit"):
        await model.ainvoke("x")
