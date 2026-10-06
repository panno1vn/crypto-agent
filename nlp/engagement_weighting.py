"""
nlp/engagement_weighting.py

Ngày 19 — Engagement-Weighted Sentiment + Aggregation
========================================================
Fix đủ các lỗi đã audit từ pseudocode gốc trong roadmap:
  1. Aggregate CHÍNH theo COIN (không phải channel) — khớp đúng thứ
     Signal Aggregator (Ngày 31) và Agent tool get_sentiment_summary
     (Ngày 36) thực sự cần.
  2. channel_credibility được JOIN thật từ telegram_channels và
     truyền vào công thức — không còn bị bỏ quên.
  3. Toàn bộ so sánh thời gian dùng UTC-aware, convert về naive-UTC
     TRƯỚC khi so sánh với created_at (quy ước "naive = UTC" của
     project) — không dùng datetime.now() trần trụi.
  4. Logging đầy đủ qua get_logger(), try/except quanh mọi thao tác DB.
  5. Import numpy tường minh (thiếu trong bản pseudocode gốc).
  6. Weighted score tính RUNTIME, KHÔNG lưu cứng vào DB — views/forwards
     của 1 message tăng dần theo thời gian, lưu cứng sẽ stale ngay lập tức.
  7. Relevance gate tự nhiên: message không nhắc coin nào (coins_mentioned
     rỗng) sẽ không khớp `coin = ANY(coins_mentioned)` ở bất kỳ coin
     nào — tự động bị loại khỏi mọi coin-level aggregate, giải quyết
     một phần nghi vấn "tone vs price-impact" (xem docs/PHOBERT_KNOWN_LIMITATIONS.md)
     mà không cần sửa model.
  8. Hằng số ENGAGEMENT_SCALE_FACTOR đọc từ env var, có thể chỉnh mà
     không cần deploy lại code — CHƯA được calibrate theo phân phối
     views/forwards thật (cần chạy percentile query trước khi tin số
     mặc định 100.0).
  9. (Ngày 27) `aggregate_coin_sentiment()` nhận thêm `as_of` optional —
     cho phép tính sentiment tại 1 mốc thời gian quá khứ thay vì luôn
     neo vào "bây giờ". Dùng bởi scripts/manual/dump_sentiment_distribution.py
     để dump phân phối 30 ngày (rag/news_confirmation.py, Ngày 27).
     CHỈ thêm ở hàm CHÍNH (theo coin) — `aggregate_channel_sentiment`
     (hàm phụ, monitoring/audit) KHÔNG có tham số này, không cần dùng
     cho mục đích lịch sử.

QUAN TRỌNG — độc lập với dag_sentiment_pipeline.py:
Module này CHỈ đọc dữ liệu đã có sẵn (sentiment_score đã được
nlp/sentiment_pipeline.py ghi vào DB từ trước). KHÔNG được import bởi
dag_sentiment_pipeline.py — hàm aggregate ở đây được gọi on-demand
bởi FastAPI endpoint (Ngày 40) hoặc Agent tool get_sentiment_summary
(Ngày 36), không chạy trong Airflow. Nếu sau này có DAG khác cần
import trực tiếp module này, nhớ thêm `numpy` vào
requirements-airflow.txt (hiện KHÔNG có trong đó).
"""

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_pipeline.logger import get_logger
from data_pipeline.models import TelegramChannel, TelegramMessage

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config — CHƯA calibrate, xem ghi chú điểm 8 ở docstring đầu file
# ---------------------------------------------------------------------------
ENGAGEMENT_SCALE_FACTOR = float(os.environ.get("SENTIMENT_ENGAGEMENT_SCALE", "100.0"))


