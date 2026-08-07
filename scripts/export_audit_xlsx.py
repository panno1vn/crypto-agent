"""
scripts/export_audit_xlsx.py

Đọc data/raw/audit_all_results.json (đã chạy audit_all_labels.py trước),
xuất ra file Excel (.xlsx) với highlight màu:
  - Đỏ nhạt: bất đồng NGHIÊM TRỌNG (đổi cực positive<->negative)
  - Vàng nhạt: bất đồng NHẸ (lệch sang/từ neutral)
  - Trắng: khớp nhau, không cần xem

Mở file bằng Excel/LibreOffice/Google Sheets — sort/filter theo cột
"Mức độ" để ưu tiên xem HIGH trước.
"""

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

INPUT_PATH = Path("data/raw/audit_all_results.json")
OUTPUT_PATH = Path("data/raw/audit_result.xlsx")

FILL_HIGH = PatternFill(
    start_color="F8CBCB", end_color="F8CBCB", fill_type="solid"
)  # đỏ nhạt
FILL_LOW = PatternFill(
    start_color="FFF2B2", end_color="FFF2B2", fill_type="solid"
)  # vàng nhạt
FONT_HEADER = Font(name="Arial", bold=True, color="FFFFFF")
FILL_HEADER = PatternFill(start_color="404040", end_color="404040", fill_type="solid")
FONT_BODY = Font(name="Arial", size=10)


def severity(human_label: str, llm_label: str) -> str:
    if human_label == llm_label:
        return "MATCH"
    if {human_label, llm_label} == {"positive", "negative"}:
        return "HIGH"
    return "LOW"


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Không thấy {INPUT_PATH}. Chạy audit_all_labels.py trước."
        )

    with open(INPUT_PATH, encoding="utf-8") as f:
        results = json.load(f)

    wb = Workbook()
    ws = wb.active
    ws.title = "Audit"

    headers = [
        "Task ID",
        "Kênh",
        "Thời gian",
        "Nội dung tin nhắn",
        "Nhãn của bạn",
        "Nhãn Claude",
        "Bước dừng",
        "Claude giải thích",
        "Mức độ",
    ]
    ws.append(headers)
    for col_idx, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = FONT_HEADER
        cell.fill = FILL_HEADER
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    # Giữ nguyên thứ tự theo Task ID tăng dần — khớp đúng thứ tự mặc định
    # trong Label Studio Data Manager, để bạn dễ đối chiếu ngược lại tìm
    # đúng task cần sửa. KHÔNG sort theo mức độ — dùng AutoFilter bên dưới
    # để bạn tự lọc khi cần xem riêng HIGH, vẫn giữ được thứ tự gốc khi
    # xóa filter.
    results_sorted = sorted(results, key=lambda r: r["task_id"])

    row_idx = 2
    high_count = low_count = match_count = 0
    for r in results_sorted:
        if not r.get("llm_label"):
            continue  # bỏ qua case RATE_LIMIT_EXHAUSTED chưa xử lý được

        sev = severity(r["human_label"], r["llm_label"])
        if sev == "HIGH":
            high_count += 1
        elif sev == "LOW":
            low_count += 1
        else:
            match_count += 1

        ws.append(
            [
                r["task_id"],
                r.get("channel_name", ""),
                r.get("created_at", ""),
                r["text"][:500],  # giới hạn độ dài tránh ô quá to
                r["human_label"],
                r["llm_label"],
                r.get("llm_step_reached", ""),
                r.get("llm_reasoning", ""),
                sev,
            ]
        )

        fill = FILL_HIGH if sev == "HIGH" else (FILL_LOW if sev == "LOW" else None)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = FONT_BODY
            cell.alignment = Alignment(vertical="top", wrap_text=(col_idx in (4, 8)))
            if fill:
                cell.fill = fill

        row_idx += 1

    # Độ rộng cột hợp lý
    widths = {1: 10, 2: 18, 3: 20, 4: 60, 5: 14, 6: 14, 7: 10, 8: 40, 9: 10}
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # AutoFilter — bấm mũi tên ở header để lọc/sort theo bất kỳ cột nào
    # (vd Mức độ = HIGH) mà không phá thứ tự gốc; xóa filter là về lại
    # đúng thứ tự Task ID ban đầu.
    last_row = row_idx - 1
    ws.auto_filter.ref = f"A1:I{last_row}"

    wb.save(OUTPUT_PATH)

    print(f"[DONE] Xuất file: {OUTPUT_PATH}")
    print(f"[HIGH] {high_count} dòng đỏ (đổi cực positive<->negative) — xem TRƯỚC TIÊN")
    print(f"[LOW] {low_count} dòng vàng (lệch neutral)")
    print(f"[MATCH] {match_count} dòng khớp nhau — không cần xem")


if __name__ == "__main__":
    main()
