"""
data_pipeline/telegram/historical_scraper.py

Ngày 4 — Historical Scraper (hoàn chỉnh)
=========================================
Bao gồm:
  - TelegramMessage schema (Pydantic v2)
  - scrape_channel_history()   — async generator, humanized throttling
  - detect_language()          — detect vi/en cho NLP downstream
  - DatabaseWriter             — async bulk upsert vào PostgreSQL
  - CheckpointManager          — resume scrape nếu bị interrupt
  - run_historical_backfill()  — orchestrator chạy multi-channel
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import asyncpg
from langdetect import LangDetectException, detect
from pydantic import BaseModel, Field, field_validator
from telethon import TelegramClient
from telethon.errors import FloodWaitError

from data_pipeline.logger import get_logger

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
Path("logs").mkdir(exist_ok=True)
Path("checkpoints/telegram").mkdir(parents=True, exist_ok=True)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CRYPTO_ENTITIES = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "DOT",
    "AVAX",
    "MATIC",
    "LINK",
    "UNI",
    "ATOM",
    "LTC",
    "TRX",
]

SUPPORTED_LANGUAGES = {"vi", "en"}  # PhoBERT & FinBERT
CHECKPOINT_DIR = Path("checkpoints/telegram")


# ---------------------------------------------------------------------------
# Helper: coin extraction
# ---------------------------------------------------------------------------
def extract_coins(text: str) -> list[str]:
    """Extract token/coin symbols from message content."""
    if not text:
        return []
    text_upper = text.upper()
    return [coin for coin in CRYPTO_ENTITIES if coin in text_upper]


# ---------------------------------------------------------------------------
# Helper: language detection
# ---------------------------------------------------------------------------
def detect_language(text: str) -> Optional[str]:
    """
    Detect language of a message.
    Returns 'vi', 'en', or None (unsupported / too short to detect).

    Used downstream to route:
      - 'vi' → PhoBERT sentiment pipeline
      - 'en' → FinBERT sentiment pipeline
      - None → skip sentiment analysis
    """
    if not text or len(text.strip()) < 20:
        return None
    try:
        lang = detect(text)
        return lang if lang in SUPPORTED_LANGUAGES else None
    except LangDetectException:
        return None


# ---------------------------------------------------------------------------
# Pydantic Schema
# ---------------------------------------------------------------------------
class TelegramMessage(BaseModel):
    """Input data standardization schema using Pydantic V2."""

    id: int
    channel_name: str
    message_text: Optional[str]
    language: Optional[str] = None  # 'vi' | 'en' | None
    views: int = Field(default=0, ge=0)
    forwards: int = Field(default=0, ge=0)
    created_at: datetime
    coins_mentioned: list[str] = []

    @field_validator("message_text")
    @classmethod
    def text_not_too_short(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v.strip()) < 10:
            raise ValueError("Message too short")
        return v


# ---------------------------------------------------------------------------
# Checkpoint Manager
# ---------------------------------------------------------------------------
class CheckpointManager:
    """
    Lưu/đọc trạng thái scrape để resume nếu bị interrupt.

    File: checkpoints/telegram/<channel_safe>.json
    Format: { "last_message_id": int, "last_scraped_at": ISO8601 }
    """

    def __init__(self, channel: str):
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = channel.lstrip("@").replace("/", "_")
        self._path = CHECKPOINT_DIR / f"{safe_name}.json"

    def load(self) -> Optional[dict]:
        if self._path.exists():
            with open(self._path) as f:
                data = json.load(f)
            logger.info(
                f"[CHECKPOINT] Loaded for {self._path.stem}: "
                f"last_id={data.get('last_message_id')}"
            )
            return data
        return None

    def save(self, last_message_id: int) -> None:
        with open(self._path, "w") as f:
            json.dump(
                {
                    "last_message_id": last_message_id,
                    "last_scraped_at": datetime.now(timezone.utc).isoformat(),
                },
                f,
                indent=2,
            )

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


# ---------------------------------------------------------------------------
# Async Generator — scrape_channel_history
# ---------------------------------------------------------------------------
async def scrape_channel_history(
    client: TelegramClient,
    channel: str,
    limit: int = 5000,
    offset_date: Optional[datetime] = None,
    min_message_id: Optional[int] = None,  # resume từ checkpoint
) -> AsyncGenerator[TelegramMessage, None]:
    """
    Async generator — yield từng TelegramMessage validated.

    Features:
      - Humanized proactive throttling (ngủ ngẫu nhiên mỗi 35–65 msg)
      - FloodWaitError handling với kill-switch (>1h hoặc >3 lần liên tiếp)
      - Language detection tích hợp
      - min_message_id: dừng khi gặp message cũ hơn checkpoint
    """
    message_count = 0
    consecutive_flood_errors = 0
    MAX_FLOOD_ERRORS = 3

    next_rest_target = random.randint(35, 65)

    logger.info(
        f"[SCRAPER] Start channel={channel} limit={limit} "
        f"offset_date={offset_date} resume_from_id={min_message_id}"
    )

    try:
        async for message in client.iter_messages(
            channel,
            limit=limit,
            offset_date=offset_date,
        ):
            # --- Resume: bỏ qua message đã scrape trước đó ---
            if min_message_id and message.id <= min_message_id:
                logger.info(
                    f"[SCRAPER] Reached checkpoint (id={message.id}). "
                    f"Total new messages: {message_count}"
                )
                return

            consecutive_flood_errors = 0  # reset nếu request thành công

            if not message.text:
                continue

            try:
                parsed_msg = TelegramMessage(
                    id=message.id,
                    channel_name=channel,
                    message_text=message.text,
                    language=detect_language(message.text),
                    views=getattr(message, "views", 0) or 0,
                    forwards=getattr(message, "forwards", 0) or 0,
                    created_at=message.date,
                    coins_mentioned=extract_coins(message.text),
                )
                yield parsed_msg
                message_count += 1

            except ValueError as e:
                logger.debug(f"[SKIP] id={message.id} reason={e}")

            # --- Humanized throttling ---
            if message_count >= next_rest_target:
                sleep_time = round(random.uniform(3.5, 8.2), 2)
                logger.info(
                    f"[THROTTLE] {message_count} msgs scraped. "
                    f"Sleeping {sleep_time}s..."
                )
                await asyncio.sleep(sleep_time)
                next_rest_target = message_count + random.randint(35, 65)

    except FloodWaitError as e:
        consecutive_flood_errors += 1
        logger.warning(
            f"[FLOOD] Attempt {consecutive_flood_errors}/{MAX_FLOOD_ERRORS}. "
            f"Wait={e.seconds}s"
        )

        # Kill-switch 1: wait > 1 giờ
        if e.seconds > 3600:
            logger.error("[FLOOD] Wait > 1h. Emergency stop.")
            return

        # Kill-switch 2: quá nhiều lần liên tiếp
        if consecutive_flood_errors >= MAX_FLOOD_ERRORS:
            logger.error("[FLOOD] Too many consecutive rate limits. Emergency stop.")
            return

        await asyncio.sleep(e.seconds)

    except Exception as e:
        logger.error(f"[ERROR] channel={channel}: {e}", exc_info=True)

    finally:
        logger.info(
            f"[SCRAPER] Done channel={channel}. "
            f"Total valid messages: {message_count}"
        )


# ---------------------------------------------------------------------------
# Database Writer
# ---------------------------------------------------------------------------
class DatabaseWriter:
    """
    Async bulk upsert TelegramMessage vào PostgreSQL.

    Dùng asyncpg trực tiếp để tận dụng executemany() hiệu quả hơn ORM.
    ON CONFLICT (id) DO NOTHING — safe để chạy lại nhiều lần.
    """

    def __init__(self, dsn: str, batch_size: int = 100):
        """
        Args:
            dsn:        PostgreSQL DSN, e.g. "postgresql://user:pass@localhost/crypto_agent"
            batch_size: Flush to DB sau mỗi N messages.
        """
        self._dsn = dsn
        self._batch_size = batch_size
        self._pool: Optional[asyncpg.Pool] = None
        self._buffer: list[TelegramMessage] = []

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=5)
        logger.info("[DB] Connection pool created.")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("[DB] Connection pool closed.")

    async def write(self, msg: TelegramMessage) -> None:
        """Buffer message. Flush khi đủ batch_size."""
        self._buffer.append(msg)
        if len(self._buffer) >= self._batch_size:
            await self._flush()

    async def flush_remaining(self) -> None:
        """Gọi cuối pipeline để flush phần dư."""
        if self._buffer:
            await self._flush()

    async def _flush(self) -> None:
        if not self._buffer or not self._pool:
            return

        records = [
            (
                msg.id,
                msg.channel_name,
                msg.message_text,
                msg.language,
                msg.views,
                msg.forwards,
                msg.coins_mentioned,
                msg.created_at.replace(tzinfo=timezone.utc)
                if msg.created_at.tzinfo is None
                else msg.created_at,
            )
            for msg in self._buffer
        ]

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO telegram_messages (
                    id, channel_name, message_text, language,
                    views, forwards, coins_mentioned, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (id) DO NOTHING
                """,
                records,
            )

        logger.info(f"[DB] Flushed {len(self._buffer)} messages to PostgreSQL.")
        self._buffer.clear()


