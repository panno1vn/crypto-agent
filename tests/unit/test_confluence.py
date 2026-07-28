"""
tests/unit/test_confluence.py

Ngày 11 — Unit Tests cho Confluence Analyzer (đã fix + bổ sung)
==================================================================
Bổ sung so với bản trước:
  - test_count_aligned_neutral_direction   (fix bug direction='neutral')
  - test_weighted_vote_empty_signals       (tránh chia 0)
  - test_weighted_vote_partial_timeframes  (chuẩn hóa total_weight_used)
  - test_find_entry_zone_no_levels_returns_zero
  - test_find_entry_zone_uses_current_price_when_given
  - test_merge_sr_levels_deduplicates_close_floats  (không tái phạm lỗi Ngày 9)
"""

from dataclasses import dataclass

import pytest

from technical_analysis.confluence import (
    classify_macd,
    classify_rsi,
    count_aligned,
    find_entry_zone,
    merge_sr_levels,
    weighted_vote,
)


@dataclass
class MockIndicators:
    macd_line: float
    macd_signal: float


# ---------------------------------------------------------------------------
# Group 1: classify_rsi — 2 tests
# ---------------------------------------------------------------------------


def test_classify_rsi_bullish():
    assert classify_rsi(70) == "bullish"


def test_classify_rsi_bearish():
    assert classify_rsi(30) == "bearish"
    assert classify_rsi(50) == "neutral"


# ---------------------------------------------------------------------------
# Group 2: classify_macd — 2 tests
# ---------------------------------------------------------------------------


def test_classify_macd_bullish():
    indicators = MockIndicators(macd_line=1.5, macd_signal=1.0)
    assert classify_macd(indicators) == "bullish"


def test_classify_macd_bearish():
    indicators = MockIndicators(macd_line=-1.0, macd_signal=0.0)
    assert classify_macd(indicators) == "bearish"


# ---------------------------------------------------------------------------
# Group 3: weighted_vote — 4 tests
# ---------------------------------------------------------------------------


def test_weighted_vote_strong_long():
    signals = {
        "1d": {
            "trend": {"direction": "uptrend"},
            "rsi_signal": "bullish",
            "macd_signal": "bullish",
        },
        "4h": {
            "trend": {"direction": "uptrend"},
            "rsi_signal": "neutral",
            "macd_signal": "bullish",
        },
    }
    weights = {"1d": 0.6, "4h": 0.4}
    direction, strength = weighted_vote(signals, weights)
    assert direction == "long"
    assert strength > 0.8


def test_weighted_vote_mixed_signals():
    signals = {
        "1d": {
            "trend": {"direction": "downtrend"},
            "rsi_signal": "bearish",
            "macd_signal": "bearish",
        },
        "1h": {
            "trend": {"direction": "uptrend"},
            "rsi_signal": "bullish",
            "macd_signal": "bullish",
        },
    }
    weights = {"1d": 0.8, "1h": 0.2}
    direction, strength = weighted_vote(signals, weights)
    assert direction == "short"  # 1d kéo điểm xuống mạnh hơn


def test_weighted_vote_empty_signals():
    """Signals rỗng không được gây ZeroDivisionError."""
    direction, strength = weighted_vote({}, {})
    assert direction == "neutral"
    assert strength == 0.0


def test_weighted_vote_partial_timeframes_normalizes_correctly():
    """
    Chỉ có 1 timeframe (1d, weight=0.4 trong TF_WEIGHTS gốc) do 3
    timeframe kia bị lỗi fetch. Trước fix, score=0.4 sẽ không bao giờ
    vượt ngưỡng 0.2 một cách "công bằng" vì thang đo bị lệch. Sau fix,
    chuẩn hóa theo total_weight_used=0.4 nên vẫn ra kết quả đúng.
    """
    signals = {
        "1d": {
            "trend": {"direction": "uptrend"},
            "rsi_signal": "bullish",
            "macd_signal": "bullish",
        },
    }
    weights = {"1d": 0.4}  # chỉ 1 timeframe có mặt
    direction, strength = weighted_vote(signals, weights)
    assert direction == "long"
    assert strength == pytest.approx(
        1.0, abs=0.01
    )  # normalized_score = 1.4/0.4 -> clamp về 1.0