def _utc_naive_now() -> datetime:
    """Giờ hiện tại, UTC, đã strip tzinfo — khớp quy ước naive=UTC của DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Core weighting function — pure, không cần DB
# ---------------------------------------------------------------------------
def engagement_weighted_score(
    sentiment_score: float,
    views: int,
    forwards: int,
    channel_credibility: float = 1.0,
    scale_factor: float = ENGAGEMENT_SCALE_FACTOR,
) -> float:
    """
    Tin được forward nhiều = signal mạnh hơn. log1p để tránh tin viral
    dominate quá mức.

    Args:
        sentiment_score:     đã signed -1.0 -> 1.0 (từ SentimentResult.score,
                              xem nlp/schemas.py — KHÔNG phải confidence trần).
        views, forwards:     >= 0, từ telegram_messages.
        channel_credibility: 0.0 -> ~1.0+, từ telegram_channels.credibility.
                              Mặc định 1.0 nếu message không gắn channel_id
                              (channel_id NULL) hoặc credibility NULL.
        scale_factor:        hằng số chia engagement — xem ENGAGEMENT_SCALE_FACTOR.

    Returns:
        Score đã weighted, luôn trong khoảng [-1.0, 1.0].

    Raises:
        ValueError: nếu views hoặc forwards âm (dữ liệu input sai từ gốc).
    """
    if views < 0 or forwards < 0:
        raise ValueError(
            f"views/forwards không được âm: views={views} forwards={forwards}"
        )

    engagement = math.log1p(views) + math.log1p(forwards * 3)
    weight = 1.0 + (engagement / scale_factor) * channel_credibility
    return max(-1.0, min(1.0, sentiment_score * weight))


# ---------------------------------------------------------------------------
# Schemas kết quả aggregate
# ---------------------------------------------------------------------------
class CoinSentimentSummary(BaseModel):
    """
    Output CHÍNH — input trực tiếp cho Signal Aggregator (Ngày 31) và
    Agent tool get_sentiment_summary (Ngày 36).
    """

    coin: str
    mean_weighted_score: float
    message_count: int
    window_hours: int
    computed_at: datetime


class ChannelSentimentSummary(BaseModel):
    """
    Output PHỤ — dùng để theo dõi/audit 1 channel cụ thể (vd channel
    nào đang noisy, credibility có hợp lý không). KHÔNG phải input
    chính thức cho Signal Aggregator — đó là CoinSentimentSummary.
    """

    channel_name: str
    mean_weighted_score: float
    message_count: int
    window_hours: int
    computed_at: datetime


# ---------------------------------------------------------------------------
# Aggregate CHÍNH — theo coin
# ---------------------------------------------------------------------------
async def aggregate_coin_sentiment(
    session: AsyncSession,
    coin: str,
    window_hours: int = 4,
    scale_factor: float = ENGAGEMENT_SCALE_FACTOR,
    as_of: Optional[datetime] = None,
) -> CoinSentimentSummary:
    """
    Tổng hợp sentiment của 1 coin trong N giờ gần nhất, có engagement
    weighting và channel credibility.

    Chỉ tính message:
      - đã có sentiment_score (is_processed=TRUE và analyzer không trả None)
      - created_at trong window_hours gần nhất
      - coin nằm trong coins_mentioned (relevance gate tự nhiên — xem
        điểm 7 ở docstring đầu file)

    Args:
        as_of: (Ngày 27) Mốc thời gian coi là "hiện tại" khi tính cutoff.
            None (mặc định) = dùng _utc_naive_now(), giữ nguyên hành vi
            gốc Ngày 19. Truyền giá trị cụ thể để tính sentiment tại 1
            mốc quá khứ — dùng bởi
            scripts/manual/dump_sentiment_distribution.py (Ngày 27) để
            dump phân phối lịch sử. KHÔNG dùng ngoài mục đích
            backtest/phân tích lịch sử; mọi lệnh gọi runtime bình thường
            (FastAPI endpoint, Agent tool) nên để None.
    """
    now = as_of if as_of is not None else _utc_naive_now()
    cutoff = now - timedelta(hours=window_hours)

    stmt = (
        select(
            TelegramMessage.sentiment_score,
            TelegramMessage.views,
            TelegramMessage.forwards,
            TelegramChannel.credibility,
        )
        .outerjoin(TelegramChannel, TelegramMessage.channel_id == TelegramChannel.id)
        .where(TelegramMessage.sentiment_score.is_not(None))
        .where(TelegramMessage.created_at > cutoff)
        # CHẶN TRÊN — bắt buộc phải có khi `now` không phải "bây giờ thật".
        # Thiếu dòng này: với as_of=None thì vô hại (không có tin ở tương
        # lai), nhưng với as_of=1 mốc quá khứ, thiếu chặn trên khiến query
        # trả về MỌI tin từ cutoff cho tới BÂY GIỜ THẬT — cửa sổ phình to
        # dần theo mức as_of lùi xa, không còn là cửa sổ window_hours cố
        # định nữa. Bug này đã xảy ra thật (Ngày 27, phát hiện qua
        # message_count tăng đơn điệu khi dump 30 ngày) — giữ dòng này lại,
        # đừng xóa dù có vẻ dư thừa ở trường hợp as_of=None.
        .where(TelegramMessage.created_at <= now)
        .where(TelegramMessage.coins_mentioned.any(coin))
    )

    try:
        result = await session.execute(stmt)
        rows = result.all()
    except Exception:
        logger.error(f"[AGGREGATE] Query thất bại cho coin={coin}", exc_info=True)
        raise

    weighted_scores = [
        engagement_weighted_score(
            sentiment_score=float(sentiment_score),
            views=views or 0,
            forwards=forwards or 0,
            channel_credibility=float(credibility) if credibility is not None else 1.0,
            scale_factor=scale_factor,
        )
        for sentiment_score, views, forwards, credibility in rows
    ]

    mean_score = float(np.mean(weighted_scores)) if weighted_scores else 0.0

    logger.info(
        f"[AGGREGATE] coin={coin} window={window_hours}h as_of={now.isoformat()} "
        f"messages={len(weighted_scores)} mean_weighted_score={mean_score:.4f}"
    )

    return CoinSentimentSummary(
        coin=coin,
        mean_weighted_score=mean_score,
        message_count=len(weighted_scores),
        window_hours=window_hours,
        computed_at=now,
    )


# ---------------------------------------------------------------------------
# Aggregate PHỤ — theo channel (monitoring/audit, không phải input chính)
# KHÔNG có tham số as_of — hàm này không dùng cho mục đích lịch sử/backtest,
# giữ nguyên hành vi gốc Ngày 19 (luôn tính từ "bây giờ").
# ---------------------------------------------------------------------------
async def aggregate_channel_sentiment(
    session: AsyncSession,
    channel_name: str,
    window_hours: int = 4,
    scale_factor: float = ENGAGEMENT_SCALE_FACTOR,
) -> ChannelSentimentSummary:
    cutoff = _utc_naive_now() - timedelta(hours=window_hours)

    stmt = (
        select(
            TelegramMessage.sentiment_score,
            TelegramMessage.views,
            TelegramMessage.forwards,
            TelegramChannel.credibility,
        )
        .outerjoin(TelegramChannel, TelegramMessage.channel_id == TelegramChannel.id)
        .where(TelegramMessage.sentiment_score.is_not(None))
        .where(TelegramMessage.created_at > cutoff)
        .where(TelegramMessage.channel_name == channel_name)
    )

    try:
        result = await session.execute(stmt)
        rows = result.all()
    except Exception:
        logger.error(
            f"[AGGREGATE] Query thất bại cho channel={channel_name}", exc_info=True
        )
        raise

    weighted_scores = [
        engagement_weighted_score(
            sentiment_score=float(sentiment_score),
            views=views or 0,
            forwards=forwards or 0,
            channel_credibility=float(credibility) if credibility is not None else 1.0,
            scale_factor=scale_factor,
        )
        for sentiment_score, views, forwards, credibility in rows
    ]

    mean_score = float(np.mean(weighted_scores)) if weighted_scores else 0.0

    return ChannelSentimentSummary(
        channel_name=channel_name,
        mean_weighted_score=mean_score,
        message_count=len(weighted_scores),
        window_hours=window_hours,
        computed_at=_utc_naive_now(),
    )
