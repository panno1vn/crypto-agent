"""
tests/unit/test_fibonacci.py

Ngày 10 — Unit tests cho Fibonacci (6 tests)
"""

import pandas as pd
import pytest

from technical_analysis.fibonacci import calculate_fib_levels, find_recent_swing


def test_calculate_fib_levels_uptrend():
    """direction='up': fib_0 = high, fib_100 = low, fib_500 = trung điểm."""
    levels = calculate_fib_levels(swing_high=100.0, swing_low=50.0, direction="up")
    assert levels["fib_0"] == 100.0
    assert levels["fib_100"] == 50.0
    assert levels["fib_500"] == pytest.approx(75.0)
    assert levels["ext_1618"] < 50.0


def test_calculate_fib_levels_downtrend():
    """direction='down': fib_0 = low, fib_100 = high, ext nằm trên swing_high."""
    levels = calculate_fib_levels(swing_high=100.0, swing_low=50.0, direction="down")
    assert levels["fib_0"] == 50.0
    assert levels["fib_100"] == 100.0
    assert levels["ext_1618"] > 100.0


def test_calculate_fib_levels_invalid_swing_raises():
    """swing_high <= swing_low phải raise ValueError, không được âm thầm trả kết quả sai."""
    with pytest.raises(ValueError, match="phải lớn hơn"):
        calculate_fib_levels(swing_high=50.0, swing_low=100.0)


def test_find_recent_swing_basic():
    """Lấy đúng max(high)/min(low) trong lookback window."""
    df = pd.DataFrame(
        {
            "high": [10, 15, 20, 12, 8],
            "low": [5, 9, 11, 7, 3],
        }
    )
    swing_high, swing_low = find_recent_swing(df, lookback=5)
    assert swing_high == 20.0
    assert swing_low == 3.0


def test_find_recent_swing_all_nan_raises():
    """Toàn bộ high/low trong lookback là NaN -> phải raise, không trả (nan, nan)."""
    df = pd.DataFrame(
        {
            "high": [float("nan")] * 5,
            "low": [float("nan")] * 5,
        }
    )
    with pytest.raises(ValueError, match="NaN"):
        find_recent_swing(df, lookback=5)


def test_calculate_fib_levels_nan_input_raises():
    """swing_high hoặc swing_low là NaN -> phải raise trước khi so sánh."""
    with pytest.raises(ValueError, match="NaN"):
        calculate_fib_levels(swing_high=float("nan"), swing_low=50.0)
