import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from binance import AsyncClient
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationInfo, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from data_pipeline.logger import get_logger  # FIX 4: dùng logger tập trung

# ---------------------------------------------------------------------------
# Setup môi trường
# ---------------------------------------------------------------------------
load_dotenv()

# FIX 4: get_logger thay vì basicConfig
logger = get_logger(__name__)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "crypto_agent")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_async_engine(DATABASE_URL, echo=False)

TIMEFRAMES = ["15m", "1h", "4h", "1d"]
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

# Số giây của mỗi timeframe — dùng để detect gap
TIMEFRAME_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}

# Số dòng INSERT trong 1 transaction — tránh transaction quá lớn (FIX 5)
INSERT_CHUNK_SIZE = 2000


# ---------------------------------------------------------------------------
# Pydantic Schema
# ---------------------------------------------------------------------------
class OHLCV(BaseModel):
    coin: str
    timeframe: str
    open_time: datetime  # luôn là timezone-aware UTC (FIX 1)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @field_validator("open", "high", "low", "close")
    @classmethod
    def price_must_be_positive(cls, v: float, info: ValidationInfo) -> float:
        """Giá không được âm — nếu âm là dữ liệu lỗi thật sự."""
        if v < 0:
            raise ValueError(f"Giá {info.field_name} bị âm ({v})")
        return v

    @field_validator("volume")
    @classmethod
    def volume_must_be_non_negative(cls, v: float) -> float:
        """
        FIX 3: volume == 0 KHÔNG phải lỗi.

        Lý do: coin thanh khoản thấp (XRPUSDT khung 15m lúc thị trường
        đứng yên) có thể không có giao dịch → volume = 0 là dữ liệu thật.

        Nếu reject, ta mất nến thật → tạo "gap giả" → RSI/MACD tính sai.
        Chỉ reject volume âm (không thể có trong thực tế).
        """
        if v < 0:
            raise ValueError(f"Volume âm ({v}) — dữ liệu lỗi")
        return v


# ---------------------------------------------------------------------------
# parse_kline
# ---------------------------------------------------------------------------
def parse_kline(kline: list, coin: str, timeframe: str) -> OHLCV:
    """
    Chuyển raw kline list từ Binance thành OHLCV schema.

    FIX 1 — TIMEZONE:
        Binance trả về timestamp ms (UTC).
        datetime.fromtimestamp(..., tz=timezone.utc) → aware UTC datetime.
        KHÔNG gọi .replace(tzinfo=None) — làm vậy sẽ mất tzinfo,
        PostgreSQL TIMESTAMPTZ sẽ hiểu sai timezone → dữ liệu lệch giờ.

    Kline format (Binance):
        [0]  open_time (ms)
        [1]  open
        [2]  high
        [3]  low
        [4]  close
        [5]  volume
        ...  (các field khác không dùng)
    """
    return OHLCV(
        coin=coin,
        timeframe=timeframe,
        # FIX 1: giữ nguyên timezone UTC, không strip
        open_time=datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc),
        open=float(kline[1]),
        high=float(kline[2]),
        low=float(kline[3]),
        close=float(kline[4]),
        volume=float(kline[5]),
    )


# ---------------------------------------------------------------------------
# validate_gaps
# ---------------------------------------------------------------------------
def validate_gaps(data: List[OHLCV], timeframe: str) -> List[OHLCV]:
    """
    Scan chuỗi nến và cảnh báo nếu có khoảng trống bất thường.

    Không xóa nến nào — chỉ LOG WARNING để biết data có vấn đề.
    Ngưỡng: gap > 2.5x chu kỳ timeframe được coi là bất thường.

    Returns:
        Danh sách OHLCV gốc (không thay đổi) — function này chỉ kiểm tra.
    """
    if len(data) < 2:
        return data

    tf_seconds = TIMEFRAME_SECONDS[timeframe]
    max_allowed_gap = timedelta(seconds=tf_seconds * 2.5)

    for i in range(1, len(data)):
        time_diff = data[i].open_time - data[i - 1].open_time
        if time_diff > max_allowed_gap:
            logger.warning(
                f"[GAP DETECTED] {data[i].coin} {timeframe}: "
                f"{data[i - 1].open_time} → {data[i].open_time} "
                f"(gap = {time_diff})"
            )

    return data


