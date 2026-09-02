"""Усі граничні значення системи в одному місці.

Жодного числа в бізнес-логіці: кожна межа читається звідси, а сюди — з
оточення. Служби MCP беруть ті самі значення зі змінних оточення, бо вони
окремі процеси.

Безглузді значення не підмінюються умовчанням — модуль падає з ValueError:
тиха підміна означала б роботу не з тими межами, які задали.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Ключі читаються з .env — так само, як у ДЗ1 і ДЗ2. Файл у .gitignore.
# Завдяки цьому жоден скрипт не потребує ручного `source`.
load_dotenv(".env", override=False)


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    """Прочитати цілу межу з оточення.

    Args:
        name: Ім'я змінної оточення.
        default: Значення за умовчанням.
        minimum: Найменше допустиме значення.

    Returns:
        Ціле значення межі.

    Raises:
        ValueError: Значення не число або менше за minimum.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: очікується ціле число, отримано {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name}: значення {value} менше за допустиме {minimum}")
    return value


def _float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Прочитати дробову межу з оточення. Правила ті самі, що в _int.

    Args:
        name: Ім'я змінної оточення.
        default: Значення за умовчанням.
        minimum: Найменше допустиме значення.

    Returns:
        Дробове значення межі.

    Raises:
        ValueError: Значення не число або менше за minimum.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: очікується число, отримано {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name}: значення {value} менше за допустиме {minimum}")
    return value


def _list(name: str, default: list[str]) -> list[str]:
    """Прочитати список з оточення. Роздільник — кома, пробіли обрізаються.

    Args:
        name: Ім'я змінної оточення.
        default: Значення за умовчанням.

    Returns:
        Перелік рядків без зайвих пробілів.
    """
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _level(name: str, default: str) -> str:
    """Прочитати рівень журналювання з оточення.

    Args:
        name: Ім'я змінної оточення.
        default: Рівень за умовчанням.

    Returns:
        Назва рівня у верхньому регістрі.

    Raises:
        ValueError: Невідома назва рівня.
    """
    value = os.getenv(name, default).strip().upper()
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if value not in allowed:
        raise ValueError(f"{name}: невідомий рівень '{value}', очікується "
                         f"одне з {sorted(allowed)}")
    return value


def _path(name: str, default: str) -> Path:
    """Прочитати шлях з оточення.

    Args:
        name: Ім'я змінної оточення.
        default: Шлях за умовчанням.

    Returns:
        Шлях як об'єкт Path.
    """
    return Path(os.getenv(name, default))


# ── Гроші ─────────────────────────────────────────────────────────────────────
BUDGET_MONTHLY_EUR = _float("BUDGET_MONTHLY_EUR", 60.0, minimum=0.01)
SESSION_SPEND_LIMIT_EUR = _float("SESSION_SPEND_LIMIT_EUR", 40.0, minimum=0.01)
MAX_COPIES_PER_ORDER = _int("MAX_COPIES_PER_ORDER", 3)

# ── Розміри ───────────────────────────────────────────────────────────────────
MAX_INPUT_LEN = _int("MAX_INPUT_LEN", 5000)
MAX_OUTPUT_LEN = _int("MAX_OUTPUT_LEN", 8000)
MAX_TOOL_RESPONSE_BYTES = _int("MAX_TOOL_RESPONSE_BYTES", 20000)
MAX_PLAN_STEPS = _int("MAX_PLAN_STEPS", 8)

# ── Кроки ─────────────────────────────────────────────────────────────────────
MAX_STEPS_PER_AGENT = _int("MAX_STEPS_PER_AGENT", 10)
LOOP_DETECTOR_MAX_REPEATS = _int("LOOP_DETECTOR_MAX_REPEATS", 3)
MAX_HOPS = _int("MAX_HOPS", 4)

# ── Час ───────────────────────────────────────────────────────────────────────
AGENT_TIMEOUT_SEC = _float("AGENT_TIMEOUT_SEC", 90.0, minimum=1.0)
RUN_TIMEOUT_SEC = _float("RUN_TIMEOUT_SEC", 300.0, minimum=1.0)

# ── Витрати моделі ────────────────────────────────────────────────────────────
MAX_LLM_CALLS_PER_RUN = _int("MAX_LLM_CALLS_PER_RUN", 20)
MAX_TOOL_CALLS_PER_RUN = _int("MAX_TOOL_CALLS_PER_RUN", 25)
MAX_APPROVALS_PER_SESSION = _int("MAX_APPROVALS_PER_SESSION", 5)

