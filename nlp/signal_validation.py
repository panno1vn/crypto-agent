"""
nlp/signal_validation.py

Ngày 20 — Signal Validation
============================
Tính Pearson correlation giữa weighted_sentiment (mỗi window_hours) và
price_return cùng khung giờ, cho 1 coin, tách riêng theo ngôn ngữ.

KHÁC với aggregate_coin_sentiment() (nlp/engagement_weighting.py):
hàm đó trả 1 con số duy nhất cho "N giờ gần nhất" (on-demand). Module
này cần TOÀN BỘ chuỗi thời gian trong lookback_days để tính correlation
và rolling correlation — phải tự query + resample, không tái dùng
trực tiếp được, dù logic weighting bên trong (engagement_weighted_score)
tái dùng nguyên vẹn.

⚠️ Bug đã phát hiện khi viết file này: coins_mentioned lưu SHORT symbol
(vd 'BTC' — xem ngày_4/extract_coins()), trong khi OHLCV.coin và mọi
nơi khác dùng FULL symbol ('BTCUSDT'). aggregate_coin_sentiment() hiện
tại sẽ luôn trả 0 message nếu gọi bằng full symbol — cần fix riêng,
không thuộc phạm vi file này. _to_short_symbol() dưới đây xử lý cho
module này, KHÔNG sửa hàm gốc.

⚠️ price_return dùng pct_change() đơn giản — CHƯA verify khớp với
convention của backtest.py (không có file đó lúc viết). Nếu backtest.py
dùng log-return, đổi lại cho khớp trước khi dùng chung 1 threshold nào
đó giữa 2 nơi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from pydantic import BaseModel
from scipy.stats import pearsonr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_pipeline.logger import get_logger
from data_pipeline.models import TelegramChannel, TelegramMessage
from nlp.engagement_weighting import engagement_weighted_score
from technical_analysis.indicator_pipeline import fetch_ohlcv_from_db

logger = get_logger(__name__)

_COIN_SUFFIXES = ("USDT", "BUSD", "USD")


def _to_short_symbol(coin: str) -> str:
    """
    'BTCUSDT' -> 'BTC'. Idempotent nếu đã là short form.

    Xem cảnh báo ở docstring đầu file — coins_mentioned lưu short
    symbol, OHLCV.coin lưu full symbol. Đây là chỗ nối 2 convention.
    """
    for suffix in _COIN_SUFFIXES:
        if coin.endswith(suffix) and len(coin) > len(suffix):
            return coin[: -len(suffix)]
    return coin


def _utc_naive_now() -> datetime:
    """Giờ hiện tại, UTC, đã strip tzinfo — khớp quy ước naive=UTC của DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ValidationReport(BaseModel):
    coin: str
    language: Optional[str]  # 'vi' | 'en' | None nghĩa "gộp cả 2"
    correlation: float
    p_value: float
    is_significant: bool
    sample_size: int
    window_hours: int
    lookback_days: int
    computed_at: datetime


# ---------------------------------------------------------------------------
# Load & bucket sentiment thành time series
# ---------------------------------------------------------------------------
async def _load_sentiment_timeseries(
    session: AsyncSession,
    coin: str,
    lookback_days: int,
    language: Optional[str] = None,
    window_hours: int = 4,
) -> pd.DataFrame:
    """
    Trả DataFrame index=bucket_start (naive UTC, mỗi window_hours),
    columns=['weighted_sentiment', 'message_count'].

    Bucket rỗng (không có message trong window đó) sẽ KHÔNG xuất hiện
    trong kết quả (resample chỉ tạo hàng cho bucket có ít nhất 1 dòng
    input) — merge_asof ở bước sau xử lý việc này bằng tolerance, không
    cần forward-fill ở đây (forward-fill sentiment cũ sang bucket mới
    sẽ tạo correlation giả — 1 tin cũ "kéo dài" ảnh hưởng sang nhiều
    nến giá sau đó nó không thực sự liên quan).
    """
    short_coin = _to_short_symbol(coin)
    cutoff = _utc_naive_now() - timedelta(days=lookback_days)

    stmt = (
        select(
            TelegramMessage.created_at,
            TelegramMessage.sentiment_score,
            TelegramMessage.views,
            TelegramMessage.forwards,
            TelegramChannel.credibility,
        )
        .outerjoin(TelegramChannel, TelegramMessage.channel_id == TelegramChannel.id)
        .where(TelegramMessage.sentiment_score.is_not(None))
        .where(TelegramMessage.created_at > cutoff)
        .where(TelegramMessage.coins_mentioned.any(short_coin))
    )
    if language is not None:
        stmt = stmt.where(TelegramMessage.language == language)

    try:
        result = await session.execute(stmt)
        rows = result.all()
    except Exception:
        logger.error(
            f"[SIGNAL_VALIDATION] Query sentiment thất bại "
            f"coin={coin} (short={short_coin}) lang={language}",
            exc_info=True,
        )
        raise

    if not rows:
        logger.warning(
            f"[SIGNAL_VALIDATION] 0 message cho coin={coin} (short={short_coin}) "
            f"lang={language} trong {lookback_days} ngày qua."
        )
        return pd.DataFrame(columns=["weighted_sentiment", "message_count"])

    records = [
        {
            "created_at": created_at,
            "weighted_sentiment": engagement_weighted_score(
                sentiment_score=float(sentiment_score),
                views=views or 0,
                forwards=forwards or 0,
                channel_credibility=float(credibility)
                if credibility is not None
                else 1.0,
            ),
        }
        for created_at, sentiment_score, views, forwards, credibility in rows
    ]

    df = pd.DataFrame.from_records(records).set_index("created_at").sort_index()

    # label='left', closed='left': bucket [t, t+window) không nhìn thấy
    # message đúng tại biên t+window — tránh 1 message "leak" sang bucket
    # kế tiếp khi align với open_time của nến giá.
    resampled = df.resample(f"{window_hours}h", label="left", closed="left").agg(
        weighted_sentiment=("weighted_sentiment", "mean"),
        message_count=("weighted_sentiment", "count"),
    )
    # Bỏ bucket rỗng (agg vẫn tạo hàng NaN cho khoảng trống giữa 2 bucket
    # có data — resample luôn sinh đều lưới thời gian).
    return resampled.dropna(subset=["weighted_sentiment"])