# ---------------------------------------------------------------------------
# Orchestrator — run_historical_backfill
# ---------------------------------------------------------------------------
async def run_historical_backfill(
    client: TelegramClient,
    db_writer: DatabaseWriter,
    channels: list[str],
    limit_per_channel: int = 5000,
    offset_date: Optional[datetime] = None,
    use_checkpoint: bool = True,
) -> None:
    """
    Orchestrator: chạy historical scrape cho nhiều channel tuần tự.

    Tuần tự (không parallel) để tránh Telegram rate limit.
    Mỗi channel dùng CheckpointManager để resume nếu bị interrupt.

    Args:
        client:               Telethon TelegramClient đã authenticated.
        db_writer:            DatabaseWriter đã connected.
        channels:             Danh sách channel username (vd: ['@whale_alert', ...]).
        limit_per_channel:    Số message tối đa mỗi channel.
        offset_date:          Chỉ lấy message trước thời điểm này.
        use_checkpoint:       True = resume từ checkpoint nếu có.
    """
    total_channels = len(channels)

    for idx, channel in enumerate(channels, start=1):
        logger.info(f"[BACKFILL] Processing channel {idx}/{total_channels}: {channel}")

        checkpoint = CheckpointManager(channel)
        min_message_id = None

        if use_checkpoint:
            ckpt_data = checkpoint.load()
            if ckpt_data:
                min_message_id = ckpt_data.get("last_message_id")

        last_id: Optional[int] = None

        try:
            async for msg in scrape_channel_history(
                client=client,
                channel=channel,
                limit=limit_per_channel,
                offset_date=offset_date,
                min_message_id=min_message_id,
            ):
                await db_writer.write(msg)
                last_id = msg.id  # track ID mới nhất (iter_messages đi từ mới → cũ)

            await db_writer.flush_remaining()

        except Exception as e:
            logger.error(f"[BACKFILL] Failed channel={channel}: {e}", exc_info=True)
            # Không raise — tiếp tục channel kế tiếp

        finally:
            # Lưu checkpoint với ID mới nhất đã scrape
            if last_id:
                checkpoint.save(last_id)
                logger.info(
                    f"[BACKFILL] Checkpoint saved for {channel}: last_id={last_id}"
                )

        # Delay giữa các channel để tránh rate limit
        if idx < total_channels:
            inter_channel_delay = round(random.uniform(5.0, 12.0), 2)
            logger.info(f"[BACKFILL] Inter-channel delay: {inter_channel_delay}s...")
            await asyncio.sleep(inter_channel_delay)

    logger.info("[BACKFILL] All channels completed.")


