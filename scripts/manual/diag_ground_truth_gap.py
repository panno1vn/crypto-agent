"""
scripts/manual/diag_ground_truth_gap.py

Chẩn đoán nhanh: vì sao search_telegram_news(coin='BTC', hours_ago=6)
trả về 0 ứng viên dù Postgres có ground truth?

2 khả năng cần phân biệt:
  (a) Tin CHƯA được embed vào Chroma (lag của dag_embed_messages,
      chạy mỗi 30 phút) → id ground truth KHÔNG có trong Chroma.
  (b) Tin ĐÃ embed, nhưng where-filter (coins/created_ts) không khớp
      → id ground truth CÓ trong Chroma, nhưng collection.get(ids=...)
      trả về đủ, còn query với where lại rỗng.

Không cần load embedder/reranker (nhanh, không lazy-load 30s).

GIẢ ĐỊNH CẦN VERIFY: `get_collection()` trong rag/vector_store.py không
cần argument, trả về Chroma Collection object có .get(ids=...) chuẩn
API Chroma. Suy từ mô tả ở GHI_CHU_NGAY22.md, chưa test qua smoke test
riêng — nếu sai tên hàm, sửa dòng import bên dưới.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

import asyncpg

from rag.vector_store import get_collection  # VERIFY: tên hàm

DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'admin')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'admin123')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:5432/"
    f"{os.getenv('POSTGRES_DB', 'crypto_agent')}"
)

# Đổi coin/hours_ago tuỳ combo muốn kiểm tra — dùng đúng combo đã thấy
# "→ 0 ứng viên" trong log thật (BTC/6 là ví dụ rõ nhất).
CHECK_COIN = "BTC"
CHECK_HOURS_AGO = 6


async def main() -> None:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1)
    since = datetime.utcnow() - timedelta(hours=CHECK_HOURS_AGO)
    rows = await pool.fetch(
        """
        SELECT id, message_text, created_at FROM telegram_messages
        WHERE $1 = ANY(coins_mentioned) AND created_at >= $2
        ORDER BY created_at DESC
        """,
        CHECK_COIN,
        since,
    )
    await pool.close()

    print(f"Postgres: {len(rows)} tin nhắc {CHECK_COIN} trong {CHECK_HOURS_AGO}h qua")
    for r in rows:
        print(
            f"  id={r['id']}  created_at={r['created_at']}  text[:60]={r['message_text'][:60]!r}"
        )

    if not rows:
        print("Không có ground truth để kiểm — đổi CHECK_COIN/CHECK_HOURS_AGO.")
        return

    ids = [str(r["id"]) for r in rows]  # Chroma id thường lưu dạng string
    collection = get_collection()
    found = collection.get(ids=ids)
    found_ids = set(found["ids"])

    print(f"\nChroma collection.get(ids=...): tìm thấy {len(found_ids)}/{len(ids)}")
    missing = set(ids) - found_ids
    if missing:
        print(f"→ THIẾU trong Chroma (chưa embed / lag): {sorted(missing)}")
        print(
            "  Kết luận: giả thuyết (a) — embedding lag. KHÔNG phải bug "
            "truncation. Suite C hôm nay có thể bị lẫn nhiễu bởi lag này."
        )
    else:
        print("→ Tất cả ĐÃ có trong Chroma.")
        print(
            "  Kết luận: giả thuyết (b) — where-filter (coins/created_ts) "
            "không khớp dù embedding đã tồn tại. Cần đọc lại "
            "_build_where() và metadata thật của các id này (collection.get "
            "trả về metadatas — in ra so sánh coins/created_ts với query filter)."
        )
        for doc_id, meta in zip(found["ids"], found["metadatas"]):
            print(f"    id={doc_id} metadata={meta}")


if __name__ == "__main__":
    asyncio.run(main())