# ---------------------------------------------------------------------------
# fetch_ohlcv
# ---------------------------------------------------------------------------
async def fetch_ohlcv(
    client: AsyncClient,
    coin: str,
    timeframe: str,
    days_back: int = 90,
) -> List[OHLCV]:
    """
    Fetch và validate OHLCV data từ Binance cho 1 cặp coin/timeframe.

    Returns:
        List OHLCV đã pass validation và gap-check.
        Trả về list rỗng nếu Binance không có data.
    """
    start_str = f"{days_back} days ago UTC"
    raw_klines = await client.get_historical_klines(
        symbol=coin, interval=timeframe, start_str=start_str
    )

    parsed: List[OHLCV] = []
    skipped = 0

    for k in raw_klines:
        try:
            parsed.append(parse_kline(k, coin, timeframe))
        except ValueError as e:
            # Chỉ skip nến có giá âm thật sự — volume == 0 đã được cho qua ở validator
            logger.warning(f"[INVALID KLINE] {coin} {timeframe}: {e}")
            skipped += 1

    if skipped:
        logger.warning(f"[FETCH] {coin} {timeframe}: bỏ qua {skipped} nến không hợp lệ")

    return validate_gaps(parsed, timeframe)


# ---------------------------------------------------------------------------
# bulk_insert_ohlcv
# ---------------------------------------------------------------------------
async def bulk_insert_ohlcv(data: List[OHLCV]) -> None:
    """
    Upsert danh sách OHLCV vào PostgreSQL theo từng chunk.

    FIX 5 — CHUNK INSERT:
        Thay vì insert cả 8640 dòng (15m × 90 ngày) trong 1 transaction,
        chia thành lô INSERT_CHUNK_SIZE dòng.
        Lý do: transaction quá lớn chiếm lock lâu, dễ timeout, khó rollback.

    ON CONFLICT DO NOTHING:
        Safe để chạy lại nhiều lần (idempotent).
        Nếu nến đã tồn tại → bỏ qua, không ghi đè.
    """
    if not data:
        return

    insert_query = text(
        """
        INSERT INTO ohlcv (coin, timeframe, open_time, open, high, low, close, volume)
        VALUES (:coin, :timeframe, :open_time, :open, :high, :low, :close, :volume)
        ON CONFLICT (coin, timeframe, open_time) DO NOTHING
    """
    )

    # Chunk theo INSERT_CHUNK_SIZE để tránh transaction quá lớn
    for chunk_start in range(0, len(data), INSERT_CHUNK_SIZE):
        chunk = data[chunk_start : chunk_start + INSERT_CHUNK_SIZE]
        values = [item.model_dump() for item in chunk]

        async with engine.begin() as conn:
            await conn.execute(insert_query, values)

        logger.debug(
            f"[DB] Inserted chunk {chunk_start // INSERT_CHUNK_SIZE + 1}: "
            f"{len(chunk)} rows"
        )


# ---------------------------------------------------------------------------
# backfill_all  ← FIX 2: error isolation
# ---------------------------------------------------------------------------
async def backfill_all(days_back: int = 90) -> None:
    """
    Orchestrator: chạy backfill tuần tự cho tất cả COINS × TIMEFRAMES.

    FIX 2 — ERROR ISOLATION:
        Bản cũ có try/except bọc TOÀN BỘ vòng lặp → 1 lỗi làm chết tất cả.

        Bản mới: try/except nằm BÊN TRONG vòng lặp từng cặp coin/timeframe.
        Nếu BTCUSDT 4h lỗi → log + continue → BTCUSDT 1d vẫn chạy bình thường.

        Đây là anti-pattern quan trọng cần tránh trong mọi batch pipeline.
    """
    logger.info(
        f"[BACKFILL] Bắt đầu backfill {len(COINS)} coins × "
        f"{len(TIMEFRAMES)} timeframes × {days_back} ngày"
    )

    client = await AsyncClient.create(
        api_key=BINANCE_API_KEY, api_secret=BINANCE_SECRET
    )

    success_count = 0
    error_count = 0

    try:
        for coin in COINS:
            for tf in TIMEFRAMES:
                # FIX 2: try/except theo từng cặp coin/tf, không bọc cả vòng lặp
                try:
                    logger.info(f"[BACKFILL] Đang xử lý {coin} {tf}...")

                    data = await fetch_ohlcv(client, coin, tf, days_back=days_back)

                    if not data:
                        logger.warning(f"[BACKFILL] {coin} {tf}: không có data")
                        continue

                    await bulk_insert_ohlcv(data)

                    logger.info(f"[BACKFILL] ✓ {coin} {tf}: {len(data)} nến đã lưu")
                    success_count += 1

                except Exception as e:
                    # Log lỗi nhưng KHÔNG raise — tiếp tục cặp tiếp theo
                    logger.error(f"[BACKFILL] ✗ {coin} {tf}: {e}", exc_info=True)
                    error_count += 1

                # Tránh Binance rate limit giữa mỗi request
                await asyncio.sleep(0.5)

    finally:
        await client.close_connection()
        await engine.dispose()
        logger.info(
            f"[BACKFILL] Hoàn thành. "
            f"Thành công: {success_count} | Lỗi: {error_count} "
            f"/ {len(COINS) * len(TIMEFRAMES)} cặp"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Windows/WSL2: tránh lỗi "Event loop is closed"
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(backfill_all())
