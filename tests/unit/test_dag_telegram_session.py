"""
tests/unit/test_dag_telegram_session.py

Bug thật (2026-10-08 01:19): trigger tay telegram_realtime_sync trong lúc run
scheduled đang chạy → 2 TelegramClient cùng mở 1 file session SQLite →
"database is locked". DAG thiếu max_active_runs=1.

Test đọc AST (local không có Airflow): mọi file trong dags/ có tạo
TelegramClient thì lời gọi DAG(...) phải có max_active_runs=1.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DAG_FILES = sorted((REPO / "dags").glob("*.py"))


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


TELEGRAM_DAGS = [
    f
    for f in DAG_FILES
    if _calls(ast.parse(f.read_text(encoding="utf-8")), "TelegramClient")
]


def test_co_dag_telegram():
    assert TELEGRAM_DAGS, "không tìm thấy DAG nào tạo TelegramClient — test sai?"


@pytest.mark.parametrize("dag_file", TELEGRAM_DAGS, ids=lambda p: p.name)
def test_dag_telegram_max_active_runs_1(dag_file):
    tree = ast.parse(dag_file.read_text(encoding="utf-8"))
    dag_calls = _calls(tree, "DAG")
    assert dag_calls, f"{dag_file.name}: không thấy lời gọi DAG(...)"
    for call in dag_calls:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        value = kw.get("max_active_runs")
        assert isinstance(value, ast.Constant) and value.value == 1, (
            f"{dag_file.name}:{call.lineno} DAG dùng session Telegram phải có "
            "max_active_runs=1 (2 run song song → 'database is locked')"
        )
