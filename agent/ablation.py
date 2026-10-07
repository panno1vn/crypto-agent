"""
agent/ablation.py

Ngày 32 — Backtest ablation: TA only / TA + sentiment / TA + sentiment + news.

Thiết kế (chi tiết + phương án thay thế: docs/nhat-ky/, task N32):

  1. TÍNH MỘT LẦN, SO NHIỀU NHÁNH. build_component_series() gọi DB để lấy,
     tại mỗi mốc recompute, (hướng + strength của TA, sentiment lịch sử).
     Ba nhánh là hàm THUẦN trên cùng DataFrame đó, nên chắc chắn cùng cửa
     sổ, cùng nến, cùng dữ liệu sentiment. Khác nhau duy nhất là trọng số.

  2. Không look-ahead:
     - analyze_confluence(as_of=t) và aggregate_coin_sentiment(as_of=t) chỉ
       thấy dữ liệu <= t.
     - backtest_confluence_signal() shift(1) tín hiệu (N12).
     - Ngưỡng vào/ra lệnh = quantile của confidence TRÊN ĐOẠN HIỆU CHỈNH
       (calib_frac đầu cửa sổ). Backtest CHỈ chạy trên đoạn còn lại. Nợ #5:
       ngưỡng cứng 0.65 nằm ở đuôi xa của phân phối thật (mean≈0.23).
     - Dùng quantile (không dùng ngưỡng tuyệt đối chung) vì phân phối
       confidence của 3 nhánh khác nhau: nhánh có sentiment bị kéo về 0.5
       khi sentiment trung tính. Cùng quantile = cùng "độ kén" lệnh, so
       sánh công bằng hơn cùng một con số tuyệt đối.

  3. Chỉ nhánh LONG: backtest_confluence_signal() là chiến lược một chiều
     (giới hạn có chủ đích của N12). Mốc TA short/neutral → confidence 0.

  4. ⚠️ sentiment và news cùng nguồn (agent/signal_aggregator.py, mục 4).
     Nhánh 3 so với nhánh 2 đo tác dụng của bản phân ngưỡng của cùng biến.

  5. Fail loudly: lỗi DB/confluence giữa chừng raise, KHÔNG giữ giá trị cũ
     như build_confluence_signal_series() (N13). Một ablation lặng lẽ thiếu
     điểm thì không so sánh được.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from agent.signal_aggregator import (
    NEWS_STATUS_SCORE,
    TA_ONLY_WEIGHTS,
    SignalWeights,
    combine_scores,
    sentiment_alignment,
)
from data_pipeline.logger import get_logger
from rag.news_confirmation import classify_news_status

logger = get_logger(__name__)

COMPONENT_COLUMNS = ["close", "direction", "strength", "sentiment", "message_count"]


@dataclass(frozen=True)
class Branch:
    name: str
    weights: SignalWeights


def default_branches(full_weights: SignalWeights) -> list[Branch]:
    """
    3 nhánh của roadmap N32. Nhánh 2 giữ nguyên trọng số sentiment của
    nhánh 3 và dồn phần news về TA, để nhánh 2 → 3 chỉ khác đúng phần news.
    """
    ta_sent = SignalWeights(
        technical=full_weights.technical + full_weights.news,
        sentiment=full_weights.sentiment,
        news=0.0,
    )
    return [
        Branch("ta_only", TA_ONLY_WEIGHTS),
        Branch("ta_sentiment", ta_sent),
        Branch("ta_sentiment_news", full_weights),
    ]


# ---------------------------------------------------------------------------
# Phần chạm DB
# ---------------------------------------------------------------------------
async def build_component_series(
    session,
    coin: str,
    start: datetime,
    end: datetime,
    sentiment_window_hours: float,
    recompute_every_candles: int = 4,
    timeframe: str = "1h",
) -> pd.DataFrame:
    """
    DataFrame index=open_time (naive UTC), cột COMPONENT_COLUMNS:
      direction/strength: analyze_confluence(as_of=t)
      sentiment/message_count: aggregate_coin_sentiment(as_of=t); sentiment
        là NaN khi message_count == 0.
    Giữa 2 mốc recompute: forward-fill (giống N13).

    Raises:
        ValueError: không có nến trong [start, end], hoặc tham số sai.
    """
    from nlp.engagement_weighting import aggregate_coin_sentiment
    from technical_analysis.confluence import analyze_confluence
    from technical_analysis.indicator_pipeline import fetch_ohlcv_from_db

    if recompute_every_candles < 1:
        raise ValueError(
            f"recompute_every_candles phải >= 1: {recompute_every_candles}"
        )
    if start >= end:
        raise ValueError(f"start {start} phải < end {end}")

    base = await fetch_ohlcv_from_db(session, coin, timeframe, limit=1_000_000)
    if base.empty:
        raise ValueError(f"Không có OHLCV '{timeframe}' cho {coin}")
    base = base[(base.index >= start) & (base.index <= end)]
    if base.empty:
        raise ValueError(
            f"Không có nến '{timeframe}' của {coin} trong [{start}, {end}]"
        )

    rows = []
    last: Optional[dict] = None
    for i, (t, candle) in enumerate(base.iterrows()):
        if i % recompute_every_candles == 0:
            ta = await analyze_confluence(
                session, coin, current_price=float(candle["close"]), as_of=t
            )
            senti = await aggregate_coin_sentiment(
                session, coin=coin, window_hours=sentiment_window_hours, as_of=t
            )
            last = {
                "direction": ta.direction,
                "strength": float(ta.strength),
                "sentiment": (
                    float(senti.mean_weighted_score)
                    if senti.message_count > 0
                    else math.nan
                ),
                "message_count": int(senti.message_count),
            }
        rows.append({"close": float(candle["close"]), **last})

    df = pd.DataFrame(rows, index=base.index)[COMPONENT_COLUMNS]
    logger.info(
        f"[ABLATION] {coin}: {len(df)} nến, "
        f"{math.ceil(len(df) / recompute_every_candles)} mốc recompute"
    )
    return df


# ---------------------------------------------------------------------------
# Phần thuần
# ---------------------------------------------------------------------------
def validate_components(df: pd.DataFrame) -> None:
    missing = [c for c in COMPONENT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"thiếu cột: {missing}")
    if df.empty:
        raise ValueError("components rỗng")
    if not df.index.is_monotonic_increasing:
        raise ValueError("index phải tăng dần theo thời gian")
    bad = set(df["direction"].unique()) - {"long", "short", "neutral"}
    if bad:
        raise ValueError(f"direction lạ: {bad}")


def branch_confidence(
    components: pd.DataFrame,
    weights: SignalWeights,
    upper_threshold: float,
    lower_threshold: float,
    min_message_count: int,
) -> pd.Series:
    """
    Confidence của một nhánh tại từng nến, dùng ĐÚNG logic realtime:
    classify_news_status() (N27) + sentiment_alignment()/combine_scores() (N31).
    Mốc không phải long → 0.0 (mục 3 docstring).
    """
    validate_components(components)
    out = np.zeros(len(components))
    for i, row in enumerate(components.itertuples(index=False)):
        if row.direction != "long":
            continue
        count = int(row.message_count)
        sentiment = row.sentiment
        if count == 0:
            status = "no_data"
        else:
            status = classify_news_status(
                score=sentiment,
                message_count=count,
                direction="long",
                upper_threshold=upper_threshold,
                lower_threshold=lower_threshold,
                min_message_count=min_message_count,
            )
        if status == "no_data":
            senti_score = news_score = None
        else:
            senti_score = sentiment_alignment(float(sentiment), "long")
            news_score = NEWS_STATUS_SCORE[status]
        out[i], _ = combine_scores(
            float(row.strength), senti_score, news_score, weights
        )
    return pd.Series(out, index=components.index, name="confidence")


def split_calibration(
    df: pd.DataFrame, calib_frac: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cắt theo thời gian: calib_frac đầu để chọn ngưỡng, phần sau để backtest."""
    if not 0.0 < calib_frac < 1.0:
        raise ValueError(f"calib_frac phải trong (0, 1): {calib_frac}")
    cut = int(len(df) * calib_frac)
    if cut == 0 or cut == len(df):
        raise ValueError(f"{len(df)} nến quá ít để cắt calib_frac={calib_frac}")
    return df.iloc[:cut], df.iloc[cut:]


