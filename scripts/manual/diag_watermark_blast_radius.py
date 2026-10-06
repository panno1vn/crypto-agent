"""
scripts/manual/diag_watermark_blast_radius.py

Đã xác nhận (2026-08-13): watermark của dag_embed_messages tính
MAX(msg_id) TOÀN CỤC gộp mọi kênh, nhưng msg_id là ID nội bộ Telegram,
đánh số độc lập theo từng kênh. Kênh có dải ID cao (VD chatdautucrypto,
msg_id=36815) đẩy watermark vượt qua kênh có dải ID thấp (VD
bd_ventures, dừng ở 13784) — khiến tin mới của kênh ID thấp bị bỏ qua
VĨNH VIỄN, im lặng, DAG vẫn báo "success".

File này quét TOÀN BỘ kênh để biết chính xác quy mô thiệt hại trước khi
sửa rag/ingestion.py và backfill.

GIẢ ĐỊNH CẦN VERIFY: get_collection() không cần argument, hỗ trợ
where={"channel": ...} (đã dùng thành công ở diag_watermark_crosschannel.py).
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


async def main() -> None:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    channels = await pool.fetch(
        "SELECT DISTINCT channel_name FROM telegram_messages ORDER BY channel_name"
    )
    collection = get_collection()

    header = (
        f"{'Kênh':<25} {'Max ID Postgres':>16} {'Max ID Chroma':>16} {'Tin kẹt':>10}"
    )
    print(header)
    print("-" * len(header))

    total_stuck = 0
    stuck_channels = 0

    for row in channels:
        ch = row["channel_name"]
        pg_max = await pool.fetchval(
            "SELECT MAX(id) FROM telegram_messages WHERE channel_name = $1", ch
        )

        chroma_result = collection.get(where={"channel": ch}, include=["metadatas"])
        chroma_ids = [m["msg_id"] for m in chroma_result["metadatas"]]
        chroma_max = max(chroma_ids) if chroma_ids else None

        n_stuck = await pool.fetchval(
            "SELECT COUNT(*) FROM telegram_messages WHERE channel_name = $1 AND id > $2",
            ch,
            chroma_max or 0,
        )

        if n_stuck:
            total_stuck += n_stuck
            stuck_channels += 1
        flag = " ⚠️" if n_stuck else ""
        print(f"{ch:<25} {pg_max!s:>16} {chroma_max!s:>16} {n_stuck:>8}{flag}")

    await pool.close()

    print(f"\n{stuck_channels}/{len(channels)} kênh đang có tin bị kẹt.")
    print(f"Tổng số tin bị kẹt trên toàn bộ kênh: {total_stuck}")
    if total_stuck:
        print(
            "\n→ Cần: (1) sửa watermark trong rag/ingestion.py tính RIÊNG theo "
            "từng kênh (MAX(msg_id) WHERE channel=X), (2) chạy backfill một lần "
            "để vét sạch số tin đang kẹt ở trên, (3) chỉ SAU ĐÓ mới chạy lại "
            "eval_retrieval_day26.py để có số sạch."
        )


if __name__ == "__main__":
    asyncio.run(main())
