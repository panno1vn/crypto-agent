"""
technical_analysis/indicator_pipeline.py

Ngày 13 — Lưu Indicators sau mỗi OHLCV sync
===================================================================
FIX (sau khi nối data thật vào confluence.py):
  analyze_confluence() giờ là async và cần `session` — sửa lại lời gọi
  từ `analyze_confluence(coin, ...)` thành
  `await analyze_confluence(session, coin, ...)`.
Không có thay đổi nào khác so với bản trước.
"""

import math
from datetime import datetime
from decimal import Decimal
from typing import Optional

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from data_pipeline.logger import get_logger
from data_pipeline.models import OHLCV as OHLCVModel
from data_pipeline.models import TechnicalIndicator
from technical_analysis.confluence import analyze_confluence
from technical_analysis.indicators import calculate_all
from technical_analysis.support_resistance import find_swing_points

logger = get_logger(__name__)

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TIMEFRAMES = ["15m", "1h", "4h", "1d"]

MIN_CANDLES_REQUIRED = 50


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _to_decimal_list(values) -> list:
    if not values:
        return []
    result = []
    for v in values:
        f = _safe_float(v)
        if f is None:
            continue
        result.append(Decimal(str(f)))
    return result


async def fetch_ohlcv_from_db(
    session: AsyncSession,
    coin: str,
    timeframe: str,
    limit: int = 200,
    as_of: "Optional[datetime]" = None,
) -> pd.DataFrame:
    """
    Đọc `limit` nến gần nhất của (coin, timeframe) từ bảng ohlcv.

    as_of: nếu truyền, CHỈ lấy nến có open_time <= as_of (dùng cho
    backtest lịch sử — tránh look-ahead bias khi mô phỏng "tại thời
    điểm này trong quá khứ, ta chỉ biết được data đến đây"). Mặc định
    None -> hành vi CŨ không đổi (lấy N nến gần nhất tính đến hiện tại).
    """
    conditions = [OHLCVModel.coin == coin, OHLCVModel.timeframe == timeframe]
    if as_of is not None:
        conditions.append(OHLCVModel.open_time <= as_of)

    stmt = (
        select(OHLCVModel)
        .where(*conditions)
        .order_by(OHLCVModel.open_time.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return pd.DataFrame()

    rows = sorted(rows, key=lambda r: r.open_time)

    df = pd.DataFrame(
        {
            "open_time": [r.open_time for r in rows],
            "open": [float(r.open) for r in rows],
            "high": [float(r.high) for r in rows],
            "low": [float(r.low) for r in rows],
            "close": [float(r.close) for r in rows],
            "volume": [float(r.volume) for r in rows],
        }
    ).set_index("open_time")

    return df


async def upsert_indicators(session: AsyncSession, records: list[dict]) -> None:
    if not records:
        logger.warning("[UPSERT] Empty records list, skip.")
        return

    stmt = insert(TechnicalIndicator).values(records)

    update_columns = [
        "rsi_14",
        "macd_line",
        "macd_signal",
        "macd_histogram",
        "bb_upper",
        "bb_middle",
        "bb_lower",
        "ema_20",
        "ema_50",
        "atr_14",
        "support_levels",
        "resistance_levels",
        "confluence_score",
        "trend_direction",
    ]
    do_update_stmt = stmt.on_conflict_do_update(
        index_elements=["coin", "timeframe", "calculated_at"],
        set_={col: getattr(stmt.excluded, col) for col in update_columns},
    )

    try:
        await session.execute(do_update_stmt)
        await session.commit()
        logger.info(f"[UPSERT] Saved {len(records)} indicator record(s).")
    except Exception as e:
        await session.rollback()
        logger.error(f"[UPSERT] Failed: {e}", exc_info=True)
        raise


async def calculate_and_save_indicators(
    session: AsyncSession, coin: str, tf: str
) -> None:
    df = await fetch_ohlcv_from_db(session, coin, tf, limit=200)

    if df is None or df.empty:
        logger.warning(f"[INDICATORS] Skip {coin} {tf}: không có OHLCV trong DB.")
        return

    if len(df) < MIN_CANDLES_REQUIRED:
        logger.warning(
            f"[INDICATORS] Skip {coin} {tf}: chỉ có {len(df)} nến "
            f"(<{MIN_CANDLES_REQUIRED}), EMA50/MACD sẽ ra NaN."
        )
        return

    indicators = calculate_all(df)
    sr = find_swing_points(df)

    confluence_score, trend_direction = None, None
    if tf == "1h":
        try:
            current_price = float(df["close"].iloc[-1])
            # FIX: analyze_confluence giờ async, cần session
            confluence = await analyze_confluence(
                session, coin, current_price=current_price
            )
            confluence_score = _safe_float(confluence.strength)
            trend_direction = confluence.direction
        except Exception as e:
            logger.error(
                f"[INDICATORS] analyze_confluence() failed {coin}: {e}", exc_info=True
            )

    record = {
        "coin": coin,
        "timeframe": tf,
        "calculated_at": df.index[-1],
        "rsi_14": _safe_float(indicators.rsi),
        "macd_line": _safe_float(indicators.macd_line),
        "macd_signal": _safe_float(indicators.macd_signal),
        "macd_histogram": _safe_float(indicators.macd_histogram),
        "bb_upper": _safe_float(indicators.bb_upper),
        "bb_middle": _safe_float(indicators.bb_middle),
        "bb_lower": _safe_float(indicators.bb_lower),
        "ema_20": _safe_float(indicators.ema_20),
        "ema_50": _safe_float(indicators.ema_50),
        "atr_14": _safe_float(indicators.atr),
        "support_levels": _to_decimal_list(sr.get("support", [])),
        "resistance_levels": _to_decimal_list(sr.get("resistance", [])),
        "confluence_score": confluence_score,
        "trend_direction": trend_direction,
    }

    await upsert_indicators(session, [record])
    logger.info(
        f"[INDICATORS] Done {coin} {tf} calculated_at={record['calculated_at']}"
    )


async def calculate_and_save_all(session_factory) -> None:
    total, failed = 0, 0
    for coin in COINS:
        for tf in TIMEFRAMES:
            total += 1
            async with session_factory() as session:
                try:
                    await calculate_and_save_indicators(session, coin, tf)
                except Exception as e:
                    failed += 1
                    logger.error(f"[BATCH] Failed {coin} {tf}: {e}", exc_info=True)
                    continue

    logger.info(f"[BATCH] Hoàn tất: {total - failed}/{total} thành công.")
