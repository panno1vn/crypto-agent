"""
technical_analysis/confluence.py

Ngày 11 — Multi-Timeframe Confluence (đã nối data thật ở Ngày 13)
======================================================================
Thay đổi so với bản mock:
  - analyze_confluence() giờ là ASYNC, nhận thêm `session` (AsyncSession)
    để đọc OHLCV thật từ DB qua fetch_ohlcv_from_db().
  - Mỗi timeframe: fetch DF thật -> calculate_all() -> detect_trend()
    -> find_swing_points() -> find_recent_swing()+calculate_fib_levels().
  - detect_trend()/find_recent_swing()/calculate_fib_levels() đều RAISE
    ValueError khi thiếu data/NaN (theo đúng thiết kế Ngày 10) — đây là
    tình huống BÌNH THƯỜNG (coin mới, chưa đủ nến), không phải bug, nên
    bị bắt riêng và chỉ log warning, không log traceback đầy đủ.
  - Exception KHÔNG lường trước (bug thật) được log với exc_info=True
    để còn debug được, nhưng vẫn không làm sập cả hàm — timeframe đó
    bị loại khỏi vote, các timeframe khác vẫn tính bình thường.
  - Import fetch_ohlcv_from_db được đặt CỤC BỘ trong hàm để tránh
    circular import (indicator_pipeline.py cũng import từ confluence.py).

Toàn bộ helper (weighted_vote, count_aligned, find_entry_zone,
merge_sr_levels, classify_rsi, classify_macd) GIỮ NGUYÊN không đổi —
đã có unit test riêng ở tests/unit/test_confluence.py, không cần sửa.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

from data_pipeline.logger import get_logger
from technical_analysis.fibonacci import calculate_fib_levels, find_recent_swing
from technical_analysis.indicators import calculate_all
from technical_analysis.support_resistance import cluster_levels, find_swing_points
from technical_analysis.trend import detect_trend

logger = get_logger(__name__)


@dataclass
class ConfluentSignal:
    coin: str
    direction: Literal["long", "short", "neutral"]
    strength: float
    timeframes_aligned: int
    entry_zone: tuple
    key_sr_levels: dict
    atr_1h: float
    fib_levels: dict
    reasoning: dict


TF_WEIGHTS = {"1d": 0.40, "4h": 0.30, "1h": 0.20, "15m": 0.10}


# ---------------------------------------------------------------------------
# Helpers — KHÔNG đổi, giữ nguyên như bản đã review/test ở Ngày 11
# ---------------------------------------------------------------------------


def classify_rsi(rsi: float) -> str:
    """Phân loại tín hiệu RSI."""
    if rsi > 65:
        return "bullish"
    elif rsi < 35:
        return "bearish"
    return "neutral"


def classify_macd(indicators: Any) -> str:
    """Phân loại tín hiệu MACD."""
    if indicators.macd_line > indicators.macd_signal:
        return "bullish"
    elif indicators.macd_line < indicators.macd_signal:
        return "bearish"
    return "neutral"


def _get_trend_direction(data: Dict[str, Any]) -> Optional[str]:
    """
    Lấy trend direction, hỗ trợ cả TrendSignal dataclass (real) lẫn
    dict (mock/test). Trả None nếu không lấy được.
    """
    trend = data.get("trend")
    if trend is None:
        return None
    if hasattr(trend, "direction"):
        return trend.direction
    if isinstance(trend, dict):
        return trend.get("direction")
    return None


def weighted_vote(
    signals: Dict[str, Any],
    weights: Dict[str, float],
) -> Tuple[str, float]:
    """Bỏ phiếu có trọng số, chuẩn hóa theo tổng trọng số THỰC SỰ dùng."""
    if not signals:
        return "neutral", 0.0

    score = 0.0
    total_weight = 0.0

    for tf, data in signals.items():
        w = weights.get(tf, 0)
        if w == 0:
            continue
        total_weight += w

        trend_dir = _get_trend_direction(data)
        if trend_dir == "uptrend":
            score += w
        elif trend_dir == "downtrend":
            score -= w

        rsi_signal = data.get("rsi_signal")
        if rsi_signal == "bullish":
            score += w * 0.2
        elif rsi_signal == "bearish":
            score -= w * 0.2

        macd_signal = data.get("macd_signal")
        if macd_signal == "bullish":
            score += w * 0.2
        elif macd_signal == "bearish":
            score -= w * 0.2

    if total_weight == 0:
        return "neutral", 0.0

    normalized_score = score / total_weight
    direction = (
        "long"
        if normalized_score > 0.2
        else "short" if normalized_score < -0.2 else "neutral"
    )
    strength = min(abs(normalized_score), 1.0)

    return direction, strength


def count_aligned(signals: Dict[str, Any], final_direction: str) -> int:
    """Đếm số khung thời gian đồng thuận với hướng quyết định."""
    if final_direction == "long":
        target_trend = "uptrend"
    elif final_direction == "short":
        target_trend = "downtrend"
    else:
        target_trend = "sideways"

    count = 0
    for data in signals.values():
        if _get_trend_direction(data) == target_trend:
            count += 1
    return count


def find_entry_zone(
    signals: Dict[str, Any],
    direction: str,
    current_price: Optional[float] = None,
) -> tuple:
    """Tìm vùng giá vào lệnh dựa trên S/R của khung 1h."""
    sr_1h = signals.get("1h", {}).get("sr", {})
    key = "support" if direction == "long" else "resistance"
    levels = sr_1h.get(key, [])

    if not levels:
        return (0.0, 0.0)

    if current_price is not None:
        nearest = min(levels, key=lambda lv: abs(lv - current_price))
    else:
        logger.warning(
            "find_entry_zone() gọi không có current_price — dùng "
            "positional fallback, kết quả có thể không chính xác."
        )
        nearest = levels[-1] if direction == "long" else levels[0]

    return (nearest * 0.995, nearest * 1.005)


def merge_sr_levels(signals: Dict[str, Any]) -> dict:
    """Gộp và lọc các mức Hỗ trợ/Kháng cự từ tất cả các khung."""
    merged_support: list = []
    merged_resistance: list = []

    for data in signals.values():
        sr = data.get("sr", {})
        merged_support.extend(sr.get("support", []))
        merged_resistance.extend(sr.get("resistance", []))

    support_clustered = cluster_levels(merged_support)
    resistance_clustered = cluster_levels(merged_resistance)

    return {
        "support": support_clustered[-5:],
        "resistance": resistance_clustered[:5],
    }


# ---------------------------------------------------------------------------
# Main function — giờ ASYNC, dùng data thật
# ---------------------------------------------------------------------------


async def analyze_confluence(
    session,
    coin: str,
    current_price: Optional[float] = None,
    as_of=None,  # datetime | None — truyền cho backtest lịch sử
) -> ConfluentSignal:
    """
    Tính confluence signal đa khung thời gian dựa trên OHLCV thật trong DB.

    Mỗi timeframe được bọc try/except riêng theo 2 loại lỗi:
      - ValueError (thiếu data / NaN do warm-up period): tình huống
        BÌNH THƯỜNG, chỉ log warning ngắn gọn, loại timeframe khỏi vote.
      - Exception khác (bug thật, DB lỗi...): log đầy đủ traceback để
        debug, nhưng vẫn không crash cả hàm.
    as_of: nếu truyền, mọi timeframe chỉ dùng data <= as_of. Mặc định
    None -> hành vi realtime cũ không đổi.
    """
    # Import cục bộ để tránh circular import: indicator_pipeline.py
    # cũng import analyze_confluence từ module này.
    from technical_analysis.indicator_pipeline import fetch_ohlcv_from_db

    signals: Dict[str, Any] = {}

    for tf in ["15m", "1h", "4h", "1d"]:
        try:
            df = await fetch_ohlcv_from_db(session, coin, tf, limit=200, as_of=as_of)

            if df is None or df.empty:
                logger.warning(f"[CONFLUENCE] {coin} {tf}: không có OHLCV, bỏ qua.")
                continue

            indicators = calculate_all(df)
            trend = detect_trend(df)  # raise ValueError nếu thiếu data/NaN
            sr = find_swing_points(df)
            swing_high, swing_low = find_recent_swing(df)

            fib_direction = "up" if trend.direction == "uptrend" else "down"
            fib = calculate_fib_levels(swing_high, swing_low, direction=fib_direction)

            rsi_val = indicators.rsi
            atr_val = indicators.atr

            signals[tf] = {
                "trend": trend,
                "rsi_signal": (
                    classify_rsi(rsi_val) if not math.isnan(rsi_val) else "neutral"
                ),
                "macd_signal": classify_macd(indicators),
                "sr": sr,
                "fib": fib,
                "atr": atr_val if not math.isnan(atr_val) else 0.0,
            }

        except ValueError as e:
            # Thiếu data / NaN — bình thường, không phải bug.
            logger.warning(f"[CONFLUENCE] Bỏ qua timeframe={tf} coin={coin}: {e}")
            continue
        except Exception as e:
            # Lỗi không lường trước — cần biết traceback để debug.
            logger.error(
                f"[CONFLUENCE] Lỗi không mong đợi timeframe={tf} coin={coin}: {e}",
                exc_info=True,
            )
            continue

    if not signals:
        logger.error(f"[CONFLUENCE] Không lấy được timeframe nào cho {coin}.")
        return ConfluentSignal(
            coin=coin,
            direction="neutral",
            strength=0.0,
            timeframes_aligned=0,
            entry_zone=(0.0, 0.0),
            key_sr_levels={"support": [], "resistance": []},
            atr_1h=0.0,
            fib_levels={},
            reasoning={},
        )

    direction, strength = weighted_vote(signals, TF_WEIGHTS)

    return ConfluentSignal(
        coin=coin,
        direction=direction,
        strength=strength,
        timeframes_aligned=count_aligned(signals, direction),
        entry_zone=find_entry_zone(signals, direction, current_price),
        key_sr_levels=merge_sr_levels(signals),
        atr_1h=signals.get("1h", {}).get("atr", 0.0),
        fib_levels=signals.get("4h", {}).get("fib", {}),
        reasoning={tf: _get_trend_direction(s) for tf, s in signals.items()},
    )