# ---------------------------------------------------------------------------
# Group 4: count_aligned — 2 tests
# ---------------------------------------------------------------------------


def test_count_aligned():
    signals = {
        "1d": {"trend": {"direction": "uptrend"}},
        "4h": {"trend": {"direction": "uptrend"}},
        "1h": {"trend": {"direction": "downtrend"}},
    }
    assert count_aligned(signals, "long") == 2
    assert count_aligned(signals, "short") == 1


def test_count_aligned_neutral_direction():
    """
    Bug case đã fix: trước đây direction='neutral' bị rơi vào nhánh
    else -> so sánh nhầm với 'downtrend'. Giờ phải so đúng với 'sideways'.
    """
    signals = {
        "1d": {"trend": {"direction": "sideways"}},
        "4h": {"trend": {"direction": "downtrend"}},
        "1h": {"trend": {"direction": "sideways"}},
    }
    assert count_aligned(signals, "neutral") == 2  # chỉ đếm 2 cái sideways
    assert count_aligned(signals, "short") == 1


# ---------------------------------------------------------------------------
# Group 5: find_entry_zone — 4 tests
# ---------------------------------------------------------------------------


def test_find_entry_zone_long_fallback_no_price():
    """Không truyền current_price -> fallback vị trí cũ (tương thích ngược)."""
    signals = {"1h": {"sr": {"support": [39000, 40000]}}}
    zone = find_entry_zone(signals, "long")
    assert zone[0] < 40000 < zone[1]


def test_find_entry_zone_short_fallback_no_price():
    signals = {"1h": {"sr": {"resistance": [42000, 43000]}}}
    zone = find_entry_zone(signals, "short")
    assert zone[0] < 42000 < zone[1]


def test_find_entry_zone_uses_current_price_when_given():
    """
    Đây là test quan trọng nhất cho fix: list KHÔNG theo thứ tự
    "gần nhất ở cuối" như find_entry_zone giả định trước đây. Nếu code
    vẫn dùng positional fallback, kết quả sẽ sai (chọn 35000 thay vì
    39500 — mức thực sự gần current_price=39800 nhất).
    """
    signals = {
        "1h": {"sr": {"support": [35000, 39500]}}
    }  # cố tình không sort theo độ gần
    zone = find_entry_zone(signals, "long", current_price=39800)
    assert zone[0] < 39500 < zone[1]


def test_find_entry_zone_no_levels_returns_zero():
    assert find_entry_zone({"1h": {"sr": {}}}, "long") == (0.0, 0.0)
    assert find_entry_zone({}, "long") == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Group 6: merge_sr_levels — 2 tests
# ---------------------------------------------------------------------------


def test_merge_sr_levels_basic_dedup():
    signals = {
        "1d": {"sr": {"support": [30000], "resistance": [50000]}},
        "1h": {"sr": {"support": [30000, 32000], "resistance": [48000, 50000]}},
    }
    merged = merge_sr_levels(signals)
    assert len(merged["support"]) == 2
    assert 32000 in merged["support"]


def test_merge_sr_levels_deduplicates_close_floats():
    """
    Không được tái phạm lỗi float-equality của Ngày 9: hai mức gần
    nhau (40000.0 và 40001.5, cách nhau < 0.5%) đến từ 2 timeframe
    khác nhau phải được gộp thành 1 cluster, không phải 2 mức riêng
    biệt như khi dùng set().
    """
    signals = {
        "1d": {"sr": {"support": [40000.0], "resistance": []}},
        "1h": {"sr": {"support": [40001.5], "resistance": []}},
    }
    merged = merge_sr_levels(signals)
    assert len(merged["support"]) == 1  # phải gộp thành 1 cluster
