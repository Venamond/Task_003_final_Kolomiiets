"""Інструменти ReAct-агента для доменної задачі «історія філософії».

Перенесено з ДЗ1 (hw1_react_agent/tools.py) без змін. Додано read_audit:
журнал безпеки читається таким самим Pydantic-інструментом, тому фільтр за
сесією — це перевірка схеми, а не розсипані по коду умови.

Кожен інструмент має:
  - Pydantic v2 модель параметрів із Field(description=...);
  - щонайменше один field_validator;
  - docstring українською -- саме його читає LLM, обираючи інструмент.

Джерела даних:
  - Wikidata Search API -- пошук QID за іменем;
  - Wikidata SPARQL     -- деталі та граф впливів (P737);
  - Gutendex            -- праці у вільному доступі.
"""

import re
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from cache import HttpClient

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
GUTENDEX_API = "https://gutendex.com/books/"

# Спільний клієнт для всіх інструментів.
http_client = HttpClient()


def parse_wikidata_year(iso: str | None) -> int | None:
    """Перетворити дату Wikidata на цілий рік зі знаком.

    Wikidata віддає '1844-10-15T00:00:00Z', а для античності --
    '-0428-01-01T00:00:00Z' із ведучим мінусом. До н.е. -- від'ємний рік.

    Args:
        iso: Дата у форматі Wikidata або None.

    Returns:
        Рік зі знаком або None.
    """
    if not iso:
        return None
    is_bc = iso.startswith("-")
    body = iso[1:] if is_bc else iso
    year = int(body.split("-")[0])
    return -year if is_bc else year


def _sparql(query: str) -> list[dict[str, Any]]:
    """Виконати SPARQL-запит і повернути список рядків результату.

    Args:
        query: Текст SPARQL-запиту.

    Returns:
        Перелік рядків результату.
    """
    data = http_client.get_json(
        WIKIDATA_SPARQL, {"query": query, "format": "json"}
    )
    return data.get("results", {}).get("bindings", [])


def _cell(row: dict, key: str) -> str | None:
    """Дістати значення комірки SPARQL-результату або None.

    Args:
        row: Рядок результату SPARQL.
        key: Ім'я комірки.

    Returns:
        Значення комірки або None.
    """
    value = row.get(key, {}).get("value")
    return value if value else None


# --- Інструмент 1: пошук філософа -------------------------------------------


