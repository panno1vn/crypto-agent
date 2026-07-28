"""
technical_analysis/trend.py

Ngày 10 — Trend Detection (EMA crossover + RSI confirmation)
================================================================
Dùng thư viện `ta` (không phải pandas_ta) để nhất quán với
technical_analysis/indicators.py (Ngày 8).
"""

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

from data_pipeline.logger import get_logger

logger = get_logger(__name__)

TrendDirection = Literal["uptrend", "downtrend", "sideways"]
TrendStrength = Literal["strong", "weak", "neutral"]


@dataclass
class TrendSignal:
    direction: TrendDirection
    strength: TrendStrength
    price: float
    ema_short: float
    ema_long: float
    rsi: float


def detect_trend(
    df: pd.DataFrame,
    ema_short_period: int = 20,
    ema_long_period: int = 50,
    rsi_period: int = 14,
) -> TrendSignal:
    """
    Xác định xu hướng dựa trên vị trí giá so với EMA20/EMA50 + RSI xác nhận.

    Logic (giữ nguyên ý tưởng roadmap, KHÔNG đổi ngưỡng):
        price > ema20 > ema50 và rsi > 50  -> uptrend / strong
        price > ema50 (không đủ điều kiện trên)  -> uptrend / weak
        price < ema20 < ema50 và rsi < 50  -> downtrend / strong
        price < ema50 (không đủ điều kiện trên)  -> downtrend / weak
        còn lại                             -> sideways / neutral

    Khác với pseudocode gốc: hàm này KHÔNG âm thầm rơi vào 'sideways' khi
    indicator là NaN do thiếu data — nó raise ValueError rõ ràng, vì
    NaN-so-sánh-luôn-False sẽ khiến 'sideways' trông giống 1 tín hiệu thật
    trong khi thực chất là "chưa đủ data để tính EMA50".

    Args:
        df: DataFrame OHLCV, cần cột 'close'. Phải có ít nhất
            max(ema_long_period, rsi_period) + 1 dòng.

    Returns:
        TrendSignal

    Raises:
        ValueError: nếu df thiếu cột 'close', không đủ dữ liệu, hoặc
                     indicator tính ra NaN (dữ liệu có gap/NaN ở đầu vào).
    """
    if "close" not in df.columns:
        raise ValueError("df thiếu cột 'close'")

    min_rows_required = max(ema_long_period, rsi_period) + 1
    if len(df) < min_rows_required:
        logger.error(
            f"[TREND] Không đủ dữ liệu: có {len(df)} dòng, "
            f"cần tối thiểu {min_rows_required}"
        )
        raise ValueError(
            f"Cần ít nhất {min_rows_required} nến để tính trend, " f"chỉ có {len(df)}"
        )

    close = df["close"]

    ema_short = (
        EMAIndicator(close=close, window=ema_short_period).ema_indicator().iloc[-1]
    )
    ema_long = (
        EMAIndicator(close=close, window=ema_long_period).ema_indicator().iloc[-1]
    )
    rsi = RSIIndicator(close=close, window=rsi_period).rsi().iloc[-1]
    price = float(close.iloc[-1])

    # Guard: NaN không được phép lọt xuống logic so sánh im lặng
    values = {"ema_short": ema_short, "ema_long": ema_long, "rsi": rsi, "price": price}
    nan_fields = [name for name, v in values.items() if v is None or math.isnan(v)]
    if nan_fields:
        logger.error(f"[TREND] Indicator trả về NaN cho: {nan_fields}")
        raise ValueError(
            f"Không thể xác định trend — NaN ở: {nan_fields}. "
            "Thường do dữ liệu đầu chuỗi thiếu warm-up period."
        )

    ema_short, ema_long, rsi = float(ema_short), float(ema_long), float(rsi)

    if price > ema_short > ema_long and rsi > 50:
        direction, strength = "uptrend", "strong"
    elif price > ema_long:
        direction, strength = "uptrend", "weak"
    elif price < ema_short < ema_long and rsi < 50:
        direction, strength = "downtrend", "strong"
    elif price < ema_long:
        direction, strength = "downtrend", "weak"
    else:
        direction, strength = "sideways", "neutral"

    logger.info(
        f"[TREND] direction={direction} strength={strength} "
        f"price={price:.4f} ema{ema_short_period}={ema_short:.4f} "
        f"ema{ema_long_period}={ema_long:.4f} rsi={rsi:.2f}"
    )

    return TrendSignal(
        direction=direction,
        strength=strength,
        price=price,
        ema_short=ema_short,
        ema_long=ema_long,
        rsi=rsi,
    )
