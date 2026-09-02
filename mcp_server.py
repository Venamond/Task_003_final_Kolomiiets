"""Служба-бібліотека самоосвіти — MCP-сервер над каталогом, замовленнями і бюджетом.

Агент не має доступу до цих файлів: він звертається через протокол, а служба
сама вирішує, виконати чи відмовити, і перевіряє аргументи власною схемою.

Логіка живе в чистих функціях *_payload, @mcp.tool() лишається тонкою
обгорткою — тести логіки не потребують підняття сервера.

Запуск: python mcp_server.py (транспорт stdio).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import Field

import config
from state_store import StateStore

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# ── Сховище ───────────────────────────────────────────────────────────────────
# Служба живе рівно стільки, скільки підпроцес, тому словник у пам'яті не
# годиться. Механізм запису спільний зі службою пошти (state_store).

_STORE = StateStore()


def reset_state_dir() -> None:
    """Скинути кеш шляху. Потрібно тестам, які підміняють STATE_DIR."""
    _STORE.reset()


def _read_json(name: str, default: Any) -> Any:
    """Прочитати файл стану служби.

    Args:
        name: Ім'я файлу в теці стану.
        default: Що повернути, якщо файлу немає.

    Returns:
        Розібраний вміст або default.
    """
    return _STORE.read(name, default)


def _write_json(name: str, data: Any) -> None:
    """Записати файл стану служби атомарно.

    Args:
        name: Ім'я файлу в теці стану.
        data: Дані для запису.
    """
    _STORE.write(name, data)


def read_catalog() -> list[dict]:
    """Каталог друкованих видань.

    Returns:
        Перелік позицій каталогу.
    """
    return _read_json("catalog.json", [])


def read_orders() -> list[dict]:
    """Журнал замовлень.

    Returns:
        Перелік замовлень.
    """
    return _read_json("orders.json", [])


def read_budget() -> dict[str, dict[str, float]]:
    """Облік витрат за місяцями.

    Returns:
        Витрати за місяцями.
    """
    return _read_json("budget.json", {})


# ── Бізнес-логіка ─────────────────────────────────────────────────────────────

def search_catalog_payload(query: str, lang: str, limit: int) -> dict[str, Any]:
    """Пошук друкованого видання в каталозі за назвою або автором.

    Args:
        query: Пошуковий рядок.
        lang: Мова видання.
        limit: Скільки позицій повернути щонайбільше.

    Returns:
        Словник з полями found та items, або {"error": ...}.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "Порожній пошуковий запит"}
    if limit < 1:
        return {"error": "limit має бути щонайменше 1"}

    # Модель пише назву й автора одним рядком: «Роздуми Марк Аврелій». Підрядок
    # окремо в назві або авторі дає нуль, тому шукаємо всі слова запиту в парі
    # «назва + автор».
    catalog = [item for item in read_catalog() if item.get("lang") == lang]

    def haystack(item: dict) -> str:
        """Рядок, у якому шукаємо: назва плюс автор.

        Args:
            item: Позиція каталогу.

        Returns:
            Рядок «назва + автор» у нижньому регістрі.
        """
        return f"{item['title']} {item['author']}".casefold()

    words = query.casefold().split()
    matched = [i for i in catalog if all(w in haystack(i) for w in words)]

    # Строгий збіг порожній — повторюємо без слів, яких у каталозі немає взагалі
    # («тверда обкладинка» тощо). Наявні слова лишаються обовʼязковими, тому
    # пошук не стає всеїдним.
    relaxed_from: list[str] = []
    if not matched and len(words) > 1:
        known = [w for w in words if any(w in haystack(i) for i in catalog)]
        if known and len(known) < len(words):
            relaxed_from = [w for w in words if w not in known]
            matched = [i for i in catalog if all(w in haystack(i) for w in known)]

    result = {"query": query, "found": len(matched), "items": matched[:limit]}
    if relaxed_from:
        result["note"] = ("Точного збігу не знайдено; проігноровано слова, яких "
                          f"немає в каталозі: {', '.join(relaxed_from)}")
    return result


def get_order_payload(order_id: str) -> dict[str, Any]:
    """Стан замовлення за ідентифікатором.

    Args:
        order_id: Ідентифікатор у форматі ORD-XXXXXXXX.

    Returns:
        Запис замовлення або {"error": ...}.
    """
    for order in read_orders():
        if order.get("order_id") == order_id:
            return order
    return {"error": f"Замовлення '{order_id}' не знайдено"}


