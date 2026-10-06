"""
tests/unit/test_news_confirmation.py

Ngày 27 — Test cho rag/news_confirmation.py

Mock 2 biên phụ thuộc ngoài (aggregate_coin_sentiment: Postgres,
search_telegram_news: Chroma) — logic status/threshold/stopgap-symbol
là pure logic, không cần DB/Chroma thật để test đúng sai.

GIẢ ĐỊNH CHƯA XÁC NHẬN: dùng pytest-asyncio (`@pytest.mark.asyncio`).
Nếu project dùng config khác (anyio, hoặc asyncio_mode="auto" trong
pyproject.toml khiến decorator này thừa/thiếu), sửa lại theo đúng
convention thật của conftest.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nlp.engagement_weighting import CoinSentimentSummary
from rag.news_confirmation import correlate_news_with_technical


def _make_signal(coin: str, direction: str):
    """
    Stub tối giản — news_confirmation.py chỉ đọc .coin và .direction từ
    ConfluentSignal, không cần dựng đủ dataclass thật (entry_zone,
    fib_levels, ...) để test module này.
    """
    return SimpleNamespace(coin=coin, direction=direction)


def _make_summary(coin: str, score: float, count: int) -> CoinSentimentSummary:
    from datetime import datetime, timezone

    return CoinSentimentSummary(
        coin=coin,
        mean_weighted_score=score,
        message_count=count,
        window_hours=6,
        computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


@pytest.fixture
def mock_session():
    return AsyncMock()


# ---------------------------------------------------------------------------
# no_data
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_data_khi_message_count_bang_0(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.0, 0)),
    ):
        result = await correlate_news_with_technical(
            mock_session, "BTC", _make_signal("BTC", "long")
        )

    assert result.status == "no_data"
    assert result.sentiment_score is None
    assert result.message_count == 0
    assert result.confidence_adjustment == 0.0
    assert result.key_news == []


# ---------------------------------------------------------------------------
# direction == 'long'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_long_score_cao_hon_upper_threshold_la_confirmed(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.5, 10)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
            confirm_boost=0.10,
            conflict_penalty=-0.15,
        )

    assert result.status == "confirmed"
    assert result.confidence_adjustment == 0.10


@pytest.mark.asyncio
async def test_long_score_thap_hon_lower_threshold_la_conflicted(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", -0.5, 10)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
            confirm_boost=0.10,
            conflict_penalty=-0.15,
        )

    assert result.status == "conflicted"
    assert result.confidence_adjustment == -0.15


@pytest.mark.asyncio
async def test_long_score_giua_2_nguong_la_neutral(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.02, 10)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
        )

    assert result.status == "neutral"
    assert result.confidence_adjustment == 0.0


# ---------------------------------------------------------------------------
# direction == 'short' — pin lại fix bug đối xứng (pseudocode gốc thiếu nhánh này)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_short_score_thap_hon_lower_threshold_la_confirmed(mock_session):
    """Sentiment càng ÂM càng xác nhận short — đối xứng với nhánh long."""
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", -0.5, 10)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "short"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
            confirm_boost=0.10,
            conflict_penalty=-0.15,
        )

    assert result.status == "confirmed"
    assert result.confidence_adjustment == 0.10


@pytest.mark.asyncio
async def test_short_score_cao_hon_upper_threshold_la_conflicted(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.5, 10)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "short"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
            confirm_boost=0.10,
            conflict_penalty=-0.15,
        )

    assert result.status == "conflicted"
    assert result.confidence_adjustment == -0.15


# ---------------------------------------------------------------------------
# direction == 'neutral'
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_technical_neutral_luon_ra_neutral_bat_ke_sentiment(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.9, 10)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "neutral"),
        )

    assert result.status == "neutral"
    assert result.confidence_adjustment == 0.0


# ---------------------------------------------------------------------------
# Guard: coin không khớp technical_signal.coin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_coin_khong_khop_technical_signal_raise_value_error(mock_session):
    with pytest.raises(ValueError, match="không khớp"):
        await correlate_news_with_technical(
            mock_session,
            "ETH",
            _make_signal("BTC", "long"),
        )


@pytest.mark.asyncio
async def test_direction_khong_hop_le_raise_value_error(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.5, 10)),
    ):
        with pytest.raises(ValueError, match="direction không hợp lệ"):
            await correlate_news_with_technical(
                mock_session,
                "BTC",
                _make_signal("BTC", "sideways"),
            )


# ---------------------------------------------------------------------------
# Stopgap symbol normalization (nợ N31) — xem docstring mục 4 trong
# rag/news_confirmation.py
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_symbol_binance_pair_duoc_chuan_hoa_truoc_khi_goi_aggregate(mock_session):
    """
    coin='BTCUSDT' (dạng Binance) phải được cắt về 'BTC' trước khi gọi
    aggregate_coin_sentiment — nếu không, query sẽ luôn ra 0 dòng vì
    coins_mentioned lưu dạng ngắn. Đây là test pin lại stopgap, không
    phải fix chính thức của nợ N31.
    """
    mock_aggregate = AsyncMock(return_value=_make_summary("BTC", 0.5, 10))
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment", new=mock_aggregate
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTCUSDT",
            _make_signal("BTCUSDT", "long"),
        )

    called_kwargs = mock_aggregate.call_args.kwargs
    assert called_kwargs["coin"] == "BTC"
    assert result.coin == "BTC"


# ---------------------------------------------------------------------------
# Gate min_message_count — phương án lai thêm 2026-08-15
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_min_message_count_mac_dinh_1_khong_doi_hanh_vi_cu(mock_session):
    """Mặc định min_message_count=1: message_count=1 vẫn tính status bình thường."""
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.5, 1)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
        )

    assert result.status == "confirmed"


@pytest.mark.asyncio
async def test_min_message_count_cao_hon_ep_ve_no_data_nhung_giu_score_that(
    mock_session,
):
    """message_count=1 < min_message_count=3 → no_data, nhưng sentiment_score
    vẫn là giá trị thật (không ép None) để giữ khả năng debug."""
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.9, 1)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
            min_message_count=3,
        )

    assert result.status == "no_data"
    assert result.sentiment_score == 0.9  # KHÔNG None — khác ca message_count=0
    assert result.message_count == 1
    assert result.confidence_adjustment == 0.0


@pytest.mark.asyncio
async def test_min_message_count_du_nguong_tinh_status_binh_thuong(mock_session):
    """message_count=3 == min_message_count=3 → đủ ngưỡng, tính status bình thường."""
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.5, 3)),
    ), patch("rag.news_confirmation.search_telegram_news", return_value=[]):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
            min_message_count=3,
        )

    assert result.status == "confirmed"


# ---------------------------------------------------------------------------
# key_news lỗi không được làm sập status/sentiment_score
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_telegram_news_loi_khong_lam_sap_ham(mock_session):
    with patch(
        "rag.news_confirmation.aggregate_coin_sentiment",
        new=AsyncMock(return_value=_make_summary("BTC", 0.5, 10)),
    ), patch(
        "rag.news_confirmation.search_telegram_news",
        side_effect=RuntimeError("Chroma down"),
    ):
        result = await correlate_news_with_technical(
            mock_session,
            "BTC",
            _make_signal("BTC", "long"),
            upper_threshold=0.1,
            lower_threshold=-0.1,
        )

    assert result.status == "confirmed"
    assert result.sentiment_score == 0.5
    assert result.key_news == []
