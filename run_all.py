"""Прогін усього, що можна прогнати, з фіксацією виводу в outputs/.

Порядок навмисний: спершу те, що не потребує ключа. Якщо ключа немає,
перші кроки все одно відпрацюють і покажуть, що система жива.

Запуск: python run_all.py           (усе)
        python run_all.py --no-key  (лише те, що не потребує ключа)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

OUTPUTS = Path("outputs")

FREE = [
    ("pytest без моделі", [sys.executable, "-m", "pytest", "-v",
                           "-m", "not needs_model"], "pytest_output.txt"),
    ("demo1: MCP зсередини", [sys.executable, "demo1_mcp_server.py"],
     "demo1_mcp_server.txt"),
    ("demo5: рубежі захисту", [sys.executable, "demo5_guardrails.py"],
     "demo5_guardrails.txt"),
    ("red-team без моделі", [sys.executable, "red_team.py", "--offline"],
     "red_team_offline.txt"),
]

PAID = [
    ("demo2: MCP + LangGraph", [sys.executable, "demo2_mcp_langgraph.py"],
     "demo2_mcp_langgraph.txt"),
    ("demo3: MAS на трьох запитах", [sys.executable, "demo3_mas.py"],
     "demo3_mas.txt"),
    ("demo4: обрив і відновлення", [sys.executable, "demo4_persistence.py"],
     "demo4_persistence.txt"),
    ("demo6: підтвердження людиною", [sys.executable, "demo6_hitl.py"],
     "demo6_hitl.txt"),
    ("demo7: резервний провайдер", [sys.executable, "demo7_fallback.py"],
     "demo7_fallback.txt"),
    ("оцінки", [sys.executable, "evals.py"], "evals.txt"),
    ("red-team повністю", [sys.executable, "red_team.py"], "red_team.txt"),
]


def run(label: str, command: list[str], out_name: str) -> bool:
    """Виконати один крок і записати вивід.

    Args:
        label: Назва кроку для виводу.
        command: Команда.
        out_name: Ім'я файлу у теці outputs.

    Returns:
        True, якщо крок завершився успішно.
    """
    OUTPUTS.mkdir(exist_ok=True)
    print(f"\n▶ {label}")
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True)
    elapsed = time.monotonic() - started
    (OUTPUTS / out_name).write_text(result.stdout + result.stderr,
                                    encoding="utf-8")
    ok = result.returncode == 0
    print(f"  {'✅' if ok else '❌'} {elapsed:.1f} с → outputs/{out_name}")
    return ok


def main() -> None:
    """Прогнати всі кроки."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-key", action="store_true",
                        help="лише кроки, що не потребують ключа API")
    args = parser.parse_args()

    steps = FREE if args.no_key else FREE + PAID
    results = [run(*step) for step in steps]

    print("\n" + "═" * 78)
    print(f"  Успішно: {sum(results)} з {len(results)}")
    print("  Усі виводи — у теці outputs/")
    print("═" * 78)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