def get_budget_status_payload(month: str) -> dict[str, Any]:
    """Скільки витрачено з місячного ліміту.

    Args:
        month: Місяць у форматі YYYY-MM.

    Returns:
        Словник з полями month, spent, limit, remaining, або {"error": ...}.
    """
    if not MONTH_RE.match(month or ""):
        return {"error": "Очікується місяць у форматі YYYY-MM, наприклад 2026-09"}
    spent = float(read_budget().get(month, {}).get("spent", 0.0))
    limit = config.BUDGET_MONTHLY_EUR
    return {
        "month": month,
        "spent": round(spent, 2),
        "limit": round(limit, 2),
        "remaining": round(limit - spent, 2),
    }


# ── Ідемпотентність ───────────────────────────────────────────────────────────
# Механізм у state_store: відбиток записується ДО спроби виконання.

def idem_lookup(name: str, key: str) -> dict[str, Any] | None:
    """Знайти результат попереднього виконання за відбитком.

    Args:
        name: Ім'я файлу журналу відбитків.
        key: Відбиток дії.

    Returns:
        Збережений результат, запис про незавершену спробу або None.
    """
    return _STORE.idem_lookup(name, key)


def idem_begin(name: str, key: str) -> None:
    """Позначити відбиток як розпочатий ДО спроби виконання.

    Args:
        name: Ім'я файлу журналу відбитків.
        key: Відбиток дії.
    """
    _STORE.idem_begin(name, key)


def idem_finish(name: str, key: str, result: dict[str, Any]) -> None:
    """Записати підсумок виконання поверх позначки про початок.

    Args:
        name: Ім'я файлу журналу відбитків.
        key: Відбиток дії.
        result: Підсумок виконання.
    """
    _STORE.idem_finish(name, key, result)


def place_print_order_payload(title: str, author: str, copies: int,
                              price_per_copy: float,
                              idempotency_key: str) -> dict[str, Any]:
    """Оформити замовлення друкованого видання. РИЗИКОВА, НЕЗВОРОТНА ДІЯ.

    Ліміти регламенту перевіряються тут, у коді, незалежно від рішення людини:
    підтвердження людини — це друга лінія, а не заміна перевірці.

    Args:
        title: Назва видання.
        author: Автор видання.
        copies: Кількість примірників.
        price_per_copy: Ціна одного примірника в EUR.
        idempotency_key: Відбиток дії; повтор з тим самим відбитком не створює
            друге замовлення.

    Returns:
        Словник з ordered, total_cost, message і — у разі успіху — order_id.
    """
    # Критична секція під блокуванням: «прочитати — змінити — записати»
    # має бути неподільною, бо клієнт піднімає окремий процес на кожен
    # виклик, і два одночасні виклики затирали б зміни один одного.
    with _STORE.locked():
        if not idempotency_key:
            return {"ordered": False, "total_cost": 0.0,
                    "message": "Відмовлено: не передано відбиток ідемпотентності"}

        previous = idem_lookup("order_idem.json", idempotency_key)
        if previous is not None:
            if previous["status"] == "done":
                return previous["result"]
            return {"ordered": False, "total_cost": 0.0,
                    "message": ("Відмовлено: попередня спроба з тим самим відбитком "
                                "не завершилась. Повторне виконання заборонене.")}

        if len(title.strip()) < 2 or len(author.strip()) < 2:
            return {"ordered": False, "total_cost": 0.0,
                    "message": "Відмовлено: назва та автор мають містити щонайменше 2 символи"}
        if not 0.01 <= price_per_copy <= 500:
            return {"ordered": False, "total_cost": 0.0,
                    "message": "Відмовлено: ціна має бути в межах від 0.01 до 500 EUR"}
        if copies < 1:
            return {"ordered": False, "total_cost": 0.0,
                    "message": "Відмовлено: кількість примірників має бути щонайменше 1"}

        total = round(copies * price_per_copy, 2)

        if copies > config.MAX_COPIES_PER_ORDER:
            return {"ordered": False, "total_cost": total,
                    "message": (f"Відмовлено: {copies} примірників перевищує ліміт "
                                f"{config.MAX_COPIES_PER_ORDER} на одне замовлення.")}

        month = datetime.now(timezone.utc).strftime("%Y-%m")
        budget = read_budget()
        spent = float(budget.get(month, {}).get("spent", 0.0))
        if spent + total > config.BUDGET_MONTHLY_EUR:
            return {"ordered": False, "total_cost": total,
                    "message": (f"Відмовлено: {total} EUR понад витрачені {spent} EUR "
                                f"перевищує місячний ліміт {config.BUDGET_MONTHLY_EUR} EUR.")}

        idem_begin("order_idem.json", idempotency_key)

        # Спершу списання, потім замовлення. Двох записів однією транзакцією тут
        # немає, тому при обриві між ними доводиться обирати бік: зайве списання
        # видно людині й виправляється скасуванням, а безкоштовне замовлення тихо
        # обходить місячний ліміт.
        budget.setdefault(month, {"spent": 0.0})["spent"] = round(spent + total, 2)
        _write_json("budget.json", budget)

        order_id = f"ORD-{idempotency_key[:8].upper()}"
        record = {"order_id": order_id, "title": title, "author": author,
                  "copies": copies, "price_per_copy": price_per_copy,
                  "total_cost": total, "status": "placed",
                  "ts": datetime.now(timezone.utc).isoformat()}
        orders = read_orders()
        orders.append(record)
        _write_json("orders.json", orders)

        result = {"ordered": True, "order_id": order_id, "total_cost": total,
                  "message": f"Замовлено: {copies} прим. «{title}» на суму {total} EUR"}
        idem_finish("order_idem.json", idempotency_key, result)
        return result