# ---------------------------------------------------------------------------
# Load price return
# ---------------------------------------------------------------------------
async def _load_price_returns(
    session: AsyncSession,
    coin: str,
    timeframe: str = "4h",
) -> pd.DataFrame:
    """
    Trả DataFrame index=open_time, columns=['close', 'price_return'].
    price_return = pct_change giữa 2 nến liên tiếp — xem cảnh báo giả
    định ở docstring đầu file.
    """
    df = await fetch_ohlcv_from_db(session, coin, timeframe, limit=100000)
    if df.empty:
        raise ValueError(
            f"Không có OHLCV '{timeframe}' nào cho {coin} — kiểm tra "
            f"binance_ohlcv_hourly_sync có đang Paused không "
            f"(xem GHI_CHU_NGAY19 PHẦN 6)."
        )
    df = df.sort_index().copy()
    df["price_return"] = df["close"].astype(float).pct_change()
    return df[["close", "price_return"]].dropna(subset=["price_return"])


# ---------------------------------------------------------------------------
# Align + Pearson correlation
# ---------------------------------------------------------------------------
def _align(
    sentiment_df: pd.DataFrame, price_df: pd.DataFrame, window_hours: int
) -> pd.DataFrame:
    aligned = pd.merge_asof(
        sentiment_df.reset_index().rename(columns={"created_at": "timestamp"}),
        price_df.reset_index().rename(columns={"open_time": "timestamp"}),
        on="timestamp",
        direction="nearest",
        # Tolerance = nửa window: chỉ match nến giá THỰC SỰ gần nhất,
        # không lấn sang nến kế bên nếu lệch quá xa.
        tolerance=pd.Timedelta(hours=window_hours / 2),
    )
    return aligned.dropna(subset=["weighted_sentiment", "price_return"])


async def validate_sentiment_signal(
    session: AsyncSession,
    coin: str = "BTCUSDT",
    lookback_days: int = 30,
    language: Optional[str] = None,
    window_hours: int = 4,
) -> ValidationReport:
    """
    Raises:
        ValueError: không đủ data để tính correlation có ý nghĩa
            (< 3 điểm chung sau align).
    """
    sentiment_df = await _load_sentiment_timeseries(
        session, coin, lookback_days, language, window_hours
    )
    price_df = await _load_price_returns(session, coin, timeframe=f"{window_hours}h")
    aligned = _align(sentiment_df, price_df, window_hours)

    sample_size = len(aligned)
    if sample_size < 3:
        raise ValueError(
            f"Chỉ có {sample_size} điểm dữ liệu chung sau align "
            f"(coin={coin}, lang={language}, lookback={lookback_days} ngày) — "
            f"không đủ để tính correlation có ý nghĩa."
        )

    corr, p_value = pearsonr(aligned["weighted_sentiment"], aligned["price_return"])

    logger.info(
        f"[SIGNAL_VALIDATION] coin={coin} lang={language} "
        f"n={sample_size} corr={corr:.4f} p={p_value:.4f}"
    )

    return ValidationReport(
        coin=coin,
        language=language,
        correlation=float(corr),
        p_value=float(p_value),
        is_significant=bool(p_value < 0.05),
        sample_size=sample_size,
        window_hours=window_hours,
        lookback_days=lookback_days,
        computed_at=_utc_naive_now(),
    )


# ---------------------------------------------------------------------------
# Rolling correlation (cho chart)
# ---------------------------------------------------------------------------
async def rolling_correlation(
    session: AsyncSession,
    coin: str = "BTCUSDT",
    lookback_days: int = 30,
    language: Optional[str] = None,
    window_hours: int = 4,
    rolling_window_days: int = 7,
) -> pd.Series:
    """
    Rolling correlation tính trên SỐ ĐIỂM tương ứng rolling_window_days
    (rolling_window_days*24/window_hours điểm), không phải rolling theo
    thời gian lịch — nếu có bucket trống, "7 ngày" theo count sẽ hơi
    lệch so với 7 ngày lịch thật. Chấp nhận được ở quy mô hiện tại.
    """
    sentiment_df = await _load_sentiment_timeseries(
        session, coin, lookback_days, language, window_hours
    )
    price_df = await _load_price_returns(session, coin, timeframe=f"{window_hours}h")
    aligned = _align(sentiment_df, price_df, window_hours).set_index("timestamp")

    points_per_window = max(3, int((rolling_window_days * 24) / window_hours))
    if len(aligned) < points_per_window:
        logger.warning(
            f"[SIGNAL_VALIDATION] Chỉ có {len(aligned)} điểm, ít hơn "
            f"{points_per_window} điểm cần cho rolling {rolling_window_days} ngày — "
            f"chart sẽ có nhiều NaN ở đầu."
        )

    return (
        aligned["weighted_sentiment"]
        .rolling(window=points_per_window, min_periods=max(3, points_per_window // 2))
        .corr(aligned["price_return"])
    )
