"""
tests/integration/test_news_confirmation_e2e.py

Ngày 28 — Test end-to-end (KHÔNG mock): seed data thật vào crypto_agent_test
(qua fixture db_session) -> aggregate_coin_sentiment chạy thật (Postgres)
-> correlate_news_with_technical -> NewsConfirmation đúng SỐ, không chỉ
đúng type.

Khác tests/unit/test_news_confirmation.py (N27, 14 test, mock cả 2 biên
phụ thuộc, dùng SimpleNamespace thay ConfluentSignal thật): file này
KHÔNG mock gì, dùng session thật + ConfluentSignal thật đủ 8 field.

Đã verify với data_pipeline/models.py thật (gửi sau khi v2 chạy fail).
Lịch sử: v1 sai 3 chỗ (sync/async, thiếu session, ConfluentSignal thiếu
field) — sửa ở v2. v2 chạy thật (N28): 1/5 pass, 4/5 fail CÙNG 1 nguyên
nhân — `TelegramChannel` không có field `channel_name`, tên thật là
`username` (xác nhận qua models.py). Test pass duy nhất
(no_data_khi_khong_co_tin_trong_cua_so) không cần seed nên không đụng
bug này — xác nhận phần async/session/ConfluentSignal đã đúng từ v2.
v3 (bản này) sửa đúng 1 chỗ đó, không đổi gì khác.

Đối chiếu lại toàn bộ field khác với models.py thật — KHỚP, không cần
sửa thêm: TelegramMessage.channel_name (String, NOT NULL) — đúng;
has_media/reply_count/is_processed (Boolean/Integer, NOT NULL) — đúng;
ingested_at (DateTime, NOT NULL) — đúng; sentiment_score (DECIMAL(4,3),
nullable) — đúng, giá trị test (0.5-0.9) vừa trong khoảng; channel_id
(FK, nullable) — đúng. TelegramChannel.credibility (DECIMAL(3,2),
default=1.0, nullable) — đúng.

Còn 1 việc CHƯA làm, không chặn v3 chạy: chưa test nhánh "retrieve" qua
Chroma thật (key_news). Theo thiết kế N27 (docstring rag/news_confirmation.py
mục 1), key_news KHÔNG ảnh hưởng status/sentiment_score — chỉ minh họa —
lỗi ở đó bị nuốt thành [] có chủ đích. Test cuối file chỉ xác nhận điều
này, KHÔNG dựng full Chroma + embedder thật (~20s lazy-load, chi phí
không tương xứng vì nhánh này không quyết định status).

Chạy: pytest tests/integration/test_news_confirmation_e2e.py -v
Yêu cầu: TEST_DATABASE_URL trỏ đúng crypto_agent_test đang chạy
(docker compose up -d postgres).
"""

from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.models import TelegramChannel, TelegramMessage
from rag.news_confirmation import NewsConfirmation, correlate_news_with_technical
from technical_analysis.confluence import ConfluentSignal


def _make_full_signal(coin: str, direction: str) -> ConfluentSignal:
    """
    Dựng đủ 8 field bắt buộc của ConfluentSignal thật — khác
    SimpleNamespace ở unit test N27. Đây là integration test, muốn
    chạm đúng contract thật mà Signal Aggregator (N31) sẽ dùng, dù
    news_confirmation.py chỉ đọc .coin/.direction.
    """
    return ConfluentSignal(
        coin=coin,
        direction=direction,
        strength=0.7,
        timeframes_aligned=3,
        entry_zone=(0.0, 0.0),
        key_sr_levels={"support": [], "resistance": []},
        atr_1h=0.0,
        fib_levels={},
        reasoning={},
    )


async def _seed_channel(session, username: str, credibility: float = 1.0) -> int:
    """
    TelegramChannel.username là cột NOT NULL định danh kênh (KHÔNG phải
    channel_name — đó là cột riêng trên TelegramMessage, denormalized).
    Xác nhận qua data_pipeline/models.py thật.
    """
    channel = TelegramChannel(username=username, credibility=credibility)
    session.add(channel)
    await session.flush()  # cần channel.id trước khi gán channel_id cho message
    return channel.id


async def _seed_message(
    session,
    msg_id: int,
    channel_name: str,
    channel_id: int,
    coins_mentioned: list[str],
    sentiment_score: float,
    created_at: datetime,
    views: int = 10,
    forwards: int = 1,
) -> None:
    """TODO(Pan): đối chiếu data_pipeline/models.py trước khi tin đúng cột."""
    msg = TelegramMessage(
        id=msg_id,
        channel_id=channel_id,
        channel_name=channel_name,
        message_text="Nội dung test đủ dài để qua validator (>10 ký tự).",
        language="vi",
        views=views,
        forwards=forwards,
        coins_mentioned=coins_mentioned,
        created_at=created_at,
        has_media=False,
        reply_count=0,
        ingested_at=datetime.now(timezone.utc).replace(tzinfo=None),
        is_processed=True,
        sentiment_score=sentiment_score,
    )
    session.add(msg)


