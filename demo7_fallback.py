"""Демонстрація 7: перехід на резервну модель і повернення на основну.

Показує поведінку FallbackModel на СПРАВЖНІХ моделях обох провайдерів.
Відмову основної емулюємо: перший виклик підіймає ту саму помилку, яку
Anthropic повертає при вичерпаній квоті. Справжню квоту вичерпати навмисно
не можна, а перевірити треба саме поведінку на ній.

Головне, що видно: резерв не залипає. Після відмови обгортка звертається до
Gemini, але наступний виклик знову йде в Anthropic — інакше одна помилка
квоти перевела б увесь прогін на резервного провайдера назавжди.

Потрібні обидва ключі: ANTHROPIC_API_KEY і GOOGLE_API_KEY.

Запуск: python demo7_fallback.py
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import provider

QUESTION = "Одним реченням: чим стоїцизм відрізняється від епікурейства?"


class QuotaOnce:
    """Справжня модель, яка задану кількість перших викликів імітує вичерпану квоту.

    Обгортка навмисно тонка: далі йде справжня мережа і справжня відповідь.
    Емулюється лише сам факт відмови, а не робота моделі.
    """

    def __init__(self, inner: Any, fail_times: int) -> None:
        """Створити обгортку.

        Args:
            inner: Справжня модель провайдера.
            fail_times: Скільки перших викликів мають впасти на квоті.
        """
        self.inner = inner
        self.fail_times = fail_times
        self.calls = 0

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        """Викликати модель або підняти помилку квоти.

        Args:
            *args: Позиційні аргументи виклику.
            **kwargs: Іменовані аргументи виклику.

        Returns:
            Відповідь справжньої моделі.

        Raises:
            RuntimeError: Поки лишаються заплановані відмови.
        """
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(
                "429 rate_limit_error: quota exceeded (емуляція відмови)")
        return await self.inner.ainvoke(*args, **kwargs)


async def ask(model: provider.FallbackModel, number: int, *,
              rearm: Callable[[], None] | None = None,
              attempts: int = 4) -> None:
    """Поставити питання і сказати, хто саме відповів.

    Провайдера визначаємо не за текстом відповіді, а за лічильником обгортки:
    зріс — відповідав резерв.

    Повторюємо при тимчасовій недоступності провайдера. Gemini віддає 503
    «high demand» досить часто, і на цій демонстрації така відповідь виглядала
    б як поломка резерву, хоча перемикання вже відпрацювало.

    Args:
        model: Обгортка з резервом.
        number: Номер виклику для виводу.
        rearm: Що виконати перед кожною спробою — потрібно, щоб на повторі
            основна модель знову впала на квоті, інакше повтор перевіряв би
            вже не той шлях.
        attempts: Скільки разів пробувати.

    Raises:
        Exception: Якщо провайдер недоступний після всіх спроб.
    """
    for attempt in range(1, attempts + 1):
        if rearm is not None:
            rearm()
        before = model.fallback_used
        try:
            reply = await model.ainvoke(QUESTION)
        except Exception as exc:
            if attempt == attempts:
                raise
            print(f"  … провайдер тимчасово недоступний "
                  f"({type(exc).__name__}), спроба {attempt + 1} з {attempts}")
            await asyncio.sleep(5)
            continue
        who = ("резервна — Gemini" if model.fallback_used > before
               else "основна — Anthropic")
        print(f"\n  ВИКЛИК {number}: відповідала {who}")
        print(f"  {provider.text_of(reply)[:220]}")
        return


async def main() -> None:
    """Прогнати три виклики: відмова, резерв, повернення."""
    print("═" * 78)
    print("  РЕЗЕРВНИЙ ПРОВАЙДЕР: відмова основної моделі та повернення до неї")
    print("═" * 78)

    primary = QuotaOnce(provider.make_llm("anthropic"), fail_times=1)
    model = provider.FallbackModel(primary, lambda: provider.make_llm("google"))

    print("\n  Перший виклик отримає помилку квоти від основної моделі.")
    await ask(model, 1, rearm=lambda: setattr(primary, "fail_times", 1))

    print("\n  Відмов більше не заплановано — основна модель має відповідати сама.")
    await ask(model, 2)
    await ask(model, 3)

    print("\n" + "═" * 78)
    print(f"  Звернень до основної моделі:  {primary.calls}")
    print(f"  Переходів на резерв:          {model.fallback_used}")
    print("  Резерв спрацював рівно на відмові й не залишився назавжди:")
    print("  наступні виклики знову пішли в основну модель.")
    print("═" * 78)


if __name__ == "__main__":
    asyncio.run(main())
