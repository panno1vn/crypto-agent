from datetime import datetime, timezone

import pytest

from data_pipeline.telegram.historical_scraper import (TelegramMessage,
                                                       detect_language,
                                                       extract_coins)

# ---------------------------------------------------------------------------
# Group 1: extract_coins — 3 tests
# ---------------------------------------------------------------------------


def test_extract_coins_btc():
    """Kiểm tra bóc tách 1 coin chuẩn xác."""
    text = "Thị trường hôm nay chứng kiến BTC tăng mạnh."
    coins = extract_coins(text)
    assert "BTC" in coins
    assert len(coins) == 1


def test_extract_coins_multiple():
    """
    Bóc tách nhiều coin cùng lúc, không phân biệt hoa thường.

    'btc' và 'Eth' phải được normalize thành 'BTC' và 'ETH'.
    Đây là edge case quan trọng vì user thường gõ chữ thường.
    """
    text = "Cá voi đang gom btc và Eth liên tục."
    coins = extract_coins(text)
    assert "BTC" in coins
    assert "ETH" in coins
    assert len(coins) == 2


def test_extract_coins_no_match():
    """Không có coin nào trong danh sách CRYPTO_ENTITIES → trả về list rỗng."""
    text = "Tin tức bình thường không có đồng tiền ảo nào."
    coins = extract_coins(text)
    assert coins == []


# ---------------------------------------------------------------------------
# Group 2: TelegramMessage Pydantic schema — 2 tests
# ---------------------------------------------------------------------------


def test_parse_valid_message():
    """Schema khởi tạo thành công với tin nhắn hợp lệ."""
    msg = TelegramMessage(
        id=999,
        channel_name="whale_alert",
        message_text="Tin tức phân tích thị trường rất chi tiết và dài đủ tiêu chuẩn.",
        language="vi",
        views=1500,
        forwards=20,
        created_at=datetime.now(timezone.utc),
        coins_mentioned=["BTC"],
    )
    assert msg.id == 999
    assert msg.channel_name == "whale_alert"
    assert msg.views == 1500


def test_skip_short_message():
    """
    Schema ném ValueError nếu message_text < 10 ký tự.

    Validator 'text_not_too_short' trong TelegramMessage phải bắt case này.
    """
    with pytest.raises(ValueError, match="Message too short"):
        TelegramMessage(
            id=1000,
            channel_name="whale_alert",
            message_text="Quá ngắn",  # 8 ký tự — dưới ngưỡng 10
            created_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Group 3: detect_language — 2 tests (bổ sung)
# ---------------------------------------------------------------------------


def test_detect_language_vietnamese():
    """
    Câu tiếng Việt đủ dài phải được detect là 'vi'.

    Dùng câu có từ thuần Việt rõ ràng để langdetect không nhầm.
    """
    text = "Thị trường tiền điện tử hôm nay biến động mạnh với Bitcoin tăng vọt."
    result = detect_language(text)
    assert result == "vi"


def test_detect_language_too_short():
    """
    Text < 20 ký tự → trả về None (không thể detect chính xác).

    Quan trọng vì downstream NLP pipeline sẽ skip message có language=None.
    """
    assert detect_language("BTC") is None
    assert detect_language("") is None
    assert detect_language(None) is None