def cancel_order_payload(order_id: str, reason: str,
                         idempotency_key: str) -> dict[str, Any]:
    """Скасувати раніше оформлене замовлення і повернути суму в бюджет.

    Args:
        order_id: Ідентифікатор замовлення.
        reason: Причина скасування — потрапляє в журнал для аудиту.
        idempotency_key: Відбиток дії.

    Returns:
        Словник з cancelled, refunded і message.
    """
    # Критична секція під блокуванням: «прочитати — змінити — записати»
    # має бути неподільною, бо клієнт піднімає окремий процес на кожен
    # виклик, і два одночасні виклики затирали б зміни один одного.
    with _STORE.locked():
        if not idempotency_key:
            return {"cancelled": False, "refunded": 0.0,
                    "message": "Відмовлено: не передано відбиток ідемпотентності"}

        previous = idem_lookup("order_idem.json", idempotency_key)
        if previous is not None:
            if previous["status"] == "done":
                return previous["result"]
            return {"cancelled": False, "refunded": 0.0,
                    "message": ("Відмовлено: попередня спроба з тим самим відбитком "
                                "не завершилась. Повторне виконання заборонене.")}

        orders = read_orders()
        target = next((o for o in orders if o.get("order_id") == order_id), None)
        if target is None:
            return {"cancelled": False, "refunded": 0.0,
                    "message": f"Замовлення '{order_id}' не знайдено"}
        if target.get("status") == "cancelled":
            return {"cancelled": False, "refunded": 0.0,
                    "message": f"Замовлення '{order_id}' вже скасоване"}

        idem_begin("order_idem.json", idempotency_key)

        refunded = float(target["total_cost"])
        target["status"] = "cancelled"
        target["cancel_reason"] = reason
        target["cancelled_ts"] = datetime.now(timezone.utc).isoformat()
        _write_json("orders.json", orders)

        # Повертаємо в місяць, у якому замовлення було оформлене, а не в поточний.
        # Інакше скасування торішнього замовлення звільняло б ліміт цього місяця,
        # і місячна межа обходилась би штатним викликом дозволеної дії.
        month = str(target.get("ts", ""))[:7] or datetime.now(timezone.utc).strftime("%Y-%m")
        budget = read_budget()
        spent = float(budget.get(month, {}).get("spent", 0.0))
        budget.setdefault(month, {"spent": 0.0})["spent"] = round(max(0.0, spent - refunded), 2)
        _write_json("budget.json", budget)

        result = {"cancelled": True, "refunded": refunded,
                  "message": f"Скасовано {order_id}, повернуто {refunded} EUR"}
        idem_finish("order_idem.json", idempotency_key, result)
        return result


MAX_PROMPT_ARG_LEN = 200


def escape_prompt_arg(value: str) -> str:
    """Знешкодити параметр заготовки перед підстановкою в текст.

    Значення підставляється в текст, який читає модель. Перенос рядка відокремив
    би «нову інструкцію» від попередньої — це і є ін'єкція через шаблон, тому
    переноси і керуючі символи прибираються, а довжина обрізається.

    Args:
        value: Значення параметра як прийшло.

    Returns:
        Однорядкове значення обмеженої довжини.
    """
    flat = " ".join(str(value).split())
    cleaned = "".join(ch for ch in flat if ch.isprintable())
    return cleaned[:MAX_PROMPT_ARG_LEN]


