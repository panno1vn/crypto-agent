"""
technical_analysis/indicators.py

Ngày 8 — Indicator Calculator (đã fix sau review)
====================================================
Fixes so với bản trước:
  1. safe_calc() giờ log warning khi exception thật xảy ra
     (phân biệt được "thiếu data" vs "bug thật")
  2. Guard DataFrame rỗng trước khi truy cập .iloc[-1] — tránh crash
  3. Bỏ default vô nghĩa trong .get() vì cột luôn tồn tại
  4. Guard thiếu cột bắt buộc (open/high/low/close/volume)
"""

import warnings
from dataclasses import dataclass, fields

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands

from data_pipeline.logger import get_logger

logger = get_logger(__name__)

# Bỏ qua các cảnh báo phân mảnh DataFrame (không liên quan đến tính đúng đắn)
warnings.simplefilter(action="ignore", category=pd.errors.PerformanceWarning)

REQUIRED_COLUMNS = {"open", "high", "low", "close", "volume"}


@dataclass
class IndicatorSet:
    rsi: float
    macd_line: float
    macd_signal: float
    macd_histogram: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    ema_20: float
    ema_50: float
    atr: float
    volume_sma: float


def _empty_indicator_set() -> IndicatorSet:
    """Trả về IndicatorSet toàn NaN — dùng khi input không hợp lệ."""
    return IndicatorSet(**{f.name: np.nan for f in fields(IndicatorSet)})


def safe_calc(calc_func, name: str = "unknown"):
    """
    Lập trình phòng ngự: bắt exception từ thư viện bên thứ 3.

    QUAN TRỌNG: log lại lý do fail. NaN do thiếu data (rolling window
    tự nhiên) sẽ KHÔNG raise exception nên không vào đây — chỉ những
    lỗi thật sự (sai dtype, cột None, thư viện lỗi...) mới rơi vào
    except, và ta cần biết chuyện gì đã xảy ra thay vì im lặng.
    """
    try:
        return calc_func()
    except Exception as e:
        logger.warning(f"[INDICATOR] '{name}' failed to compute: {e}")
        return np.nan


def calculate_all(df: pd.DataFrame) -> IndicatorSet:
    # --- Guard 1: DataFrame rỗng hoặc None ---
    if df is None or df.empty:
        logger.warning("[INDICATOR] Received empty/None DataFrame. Returning NaN set.")
        return _empty_indicator_set()

    # --- Guard 2: thiếu cột bắt buộc ---
    missing_cols = REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        logger.warning(f"[INDICATOR] Missing required columns: {missing_cols}")
        return _empty_indicator_set()

    temp_df = df.copy()

    # 1. RSI
    temp_df["RSI_14"] = safe_calc(
        lambda: RSIIndicator(close=temp_df["close"], window=14).rsi(),
        name="RSI_14",
    )

    # 2. MACD (3 dải cùng lúc)
    def _calc_macd():
        macd = MACD(
            close=temp_df["close"], window_slow=26, window_fast=12, window_sign=9
        )
        return macd.macd(), macd.macd_signal(), macd.macd_diff()

    macd_result = safe_calc(_calc_macd, name="MACD")
    if isinstance(macd_result, tuple):
        temp_df["MACD_line"], temp_df["MACD_signal"], temp_df["MACD_hist"] = macd_result
    else:
        temp_df["MACD_line"] = temp_df["MACD_signal"] = temp_df["MACD_hist"] = np.nan

    # 3. Bollinger Bands (3 dải cùng lúc)
    def _calc_bb():
        bb = BollingerBands(close=temp_df["close"], window=20, window_dev=2)
        return bb.bollinger_hband(), bb.bollinger_mavg(), bb.bollinger_lband()

    bb_result = safe_calc(_calc_bb, name="BollingerBands")
    if isinstance(bb_result, tuple):
        temp_df["BB_upper"], temp_df["BB_middle"], temp_df["BB_lower"] = bb_result
    else:
        temp_df["BB_upper"] = temp_df["BB_middle"] = temp_df["BB_lower"] = np.nan

    # 4. EMAs
    temp_df["EMA_20"] = safe_calc(
        lambda: EMAIndicator(close=temp_df["close"], window=20).ema_indicator(),
        name="EMA_20",
    )
    temp_df["EMA_50"] = safe_calc(
        lambda: EMAIndicator(close=temp_df["close"], window=50).ema_indicator(),
        name="EMA_50",
    )

    # 5. ATR & Volume SMA
    temp_df["ATR_14"] = safe_calc(
        lambda: AverageTrueRange(
            high=temp_df["high"], low=temp_df["low"], close=temp_df["close"], window=14
        ).average_true_range(),
        name="ATR_14",
    )
    temp_df["VOL_SMA_20"] = safe_calc(
        lambda: SMAIndicator(close=temp_df["volume"], window=20).sma_indicator(),
        name="VOL_SMA_20",
    )

    # Lấy hàng cuối cùng (cây nến hiện tại)
    # Không cần default trong .get() — các cột này luôn tồn tại vì vừa gán ở trên
    last_row = temp_df.iloc[-1]

    return IndicatorSet(
        rsi=last_row["RSI_14"],
        macd_line=last_row["MACD_line"],
        macd_signal=last_row["MACD_signal"],
        macd_histogram=last_row["MACD_hist"],
        bb_upper=last_row["BB_upper"],
        bb_middle=last_row["BB_middle"],
        bb_lower=last_row["BB_lower"],
        ema_20=last_row["EMA_20"],
        ema_50=last_row["EMA_50"],
        atr=last_row["ATR_14"],
        volume_sma=last_row["VOL_SMA_20"],
    )
