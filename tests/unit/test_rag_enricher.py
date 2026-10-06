"""
tests/unit/test_rag_enricher.py

Ngày 22 — Unit tests cho rag/enricher.py

Không cần ChromaDB server, không cần tải model. Chạy được offline.
"""

from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.telegram.historical_scraper import TelegramMessage
from rag.enricher import build_metadata, enrich_message_for_embedding, to_utc


def make_msg(**overrides) -> TelegramMessage:
    """Factory tin nhắn hợp lệ, cho phép ghi đè từng field."""
    defaults = dict(
        id=12345,
        channel_name="bitcoin_vietnam_news",
        message_text="Cá voi đang gom BTC mạnh trong phiên hôm nay.",
        language="vi",
        views=1500,
        forwards=20,
        created_at=datetime(2026, 8, 10, 7, 30, tzinfo=timezone.utc),
        coins_mentioned=["BTC"],
    )
    defaults.update(overrides)
    return TelegramMessage(**defaults)


# ---------------------------------------------------------------------------
# Group 1: to_utc — quy ước timezone của project
# ---------------------------------------------------------------------------


def test_to_utc_naive_duoc_coi_la_utc():
    """
    Datetime naive đọc từ Postgres phải được HIỂU là UTC, không phải giờ
    local. Đây là bug lặp lại nhiều lần trong project (Ngày 15, Ngày 20).
    """
    naive = datetime(2026, 8, 10, 7, 30)
    result = to_utc(naive)
    assert result.tzinfo == timezone.utc
    assert result.hour == 7  # KHÔNG bị dịch sang 0h hay 14h


def test_to_utc_aware_duoc_chuyen_ve_utc():
    """Datetime aware ở múi khác phải được quy đổi, không phải gắn đè."""
    vn = timezone(timedelta(hours=7))
    aware = datetime(2026, 8, 10, 14, 30, tzinfo=vn)
    result = to_utc(aware)
    assert result.tzinfo == timezone.utc
    assert result.hour == 7


def test_created_ts_khong_lech_theo_may_chay():
    """
    `created_ts` phải giống hệt nhau dù datetime naive hay aware.

    Nếu dùng `naive.timestamp()` trực tiếp, Python lấy timezone của máy
    (VN = UTC+7) → lệch 25200 giây → bộ lọc "6h qua" ở Ngày 24 sai.
    """
    naive_msg = make_msg(created_at=datetime(2026, 8, 10, 7, 30))
    aware_msg = make_msg(created_at=datetime(2026, 8, 10, 7, 30, tzinfo=timezone.utc))
    assert (
        build_metadata(naive_msg)["created_ts"]
        == build_metadata(aware_msg)["created_ts"]
    )


# ---------------------------------------------------------------------------
# Group 2: enrich_message_for_embedding
# ---------------------------------------------------------------------------


def test_enrich_chua_du_cac_field():
    """
    ENRICH_INCLUDE_TIME=false, ENRICH_INCLUDE_ENGAGEMENT=false trong .env
    (quyết định Ngày 22 — cả 2 field gần như vô nghĩa về ngữ nghĩa và đã
    có sẵn trong metadata để lọc bằng where ở Ngày 24-25). Test bản gốc
    viết trước quyết định này nên assert nhầm cả 2 field vẫn còn trong
    text — sửa lại Ngày 24 sau khi chạy full suite lộ ra lỗi.
    """
    msg = make_msg()
    text = enrich_message_for_embedding(msg)
    assert "[Channel: bitcoin_vietnam_news]" in text
    assert "[Coins: BTC]" in text
    assert "Cá voi đang gom BTC" in text
    assert "[Time:" not in text
    assert "[Engagement:" not in text


def test_enrich_message_text_none_khong_sinh_chu_None():
    """
    `message_text` là Optional[str]. f-string trên None tạo ra chuỗi
    "None" rồi đem đi embed — rác ngữ nghĩa, không có lỗi nào báo ra.
    """
    msg = make_msg(message_text=None)
    text = enrich_message_for_embedding(msg)
    assert "None" not in text


def test_enrich_bo_coins_khi_khong_co():
    """Không có coin thì không chèn `[Coins: ]` rỗng vào text."""
    msg = make_msg(coins_mentioned=[])
    text = enrich_message_for_embedding(msg)
    assert "[Coins:" not in text


def test_enrich_tat_time_va_engagement():
    """Hai field này tắt được để tiết kiệm ngân sách 128 token."""
    msg = make_msg()
    text = enrich_message_for_embedding(
        msg, include_time=False, include_engagement=False
    )
    assert "[Time:" not in text
    assert "[Engagement:" not in text
    assert "[Channel:" in text
    assert "[Coins: BTC]" in text


def test_enrich_deterministic():
    """Cùng input phải cho cùng output — nếu không, upsert lại sẽ ghi
    document khác nhau giữa các lần chạy."""
    msg = make_msg(coins_mentioned=["ETH", "BTC", "SOL"])
    assert enrich_message_for_embedding(msg) == enrich_message_for_embedding(msg)


# ---------------------------------------------------------------------------
# Group 3: build_metadata — các ràng buộc CỨNG của Chroma
# ---------------------------------------------------------------------------


def test_metadata_khong_bao_gio_co_gia_tri_none():
    """Chroma ném TypeError nếu metadata chứa None."""
    msg = make_msg(language=None)
    meta = build_metadata(msg)
    assert None not in meta.values()
    assert meta["language"] == "unknown"


def test_metadata_bo_han_key_coins_khi_rong():
    """
    Chroma từ chối mảng rỗng:
    `ValueError: Expected metadata list value for key 'coins' to be
    non-empty in add`. Phải bỏ hẳn key, không phải để [].
    """
    msg = make_msg(coins_mentioned=[])
    meta = build_metadata(msg)
    assert "coins" not in meta
    assert meta["coins_count"] == 0


def test_metadata_coins_la_list_khong_phai_chuoi():
    """
    `$contains` chỉ hoạt động trên mảng. Nếu lưu "BTC,ETH" dạng chuỗi,
    Chroma trả về [] trong IM LẶNG — không lỗi, chỉ mất kết quả.
    """
    msg = make_msg(coins_mentioned=["BTC", "ETH"])
    meta = build_metadata(msg)
    assert isinstance(meta["coins"], list)
    assert meta["coins"] == ["BTC", "ETH"]


def test_metadata_created_ts_la_int():
    """`$gte`/`$lte` của Chroma chỉ nhận int/float, không nhận chuỗi ISO."""
    meta = build_metadata(make_msg())
    assert isinstance(meta["created_ts"], int)
    assert isinstance(meta["created_at"], str)


def test_metadata_coins_duoc_chuan_hoa_va_khu_trung_lap():
    msg = make_msg(coins_mentioned=["btc", "BTC", " eth ", ""])
    meta = build_metadata(msg)
    assert meta["coins"] == ["BTC", "ETH"]
    assert meta["coins_count"] == 2


@pytest.mark.parametrize(
    "key,expected_type",
    [
        ("msg_id", int),
        ("channel", str),
        ("language", str),
        ("created_at", str),
        ("created_ts", int),
        ("views", int),
        ("forwards", int),
        ("coins_count", int),
    ],
)
def test_metadata_kieu_du_lieu(key, expected_type):
    """Chroma chỉ nhận str/int/float/bool (hoặc mảng đồng nhất)."""
    meta = build_metadata(make_msg())
    assert isinstance(meta[key], expected_type)
