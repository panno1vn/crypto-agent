"""
scripts/audit_all_labels.py

Đối chiếu TOÀN BỘ message đã label (data/raw/full_labeled_export.json)
với gợi ý từ Claude (rubric 3 bước) — ghi ra kết quả thô để bước sau
(export_audit_xlsx.py) build file Excel có highlight.

Chạy SAU KHI label xong toàn bộ (không chỉ 1000 cũ), sau khi đã export
lại full_labeled_export.json (xem hướng dẫn export ở đầu conversation).

XONG VIỆC → REVOKE ANTHROPIC_API_KEY NGAY tại console.anthropic.com.
"""

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
TRACKED_COINS = ["BTC", "ETH", "BNB", "SOL", "XRP"]

INPUT_PATH = Path("data/raw/full_labeled_export.json")
OUTPUT_PATH = Path("data/raw/audit_all_results.json")

SYSTEM_PROMPT = f"""Bạn là annotator chuyên gán nhãn sentiment cho tin tức crypto tiếng Việt.

Áp dụng ĐÚNG quy trình 3 bước sau, theo thứ tự, không được bỏ bước:

BƯỚC 1: Tin có nhắc TRỰC TIẾP tới 1 trong 5 coin ({', '.join(TRACKED_COINS)}),
hoặc nói về CẢ THỊ TRƯỜNG crypto nói chung không?
→ KHÔNG → "neutral", dừng lại.
→ CÓ → sang bước 2.

BƯỚC 2: Tin có mô tả 1 SỰ KIỆN CỤ THỂ (không phải ý kiến/phân tích/giáo
dục chung chung) có khả năng ảnh hưởng giá? Ví dụ hợp lệ: niêm yết sàn,
hack, lệnh cấm, hợp tác, ETF, gọi vốn, lệnh trade cụ thể (Entry/SL/TP),
dump/bán tháo. Ví dụ KHÔNG hợp lệ: bình luận chung, giáo dục, meme.
→ KHÔNG → "neutral", dừng lại.
→ CÓ → sang bước 3.

BƯỚC 3: Sự kiện đó đẩy giá LÊN hay XUỐNG?
→ LÊN rõ ràng → "positive"
→ XUỐNG rõ ràng → "negative"
→ Không rõ chiều → "neutral"

Phân vân ở bước nào → luôn lùi về "neutral".

Luôn gọi tool submit_audit_label để trả kết quả."""

SUBMIT_AUDIT_TOOL = {
    "name": "submit_audit_label",
    "description": "Nộp nhãn sentiment cho 1 tin nhắn crypto, kèm bước dừng trong quy trình 3 bước",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "step_reached": {"type": "string", "enum": ["1", "2", "3"]},
            "reasoning": {
                "type": "string",
                "description": "1 câu ngắn giải thích, tiếng Việt",
            },
        },
        "required": ["label", "step_reached", "reasoning"],
    },
}


def call_llm(text: str, max_retries: int = 5) -> dict:
    for attempt in range(max_retries):
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 300,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": text}],
                "tools": [SUBMIT_AUDIT_TOOL],
                "tool_choice": {"type": "tool", "name": "submit_audit_label"},
            },
            timeout=30,
        )
        if response.status_code in (429, 529):
            wait = min(30, 3 * (2**attempt))
            print(f"[RATE LIMIT] {response.status_code}, chờ {wait}s...")
            time.sleep(wait)
            continue
        response.raise_for_status()
        data = response.json()
        for block in data.get("content", []):
            if (
                block.get("type") == "tool_use"
                and block.get("name") == "submit_audit_label"
            ):
                return block["input"]
        return {
            "label": "neutral",
            "step_reached": "1",
            "reasoning": "NO_TOOL_USE_BLOCK",
        }

    return {"label": None, "step_reached": None, "reasoning": "RATE_LIMIT_EXHAUSTED"}


