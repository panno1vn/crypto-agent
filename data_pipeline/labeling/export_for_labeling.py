"""
data_pipeline/labeling/export_for_labeling.py

Ngày 15 — Export tin nhắn tiếng Việt cho Label Studio
========================================================
CẬP NHẬT (fix lệch nguồn): thay vì lấy pool bằng
'ORDER BY created_at DESC LIMIT N' trên toàn bộ channel gộp chung (dễ bị
1-2 kênh đăng dày đặc chiếm gần hết pool), giờ lấy CÂN BẰNG theo kênh —
mỗi kênh đóng góp tối đa `max_per_channel` tin vào pool trước khi gộp +
shuffle + cắt còn target_count.

Vẫn giữ:
  - Kiểm tra tổng số tin vi thực tế trong DB TRƯỚC khi xuất, cảnh báo
    nếu không đủ target_count.
  - Log phân bố theo channel TRƯỚC và SAU khi lấy mẫu, để thấy rõ tỷ lệ
    đã cân bằng hơn thế nào.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from data_pipeline.logger import get_logger

logger = get_logger(__name__)

OUTPUT_DIR = Path("data_pipeline/labeling/exports")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def build_dsn() -> str:
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )


async def check_vi_message_count(conn: asyncpg.Connection) -> int:
    """Đếm tổng số tin vi hợp lệ trước khi export, để không xuất 'lụi'."""
    count = await conn.fetchval("""
        SELECT COUNT(*)
        FROM telegram_messages
        WHERE language = 'vi'
          AND message_text IS NOT NULL
          AND LENGTH(message_text) > 20
        """)
    return count


async def channel_distribution(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Phân bố tin vi theo channel — dùng để phát hiện lệch nguồn."""
    return await conn.fetch("""
        SELECT channel_name, COUNT(*) AS cnt
        FROM telegram_messages
        WHERE language = 'vi'
          AND message_text IS NOT NULL
          AND LENGTH(message_text) > 20
        GROUP BY channel_name
        ORDER BY cnt DESC
        """)


async def fetch_balanced_pool(
    conn: asyncpg.Connection,
    max_per_channel: int,
) -> list[asyncpg.Record]:
    """
    Lấy pool CÂN BẰNG theo kênh: mỗi kênh đóng góp tối đa max_per_channel
    tin gần nhất, thay vì để 1-2 kênh đăng dày đặc chiếm gần hết pool.

    Dùng window function ROW_NUMBER() OVER (PARTITION BY channel_name ...)
    để lấy N tin mới nhất MỖI kênh trong 1 query duy nhất, hiệu quả hơn
    N query riêng lẻ.
    """
    return await conn.fetch(
        """
        WITH ranked AS (
            SELECT
                id, message_text, channel_name, created_at, views, forwards,
                ROW_NUMBER() OVER (
                    PARTITION BY channel_name
                    ORDER BY created_at DESC
                ) AS rn
            FROM telegram_messages
            WHERE language = 'vi'
              AND message_text IS NOT NULL
              AND LENGTH(message_text) > 20
        )
        SELECT id, message_text, channel_name, created_at, views, forwards
        FROM ranked
        WHERE rn <= $1
        """,
        max_per_channel,
    )


def to_label_studio_tasks(rows: list[asyncpg.Record]) -> list[dict]:
    """
    Format chuẩn Label Studio import: list các task, mỗi task có key 'data'.
    KHÔNG set top-level 'id' — để Label Studio tự sinh task_id nội bộ,
    tránh nhầm lẫn với id gốc của telegram_messages.
    """
    tasks = []
    for row in rows:
        tasks.append(
            {
                "data": {
                    "message_id": row["id"],
                    "text": row["message_text"],
                    "channel_name": row["channel_name"],
                    "created_at": row["created_at"].isoformat(),
                    "views": row["views"],
                    "forwards": row["forwards"],
                }
            }
        )
    return tasks


async def main(
    target_count: int = 2000,
    max_per_channel: int = 400,
    seed: int = 42,
) -> None:
    """
    Args:
        target_count:     Số tin cuối cùng cần label.
        max_per_channel:  Trần số tin lấy từ MỖI kênh vào pool trước khi
                           shuffle + cắt còn target_count. Với 9 kênh vi
                           hiện có, 400/kênh → pool tối đa ~3600, đủ dư so
                           với target 2000 mà không để 1 kênh áp đảo.
        seed:              Cố định để reproducible giữa các lần chạy.
    """
    load_dotenv()
    dsn = build_dsn()

    conn = await asyncpg.connect(dsn)
    try:
        total_vi = await check_vi_message_count(conn)
        logger.info(f"[EXPORT] Tổng số tin vi hợp lệ trong DB: {total_vi}")

        if total_vi < target_count:
            logger.warning(
                f"[EXPORT] CHỈ CÓ {total_vi} tin vi, thiếu so với mục tiêu "
                f"{target_count}. Kiểm tra lại danh sách channel tiếng Việt "
                f"trước khi label — label trên tập quá nhỏ sẽ làm F1 score "
                f"Ngày 17 không đáng tin."
            )

        dist_before = await channel_distribution(conn)
        logger.info("[EXPORT] Phân bố TRƯỚC khi lấy mẫu (toàn bộ DB):")
        for r in dist_before:
            logger.info(f"  {r['channel_name']}: {r['cnt']}")

        rows = await fetch_balanced_pool(conn, max_per_channel)
        rows = list(rows)

        dist_after_cap = Counter(r["channel_name"] for r in rows)
        logger.info(
            f"[EXPORT] Phân bố SAU khi cap {max_per_channel}/kênh "
            f"(pool={len(rows)}):"
        )
        for channel, cnt in dist_after_cap.most_common():
            logger.info(f"  {channel}: {cnt}")

        random.seed(seed)
        random.shuffle(rows)
        selected = rows[:target_count]

        dist_final = Counter(r["channel_name"] for r in selected)
        logger.info(
            f"[EXPORT] Phân bố CUỐI CÙNG trong {len(selected)} tin đã chọn "
            f"để label:"
        )
        for channel, cnt in dist_final.most_common():
            pct = 100 * cnt / len(selected)
            logger.info(f"  {channel}: {cnt} ({pct:.1f}%)")

        tasks = to_label_studio_tasks(selected)

        out_path = (
            OUTPUT_DIR / f"labeling_batch_{datetime.now(timezone.utc):%Y%m%d}.json"
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)

        logger.info(f"[EXPORT] Đã ghi {len(tasks)} tasks vào {out_path}")
        logger.info(
            "[EXPORT] Import file này vào Label Studio qua UI "
            "(Create Project → Import) hoặc qua API."
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
