"""
tests/unit/test_dag_imports.py

Preflight cho mọi DAG trong dags/ — không cần Airflow.

Bug thật (phát hiện 2026-10-08): ngày 2026-08-13 rag/ingestion.py xóa
`get_last_embedded_id` và bỏ tham số `after_id` của `run_ingestion()` (sửa
bug watermark theo kênh), nhưng dags/dag_embed_messages.py vẫn import tên
cũ. DAG import module của repo BÊN TRONG thân hàm (để scheduler parse nhanh),
nên lỗi chỉ lộ ra lúc task chạy: ImportError mọi lần, gần 2 tháng.

2026-10-08 (nợ #15): mở rộng sang scripts/manual/*.py — ingest_to_chroma.py
cũng gọi run_ingestion(after_id=...) đã bị xóa, chỉ lộ ra khi cần chạy tay.

Test này đọc AST của từng file DAG (không import file DAG, vì local không có
Airflow), tìm mọi `from <package của repo> import ...`, rồi kiểm:
  1. Tên được import có tồn tại trong module.
  2. Mọi lời gọi tới tên đó dùng keyword argument có trong chữ ký hàm.
"""

import ast
import importlib
import inspect
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DAG_FILES = sorted((REPO / "dags").glob("*.py")) + sorted(
    (REPO / "scripts" / "manual").glob("*.py")
)
REPO_PACKAGES = {"agent", "data_pipeline", "nlp", "rag", "technical_analysis"}


def _repo_imports(tree: ast.AST) -> list[tuple[str, str, int]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in REPO_PACKAGES:
                for alias in node.names:
                    out.append((node.module, alias.name, node.lineno))
    return out


def _keyword_calls(tree: ast.AST, name: str) -> list[tuple[list[str], int]]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ):
            kws = [k.arg for k in node.keywords if k.arg is not None]
            calls.append((kws, node.lineno))
    return calls


def test_co_file_dag():
    assert DAG_FILES, "không tìm thấy dags/*.py — đường dẫn test sai?"


@pytest.mark.parametrize("dag_file", DAG_FILES, ids=lambda p: p.name)
def test_dag_import_va_keyword_khop_source(dag_file):
    tree = ast.parse(dag_file.read_text(encoding="utf-8"))
    errors = []
    for module_name, name, lineno in _repo_imports(tree):
        module = importlib.import_module(module_name)
        if not hasattr(module, name):
            errors.append(f"{dag_file.name}:{lineno} {module_name} không có {name!r}")
            continue
        obj = getattr(module, name)
        if not callable(obj):
            continue
        params = inspect.signature(obj).parameters
        accepts_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        for kws, call_line in _keyword_calls(tree, name):
            unknown = [k for k in kws if k not in params]
            if unknown and not accepts_kwargs:
                errors.append(
                    f"{dag_file.name}:{call_line} {name}() không nhận {unknown}"
                )
    assert not errors, "\n".join(errors)