def extract_human_label(task: dict) -> str | None:
    try:
        return task["annotations"][0]["result"][0]["value"]["choices"][0]
    except (KeyError, IndexError):
        return None


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Không thấy {INPUT_PATH}. Export lại toàn bộ task đã label trước "
            f"(xem lệnh curl /export ở hướng dẫn), lưu đúng tên file này."
        )

    with open(INPUT_PATH, encoding="utf-8") as f:
        all_tasks = json.load(f)
    print(f"[INFO] Đọc {len(all_tasks)} task đã label từ {INPUT_PATH}")

    results = []
    checked_ids = set()
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            results = json.load(f)
        checked_ids = {r["task_id"] for r in results if r.get("llm_label") is not None}
        print(f"[RESUME] {len(checked_ids)} task đã xử lý trước đó, bỏ qua.")

    total = len(all_tasks)
    for i, task in enumerate(all_tasks, 1):
        task_id = task["id"]
        if task_id in checked_ids:
            continue

        text = task.get("data", {}).get("text", "")
        human_label = extract_human_label(task)
        if not text or not human_label:
            continue

        try:
            llm_result = call_llm(text)
        except requests.exceptions.HTTPError as e:
            # Lưu NGAY những gì đã có trước khi làm bất cứ điều gì khác —
            # ưu tiên số 1 là không mất message đã trả tiền.
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)

            status = e.response.status_code if e.response is not None else None
            body = e.response.text[:300] if e.response is not None else str(e)

            if status == 400 and (
                "credit" in body.lower() or "balance" in body.lower()
            ):
                print(
                    f"\n[HẾT CREDIT] Dừng tại message {i}/{total}. Đã lưu {len(results)} kết quả vào {OUTPUT_PATH}."
                )
                print(f"[CHI TIẾT LỖI] {body}")
                print(
                    "→ Nạp thêm credit tại console.anthropic.com, rồi chạy lại đúng lệnh này —"
                )
                print(
                    "  script tự resume từ chỗ dừng, KHÔNG tính tiền lại các message đã xong."
                )
                print(
                    f"→ Muốn xuất Excel NGAY với {len(results)} message đã có: python3 scripts/export_audit_xlsx.py"
                )
                return
            else:
                print(
                    f"\n[LỖI KHÔNG XÁC ĐỊNH] status={status} tại message {i}/{total}. Đã lưu {len(results)} kết quả."
                )
                print(f"[CHI TIẾT] {body}")
                print("→ Kiểm tra lỗi trên, sửa xong chạy lại — script tự resume.")
                return
        except Exception as e:
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(
                f"\n[LỖI] {type(e).__name__}: {e} tại message {i}/{total}. Đã lưu {len(results)} kết quả."
            )
            print("→ Chạy lại đúng lệnh này — script tự resume từ chỗ dừng.")
            return

        results.append(
            {
                "task_id": task_id,
                "channel_name": task.get("data", {}).get("channel_name", ""),
                "created_at": task.get("data", {}).get("created_at", ""),
                "text": text,
                "human_label": human_label,
                "llm_label": llm_result.get("label"),
                "llm_step_reached": llm_result.get("step_reached"),
                "llm_reasoning": llm_result.get("reasoning"),
            }
        )

        # Lưu sau MỖI message — không đợi mỗi 20 nữa. Với ~2000 message,
        # ghi JSON ra đĩa mỗi lần gần như không tốn thời gian đáng kể,
        # nhưng đổi lại tối đa chỉ mất 1 message nếu crash giữa chừng,
        # thay vì tối đa 19 message như trước.
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        if i % 20 == 0:
            print(f"[PROGRESS] {i}/{total}")

        time.sleep(1.5)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    mismatches = [
        r for r in results if r["llm_label"] and r["llm_label"] != r["human_label"]
    ]
    print(f"\n[DONE] Xử lý {len(results)} message.")
    print(f"[MISMATCH] {len(mismatches)} bất đồng giữa bạn và Claude.")
    print(f"[SAVE] -> {OUTPUT_PATH}")
    print(
        "\n→ Chạy tiếp: python3 scripts/export_audit_xlsx.py để xuất file Excel có highlight."
    )


if __name__ == "__main__":
    main()
