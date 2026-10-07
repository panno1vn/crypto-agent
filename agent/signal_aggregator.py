"""
agent/signal_aggregator.py

Ngày 31 — Signal Aggregator: gộp tín hiệu kỹ thuật (ConfluentSignal, N11)
và xác nhận tin tức (NewsConfirmation, N27) thành một FinalSignal.

Quyết định thiết kế (chi tiết + phương án thay thế: docs/nhat-ky/, task N31):

  1. HƯỚNG LỆNH CHỈ DO TA QUYẾT. Sentiment/news chỉ điều chỉnh confidence,
     không bao giờ lật long thành short. Lý do: sentiment chưa chứng minh
     được liên hệ với giá (correlation -0.018, p=0.87), không có cơ sở để
     cho nó quyền quyết hướng.

  2. Mọi điểm thành phần quy về [0, 1] theo nghĩa "mức ỦNG HỘ hướng của TA":
       technical_score = ConfluentSignal.strength
       sentiment_score = (s + 1) / 2 với long, (1 - s) / 2 với short
                         (s = mean_weighted_score ∈ [-1, 1]; 0.5 = trung tính)
       news_score      = confirmed 1.0 / neutral 0.5 / conflicted 0.0
     confidence = tổng có trọng số, rồi chặn cứng ở CONFIDENCE_CAP = 0.85.

  3. sentiment_available = NewsConfirmation.status != 'no_data' (đúng
     roadmap). Khi False: TOÀN BỘ trọng số chuyển về TA, sentiment_score và
     news_score là None. KHÔNG coi "không có dữ liệu" là 0.5 "trung tính":
     làm vậy sẽ kéo confidence của mọi tín hiệu mạnh xuống một cách vô cớ.

  4. ⚠️ sentiment_score và news_score KHÔNG ĐỘC LẬP. Cả hai đều suy ra từ
     cùng một con số aggregate_coin_sentiment().mean_weighted_score (quyết
     định N27: news confirmation tính điểm từ Postgres, không từ tin RAG).
     news_score chỉ là bản phân ngưỡng của sentiment. Hệ quả cho N32: nhánh
     "TA + sentiment + news" đo tác dụng của việc thêm một hàm bậc thang
     của cùng biến, không phải thêm nguồn thông tin mới.

  5. NewsConfirmation.confidence_adjustment (confirm_boost/conflict_penalty
     của N27) KHÔNG được dùng ở đây. Dùng cả nó lẫn news_score là đếm tin
     tức hai lần. Nợ #10 vì vậy còn 2 tham số có tác dụng: ngưỡng và
     min_message_count.

  6. FinalSignal.coin giữ dạng cặp Binance ("BTCUSDT") của ConfluentSignal,
     vì đầu ra này đi tiếp tới Risk Manager và executor (miền Binance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from agent.config import (
    SIGNAL_MIN_CONFIDENCE_TO_TRADE,
    SIGNAL_WEIGHT_NEWS,
    SIGNAL_WEIGHT_SENTIMENT,
    SIGNAL_WEIGHT_TECHNICAL,
)
from data_pipeline.logger import get_logger
from data_pipeline.symbols import to_base_symbol

if TYPE_CHECKING:
    from rag.news_confirmation import NewsConfirmation
    from technical_analysis.confluence import ConfluentSignal

logger = get_logger(__name__)

CONFIDENCE_CAP = 0.85

NEWS_STATUS_SCORE = {"confirmed": 1.0, "neutral": 0.5, "conflicted": 0.0}

_WEIGHT_SUM_TOLERANCE = 1e-6


class SignalInputError(ValueError):
    """Input của Signal Aggregator không hợp lệ."""


@dataclass(frozen=True)
class SignalWeights:
    technical: float
    sentiment: float
    news: float

    def __post_init__(self):
        for name in ("technical", "sentiment", "news"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise SignalInputError(f"trọng số {name} không hữu hạn: {value!r}")
            if value < 0:
                raise SignalInputError(f"trọng số {name} âm: {value}")
        total = self.technical + self.sentiment + self.news
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            # Không tự chia lại cho tổng: env gõ nhầm (vd 0.7/0.15/0.5) phải
            # lộ ra ngay, không âm thầm thành bộ trọng số khác.
            raise SignalInputError(
                f"tổng trọng số phải = 1, nhận {total} "
                f"({self.technical}/{self.sentiment}/{self.news})"
            )


TA_ONLY_WEIGHTS = SignalWeights(technical=1.0, sentiment=0.0, news=0.0)


def load_signal_weights() -> SignalWeights:
    """Đọc trọng số từ agent/config.py (env). Raise nếu cấu hình sai."""
    return SignalWeights(
        technical=SIGNAL_WEIGHT_TECHNICAL,
        sentiment=SIGNAL_WEIGHT_SENTIMENT,
        news=SIGNAL_WEIGHT_NEWS,
    )


@dataclass(frozen=True)
class FinalSignal:
    coin: str
    direction: str
    confidence: float  # 0.0 → CONFIDENCE_CAP
    should_trade: bool
    technical_score: float
    sentiment_score: Optional[float]  # None khi không có dữ liệu sentiment
    news_score: Optional[float]  # None khi không có dữ liệu sentiment
    sentiment_available: bool
    news_status: str
    weights_used: SignalWeights  # trọng số SAU khi phân bổ lại
    reasoning: str


def sentiment_alignment(sentiment: float, direction: str) -> float:
    """
    Quy mean_weighted_score ∈ [-1, 1] về mức ủng hộ hướng TA ∈ [0, 1].
    0.5 = trung tính. Raise nếu ngoài miền (engagement_weighted_score luôn
    chặn trong [-1, 1], nên giá trị ngoài miền là dấu hiệu bug ở thượng nguồn).
    """
    if not math.isfinite(sentiment) or not -1.0 <= sentiment <= 1.0:
        raise SignalInputError(f"sentiment ngoài [-1, 1]: {sentiment!r}")
    if direction == "long":
        return (sentiment + 1.0) / 2.0
    if direction == "short":
        return (1.0 - sentiment) / 2.0
    raise SignalInputError(f"không có hướng để đối chiếu: {direction!r}")


def combine_scores(
    technical_score: float,
    sentiment_score: Optional[float],
    news_score: Optional[float],
    weights: SignalWeights,
) -> tuple[float, SignalWeights]:
    """
    Phần lõi thuần của aggregator, tách riêng để backtest N32 dùng lại.

    sentiment_score/news_score là None → trọng số tương ứng chuyển về TA.
    Trả (confidence đã chặn CONFIDENCE_CAP, trọng số thực dùng).
    """
    if sentiment_score is None and news_score is None:
        used = TA_ONLY_WEIGHTS
    elif sentiment_score is None or news_score is None:
        # Hai điểm cùng nguồn (mục 4 docstring) nên không có ca chỉ thiếu
        # một. Nếu xảy ra là có bug ở nơi gọi.
        raise SignalInputError(
            "sentiment_score và news_score phải cùng None hoặc cùng có giá trị"
        )
    else:
        used = weights

    confidence = used.technical * technical_score
    if sentiment_score is not None:
        confidence += used.sentiment * sentiment_score + used.news * news_score
    return min(confidence, CONFIDENCE_CAP), used


def aggregate_signal(
    technical: "ConfluentSignal",
    news: "NewsConfirmation",
    weights: Optional[SignalWeights] = None,
    min_confidence: float = SIGNAL_MIN_CONFIDENCE_TO_TRADE,
) -> FinalSignal:
    """
    Gộp ConfluentSignal + NewsConfirmation thành FinalSignal. Hàm thuần,
    không chạm DB.

    should_trade = hướng khác 'neutral' VÀ confidence > min_confidence.

    Raises:
        SignalInputError: coin 2 input không khớp, direction lạ, strength
            ngoài [0, 1] hoặc NaN, status news lạ, sentiment ngoài [-1, 1].
    """
    weights = weights if weights is not None else load_signal_weights()

    if to_base_symbol(technical.coin) != to_base_symbol(news.coin):
        raise SignalInputError(
            f"coin lệch: technical={technical.coin!r} news={news.coin!r}"
        )
    direction = technical.direction
    if direction not in ("long", "short", "neutral"):
        raise SignalInputError(f"direction không hợp lệ: {direction!r}")
    strength = technical.strength
    if not isinstance(strength, (int, float)) or not math.isfinite(strength):
        raise SignalInputError(f"strength không hữu hạn: {strength!r}")
    if not 0.0 <= strength <= 1.0:
        raise SignalInputError(f"strength ngoài [0, 1]: {strength}")
    if news.status not in ("confirmed", "conflicted", "neutral", "no_data"):
        raise SignalInputError(f"news.status không hợp lệ: {news.status!r}")

    sentiment_available = news.status != "no_data"
    technical_score = float(strength)

    if direction == "neutral":
        return FinalSignal(
            coin=technical.coin,
            direction="neutral",
            confidence=0.0,
            should_trade=False,
            technical_score=technical_score,
            sentiment_score=None,
            news_score=None,
            sentiment_available=sentiment_available,
            news_status=news.status,
            weights_used=TA_ONLY_WEIGHTS,
            reasoning="TA không cho hướng (neutral) → không giao dịch.",
        )

    if sentiment_available:
        if news.sentiment_score is None:
            raise SignalInputError(
                f"status={news.status!r} nhưng sentiment_score=None — "
                f"NewsConfirmation mâu thuẫn"
            )
        sentiment_score = sentiment_alignment(news.sentiment_score, direction)
        news_score = NEWS_STATUS_SCORE[news.status]
    else:
        sentiment_score = None
        news_score = None

    confidence, used = combine_scores(
        technical_score, sentiment_score, news_score, weights
    )
    should_trade = confidence > min_confidence

    if sentiment_available:
        reasoning = (
            f"TA {direction} strength={technical_score:.3f} (w={used.technical}); "
            f"sentiment={news.sentiment_score:+.3f} → ủng hộ={sentiment_score:.3f} "
            f"(w={used.sentiment}); news={news.status} (w={used.news}); "
            f"{news.message_count} tin."
        )
    else:
        reasoning = (
            f"TA {direction} strength={technical_score:.3f}; không có dữ liệu "
            f"sentiment đủ tin ({news.message_count} tin) → 100% trọng số về TA."
        )
    if confidence >= CONFIDENCE_CAP:
        reasoning += f" Confidence chạm trần {CONFIDENCE_CAP}."
    reasoning += (
        f" confidence={confidence:.3f} {'>' if should_trade else '<='} "
        f"ngưỡng {min_confidence} → should_trade={should_trade}."
    )

    logger.info(
        f"[AGGREGATOR] coin={technical.coin} dir={direction} conf={confidence:.3f} "
        f"trade={should_trade} sentiment_available={sentiment_available}"
    )

    return FinalSignal(
        coin=technical.coin,
        direction=direction,
        confidence=confidence,
        should_trade=should_trade,
        technical_score=technical_score,
        sentiment_score=sentiment_score,
        news_score=news_score,
        sentiment_available=sentiment_available,
        news_status=news.status,
        weights_used=used,
        reasoning=reasoning,
    )


async def generate_final_signal(session, coin: str) -> FinalSignal:
    """
    Đường đi realtime: analyze_confluence → correlate_news_with_technical →
    aggregate_signal. Mỏng, chỉ nối 3 bước; logic nằm ở 3 hàm kia.
    Import cục bộ để module này import được mà không kéo theo Chroma/torch.
    """
    from rag.news_confirmation import correlate_news_with_technical
    from technical_analysis.confluence import analyze_confluence

    technical = await analyze_confluence(session, coin)
    news = await correlate_news_with_technical(session, coin, technical)
    return aggregate_signal(technical, news)