class PhilosopherQuery(BaseModel):
    """Параметри пошуку філософа у Wikidata."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(
        description=(
            "Повне ім'я та прізвище філософа, наприклад 'Фрідріх Ніцше' "
            "або 'Артур Шопенгауер'. Лише прізвище шукати НЕ можна -- "
            "Wikidata поверне сторінку неоднозначності."
        )
    )
    lang: Literal["uk", "en"] = Field(
        default="uk",
        description="Мова пошуку та підписів: 'uk' або 'en'.",
    )

    @field_validator("name")
    @classmethod
    def name_is_meaningful(cls, v: str) -> str:
        """Ім'я має бути осмисленим рядком, а не числом чи одним символом.

        Args:
            v: Ім'я філософа.

        Returns:
            Те саме ім'я, якщо воно осмислене.

        Raises:
            ValueError: Ім'я коротше за 2 символи або складається з цифр.
        """
        if len(v) < 2:
            raise ValueError("Ім'я філософа має містити щонайменше 2 символи")
        if v.replace(" ", "").isdigit():
            raise ValueError("Ім'я філософа не може бути числом")
        return v


@tool(args_schema=PhilosopherQuery)
def find_philosopher(name: str, lang: str = "uk") -> dict:
    """Знайти філософа у Wikidata: роки життя, течію, країну та QID.

    Використовуйте цей інструмент ПЕРШИМ, коли у питанні згадано філософа.
    Він повертає qid -- ідентифікатор виду 'Q9358', який ПОТРІБЕН
    інструменту get_influences. Без qid граф впливів отримати неможливо.

    Передавайте повне ім'я та прізвище ('Фрідріх Ніцше'), а не саме
    прізвище: пошук за одним прізвищем повертає не того.

    Приклад: find_philosopher(name="Фрідріх Ніцше").

    Args:
        name: Повне ім'я філософа.
        lang: Мова пошуку ('uk' або 'en').

    Returns:
        Словник: qid, label_uk, label_en, birth_year, death_year,
        school, country, wikidata_url. Поля school та country можуть
        бути None -- у Wikidata вони заповнені не для всіх філософів.
    """
    # Фаза 1: знайти QID за іменем.
    search = http_client.get_json(
        WIKIDATA_API,
        {
            "action": "wbsearchentities",
            "search": name,
            "language": lang,
            "format": "json",
            "limit": 1,
        },
    )
    hits = search.get("search", [])
    if not hits:
        return {"error": f"Філософа '{name}' не знайдено у Wikidata"}

    qid = hits[0]["id"]

    # Фаза 2: деталі за знайденим QID.
    # qid безпечно підставляти у запит -- він щойно прийшов від Wikidata.
    query = f"""
    SELECT ?birth ?death ?labelUk ?labelEn ?p135 ?p101 ?countryLabel WHERE {{
      OPTIONAL {{ wd:{qid} wdt:P569 ?birth }}
      OPTIONAL {{ wd:{qid} wdt:P570 ?death }}
      OPTIONAL {{ wd:{qid} rdfs:label ?labelUk . FILTER(LANG(?labelUk) = "uk") }}
      OPTIONAL {{ wd:{qid} rdfs:label ?labelEn . FILTER(LANG(?labelEn) = "en") }}
      OPTIONAL {{ wd:{qid} wdt:P135 ?s . ?s rdfs:label ?p135 . FILTER(LANG(?p135) = "uk") }}
      OPTIONAL {{ wd:{qid} wdt:P101 ?f . ?f rdfs:label ?p101 . FILTER(LANG(?p101) = "uk") }}
      OPTIONAL {{ wd:{qid} wdt:P27 ?c . ?c rdfs:label ?countryLabel . FILTER(LANG(?countryLabel) = "uk") }}
    }}
    LIMIT 1
    """
    rows = _sparql(query)
    row = rows[0] if rows else {}

    # Течія (P135) заповнена не завжди -- у Ніцше її немає.
    # Тоді беремо галузь знань (P101), а якщо й вона порожня -- None.
    school = _cell(row, "p135") or _cell(row, "p101")

    return {
        "qid": qid,
        "label_uk": _cell(row, "labelUk") or hits[0].get("label"),
        "label_en": _cell(row, "labelEn") or hits[0].get("label"),
        "birth_year": parse_wikidata_year(_cell(row, "birth")),
        "death_year": parse_wikidata_year(_cell(row, "death")),
        "school": school,
        "country": _cell(row, "countryLabel"),
        "wikidata_url": f"https://www.wikidata.org/wiki/{qid}",
    }


# --- Інструмент 2: граф впливів ----------------------------------------------

# Wikidata має ЛИШЕ властивість P737 ("під впливом").
# Зворотного P738 не існує -- зворотний напрям отримуємо інверсією трійки.
QID_PATTERN = r"^Q\d+$"


class InfluenceQuery(BaseModel):
    """Параметри запиту графа впливів між філософами."""

    model_config = ConfigDict(str_strip_whitespace=True)

    qid: str = Field(
        description=(
            "Ідентифікатор Wikidata у форматі 'Q9358'. Отримайте його "
            "від інструменту find_philosopher. Ім'я філософа сюди "
            "передавати НЕ можна."
        )
    )
    direction: Literal["influenced_by", "influenced"] = Field(
        default="influenced_by",
        description=(
            "'influenced_by' -- хто вплинув на цього філософа; "
            "'influenced' -- на кого вплинув він сам."
        ),
    )
    limit: int = Field(
        default=10,
        description="Скільки імен повернути, від 1 до 20.",
    )

    @field_validator("qid")
    @classmethod
    def qid_has_valid_format(cls, v: str) -> str:
        """Відхилити все, що не є ідентифікатором Wikidata.

        Args:
            v: Ідентифікатор Wikidata.

        Returns:
            Той самий ідентифікатор, якщо форма правильна.

        Raises:
            ValueError: Значення не схоже на QID.
        """
        if not re.match(QID_PATTERN, v):
            raise ValueError(
                f"'{v}' не є ідентифікатором Wikidata. Очікується значення "
                f"формату Q9358. Спочатку викличте find_philosopher."
            )
        return v

    @field_validator("limit")
    @classmethod
    def limit_in_range(cls, v: int) -> int:
        """Перевірити межі поля limit.

        Args:
            v: Значення поля.

        Returns:
            Те саме значення, якщо воно в межах 1..20.

        Raises:
            ValueError: Значення поза межами.
        """
        if not 1 <= v <= 20:
            raise ValueError("limit має бути в межах від 1 до 20")
        return v


@tool(args_schema=InfluenceQuery)
def get_influences(
    qid: str, direction: str = "influenced_by", limit: int = 10
) -> dict:
    """Отримати граф впливів філософа за даними Wikidata (властивість P737).

    Використовуйте, коли питання стосується того, ХТО вплинув на філософа
    або НА КОГО вплинув він сам.

    Приймає ЛИШЕ qid виду 'Q9358'. Якщо ви знаєте тільки ім'я --
    спершу викличте find_philosopher.

    Приклад: get_influences(qid="Q9358", direction="influenced_by").

    Args:
        qid: Ідентифікатор Wikidata, наприклад 'Q9358'.
        direction: 'influenced_by' або 'influenced'.
        limit: Кількість імен (1-20).

    Returns:
        Словник: direction, count, people (список qid, label, wikidata_url).
    """
    if direction == "influenced_by":
        # Кого вказано як джерело впливу для нашого філософа.
        triple = f"wd:{qid} wdt:P737 ?other"
    else:
        # Інверсія: у кого наш філософ вказаний як джерело впливу.
        triple = f"?other wdt:P737 wd:{qid}"

    query = f"""
    SELECT ?other ?otherLabel WHERE {{
      {triple} .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "uk,en". }}
    }}
    LIMIT {limit}
    """
    rows = _sparql(query)

    people = []
    for row in rows:
        uri = _cell(row, "other") or ""
        other_qid = uri.rsplit("/", 1)[-1]
        people.append(
            {
                "qid": other_qid,
                "label": _cell(row, "otherLabel") or other_qid,
                "wikidata_url": f"https://www.wikidata.org/wiki/{other_qid}",
            }
        )

    return {"direction": direction, "count": len(people), "people": people}


# --- Інструмент 3: праці у вільному доступі ----------------------------------

# Каталог Project Gutenberg переважно англомовний.
ALLOWED_LANGS = {"en", "uk", "de", "fr"}


class TextQuery(BaseModel):
    """Параметри пошуку праць філософа у Project Gutenberg."""

    model_config = ConfigDict(str_strip_whitespace=True)

    author: str = Field(
        description=(
            "Ім'я автора АНГЛІЙСЬКОЮ, наприклад 'Friedrich Nietzsche'. "
            "Візьміть значення label_en з результату find_philosopher -- "
            "каталог Gutenberg україномовних підписів не має."
        )
    )
    lang: str = Field(
        default="en",
        description="Мова видання: en, uk, de або fr.",
    )
    limit: int = Field(
        default=5,
        description="Скільки книжок повернути, від 1 до 10.",
    )

    @field_validator("author")
    @classmethod
    def author_is_meaningful(cls, v: str) -> str:
        """Відсікти надто короткі імена авторів.

        Args:
            v: Значення поля.

        Returns:
            Те саме значення, якщо в ньому щонайменше 3 символи.

        Raises:
            ValueError: Ім'я коротше за 3 символи.
        """
        if len(v) < 3:
            raise ValueError("Ім'я автора має містити щонайменше 3 символи")
        return v

    @field_validator("lang")
    @classmethod
    def lang_is_supported(cls, v: str) -> str:
        """Перевірити мову за переліком ALLOWED_LANGS.

        Args:
            v: Код мови.

        Returns:
            Той самий код, якщо мова підтримується.

        Raises:
            ValueError: Мови немає в переліку.
        """
        if v not in ALLOWED_LANGS:
            raise ValueError(
                f"Мова '{v}' не підтримується. "
                f"Підтримувані мови: {', '.join(sorted(ALLOWED_LANGS))}"
            )
        return v

    @field_validator("limit")
    @classmethod
    def limit_in_range(cls, v: int) -> int:
        """Перевірити межі поля limit.

        Args:
            v: Значення поля.

        Returns:
            Те саме значення, якщо воно в межах 1..10.

        Raises:
            ValueError: Значення поза межами.
        """
        if not 1 <= v <= 10:
            raise ValueError("limit має бути в межах від 1 до 10")
        return v


@tool(args_schema=TextQuery)
def find_texts(author: str, lang: str = "en", limit: int = 5) -> dict:
    """Знайти праці філософа, доступні безкоштовно у Project Gutenberg.

    Використовуйте, коли питання стосується творів, книжок або текстів
    філософа -- зокрема «які праці доступні безкоштовно».

    Ім'я автора передавайте АНГЛІЙСЬКОЮ (поле label_en з find_philosopher).

    Приклад: find_texts(author="Arthur Schopenhauer", lang="en").

    Args:
        author: Ім'я автора англійською.
        lang: Мова видання (en, uk, de, fr).
        limit: Кількість книжок (1-10).

    Returns:
        Словник: author, count, books (title, year, gutenberg_url,
        has_plain_text).
    """
    data = http_client.get_json(
        GUTENDEX_API, {"search": author, "languages": lang}
    )

    books = []
    for item in data.get("results", [])[:limit]:
        formats = item.get("formats", {})
        has_plain_text = any("text/plain" in key for key in formats)
        books.append(
            {
                "title": item.get("title", ""),
                "year": None,
                "gutenberg_url": f"https://www.gutenberg.org/ebooks/{item['id']}",
                "has_plain_text": has_plain_text,
            }
        )

    return {"author": author, "count": data.get("count", 0), "books": books}


# --- Інструмент 4: перетин життів (локальний розрахунок) ---------------------

MIN_YEAR = -800
MAX_YEAR = 2026


class OverlapQuery(BaseModel):
    """Параметри перевірки перетину життів двох філософів."""

    birth_a: int = Field(description="Рік народження першого (до н.е. -- від'ємний).")
    death_a: int = Field(description="Рік смерті першого.")
    birth_b: int = Field(description="Рік народження другого.")
    death_b: int = Field(description="Рік смерті другого.")
    min_age: int = Field(
        default=15,
        description="Мінімальний вік, з якого зустріч вважається можливою (0-40).",
    )

    @field_validator("birth_a", "death_a", "birth_b", "death_b")
    @classmethod
    def year_in_range(cls, v: int) -> int:
        """Роки поза історичним діапазоном -- ознака галюцинації моделі.

        Args:
            v: Рік.

        Returns:
            Той самий рік, якщо він у межах MIN_YEAR..MAX_YEAR.

        Raises:
            ValueError: Рік поза діапазоном.
        """
        if not MIN_YEAR <= v <= MAX_YEAR:
            raise ValueError(
                f"Рік {v} поза допустимим діапазоном від {MIN_YEAR} до {MAX_YEAR}"
            )
        return v

    @field_validator("min_age")
    @classmethod
    def min_age_in_range(cls, v: int) -> int:
        """Перевірити межі поля min_age.

        Args:
            v: Значення поля.

        Returns:
            Те саме значення, якщо воно в межах 0..40.

        Raises:
            ValueError: Значення поза межами.
        """
        if not 0 <= v <= 40:
            raise ValueError("min_age має бути в межах від 0 до 40")
        return v

    @model_validator(mode="after")
    def birth_before_death(self) -> "OverlapQuery":
        """Перевірка охоплює два поля одразу, тому це model_validator.

        Returns:
            Той самий об'єкт, якщо роки узгоджені.

        Raises:
            ValueError: Рік народження не раніший за рік смерті.
        """
        if self.birth_a >= self.death_a:
            raise ValueError(
                "Рік народження першого має бути раніше за рік смерті"
            )
        if self.birth_b >= self.death_b:
            raise ValueError(
                "Рік народження другого має бути раніше за рік смерті"
            )
        return self


@tool(args_schema=OverlapQuery)
def check_lifespan_overlap(
    birth_a: int,
    death_a: int,
    birth_b: int,
    death_b: int,
    min_age: int = 15,
) -> dict:
    """Обчислити, чи перетиналися життя двох філософів і чи МОГЛИ вони зустрітися.

    Використовуйте, коли питання стосується того, чи були філософи
    сучасниками, чи могли зустрітися, чи жили в одну епоху.

    ВАЖЛИВО: інструмент відповідає на питання про ФІЗИЧНУ МОЖЛИВІСТЬ
    зустрічі за роками життя. Він НЕ стверджує, що зустріч відбулася.
    У фінальній відповіді формулюйте це саме як можливість.

    Роки до н.е. передавайте від'ємними: Платон -- birth_a=-428.

    Приклад: check_lifespan_overlap(birth_a=1844, death_a=1900,
    birth_b=1788, death_b=1860).

    Args:
        birth_a: Рік народження першого філософа.
        death_a: Рік смерті першого.
        birth_b: Рік народження другого.
        death_b: Рік смерті другого.
        min_age: Мінімальний вік для можливості зустрічі (типово 15).

    Returns:
        Словник: overlap_years, could_have_met, explanation.
    """
    # Перетин відрізків життя.
    start = max(birth_a, birth_b)
    end = min(death_a, death_b)
    overlap = max(0, end - start)

    # Зустріч можлива, якщо в спільний період обом виповнилося min_age.
    ready_a = birth_a + min_age
    ready_b = birth_b + min_age
    met_start = max(ready_a, ready_b)
    could_have_met = met_start <= end

    if overlap == 0:
        explanation = (
            "Життя не перетиналися -- філософи належать до різних епох."
        )
    elif could_have_met:
        explanation = (
            f"Життя перетиналися {overlap} р. Зустріч була фізично можливою "
            f"починаючи з {met_start} року, коли обом виповнилось {min_age}. "
            f"Це не означає, що вона відбулася."
        )
    else:
        explanation = (
            f"Життя перетиналися {overlap} р., але молодшому не виповнилось "
            f"{min_age} років до смерті старшого -- зустріч малоймовірна."
        )

    return {
        "overlap_years": overlap,
        "could_have_met": could_have_met,
        "explanation": explanation,
    }


# --- Перелік інструментів для bind_tools -------------------------------------

ALL_TOOLS = [
    find_philosopher,
    get_influences,
    find_texts,
    check_lifespan_overlap,
]


# --- Інструмент 5: читання журналу безпеки (нове в ДЗ3) ----------------------


class AuditQuery(BaseModel):
    """Параметри читання журналу безпеки."""

    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str = Field(
        description="Ідентифікатор сесії -- читати можна ЛИШЕ власну."
    )
    kind: Literal["guardrail", "hitl", "risky_action", "manifest", "all"] = Field(
        default="all",
        description="Рід подій: guardrail, hitl, risky_action, manifest або all.",
    )
    limit: int = Field(
        default=20,
        description="Скільки останніх записів повернути, від 1 до 100.",
    )

    @field_validator("session_id")
    @classmethod
    def session_is_meaningful(cls, v: str) -> str:
        """Ідентифікатор сесії має бути непорожнім.

        Args:
            v: Ідентифікатор сесії.

        Returns:
            Той самий ідентифікатор, якщо він непорожній.

        Raises:
            ValueError: Порожній ідентифікатор.
        """
        if len(v) < 1:
            raise ValueError("session_id не може бути порожнім")
        return v

    @field_validator("limit")
    @classmethod
    def limit_in_range(cls, v: int) -> int:
        """Обмежити кількість записів розумним діапазоном.

        Args:
            v: Кількість записів.

        Returns:
            Те саме значення, якщо воно в межах 1..100.

        Raises:
            ValueError: Значення поза межами.
        """
        if not 1 <= v <= 100:
            raise ValueError("limit має бути в межах від 1 до 100")
        return v


@tool(args_schema=AuditQuery)
def read_audit(session_id: str, kind: str = "all", limit: int = 20) -> str:
    """Прочитати журнал безпеки власної сесії і пояснити, що сталося.

    Використовуйте для питань «чому мені відмовили», «які перевірки
    спрацювали», «що система зробила з моїм замовленням».

    Журнал НЕ показує текст заблокованої спроби -- лише яке правило
    спрацювало. Це навмисно: за текстом можна було б з'ясувати, які прийоми
    ловляться, і підібрати обхід.

    Приклад: read_audit(session_id="demo-1", kind="hitl", limit=5).

    Args:
        session_id: Ідентифікатор власної сесії.
        kind: Рід подій або "all".
        limit: Кількість останніх записів (1-100).

    Returns:
        Події у вигляді рядків «час · рід · правило · вердикт · пояснення».
    """
    from audit import read as read_audit_records

    records = read_audit_records(session_id, kind=kind, limit=limit)
    if not records:
        return "У журналі безпеки цієї сесії подій немає."
    lines = [
        f"{r['ts'][:19]} · {r['kind']} · {r['name']} · {r['verdict']}"
        + (f" · {r['detail']}" if r["detail"] else "")
        for r in records
    ]
    return "\n".join(lines)


# Реекспорт RAG-інструмента: завдання вимагає, щоб tools_legacy.py містив
# і Pydantic-інструменти з ДЗ1, і пошук у базі знань з ДЗ2.
from knowledge import search_knowledge  # noqa: E402,F401
