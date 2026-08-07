#!/usr/bin/env python3
"""
scripts/manual/add_future_annotations.py

Chạy 1 lần để chèn `from __future__ import annotations` vào đúng vị
trí (ngay sau docstring module nếu có, nếu không thì làm dòng đầu
tiên) cho danh sách file bị dính lỗi bare generic annotation
(dict[]/list[]/set[]/tuple[]) hoặc union `X | Y` — cả 2 đều chỉ chạy
được từ Python 3.9+/3.10+, không tương thích Python 3.8 trong Airflow
container.

An toàn: verify lại bằng ast.parse() sau khi sửa mỗi file, dừng ngay
nếu có file nào bị hỏng cú pháp sau khi chèn. KHÔNG phải code
production — xoá sau khi dùng xong (script trong scripts/manual/
không bị pytest quét, theo cấu trúc đã có ở WEEK2.md).

Chạy: python3 scripts/manual/add_future_annotations.py
(chạy từ thư mục gốc project, nơi có data_pipeline/, technical_analysis/, nlp/)
"""

import ast
import sys
from pathlib import Path

# Danh sách xác định từ: grep -rn bare-generic pattern + phân tích
# import chain của dag_calculate_indicators / dag_sentiment_pipeline /
# dag_telegram_realtime. Thêm file khác vào đây nếu grep phát hiện
# thêm (xem lệnh grep union `|` gợi ý bên dưới).
FILES = [
    "technical_analysis/fibonacci.py",
    "technical_analysis/indicator_pipeline.py",
    "technical_analysis/backtest_data.py",
    "data_pipeline/telegram/channel_config.py",
    "data_pipeline/telegram/historical_scraper.py",
    "data_pipeline/labeling/export_for_labeling.py",
    "nlp/finbert_analyzer.py",
    "nlp/xlmr_analyzer.py",
    "nlp/phobert_analyzer.py",
]

FUTURE_LINE = "from __future__ import annotations\n"


def already_has_future_import(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
    return False


def find_insert_line(tree: ast.Module) -> int:
    """Trả về số dòng (0-indexed) để chèn future import vào."""
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        # Có docstring module -> chèn NGAY SAU dòng cuối docstring
        return tree.body[0].end_lineno
    # Không có docstring -> chèn làm dòng đầu tiên
    return 0


def process(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    if already_has_future_import(tree):
        return "SKIP (đã có sẵn từ trước)"

    lines = source.splitlines(keepends=True)
    insert_at = find_insert_line(tree)

    new_lines = lines[:insert_at] + [FUTURE_LINE] + lines[insert_at:]
    new_source = "".join(new_lines)

    # Verify NGAY trước khi ghi đè — không ghi nếu kết quả hỏng cú pháp
    ast.parse(new_source)

    path.write_text(new_source, encoding="utf-8")
    return f"OK (chèn tại dòng {insert_at + 1})"


def main():
    root = Path(".")
    any_error = False

    for rel_path in FILES:
        path = root / rel_path
        if not path.exists():
            print(f"[BỎ QUA - không tìm thấy] {rel_path}")
            continue
        try:
            result = process(path)
            print(f"[{result}] {rel_path}")
        except Exception as e:
            print(f"[LỖI - KHÔNG ghi đè] {rel_path}: {e}")
            any_error = True

    if any_error:
        print("\nCó lỗi xảy ra — kiểm tra lại các file [LỖI] ở trên trước khi tiếp tục.")
        sys.exit(1)

    print("\nHoàn tất. Chạy lại pytest + verify Airflow trước khi commit.")


if __name__ == "__main__":
    main()