# ── Частота ───────────────────────────────────────────────────────────────────
RATE_LIMIT_MAX_CALLS = _int("RATE_LIMIT_MAX_CALLS", 30)
RATE_LIMIT_WINDOW_SEC = _float("RATE_LIMIT_WINDOW_SEC", 60.0, minimum=1.0)

# ── Мережа ────────────────────────────────────────────────────────────────────
EGRESS_ALLOWED_HOSTS = _list("EGRESS_ALLOWED_HOSTS", [
    "www.wikidata.org", "query.wikidata.org", "gutendex.com",
])
MAIL_ALLOWED_DOMAINS = _list("MAIL_ALLOWED_DOMAINS", ["example.com", "example.org"])

# ── Шляхи ─────────────────────────────────────────────────────────────────────
LOG_LEVEL = _level("LOG_LEVEL", "WARNING")
STATE_DIR = _path("STATE_DIR", "state")
CHROMA_PATH = _path("CHROMA_PATH", "./chroma_db")
DB_PATH = _path("DB_PATH", "state/agent_state.db")
TRAJECTORY_PATH = _path("TRAJECTORY_PATH", "trajectory.json")
AUDIT_PATH = _path("AUDIT_PATH", "state/audit.jsonl")
MCP_MANIFEST_PATH = _path("MCP_MANIFEST_PATH", "mcp_manifest.json")

STATE_DIR.mkdir(parents=True, exist_ok=True)

# ── Модель ────────────────────────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "google")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_LITE_MODEL = os.getenv("GEMINI_LITE_MODEL", "gemini-flash-lite-latest")
PRICE_ANTHROPIC_INPUT = _float("PRICE_ANTHROPIC_INPUT", 1.0)
PRICE_ANTHROPIC_OUTPUT = _float("PRICE_ANTHROPIC_OUTPUT", 5.0)
PRICE_GOOGLE_INPUT = _float("PRICE_GOOGLE_INPUT", 0.075)
PRICE_GOOGLE_OUTPUT = _float("PRICE_GOOGLE_OUTPUT", 0.3)

# ── Перевірка атаками ─────────────────────────────────────────────────────────
DEEPTEAM_ATTACKS_PER_TYPE = _int("DEEPTEAM_ATTACKS_PER_TYPE", 1)
DEEPTEAM_VULNERABILITIES = _list("DEEPTEAM_VULNERABILITIES", [
    "RBAC", "BFLA", "BOLA", "PIILeakage", "PromptLeakage",
    "ExcessiveAgency", "GoalTheft", "SSRF", "Misinformation",
    "IntellectualProperty",
])
DEEPTEAM_ENHANCEMENTS = _list("DEEPTEAM_ENHANCEMENTS", [
    "Multilingual", "Base64", "Roleplay", "Crescendo",
])


def budget_policy_text() -> str:
    """Регламент витрат словами — його читає модель через ресурс policy://budget.

    Текст будується з тих самих констант, тому число і слово не розійдуться:
    інакше модель назве людині ліміт, якого немає.

    Returns:
        Текст регламенту українською.
    """
    return (
        f"Регламент витрат на самоосвіту.\n"
        f"Місячний ліміт: {BUDGET_MONTHLY_EUR:.2f} EUR.\n"
        f"Максимум примірників в одному замовленні: {MAX_COPIES_PER_ORDER}.\n"
        f"Ліміт витрат за одну сесію: {SESSION_SPEND_LIMIT_EUR:.2f} EUR.\n"
        f"Замовлення друкованого видання незворотне і потребує підтвердження людини.\n"
        f"Спершу слід перевірити, чи текст доступний безкоштовно."
    )


def mail_policy_text() -> str:
    """Регламент відправлення словами — ресурс policy://mail.

    Returns:
        Текст регламенту з переліком дозволених доменів отримувача.
    """
    domains = ", ".join(MAIL_ALLOWED_DOMAINS)
    return (
        f"Регламент відправлення списків літератури.\n"
        f"Дозволені домени отримувача: {domains}.\n"
        f"Лист неможливо відкликати, тому відправлення потребує підтвердження людини.\n"
        f"Редагувати адресу отримувача заборонено: підміна адреси — це і є атака."
    )
