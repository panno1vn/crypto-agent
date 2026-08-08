"""
scripts/manual/backfill_telegram_gap.py  (v2 — tự query min_message_id
lúc runtime thay vì hardcode, để chạy lại nhiều lần an toàn và không
quét dư phần đã xong)
"""

import asyncio
import os

import asyncpg
from dotenv import load_dotenv
from telethon import TelegramClient

from data_pipeline.logger import get_logger
from data_pipeline.telegram.historical_scraper import (
    DatabaseWriter,
    scrape_channel_history,
)

load_dotenv()
logger = get_logger(__name__)

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]

DB_DSN = (
    f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
    f":{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
)

CHANNELS = [
    "coin369channel",
    "coingape",
    "Cointelegraph",
    "bd_ventures",
    "binance_announcements",
    "chatdautucrypto",
    "thichcheatair",
    "nghiencryptochannel",
    "crypto_musk1m",
    "RIC_Capital_Channel",
    "Tradecoinspeed",
    "bitcoin_vietnam_news",
    "whale_alert",
]

LIMIT_PER_CHANNEL = 5000


async def get_current_max_ids(dsn: str) -> dict[str, int]:
    """Query MAX(id) THẬT NGAY LÚC CHẠY — không dùng số cũ hardcode."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT channel_name, MAX(id) AS max_id FROM telegram_messages "
            "GROUP BY channel_name"
        )
        return {r["channel_name"]: r["max_id"] for r in rows}
    finally:
        await conn.close()


async def main() -> None:
    max_ids = await get_current_max_ids(DB_DSN)
    logger.info(
        f"[GAP_BACKFILL] Đã lấy min_message_id hiện tại cho {len(max_ids)} channel"
    )

    client = TelegramClient("crypto_session", API_ID, API_HASH)
    db_writer = DatabaseWriter(dsn=DB_DSN, batch_size=100)

    async with client:
        await db_writer.connect()
        try:
            total = len(CHANNELS)
            for idx, channel in enumerate(CHANNELS, start=1):
                min_id = max_ids.get(channel)  # None nếu channel chưa có data
                logger.info(
                    f"[GAP_BACKFILL] ({idx}/{total}) channel={channel} "
                    f"resume_from_id={min_id}"
                )
                count = 0
                try:
                    async for msg in scrape_channel_history(
                        client=client,
                        channel=channel,
                        limit=LIMIT_PER_CHANNEL,
                        offset_date=None,
                        min_message_id=min_id,
                    ):
                        await db_writer.write(msg)
                        count += 1
                    await db_writer.flush_remaining()
                    logger.info(f"[GAP_BACKFILL] {channel}: {count} tin nhắn mới")
                except Exception:
                    # Lỗi 1 channel không chặn các channel còn lại — cùng
                    # nguyên tắc FIX 2 đã áp dụng ở ohlcv_pipeline.py.
                    logger.error(f"[GAP_BACKFILL] Lỗi ở {channel}", exc_info=True)

                if idx < total:
                    await asyncio.sleep(8)

        finally:
            await db_writer.close()

    logger.info("[GAP_BACKFILL] Hoàn tất toàn bộ 13 channel.")


if __name__ == "__main__":
    asyncio.run(main())
