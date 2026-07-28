"""
tests/test_indicators.py

Ngày 8 — Unit Tests cho IndicatorCalculator (đã fix sau review)
==================================================================
Fixes so với bản trước:
  1. Fixture dùng random-walk thay vì 4 cột random độc lập
     → tôn trọng OHLC invariant (high là max, low là min thật)
  2. Thêm test cho DataFrame rỗng (Guard 1)
  3. Thêm test cho thiếu cột bắt buộc (Guard 2)
  4. Thêm test ép exception thật (không phải thiếu data) để
     verify nhánh except trong safe_calc() thực sự chạy qua
"""

import numpy as np
import pandas as pd
import pytest

from technical_analysis.indicators import IndicatorSet, calculate_all


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_ohlcv_data():
    """
    Fixture: 100 cây nến giả lập bằng random-walk, tôn trọng OHLC invariant
    (high luôn >= max(open,close), low luôn <= min(open,close)).
    """
    np.random.seed(42)
    n = 100
    close = 60000 + np.cumsum(np.random.normal(0, 150, n))
    open_ = close + np.random.normal(0, 50, n)

    # high/low được suy ra TỪ open/close, đảm bảo high là giá lớn nhất thật
    spread = np.abs(np.random.normal(100, 30, n)) + 10
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = np.random.uniform(100, 1000, n)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


@pytest.fixture
def short_ohlcv_data():
    """Data quá ngắn (10 nến) để test NaN do thiếu data."""
    return pd.DataFrame(
        {
            "open": [60000] * 10,
            "high": [61000] * 10,
            "low": [59000] * 10,
            "close": [60500] * 10,
            "volume": [500] * 10,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_1_calculate_returns_correct_type(sample_ohlcv_data):
    """Test 1: Hàm phải trả về đúng object IndicatorSet."""
    result = calculate_all(sample_ohlcv_data)
    assert isinstance(result, IndicatorSet)
    assert hasattr(result, "rsi")


def test_2_rsi_is_within_valid_bounds(sample_ohlcv_data):
    """Test 2: RSI phải luôn nằm trong khoảng [0, 100]."""
    result = calculate_all(sample_ohlcv_data)
    assert 0 <= result.rsi <= 100, f"RSI phi logic: {result.rsi}"


def test_3_bollinger_bands_logical_order(sample_ohlcv_data):
    """Test 3: Dải BB phải theo thứ tự Upper >= Middle >= Lower."""
    result = calculate_all(sample_ohlcv_data)
    assert result.bb_upper >= result.bb_middle >= result.bb_lower


def test_4_no_nan_values_with_sufficient_data(sample_ohlcv_data):
    """Test 4: Với 100 nến hợp lệ, không chỉ báo nào được phép NaN."""
    result = calculate_all(sample_ohlcv_data)
    for field_name, value in result.__dict__.items():
        assert not np.isnan(value), f"Chỉ báo {field_name} bị NaN dù đủ data"


def test_5_handles_insufficient_data_gracefully(short_ohlcv_data):
    """
    Test 5 (Edge Case): 10 nến — EMA_50 (cần 50 nến) và RSI (cần 14 nến)
    phải trả về NaN mà không làm sập chương trình.
    """
    result = calculate_all(short_ohlcv_data)
    assert np.isnan(result.ema_50), "EMA 50 nên là NaN khi data < 50 nến"
    assert np.isnan(result.rsi), "RSI nên là NaN khi data < 14 nến"


def test_6_empty_dataframe_does_not_crash():
    """Test 6 (Guard 1): DataFrame rỗng phải trả về NaN set, không crash."""
    empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    result = calculate_all(empty_df)
    assert isinstance(result, IndicatorSet)
    assert np.isnan(result.rsi)
    assert np.isnan(result.atr)


def test_7_none_input_does_not_crash():
    """Test 6b: None input cũng phải được xử lý an toàn."""
    result = calculate_all(None)
    assert isinstance(result, IndicatorSet)
    assert np.isnan(result.rsi)


def test_8_missing_required_columns_does_not_crash():
    """Test 7 (Guard 2): Thiếu cột 'volume' phải trả về NaN set, không KeyError."""
    bad_df = pd.DataFrame(
        {
            "open": [60000] * 30,
            "high": [61000] * 30,
            "low": [59000] * 30,
            "close": [60500] * 30,
            # thiếu 'volume'
        }
    )
    result = calculate_all(bad_df)
    assert isinstance(result, IndicatorSet)
    assert np.isnan(result.volume_sma)


def test_9_handles_corrupted_data_via_exception_path():
    """
    Test 8: Ép safe_calc() đi vào nhánh except THẬT (không phải NaN tự nhiên
    do thiếu data). Dùng dtype object/string trong cột close để RSIIndicator
    ném exception thật khi tính toán.
    """
    corrupted_df = pd.DataFrame(
        {
            "open": [60000] * 30,
            "high": [61000] * 30,
            "low": [59000] * 30,
            "close": ["not_a_number"] * 30,  # sai dtype cố ý
            "volume": [500] * 30,
        }
    )
    result = calculate_all(corrupted_df)
    # Không được raise exception ra ngoài — phải bắt được và trả NaN
    assert isinstance(result, IndicatorSet)
    assert np.isnan(result.rsi)
    assert np.isnan(result.ema_20)
