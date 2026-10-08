"""
tests/integration/test_sentiment_pipeline.py

Ngày 19 — Integration tests: cần DB thật (crypto_agent_test).

⚠️ LƯU Ý QUAN TRỌNG: file này dùng fixture LOCAL (tự tạo engine/session,
tự create_all/drop_all trên crypto_agent_test), KHÔNG dùng chung
fixture `db_session` từ conftest.py — vì tại thời điểm viết file này
chưa xem được nội dung thật của conftest.py (chỉ biết nó tồn tại và
mô tả chung qua docs/WEEK2.md, không biết chính xác signature/scope).

Nếu project đã có sẵn fixture `db_session` dùng chung phù hợp, CÂN
NHẮC hợp nhất — xoá fixture local ở đây, đổi test sang dùng fixture
chung, tránh 2 cách setup DB test song song gây nhầm lẫn. Gửi
conftest.py nếu muốn tôi hợp nhất giúp.

Dùng _StubAnalyzer thay vì MultilingualSentimentAnalyzer thật — KHÔNG
load PhoBERT/FinBERT (nặng, cần model file trên đĩa, không phù hợp
chạy trong GitHub Actions CI theo docs/WEEK2.md mục "code coverage >60%"
và mục tiêu "cold start <5 phút").
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from data_pipeline.models import Base, TelegramChannel, TelegramMessage
from nlp.engagement_weighting import aggregate_coin_sentiment
from nlp.schemas import SentimentResult
from nlp.sentiment_pipeline import process_unprocessed_messages

# pytest KHÔNG tự đọc .env như script chạy tay (python -m ...) — phải
# gọi tường minh, nếu không os.environ['POSTGRES_USER'] sẽ KeyError.
load_dotenv()


def _test_db_dsn() -> str:
    return (
        f"postgresql+asyncpg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/crypto_agent_test"
    )


def _utc_naive(hours_ago: float = 0) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago)


class _StubAnalyzer:
    """
    Analyzer giả lập — map text cố định -> SentimentResult, hoặc raise
    nếu text nằm trong fail_on. KHÔNG load model thật.
    """

    def __init__(
        self,
        canned: dict[str, Optional[SentimentResult]],
        fail_on: Optional[set[str]] = None,
    ):
        self._canned = canned
        self._fail_on = fail_on or set()

    def analyze(self, text: str) -> Optional[SentimentResult]:
        if text in self._fail_on:
            raise RuntimeError(f"[STUB] Giả lập lỗi phân tích: {text[:30]}")
        return self._canned.get(text)


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(_test_db_dsn())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine):
    session_factory = sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


def _session_factory_from(engine: AsyncEngine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# process_unprocessed_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_marks_successful_messages_as_processed(db_session, db_engine):
    text = "BTC tăng mạnh hôm nay, tin tốt cho thị trường."
    msg = TelegramMessage(
        id=900001,
        channel_name="test_channel",
        message_text=text,
        language="vi",
        views=100,
        forwards=10,
        created_at=_utc_naive(),
        coins_mentioned=["BTC"],
        is_processed=False,
    )
    db_session.add(msg)
    await db_session.commit()

    analyzer = _StubAnalyzer(
        canned={
            text: SentimentResult(
                label="positive",
                score=0.8,
                confidence=0.8,
                language="vi",
                model_used="phobert-v2-stub",
            )
        }
    )

    count = await process_unprocessed_messages(
        session_factory=_session_factory_from(db_engine),
        analyzer=analyzer,
        batch_size=10,
    )
    assert count == 1

    refreshed = await db_session.get(
        TelegramMessage, {"channel_name": "test_channel", "id": 900001}
    )
    await db_session.refresh(refreshed)
    assert refreshed.is_processed is True
    assert refreshed.sentiment_label == "positive"
    assert float(refreshed.sentiment_score) == pytest.approx(0.8, abs=1e-3)
    assert refreshed.sentiment_model_version == "phobert-v2-stub"
    assert refreshed.sentiment_analyzed_at is not None


@pytest.mark.asyncio
async def test_failed_message_is_not_marked_processed(db_session, db_engine):
    """
    Message lỗi phân tích PHẢI giữ is_processed=False để được thử lại
    ở lần chạy DAG kế tiếp — không được mất data vĩnh viễn.
    """
    bad_text = "Message sẽ gây lỗi phân tích."
    msg = TelegramMessage(
        id=900002,
        channel_name="test_channel",
        message_text=bad_text,
        language="vi",
        views=5,
        forwards=0,
        created_at=_utc_naive(),
        coins_mentioned=[],
        is_processed=False,
    )
    db_session.add(msg)
    await db_session.commit()

    analyzer = _StubAnalyzer(canned={}, fail_on={bad_text})

    count = await process_unprocessed_messages(
        session_factory=_session_factory_from(db_engine),
        analyzer=analyzer,
        batch_size=10,
    )
    assert count == 0

    refreshed = await db_session.get(
        TelegramMessage, {"channel_name": "test_channel", "id": 900002}
    )
    await db_session.refresh(refreshed)
    assert refreshed.is_processed is False
    assert refreshed.sentiment_label is None


@pytest.mark.asyncio
async def test_none_result_still_marks_processed(db_session, db_engine):
    """
    analyzer.analyze() trả None (ngôn ngữ không xác định) là kết quả
    HỢP LỆ, không phải lỗi -> vẫn phải mark processed để không bị
    query lại vô thời hạn mỗi lần DAG chạy.
    """
    msg = TelegramMessage(
        id=900003,
        channel_name="test_channel",
        message_text="OK",
        language=None,
        views=0,
        forwards=0,
        created_at=_utc_naive(),
        coins_mentioned=[],
        is_processed=False,
    )
    db_session.add(msg)
    await db_session.commit()

    analyzer = _StubAnalyzer(canned={"OK": None})

    count = await process_unprocessed_messages(
        session_factory=_session_factory_from(db_engine),
        analyzer=analyzer,
        batch_size=10,
    )
    assert count == 1  # tính là "đã xử lý" dù kết quả None

    refreshed = await db_session.get(
        TelegramMessage, {"channel_name": "test_channel", "id": 900003}
    )
    await db_session.refresh(refreshed)
    assert refreshed.is_processed is True
    assert refreshed.sentiment_label is None


@pytest.mark.asyncio
async def test_infinite_retry_guard_within_single_run(db_session, db_engine):
    """
    Message LUÔN LUÔN lỗi (fail_on chứa nó) không được gây vòng lặp
    vô hạn TRONG CÙNG 1 lần gọi process_unprocessed_messages — verify
    cơ chế attempted_ids hoạt động đúng (hàm phải return, không treo).
    """
    bad_text = "Luôn luôn lỗi."
    msg = TelegramMessage(
        id=900004,
        channel_name="test_channel",
        message_text=bad_text,
        language="vi",
        views=0,
        forwards=0,
        created_at=_utc_naive(),
        coins_mentioned=[],
        is_processed=False,
    )
    db_session.add(msg)
    await db_session.commit()

    analyzer = _StubAnalyzer(canned={}, fail_on={bad_text})

    # batch_size=1 để cố tình ép nhiều vòng lặp — nếu attempted_ids
    # không hoạt động, hàm sẽ treo vô hạn ở đây và test timeout.
    count = await process_unprocessed_messages(
        session_factory=_session_factory_from(db_engine),
        analyzer=analyzer,
        batch_size=1,
    )
    assert count == 0

    refreshed = await db_session.get(
        TelegramMessage, {"channel_name": "test_channel", "id": 900004}
    )
    await db_session.refresh(refreshed)
    assert refreshed.is_processed is False


@pytest.mark.asyncio
async def test_tin_loi_khong_chan_tin_kenh_khac_cung_msg_id(db_session, db_engine):
    """
    Nợ #15: hai kênh cùng msg_id là hai tin KHÁC NHAU. Tin kênh A lỗi
    không được loại luôn tin kênh B khỏi batch (code cũ lọc
    `id NOT IN attempted_ids` nên B bị bỏ qua cả lần chạy).
    """
    bad_text = "Tin kênh A luôn lỗi."
    good_text = "Tin kênh B phân tích được."
    for channel, text in (("kenh_a", bad_text), ("kenh_b", good_text)):
        db_session.add(
            TelegramMessage(
                id=900005,
                channel_name=channel,
                message_text=text,
                language="vi",
                views=0,
                forwards=0,
                created_at=_utc_naive(),
                coins_mentioned=[],
                is_processed=False,
            )
        )
    await db_session.commit()

    analyzer = _StubAnalyzer(
        canned={
            good_text: SentimentResult(
                label="positive",
                score=0.8,
                confidence=0.8,
                language="vi",
                model_used="phobert-v2-stub",
            )
        },
        fail_on={bad_text},
    )

    # ORDER BY (id, channel_name) đưa kenh_a lên trước; batch_size=1 để
    # vòng sau phải lọc theo attempted_ids.
    count = await process_unprocessed_messages(
        session_factory=_session_factory_from(db_engine),
        analyzer=analyzer,
        batch_size=1,
    )
    assert count == 1

    b = await db_session.get(TelegramMessage, {"channel_name": "kenh_b", "id": 900005})
    await db_session.refresh(b)
    assert b.is_processed is True
    assert b.sentiment_label == "positive"


# ---------------------------------------------------------------------------
# aggregate_coin_sentiment — relevance gate + window + credibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregate_coin_sentiment_filters_by_coin_and_window(db_session):
    channel = TelegramChannel(
        id=1, username="news_channel", credibility=1.0, created_at=_utc_naive()
    )
    db_session.add(channel)
    await db_session.flush()

    # 1. Trong window, nhắc BTC -> PHẢI được tính
    db_session.add(
        TelegramMessage(
            id=910001,
            channel_id=1,
            channel_name="news_channel",
            message_text="BTC tin tốt",
            views=100,
            forwards=10,
            created_at=_utc_naive(hours_ago=1),
            coins_mentioned=["BTC"],
            is_processed=True,
            sentiment_score=0.6,
        )
    )
    # 2. Trong window, nhắc ETH (không phải BTC) -> KHÔNG tính vào BTC
    db_session.add(
        TelegramMessage(
            id=910002,
            channel_id=1,
            channel_name="news_channel",
            message_text="ETH tin tốt",
            views=100,
            forwards=10,
            created_at=_utc_naive(hours_ago=1),
            coins_mentioned=["ETH"],
            is_processed=True,
            sentiment_score=0.9,
        )
    )
    # 3. Nhắc BTC nhưng NGOÀI window (10h trước, window=4h) -> loại
    db_session.add(
        TelegramMessage(
            id=910003,
            channel_id=1,
            channel_name="news_channel",
            message_text="BTC tin cũ",
            views=100,
            forwards=10,
            created_at=_utc_naive(hours_ago=10),
            coins_mentioned=["BTC"],
            is_processed=True,
            sentiment_score=-0.5,
        )
    )
    # 4. Nhắc BTC, trong window, nhưng CHƯA phân tích xong -> loại
    db_session.add(
        TelegramMessage(
            id=910004,
            channel_id=1,
            channel_name="news_channel",
            message_text="BTC tin chưa xử lý",
            views=100,
            forwards=10,
            created_at=_utc_naive(hours_ago=1),
            coins_mentioned=["BTC"],
            is_processed=False,
            sentiment_score=None,
        )
    )
    await db_session.commit()

    summary = await aggregate_coin_sentiment(db_session, coin="BTC", window_hours=4)

    assert summary.coin == "BTC"
    assert summary.message_count == 1  # CHỈ message id=910001
    assert summary.mean_weighted_score > 0.6  # có engagement boost, vẫn dương


@pytest.mark.asyncio
async def test_aggregate_coin_sentiment_uses_channel_credibility(db_session):
    """Channel credibility thấp -> weighted score gần base hơn (yếu hơn)."""
    low_cred_channel = TelegramChannel(
        id=2, username="low_cred", credibility=0.2, created_at=_utc_naive()
    )
    high_cred_channel = TelegramChannel(
        id=3, username="high_cred", credibility=1.0, created_at=_utc_naive()
    )
    db_session.add_all([low_cred_channel, high_cred_channel])
    await db_session.flush()

    db_session.add(
        TelegramMessage(
            id=920001,
            channel_id=2,
            channel_name="low_cred",
            message_text="SOL tin tốt (low cred)",
            views=5000,
            forwards=500,
            created_at=_utc_naive(hours_ago=1),
            coins_mentioned=["SOL"],
            is_processed=True,
            sentiment_score=0.5,
        )
    )
    await db_session.commit()

    low_summary = await aggregate_coin_sentiment(db_session, coin="SOL", window_hours=4)

    # Xoá message low-cred, thêm message high-cred cùng score/engagement
    await db_session.delete(
        await db_session.get(
            TelegramMessage, {"channel_name": "low_cred", "id": 920001}
        )
    )
    await db_session.flush()
    db_session.add(
        TelegramMessage(
            id=920002,
            channel_id=3,
            channel_name="high_cred",
            message_text="SOL tin tốt (high cred)",
            views=5000,
            forwards=500,
            created_at=_utc_naive(hours_ago=1),
            coins_mentioned=["SOL"],
            is_processed=True,
            sentiment_score=0.5,
        )
    )
    await db_session.commit()

    high_summary = await aggregate_coin_sentiment(
        db_session, coin="SOL", window_hours=4
    )

    assert high_summary.mean_weighted_score > low_summary.mean_weighted_score