# ── MCP протокольний шар ──────────────────────────────────────────────────────
#
# Обмеження оголошені в схемі, а не лише в коді: FastMCP переносить enum,
# minimum, maximum і pattern у JSON-схему, яку модель бачить ДО виклику.
# Перевірка всередині спрацювала б уже після того, як модель вигадала значення.
#
# Ті самі межі лишаються і в коді — це не дублювання: схема захищає від помилки
# моделі, код — від клієнта, який звернеться до служби повз нашу схему.

from mcp.server.fastmcp import FastMCP

# Типи-обмеження, спільні для кількох дій.
Month = Annotated[str, Field(
    pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    description="Місяць у форматі YYYY-MM, наприклад 2026-09.")]
OrderId = Annotated[str, Field(
    pattern=r"^ORD-[A-Z0-9]{4,16}$",
    description="Ідентифікатор замовлення у форматі ORD-XXXXXXXX.")]
IdemKey = Annotated[str, Field(
    default="", max_length=128,
    description=("Відбиток дії, який ПІДСТАВЛЯЄ ХОСТ-ЗАСТОСУНОК. "
                 "Не вигадуйте його: залиште порожнім."))]

mcp = FastMCP(
    name="philosophy_library",
    instructions=(
        "Служба бібліотеки самоосвіти з історії філософії: каталог друкованих "
        "видань, замовлення, облік місячного бюджету, розсилання не виконує."
    ),
    # Без цього FastMCP пише «Processing request of type ...» у stderr
    # на кожен виклик і засмічує вивід демонстрацій.
    log_level="WARNING",
)


# Docstring і анотації типів — не косметика: FastMCP будує з них JSON-схему,
# яку бачить модель. Погана docstring означає, що агент кличе дію навмання.

