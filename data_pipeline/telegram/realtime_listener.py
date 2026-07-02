"""
data_pipeline/telegram/realtime_listener.py

Ngày 5 — Realtime Listener (đã fix)
=====================================
Fixes:
  1. Dùng get_logger thay vì basicConfig (tránh duplicate handlers)
  2. db_writer.connect() nằm trong async with client (đảm bảo cleanup đúng)
  3. Thêm periodic_flush task chạy song song event loop
"""

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient, events

from data_pipeline.logger import get_logger
from data_pipeline.telegram.historical_scraper import (DatabaseWriter,
                                                       TelegramMessage,
                                                       detect_language,
                                                       extract_coins)

# ---------------------------------------------------------------------------
# Logger — dùng get_logger thay vì basicConfig
# ---------------------------------------------------------------------------
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helper: Parse và Validate
# ---------------------------------------------------------------------------
def parse_and_validate(message, channel_name: str) -> TelegramMessage | None:
    """
    Validate tin nhắn thô thành schema chuẩn.

    Returns:
        TelegramMessage nếu hợp lệ, None nếu không hợp lệ.
    """
    if not message.text:
        return None
    try:
        return TelegramMessage(
            id=message.id,
            channel_name=channel_name,
            message_text=message.text,
            language=detect_language(message.text),
            views=getattr(message, "views", 0) or 0,
            forwards=getattr(message, "forwards", 0) or 0,
            created_at=message.date,
            coins_mentioned=extract_coins(message.text),
        )
    except ValueError as e:
        logger.debug(f"[VALIDATION FAILED] id={message.id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Periodic Flush — đảm bảo buffer được flush định kỳ
# ---------------------------------------------------------------------------
async def periodic_flush(db_writer: DatabaseWriter, interval_seconds: int = 5) -> None:
    """
    Chạy song song event loop, flush buffer mỗi N giây.

    Lý do cần task này:
      - DatabaseWriter buffer tin nhắn trước khi INSERT.
      - Nếu traffic thấp (ít tin mới), buffer có thể ngồi chờ mãi.
      - Task này đảm bảo data được lưu định kỳ dù chưa đủ batch_size.

    Args:
        db_writer:        DatabaseWriter instance đang được dùng.
        interval_seconds: Khoảng thời gian giữa các lần flush (default: 5s).
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await db_writer.flush_remaining()
        except Exception as e:
            logger.error(f"[FLUSH ERROR] periodic_flush failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------
async def main() -> None:
    load_dotenv()

    API_ID = int(os.environ["TELEGRAM_API_ID"])
    API_HASH = os.environ["TELEGRAM_API_HASH"]
    DB_DSN = (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )

    CHANNEL_LIST = [
        "@whale_alert",
        "@coingape",
        "@CryptoComNews",
        "@bitcoinist_com",
    ]

    # batch_size=50: buffer 50 tin trước khi INSERT, periodic_flush đảm bảo
    # data không bị giữ quá 5 giây dù chưa đủ batch.
    # (batch_size=1 gây N round-trips DB = không tốt khi traffic cao)
    db_writer = DatabaseWriter(dsn=DB_DSN, batch_size=50)

    client = TelegramClient("crypto_session", API_ID, API_HASH)

    # FIX: db_writer.connect() nằm TRONG async with client
    # → đảm bảo cả client lẫn db pool đều được đóng đúng cách
    # dù có exception ở bất kỳ bước nào.
    async with client:
        await db_writer.connect()

        # Đăng ký Event Handler
        @client.on(events.NewMessage(chats=CHANNEL_LIST))
        async def handle_new_message(event) -> None:
            try:
                channel_name = event.chat.username or str(event.chat.id)
                msg = parse_and_validate(event.message, channel_name)

                if msg:
                    await db_writer.write(msg)
                    logger.info(
                        f"[SAVED] channel={msg.channel_name} "
                        f"id={msg.id} coins={msg.coins_mentioned}"
                    )
                else:
                    logger.warning(
                        f"[SKIPPED] invalid/short message id={event.message.id}"
                    )
            except Exception as e:
                logger.error(f"[ERROR] Failed handling message: {e}", exc_info=True)

        logger.info(
            f"[LISTENER] Started monitoring {len(CHANNEL_LIST)} channels. "
            "Waiting for messages..."
        )

        # Khởi động periodic_flush chạy song song event loop
        flush_task = asyncio.create_task(periodic_flush(db_writer, interval_seconds=5))

        try:
            await client.run_until_disconnected()
        finally:
            # Hủy flush task trước khi đóng DB
            flush_task.cancel()
            try:
                await flush_task
            except asyncio.CancelledError:
                pass  # Expected khi cancel

            # Flush nốt phần còn trong buffer trước khi tắt
            await db_writer.flush_remaining()
            await db_writer.close()
            logger.info("[LISTENER] Shutdown complete. All data flushed.")


if __name__ == "__main__":
    asyncio.run(main())