def quantile_threshold(confidence: pd.Series, q: float) -> float:
    """
    Quantile q của các giá trị > 0 (mốc có tín hiệu long). Mốc = 0 là "không
    có tín hiệu", gộp vào sẽ kéo ngưỡng về 0 và biến mọi tín hiệu thành lệnh.
    """
    if not 0.0 < q < 1.0:
        raise ValueError(f"q phải trong (0, 1): {q}")
    positive = confidence[confidence > 0]
    if positive.empty:
        raise ValueError("không có mốc long nào trong đoạn hiệu chỉnh")
    return float(positive.quantile(q))


def describe_distribution(values: pd.Series) -> dict:
    """Thống kê phân phối để log (nợ #5: strength thật nằm ở đâu)."""
    positive = values[values > 0]
    if positive.empty:
        return {"n_positive": 0}
    return {
        "n_positive": int(len(positive)),
        "share_positive": float(len(positive) / len(values)),
        "mean": float(positive.mean()),
        "std": float(positive.std(ddof=0)),
        "p50": float(positive.quantile(0.50)),
        "p75": float(positive.quantile(0.75)),
        "p90": float(positive.quantile(0.90)),
        "share_gt_0_65": float((positive > 0.65).mean()),
    }


def sentiment_coverage(components: pd.DataFrame, min_message_count: int) -> float:
    """Tỉ lệ mốc LONG có sentiment đủ tin. Thấp → 3 nhánh gần như trùng nhau."""
    long_rows = components[components["direction"] == "long"]
    if long_rows.empty:
        return 0.0
    ok = (long_rows["message_count"] >= max(1, min_message_count)).mean()
    return float(ok)


