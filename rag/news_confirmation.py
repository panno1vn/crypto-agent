"""
rag/news_confirmation.py

Ngày 27 — News-Technical Confirmation

Đối chiếu tín hiệu kỹ thuật (ConfluentSignal, Ngày 11) với sentiment tin
tức Telegram gần đây, trả về NewsConfirmation dùng làm input phụ cho
Signal Aggregator (Ngày 31).

QUYẾT ĐỊNH KIẾN TRÚC khác với pseudocode roadmap v1/v2 — lý do:

  1. KHÔNG tính sentiment từ `news` (kết quả search_telegram_news()).
     Xác nhận thực nghiệm 2026-08-15: `metadata` trả về từ retriever
     không mang sentiment_score (grep "sentiment" rag/enricher.py rỗng
     — build_metadata() chỉ lưu coins/coins_count/channel/created_ts/
     views/forwards/language). Sentiment thật nằm ở Postgres
     (telegram_messages.sentiment_score) và đã có sẵn hàm đúng việc:
     `aggregate_coin_sentiment()` (nlp/engagement_weighting.py) —
     engagement-weighted, join channel_credibility, relevance gate qua
     coins_mentioned. Dùng lại thay vì viết hàm mới trùng logic.

     Hệ quả: search_telegram_news() ở đây CHỈ dùng để lấy `key_news`
     minh họa cho người đọc, KHÔNG dùng để tính điểm. Hai pipeline này
     độc lập (dag_embed_messages 30 phút/lần vs dag_sentiment_pipeline
     10 phút/lần) nên `key_news` có thể rỗng dù message_count > 0 —
     KHÔNG coi đó là no_data.

  2. `status='no_data'` được quyết định bởi `message_count == 0` từ
     aggregate_coin_sentiment (nguồn sự thật của điểm số), KHÔNG phải
     bởi `news == []` như chữ trong checklist roadmap viết theo nghĩa
     đen — vì `news` không còn là nguồn của điểm số nữa (xem mục 1).

  3. Logic status ĐỐI XỨNG long/short. Pseudocode gốc (cả v1 lẫn v2)
     chỉ xử lý nhánh `direction == 'long'` — mọi tín hiệu short sẽ luôn
     rơi vào 'neutral' bất kể sentiment nói gì. Đây là bug thiết kế,
     không phải rút gọn có chủ đích, nên đã sửa ở bản này.

  4. ⚠️ STOPGAP cho nợ kỹ thuật CHƯA FIX (ledger: "BTC vs BTCUSDT symbol
     mismatch trong aggregate_coin_sentiment()", hạn Ngày 31). Nếu
     `ConfluentSignal.coin` ở dạng cặp Binance ("BTCUSDT") mà truyền
     thẳng vào aggregate_coin_sentiment (lọc theo coins_mentioned dạng
     "BTC"), MỌI lệnh gọi sẽ ra message_count=0 → no_data vĩnh viễn,
     âm thầm sai — đúng kiểu bug ledger đã cảnh báo, chỉ lộ ra sớm hơn
     dự kiến. `_to_base_symbol()` bên dưới cắt hậu tố quote phổ biến để
     module này không sập ngay khi tích hợp. ĐÂY KHÔNG PHẢI FIX CHÍNH
     THỨC — khi Ngày 31 xử lý nợ này tận gốc (chuẩn hóa symbol xuyên
     suốt pipeline), hàm `_to_base_symbol()` phải bị XÓA và thay bằng
     lời gọi tới giải pháp chuẩn hóa chính thức. Không giữ 2 bản song
     song — sẽ lệch nhau âm thầm.

  5. Ngưỡng threshold/confidence_adjustment đọc từ rag/config.py, CHƯA
     calibrate (xem cảnh báo trong config.py và
     scripts/manual/dump_sentiment_distribution.py). KHÔNG coi giá trị
     mặc định ±0.1 / +0.10 / -0.15 là số đã kiểm chứng — đó là số bịa
     của roadmap v1, giữ nguyên tạm thời chỉ để có default chạy được.

  6. `min_message_count` (mặc định 1, KHÔNG đổi hành vi hiện tại) —
     phát hiện thực nghiệm 2026-08-15 khi dump phân phối 30 ngày:
     phần lớn cửa sổ 6h chỉ có 0-2 tin, nên `mean_weighted_score` nhiều
     khi chỉ phản ánh 1 tin đơn lẻ (rủi ro thật: PhoBERT F1=0.6385,
     ~36% khả năng nhãn sai). Tham số này để sẵn chỗ cắm — khi N32
     ablation xác định N tối thiểu có ý nghĩa thống kê, chỉ cần đổi
     env `NEWS_CONFIRMATION_MIN_MESSAGE_COUNT`, KHÔNG cần sửa code.
     Dưới ngưỡng: status ép về 'no_data', confidence_adjustment=0.0,
     nhưng `sentiment_score` vẫn trả giá trị thật (không ép None) để
     giữ khả năng quan sát/debug — chỉ status là tín hiệu "đừng hành
     động theo con số này", không phải "con số này không tồn tại".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from data_pipeline.logger import get_logger
from nlp.engagement_weighting import aggregate_coin_sentiment
from rag.config import (
    NEWS_CONFIRMATION_CONFIRM_BOOST,
    NEWS_CONFIRMATION_CONFLICT_PENALTY,
    NEWS_CONFIRMATION_KEY_NEWS_COUNT,
    NEWS_CONFIRMATION_LOWER_THRESHOLD,
    NEWS_CONFIRMATION_MIN_MESSAGE_COUNT,
    NEWS_CONFIRMATION_UPPER_THRESHOLD,
    NEWS_CONFIRMATION_WINDOW_HOURS,
)
from rag.retriever import search_telegram_news

if TYPE_CHECKING:
    # Chỉ dùng cho type hint — tránh phụ thuộc runtime giữa rag/ và
    # technical_analysis/ (không rõ có rủi ro circular import hay
    # không, an toàn hơn là không import thật).
    from technical_analysis.confluence import ConfluentSignal

logger = get_logger(__name__)

NewsStatus = Literal["confirmed", "conflicted", "neutral", "no_data"]

# Hậu tố quote phổ biến trên Binance — dùng cho stopgap ở mục 4 docstring
# đầu file. Thứ tự không quan trọng vì chỉ khớp 1 hậu tố/coin.
_QUOTE_SUFFIXES = ("USDT", "BUSD", "USDC", "USD")


def _to_base_symbol(coin: str) -> str:
    """
    STOPGAP — xem mục 4, docstring đầu file. Xóa khi nợ N31 được xử lý
    tận gốc, thay bằng hàm chuẩn hóa symbol chính thức của pipeline.
    """
    coin_upper = coin.upper()
    for suffix in _QUOTE_SUFFIXES:
        if coin_upper.endswith(suffix) and len(coin_upper) > len(suffix):
            return coin_upper[: -len(suffix)]
    return coin_upper


@dataclass
class NewsConfirmation:
    coin: str
    status: NewsStatus
    sentiment_score: Optional[float]
    # None khi status='no_data' — 0.0 là giá trị THẬT (sentiment trung
    # tính đo được), khác hẳn "không có dữ liệu để đo". Không gộp 2
    # trường hợp này thành cùng 1 giá trị.
    message_count: int
    confidence_adjustment: float
    key_news: list[dict[str, Any]] = field(default_factory=list)
    window_hours: float = NEWS_CONFIRMATION_WINDOW_HOURS


async def correlate_news_with_technical(
    session: AsyncSession,
    coin: str,
    technical_signal: "ConfluentSignal",
    window_hours: float = NEWS_CONFIRMATION_WINDOW_HOURS,
    upper_threshold: float = NEWS_CONFIRMATION_UPPER_THRESHOLD,
    lower_threshold: float = NEWS_CONFIRMATION_LOWER_THRESHOLD,
    confirm_boost: float = NEWS_CONFIRMATION_CONFIRM_BOOST,
    conflict_penalty: float = NEWS_CONFIRMATION_CONFLICT_PENALTY,
    min_message_count: int = NEWS_CONFIRMATION_MIN_MESSAGE_COUNT,
) -> NewsConfirmation:
    """
    Đối chiếu sentiment tin tức Telegram gần đây với hướng tín hiệu kỹ
    thuật của 1 coin.

    Args:
        session: AsyncSession SQLAlchemy đang mở — hàm này KHÔNG tự tạo
            session/engine, giống contract của aggregate_coin_sentiment().
        coin: Mã coin — chấp nhận cả dạng cặp Binance ("BTCUSDT") lẫn
            dạng ngắn ("BTC"), sẽ tự chuẩn hóa (xem stopgap mục 4).
        technical_signal: Output của analyze_confluence() (Ngày 11).
            Chỉ dùng 2 field: .coin (để đối chiếu chéo) và .direction.
        window_hours: Cửa sổ thời gian tính sentiment VÀ tìm key_news.
        upper_threshold / lower_threshold: Ngưỡng phân loại
            confirmed/conflicted/neutral. CHƯA calibrate — xem cảnh báo
            rag/config.py.
        confirm_boost / conflict_penalty: Điều chỉnh confidence cộng
            dồn cho Signal Aggregator (Ngày 31). CHƯA calibrate — N32
            ablation sẽ quyết định giá trị cuối.
        min_message_count: Số tin tối thiểu trong cửa sổ để tin vào
            confirmed/conflicted. Dưới ngưỡng này → status='no_data'
            dù message_count > 0. Mặc định 1 (không lọc gì thêm so với
            hành vi ban đầu) — N32 ablation sẽ tinh chỉnh.

    Returns:
        NewsConfirmation. status='no_data' khi không có tin nào có
        sentiment_score trong cửa sổ — PHÂN BIỆT rõ với status='neutral'
        (có dữ liệu, nhưng không đủ mạnh để confirm/conflict).

    Raises:
        ValueError: `coin` (sau chuẩn hóa) không khớp `technical_signal.coin`
            (sau chuẩn hóa), hoặc `technical_signal.direction` không phải
            'long'/'short'/'neutral'.
    """
    coin_base = _to_base_symbol(coin)
    signal_coin_base = _to_base_symbol(technical_signal.coin)

    if coin_base != signal_coin_base:
        raise ValueError(
            f"coin={coin!r} (chuẩn hóa={coin_base!r}) không khớp "
            f"technical_signal.coin={technical_signal.coin!r} "
            f"(chuẩn hóa={signal_coin_base!r}) — gọi nhầm coin?"
        )

    sentiment_summary = await aggregate_coin_sentiment(
        session,
        coin=coin_base,
        window_hours=window_hours,
    )

    if sentiment_summary.message_count == 0:
        logger.info(
            f"[NEWS_CONFIRM] coin={coin_base} 0 tin có sentiment trong "
            f"{window_hours}h → no_data"
        )
        return NewsConfirmation(
            coin=coin_base,
            status="no_data",
            sentiment_score=None,
            message_count=0,
            confidence_adjustment=0.0,
            key_news=[],
            window_hours=window_hours,
        )

    score = sentiment_summary.mean_weighted_score
    direction = technical_signal.direction

    if direction not in ("neutral", "long", "short"):
        raise ValueError(f"technical_signal.direction không hợp lệ: {direction!r}")

    status: NewsStatus
    if sentiment_summary.message_count < min_message_count:
        # Có tin (message_count > 0, đã loại ca ==0 ở trên) nhưng CHƯA
        # đủ để tin — xem mục 6, docstring đầu file. sentiment_score
        # vẫn giữ giá trị thật (không ép None) để không mất khả năng
        # debug/quan sát, chỉ status báo "đừng hành động theo số này".
        status = "no_data"
        logger.info(
            f"[NEWS_CONFIRM] coin={coin_base} message_count="
            f"{sentiment_summary.message_count} < min_message_count="
            f"{min_message_count} → no_data (score thật vẫn giữ để debug)"
        )
    elif direction == "neutral":
        # Không có hướng kỹ thuật nào để confirm/conflict — sentiment
        # dù mạnh cỡ nào cũng không có gì để đối chiếu.
        status = "neutral"
    elif direction == "long":
        if score > upper_threshold:
            status = "confirmed"
        elif score < lower_threshold:
            status = "conflicted"
        else:
            status = "neutral"
    else:  # direction == "short"
        # Đối xứng với nhánh long: sentiment càng ÂM càng xác nhận
        # short, sentiment càng DƯƠNG càng mâu thuẫn với short.
        if score < lower_threshold:
            status = "confirmed"
        elif score > upper_threshold:
            status = "conflicted"
        else:
            status = "neutral"

    confidence_adjustment = {
        "confirmed": confirm_boost,
        "conflicted": conflict_penalty,
        "neutral": 0.0,
        "no_data": 0.0,
    }[status]

    # key_news CHỈ để hiển thị/giải thích — KHÔNG dùng trong tính điểm
    # (xem mục 1, docstring đầu file). Lỗi ở bước này không được làm
    # sập cả hàm — status/sentiment_score đã tính xong và đáng tin cậy
    # độc lập với key_news.
    try:
        key_news = search_telegram_news(
            query=coin_base,
            coin=coin_base,
            hours_ago=window_hours,
            require_coin_mentioned=True,
            top_k=NEWS_CONFIRMATION_KEY_NEWS_COUNT,
        )
    except Exception:
        logger.warning(
            f"[NEWS_CONFIRM] search_telegram_news thất bại cho coin={coin_base}, "
            f"key_news để rỗng (không ảnh hưởng status/sentiment_score)",
            exc_info=True,
        )
        key_news = []

    logger.info(
        f"[NEWS_CONFIRM] coin={coin_base} direction={direction} score={score:.4f} "
        f"messages={sentiment_summary.message_count} status={status} "
        f"adj={confidence_adjustment:+.2f}"
    )

    return NewsConfirmation(
        coin=coin_base,
        status=status,
        sentiment_score=score,
        message_count=sentiment_summary.message_count,
        confidence_adjustment=confidence_adjustment,
        key_news=key_news,
        window_hours=window_hours,
    )
