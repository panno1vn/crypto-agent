"""
technical_analysis/fibonacci.py

Ngày 10 — Fibonacci Retracement & Extension
=============================================
"""

import math
from typing import Literal

import pandas as pd

from data_pipeline.logger import get_logger

logger = get_logger(__name__)

Direction = Literal["up", "down"]


def calculate_fib_levels(
    swing_high: float,
    swing_low: float,
    direction: Direction = "up",
) -> dict[str, float]:
    """
    Tính các mức Fibonacci retracement + extension từ 1 cặp swing high/low.

    Args:
        swing_high: Giá đỉnh của swing.
        swing_low:  Giá đáy của swing.
        direction:
            'up'   — đo retracement của 1 đợt TĂNG (từ low → high).
                     fib_0 = high (điểm bắt đầu retrace),
                     fib_100 = low. Dùng cho long setup (mua lại khi giá
                     retrace xuống 0.5/0.618 trong uptrend).
            'down' — đo retracement của 1 đợt GIẢM (từ high → low).
                     fib_0 = low, fib_100 = high. Dùng cho short setup.

    Returns:
        dict các mức fib_0 .. fib_100 và 2 mức extension (ext_1618, ext_2618).

    Raises:
        ValueError: nếu swing_high/swing_low là NaN, hoặc swing_high <=
                     swing_low (data không hợp lệ hoặc gọi nhầm thứ tự
                     tham số).
    """
    if math.isnan(swing_high) or math.isnan(swing_low):
        logger.error(
            f"[FIBONACCI] NaN input: swing_high={swing_high}, swing_low={swing_low}"
        )
        raise ValueError("swing_high/swing_low không được là NaN")

    if swing_high <= swing_low:
        logger.error(f"[FIBONACCI] Invalid swing: high={swing_high} <= low={swing_low}")
        raise ValueError(
            f"swing_high ({swing_high}) phải lớn hơn swing_low ({swing_low})"
        )

    diff = swing_high - swing_low

    # --- Retracement levels (0% -> 100%) ---
    if direction == "up":
        # Đo từ đỉnh xuống đáy: 0% = high, 100% = low
        anchor_0, anchor_100 = swing_high, swing_low
        levels = {
            "fib_0": anchor_0,
            "fib_236": swing_high - diff * 0.236,
            "fib_382": swing_high - diff * 0.382,
            "fib_500": swing_high - diff * 0.500,
            "fib_618": swing_high - diff * 0.618,
            "fib_786": swing_high - diff * 0.786,
            "fib_100": anchor_100,
            # Extension: tiếp tục ĐI XUỐNG dưới swing_low (downside target
            # nếu giá breakdown khỏi swing_low).
            # swing_high - diff*1.618 == swing_low - diff*0.618 (tương đương
            # toán học, viết theo swing_low cho trực quan hơn).
            "ext_1618": swing_low - diff * 0.618,
            "ext_2618": swing_low - diff * 1.618,
        }
    elif direction == "down":
        # Đo từ đáy lên đỉnh: 0% = low, 100% = high
        levels = {
            "fib_0": swing_low,
            "fib_236": swing_low + diff * 0.236,
            "fib_382": swing_low + diff * 0.382,
            "fib_500": swing_low + diff * 0.500,
            "fib_618": swing_low + diff * 0.618,
            "fib_786": swing_low + diff * 0.786,
            "fib_100": swing_high,
            # Extension: tiếp tục ĐI LÊN trên swing_high (upside target
            # nếu giá breakout khỏi swing_high).
            "ext_1618": swing_high + diff * 0.618,
            "ext_2618": swing_high + diff * 1.618,
        }
    else:
        raise ValueError(f"direction phải là 'up' hoặc 'down', nhận: {direction}")

    return levels


def find_recent_swing(df: pd.DataFrame, lookback: int = 50) -> tuple[float, float]:
    """
    Tìm swing high/low gần nhất trong N nến gần nhất.

    Args:
        df: DataFrame OHLCV, cần có cột 'high' và 'low'.
        lookback: Số nến gần nhất để xét.

    Returns:
        (swing_high, swing_low)

    Raises:
        ValueError: nếu df rỗng, thiếu cột, hoặc swing high/low là NaN.
    """
    if df.empty:
        logger.error("[FIBONACCI] find_recent_swing nhận df rỗng")
        raise ValueError("df không được rỗng")

    missing = {"high", "low"} - set(df.columns)
    if missing:
        raise ValueError(f"df thiếu cột: {missing}")

    recent = df.tail(lookback)
    swing_high = float(recent["high"].max())
    swing_low = float(recent["low"].min())

    if math.isnan(swing_high) or math.isnan(swing_low):
        logger.error(
            f"[FIBONACCI] Không tính được swing high/low hợp lệ trong "
            f"{lookback} nến gần nhất (toàn NaN hoặc thiếu dữ liệu)"
        )
        raise ValueError(
            f"swing_high/swing_low là NaN — kiểm tra dữ liệu OHLCV đầu vào "
            f"({lookback} nến gần nhất)"
        )

    return swing_high, swing_low