# ---------------------------------------------------------------------------
# Happy path — confirmed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correlate_news_long_confirmed_voi_du_lieu_seed_that(db_session):
    """
    Seed 3 tin BTC sentiment dương mạnh (0.8/0.9/0.85), views/forwards
    thấp (weight ~1.0), credibility=1.0 -> mean_weighted_score phải >
    upper_threshold mặc định -> status='confirmed'. Chạm thật
    aggregate_coin_sentiment (Postgres) qua correlate_news_with_technical,
    không mock gì.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    channel_id = await _seed_channel(db_session, "test_channel_28", credibility=1.0)

    for i, score in enumerate([0.8, 0.9, 0.85]):
        await _seed_message(
            db_session,
            msg_id=100_000 + i,
            channel_name="test_channel_28",
            channel_id=channel_id,
            coins_mentioned=["BTC"],
            sentiment_score=score,
            created_at=now - timedelta(hours=1),
        )
    await db_session.commit()

    signal = _make_full_signal("BTC", "long")
    result = await correlate_news_with_technical(
        db_session, "BTC", signal, window_hours=6
    )

    assert isinstance(result, NewsConfirmation)
    assert result.message_count == 3
    assert result.status == "confirmed"
    assert result.sentiment_score > 0


# ---------------------------------------------------------------------------
# no_data — DB rỗng
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correlate_news_no_data_khi_khong_co_tin_trong_cua_so(db_session):
    """DB test rỗng hoàn toàn (không seed gì) -> message_count=0 -> no_data."""
    signal = _make_full_signal("ETH", "long")
    result = await correlate_news_with_technical(db_session, "ETH", signal)

    assert result.status == "no_data"
    assert result.sentiment_score is None
    assert result.message_count == 0


# ---------------------------------------------------------------------------
# Chặn trên/dưới cửa sổ thời gian — pin lại bug N27 (mục 4 GHI_CHU_NGAY27.md)
# trên đường dẫn THẬT qua correlate_news_with_technical, không chỉ unit
# test trực tiếp aggregate_coin_sentiment.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correlate_news_tin_ngoai_cua_so_khong_duoc_tinh(db_session):
    """Seed 1 tin BTC NGOÀI window_hours=6 -> phải bị loại, không tính vào message_count."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    channel_id = await _seed_channel(db_session, "test_channel_28b")
    await _seed_message(
        db_session,
        msg_id=200_001,
        channel_name="test_channel_28b",
        channel_id=channel_id,
        coins_mentioned=["BTC"],
        sentiment_score=0.9,
        created_at=now - timedelta(hours=48),
    )
    await db_session.commit()

    signal = _make_full_signal("BTC", "long")
    result = await correlate_news_with_technical(
        db_session, "BTC", signal, window_hours=6
    )

    assert result.message_count == 0
    assert result.status == "no_data"


# ---------------------------------------------------------------------------
# Stopgap symbol BTC/BTCUSDT (nợ #2, N31) — trên DB THẬT, không mock
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correlate_news_symbol_binance_pair_that_tren_db_that(db_session):
    """
    coins_mentioned lưu 'BTC' (dạng ngắn), gọi bằng 'BTCUSDT' (dạng cặp
    Binance) phải vẫn khớp nhờ _to_base_symbol() stopgap. Khi N31 xóa
    stopgap, test này phải ĐỎ nếu quên thay bằng chuẩn hóa chính thức.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    channel_id = await _seed_channel(db_session, "test_channel_28c")
    await _seed_message(
        db_session,
        msg_id=300_001,
        channel_name="test_channel_28c",
        channel_id=channel_id,
        coins_mentioned=["BTC"],
        sentiment_score=0.5,
        created_at=now - timedelta(hours=1),
    )
    await db_session.commit()

    signal = _make_full_signal("BTCUSDT", "long")
    result = await correlate_news_with_technical(
        db_session, "BTCUSDT", signal, window_hours=6
    )

    assert result.message_count == 1
    assert result.coin == "BTC"


# ---------------------------------------------------------------------------
# key_news không làm sập kết quả khi Chroma test không có gì khớp
# (xem giới hạn phạm vi ở docstring đầu file, mục 2)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_correlate_news_key_news_rong_khong_anh_huong_status(db_session):
    """
    Không seed Chroma -> search_telegram_news() trả về rỗng hoặc lỗi
    (tùy môi trường Chroma test) -> key_news=[] nhưng status/sentiment_score
    vẫn tính đúng từ Postgres, đúng thiết kế N27 (key_news chỉ minh họa).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    channel_id = await _seed_channel(db_session, "test_channel_28d")
    await _seed_message(
        db_session,
        msg_id=400_001,
        channel_name="test_channel_28d",
        channel_id=channel_id,
        coins_mentioned=["ETH"],
        sentiment_score=0.6,
        created_at=now - timedelta(hours=1),
    )
    await db_session.commit()

    signal = _make_full_signal("ETH", "long")
    result = await correlate_news_with_technical(
        db_session, "ETH", signal, window_hours=6
    )

    assert result.status == "confirmed"
    assert result.message_count == 1
    assert isinstance(result.key_news, list)
