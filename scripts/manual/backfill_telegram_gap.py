"""
scripts/manual/backfill_telegram_gap.py  (v3, 2026-10-08 — lấy theo KHOẢNG ID)

Lấp một lỗ dữ liệu Telegram đã đo được: lấy đúng các tin có
after_id < id < before_id của một kênh, cũ nhất trước, ghi vào Postgres
(ON CONFLICT (channel_name, id) DO NOTHING — chạy lại an toàn).

Vì sao viết lại v2 (Ngày 20): v2 lấy newest-first + limit 5000 + dừng ở
watermark MAX(id). Đúng cơ chế bug nợ #14: kênh có hơn 5000 tin trong lỗ
chỉ được lấp phần MỚI NHẤT. coin369channel (~260 tin/ngày) còn lỗ
2026-06-22 → 07-28 sau lần lấp N20. v3 không dùng watermark: lỗ nằm DƯỚI
MAX(id) nên watermark không bao giờ thấy nó.

Đo lỗ trước (read-only), xem docs/nhat-ky/2026-10-08_bug_telegram-catch-up-
newest-first-limit-lam-mat-tin-o-giua.md mục 4. after_id / before_id là id
của 2 tin ĐÃ CÓ hai bên lỗ (Telethon min_id/max_id không gồm biên).

SAU KHI CHẠY: tin mới nằm dưới watermark Chroma của kênh, dag_embed_messages
sẽ KHÔNG embed chúng → chạy scripts/manual/embed_missing.py.

Session: pause DAG telegram_realtime_sync trước nếu dùng chung
crypto_session_dag_catchup (2 client cùng mở 1 file SQLite → database is
locked).

Chạy (từ gốc repo):
    PYTHONPATH=. python scripts/manual/backfill_telegram_gap.py \\
        --channel Cointelegraph --after-id 71698 --before-id 72024 \\
        --session crypto_session_dag_catchup
"""

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

from data_pipeline.logger import get_logger  # noqa: E402
from data_pipeline.telegram.historical_scraper import (  # noqa: E402
    DatabaseWriter,
    scrape_channel_history,
)
from rag.config import get_db_dsn  # noqa: E402

logger = get_logger(__name__)

_COUNT_SQL = (
    "SELECT COUNT(*) FROM telegram_messages "
    "WHERE channel_name = $1 AND id > $2 AND id < $3"
)


async def _count_in_range(dsn: str, channel: str, after_id: int, before_id: int):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(_COUNT_SQL, channel, after_id, before_id)
    finally:
        await conn.close()


async def backfill(channel: str, after_id: int, before_id: int, session: str) -> None:
    if before_id - after_id < 2:
        raise ValueError(f"Khoảng ({after_id}, {before_id}) không chứa id nào")
    # Telethon không thấy file session sẽ tạo session RỖNG rồi treo chờ OTP.
    session_file = Path(f"{session}.session")
    if not session_file.is_file():
        raise FileNotFoundError(
            f"Không thấy {session_file.resolve()} — chạy từ gốc repo, kiểm tên session"
        )

    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    dsn = get_db_dsn()

    before = await _count_in_range(dsn, channel, after_id, before_id)
    logger.info(
        f"[GAP_BACKFILL] {channel} ({after_id}, {before_id}): "
        f"DB đang có {before} tin trong khoảng"
    )

    client = TelegramClient(session, api_id, api_hash)
    db_writer = DatabaseWriter(dsn=dsn, batch_size=100)
    fetched = 0
    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError(f"Session {session} chưa đăng nhập — không chạy tiếp")
        await db_writer.connect()
        try:
            async for msg in scrape_channel_history(
                client=client,
                channel=channel,
                limit=None,
                min_message_id=after_id,
                max_message_id=before_id,
                oldest_first=True,
            ):
                await db_writer.write(msg)
                fetched += 1
        finally:
            # Tin đã buffer vẫn được ghi nếu dừng giữa chừng (FloodWait):
            # chạy lại cùng lệnh, ON CONFLICT bỏ qua phần đã có.
            await db_writer.flush_remaining()
            await db_writer.close()

    after = await _count_in_range(dsn, channel, after_id, before_id)
    logger.info(
        f"[GAP_BACKFILL] {channel}: Telegram trả {fetched} tin hợp lệ, "
        f"DB {before} → {after} (+{after - before})"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--channel", required=True)
    p.add_argument("--after-id", type=int, required=True)
    p.add_argument("--before-id", type=int, required=True)
    p.add_argument("--session", required=True)
    a = p.parse_args()
    asyncio.run(backfill(a.channel, a.after_id, a.before_id, a.session))


if __name__ == "__main__":
    main()