def run_branch(
    components: pd.DataFrame,
    branch: Branch,
    *,
    upper_threshold: float,
    lower_threshold: float,
    min_message_count: int,
    calib_frac: float,
    entry_quantile: float,
    exit_quantile: float,
    sl_pct: float,
    tp_pct: float,
    timeframe: str = "1h",
) -> dict:
    """
    Chạy một nhánh cho một coin. Trả metric backtest (đoạn đánh giá) cùng
    ngưỡng đã chọn (đoạn hiệu chỉnh).
    """
    from technical_analysis.backtest import backtest_confluence_signal

    if exit_quantile >= entry_quantile:
        raise ValueError(
            f"exit_quantile {exit_quantile} phải < entry_quantile {entry_quantile}"
        )
    conf = branch_confidence(
        components, branch.weights, upper_threshold, lower_threshold, min_message_count
    )
    calib, evaluation = split_calibration(conf, calib_frac)
    entry_th = quantile_threshold(calib, entry_quantile)
    exit_th = quantile_threshold(calib, exit_quantile)

    eval_df = pd.DataFrame(
        {"close": components["close"].loc[evaluation.index], "strength": evaluation}
    )
    metrics = backtest_confluence_signal(
        eval_df,
        timeframe=timeframe,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        strength_threshold=entry_th,
        exit_threshold=exit_th,
    )
    return {
        **metrics,
        "entry_threshold": entry_th,
        "exit_threshold": exit_th,
        "eval_candles": int(len(evaluation)),
        "eval_start": str(evaluation.index[0]),
        "eval_end": str(evaluation.index[-1]),
    }
