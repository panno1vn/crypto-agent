"""
scripts/manual/diag_watermark_crosschannel.py

Giả thuyết: watermark của dag_embed_messages là MAX(msg_id) TOÀN CỤC gộp
mọi kênh, nhưng msg_id là ID nội bộ của Telegram — đánh số ĐỘC LẬP theo
từng kênh, không liên quan gì tới thời gian hay ID của kênh khác. Nếu
đúng, một kênh có dải ID cao (VD tin id=71580) chỉ cần hoạt động là đẩy
watermark toàn cục vượt qua ID của kênh có dải ID thấp (VD bd_ventures,
id ~137xx) — khiến tin MỚI của kênh ID thấp bị bỏ qua VĨNH VIỄN, không
phải lag tạm thời sẽ tự hết khi DAG chạy lại.

Kiểm tra 3 việc:
  1. 2 tin đang thiếu (13791, 13792) thuộc kênh nào?
  2. msg_id CAO NHẤT đã embed của CHÍNH kênh đó là bao nhiêu?
  3. Có tin thuộc KÊNH KHÁC với msg_id > 13792 đã embed hay không?

Nếu (2) < 13792 và (3) có tồn tại → xác nhận giả thuyết watermark
toàn cục xuyên kênh. Đây sẽ là bug thật trong rag/ingestion.py, không
phải máy tắt/lag.

GIẢ ĐỊNH CẦN VERIFY: get_collection() không cần argument (như
diag_ground_truth_gap.py đã dùng); Chroma collection hỗ trợ where
operator $gt/$ne (chuẩn Chroma, cùng họ với $gte/$contains đã thấy
trong log retriever.py thật).
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

from rag.vector_store import get_collection  # VERIFY: tên hàm

DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'admin')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'admin123')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:5432/"
    f"{os.getenv('POSTGRES_DB', 'crypto_agent')}"
)

STUCK_IDS = [13791, 13792]


async def main() -> None:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=1)
    rows = await pool.fetch(
        "SELECT id, channel_name FROM telegram_messages WHERE id = ANY($1)",
        STUCK_IDS,
    )
    await pool.close()

    if not rows:
        print(f"Không tìm thấy {STUCK_IDS} trong Postgres — kiểm tra lại ID.")
        return

    for r in rows:
        print(f"Postgres: id={r['id']} channel={r['channel_name']}")

    channel = rows[0]["channel_name"]
    max_stuck_id = max(STUCK_IDS)

    collection = get_collection()

    # (2) msg_id cao nhất đã embed CỦA CHÍNH kênh này
    same_channel = collection.get(where={"channel": channel}, include=["metadatas"])
    same_channel_ids = [m["msg_id"] for m in same_channel["metadatas"]]
    max_same_channel = max(same_channel_ids) if same_channel_ids else None
    print(f"\nKênh '{channel}': msg_id CAO NHẤT đã embed = {max_same_channel}")
    print(f"(2 tin đang thiếu: {STUCK_IDS})")

    # (3) có tin kênh KHÁC với msg_id > max_stuck_id đã embed không?
    other_channel_higher = collection.get(
        where={
            "$and": [
                {"msg_id": {"$gt": max_stuck_id}},
                {"channel": {"$ne": channel}},
            ]
        },
        include=["metadatas"],
        limit=5,
    )
    n_found = len(other_channel_higher["metadatas"])
    print(
        f"\nSố tin KÊNH KHÁC có msg_id > {max_stuck_id} đã embed: {n_found}+ (giới hạn 5)"
    )

    if n_found and (max_same_channel is None or max_same_channel < max_stuck_id):
        sample = other_channel_higher["metadatas"][0]
        print(
            f"  Ví dụ: channel={sample.get('channel')!r} msg_id={sample.get('msg_id')}"
        )
        print(
            "\n→ XÁC NHẬN giả thuyết: watermark toàn cục đã bị kênh khác đẩy vượt "
            f"qua {max_stuck_id}, trong khi kênh '{channel}' chưa từng embed tới "
            f"ID đó. Tin mới của '{channel}' sẽ bị bỏ qua vĩnh viễn nếu watermark "
            "không tách riêng theo từng kênh. Đây là bug thật trong "
            "rag/ingestion.py, không phải lag do máy tắt."
        )
    else:
        print(
            "\n→ KHÔNG đủ bằng chứng cho giả thuyết cross-channel watermark. "
            "Có thể 2 tin này bị chặn bởi lý do khác (validation, filter ngôn "
            "ngữ, độ dài...) — cần đọc trực tiếp rag/ingestion.py."
        )


if __name__ == "__main__":
    asyncio.run(main())