# ---------------------------------------------------------------------------
# Entry point (manual run / test)
# ---------------------------------------------------------------------------
async def main():
    """
    Chạy thử backfill 30 ngày cho các channel trong config.
    Trong production sẽ được gọi bởi Airflow DAG (ngày 7).
    """
    import os
    from datetime import timedelta

    from dotenv import load_dotenv

    load_dotenv()

    API_ID = int(os.environ["TELEGRAM_API_ID"])
    API_HASH = os.environ["TELEGRAM_API_HASH"]

    # Build DSN từ các biến riêng lẻ trong .env
    DB_DSN = (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )

    # Channels từ config/channels.yaml (hardcode tạm để test)
    TEST_CHANNELS = [
        "@whale_alert",
        "@coingape",
        "@CryptoComNews",
    ]

    # Chỉ lấy 30 ngày gần nhất
    offset = datetime.now(timezone.utc) - timedelta(days=30)

    client = TelegramClient("crypto_session", API_ID, API_HASH)
    db_writer = DatabaseWriter(dsn=DB_DSN, batch_size=100)

    async with client:
        await db_writer.connect()
        try:
            await run_historical_backfill(
                client=client,
                db_writer=db_writer,
                channels=TEST_CHANNELS,
                limit_per_channel=2000,
                offset_date=offset,
                use_checkpoint=True,
            )
        finally:
            await db_writer.close()

    logger.info("Backfill complete. Run verification query:")
    logger.info("  SELECT channel_name, COUNT(*) FROM telegram_messages GROUP BY 1;")


if __name__ == "__main__":
    asyncio.run(main())