@mcp.tool()
def search_catalog(
    query: Annotated[str, Field(min_length=2, max_length=200,
                                description="Назва видання або ім'я автора.")],
    lang: Literal["uk", "en"] = "uk",
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> str:
    """Знайти друковане видання в каталозі бібліотеки за назвою або автором.

    Використовуйте перед замовленням, щоб дізнатися точну ціну і наявність.
    Не показує, чи є текст безкоштовно — для цього є окремий інструмент.

    Приклад: search_catalog(query="Роздуми", lang="uk", limit=3).

    Args:
        query: Назва видання або ім'я автора, наприклад "Марк Аврелій".
        lang: Мова видання, за умовчанням "uk".
        limit: Скільки позицій повернути щонайбільше, від 1.

    Returns:
        JSON-рядок з полями found та items; items містять id, title, author,
        publisher, series, price_eur, isbn.
    """
    return json.dumps(search_catalog_payload(query, lang, limit), ensure_ascii=False)


@mcp.tool()
def get_order(order_id: OrderId) -> str:
    """Дізнатися стан раніше оформленого замовлення.

    Приклад: get_order(order_id="ORD-1A2B3C4D").

    Args:
        order_id: Ідентифікатор замовлення у форматі ORD-XXXXXXXX.

    Returns:
        JSON-рядок із записом замовлення або з полем error, якщо не знайдено.
    """
    return json.dumps(get_order_payload(order_id), ensure_ascii=False)


@mcp.tool()
def get_budget_status(month: Month) -> str:
    """Скільки коштів витрачено з місячного ліміту самоосвіти і скільки лишилось.

    Викликайте ПЕРЕД замовленням: людина ухвалює рішення за реальними числами,
    а не за припущенням моделі.

    Приклад: get_budget_status(month="2026-09").

    Args:
        month: Місяць у форматі YYYY-MM.

    Returns:
        JSON-рядок з полями month, spent, limit, remaining.
    """
    return json.dumps(get_budget_status_payload(month), ensure_ascii=False)


@mcp.tool()
def place_print_order(
    title: Annotated[str, Field(min_length=2, max_length=200,
                                description="Назва видання, як вона стоїть у каталозі.")],
    author: Annotated[str, Field(min_length=2, max_length=120,
                                 description="Автор видання, як він стоїть у каталозі.")],
    copies: Annotated[int, Field(
        ge=1, le=config.MAX_COPIES_PER_ORDER,
        description=(f"Кількість примірників, від 1 до "
                     f"{config.MAX_COPIES_PER_ORDER} за регламентом."))],
    price_per_copy: Annotated[float, Field(
        gt=0, le=500,
        description="Ціна одного примірника в EUR, БЕРЕТЬСЯ З КАТАЛОГУ.")],
    idempotency_key: IdemKey = "",
) -> str:
    """Замовити друковане видання за власний кошт. РИЗИКОВА, НЕЗВОРОТНА ДІЯ.

    Використовуйте ЛИШЕ тоді, коли текст недоступний безкоштовно. Спершу
    перевірте вільні джерела і залишок бюджету через get_budget_status.

    Виклик цієї дії зупиняє агента і вимагає підтвердження людини. Ліміти
    регламенту перевіряються службою незалежно від рішення людини.

    Приклад: place_print_order(title="Роздуми", author="Марк Аврелій",
    copies=1, price_per_copy=18.5, idempotency_key="a3f9c1d2").

    Args:
        title: Назва видання.
        author: Автор видання.
        copies: Кількість примірників, не більше за ліміт регламенту.
        price_per_copy: Ціна одного примірника в EUR, з каталогу.
        idempotency_key: Відбиток дії, який ПІДСТАВЛЯЄ ХОСТ-ЗАСТОСУНОК.
            Не вигадуйте його: залиште порожнім, система підставить свій.
            Повтор з тим самим відбитком поверне те саме замовлення.

    Returns:
        JSON-рядок з ordered, total_cost, message і order_id у разі успіху.
    """
    return json.dumps(
        place_print_order_payload(title, author, copies, price_per_copy,
                                  idempotency_key),
        ensure_ascii=False)


@mcp.tool()
def cancel_order(
    order_id: OrderId,
    reason: Annotated[str, Field(min_length=3, max_length=300,
                                 description="Причина — потрапляє в аудит.")],
    idempotency_key: IdemKey = "",
) -> str:
    """Скасувати замовлення і повернути суму в місячний бюджет. РИЗИКОВА ДІЯ.

    Скасування незворотне для вже оплаченого замовлення, тому потребує
    підтвердження людини.

    Приклад: cancel_order(order_id="ORD-1A2B3C4D", reason="знайшов безкоштовний
    текст", idempotency_key="b7e2f0a1").

    Args:
        order_id: Ідентифікатор замовлення у форматі ORD-XXXXXXXX.
        reason: Причина скасування — потрапляє в журнал для аудиту.
        idempotency_key: Відбиток дії, який підставляє хост-застосунок.
            Не вигадуйте його: залиште порожнім.

    Returns:
        JSON-рядок з cancelled, refunded і message.
    """
    return json.dumps(cancel_order_payload(order_id, reason, idempotency_key),
                      ensure_ascii=False)


# ── MCP resources: дані, а не дії ─────────────────────────────────────────────
# Довідник читають за адресою наперед і кладуть у контекст; дію модель кличе
# сама по ходу роботи.

@mcp.resource("policy://budget")
def budget_policy() -> str:
    """Регламент витрат на самоосвіту: місячний ліміт і межа примірників.

    Returns:
        Текст регламенту українською.
    """
    return config.budget_policy_text()


@mcp.resource("catalog://overview")
def catalog_overview() -> str:
    """Огляд каталогу: видавництва, серії та діапазон цін.

    Returns:
        Огляд каталогу українською.
    """
    items = read_catalog()
    if not items:
        return "Каталог порожній."
    publishers = sorted({item["publisher"] for item in items})
    series = sorted({item["series"] for item in items})
    prices = [item["price_eur"] for item in items]
    return (
        f"Каталог друкованих видань: {len(items)} позицій.\n"
        f"Видавництва: {', '.join(publishers)}.\n"
        f"Серії: {', '.join(series)}.\n"
        f"Ціни від {min(prices):.2f} до {max(prices):.2f} EUR."
    )


# ── MCP prompt: заготовка відповіді ───────────────────────────────────────────

@mcp.prompt()
def study_reply(name: str, topic: str, tone: str = "professional") -> str:
    """Заготовка відповіді-рекомендації читачеві.

    Args:
        name: Ім'я читача.
        topic: Тема або течія, про яку йдеться.
        tone: professional | empathetic | concise.

    Returns:
        Текст завдання для моделі з підставленими параметрами.
    """
    tones = {
        "professional": "Сформулюй стриману, чітку рекомендацію",
        "empathetic": "Сформулюй теплу рекомендацію з увагою до труднощів новачка",
        "concise": "Сформулюй коротку рекомендацію без зайвих слів",
    }
    opening = tones.get(tone, tones["professional"])
    return (f"{opening} читачеві {escape_prompt_arg(name)}. "
            f"Тема: {escape_prompt_arg(topic)}. "
            f"Назви два тексти для початку і поясни, з чого починати і чому.")


if __name__ == "__main__":
    # stdio — локальний транспорт: клієнт запускає цей файл підпроцесом
    # і говорить з ним через stdin/stdout.
    mcp.run(transport="stdio")
