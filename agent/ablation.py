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
     - Tín hiệu shift(1) trước khi vào lệnh (như N12), xem _backtest_two_sided().
     - Ngưỡng vào/ra lệnh = quantile của confidence TRÊN ĐOẠN HIỆU CHỈNH
       (calib_frac đầu cửa sổ). Backtest CHỈ chạy trên đoạn còn lại. Nợ #5:
       ngưỡng cứng 0.65 nằm ở đuôi xa của phân phối thật (mean≈0.23).
     - Dùng quantile (không dùng ngưỡng tuyệt đối chung) vì phân phối
       confidence của 3 nhánh khác nhau: nhánh có sentiment bị kéo về 0.5
       khi sentiment trung tính. Cùng quantile = cùng "độ kén" lệnh, so
       sánh công bằng hơn cùng một con số tuyệt đối.

  3. HAI CHIỀU (long + short). Lần chạy thử đầu tiên (BTC 2026-06-06 →
     06-20) có 245 mốc short, 64 long: backtest long-only của N12 bỏ ~73% tín
     hiệu và gần như không có lệnh. Vì vậy ablation có backtest 2 chiều riêng
     (_backtest_two_sided), KHÔNG sửa backtest_confluence_signal() của N12
     (baseline cũ giữ nguyên). Short ở đây giả định có thể bán khống (futures/
     margin); executor spot ở tuần 7 không làm được short — đây là đánh giá
     chất lượng TÍN HIỆU, không phải chiến lược có thể chạy ngay.
     Ngưỡng tính CHUNG cho 2 chiều vì công thức confidence đối xứng.

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
# Cache CSV
# ---------------------------------------------------------------------------
def write_components_csv(df: pd.DataFrame, path) -> None:
    validate_components(df)
    df.to_csv(path)


def read_components_csv(path) -> pd.DataFrame:
    """
    Đọc cache với float_precision="round_trip". Bộ đọc số thực mặc định của
    pandas có thể lệch bit cuối (vd strength 0.84 thành 0.84000000000000008).
    Confidence dồn đúng tại ngưỡng (trần 0.85), nên lệch 1 ulp lật được điều
    kiện `>=` và đổi kết quả backtest. Bug thật 2026-10-08: XRP ra 2.435%
    từ cache so với 1.875% từ DB, cùng dữ liệu.
    """
    df = pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip")
    validate_components(df)
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


SIDES_BOTH = ("long", "short")


def branch_confidence(
    components: pd.DataFrame,
    weights: SignalWeights,
    upper_threshold: float,
    lower_threshold: float,
    min_message_count: int,
    sides: tuple[str, ...] = SIDES_BOTH,
) -> pd.Series:
    """
    Confidence (luôn >= 0) của một nhánh tại từng nến, theo hướng TA của nến
    đó, dùng ĐÚNG logic realtime: classify_news_status() (N27) +
    sentiment_alignment()/combine_scores() (N31). Hướng của nến đọc ở cột
    `direction`. Mốc neutral hoặc hướng không thuộc `sides` → 0.0.
    """
    validate_components(components)
    bad = set(sides) - set(SIDES_BOTH)
    if not sides or bad:
        raise ValueError(f"sides không hợp lệ: {sides}")
    out = np.zeros(len(components))
    for i, row in enumerate(components.itertuples(index=False)):
        direction = row.direction
        if direction not in sides:
            continue
        count = int(row.message_count)
        sentiment = row.sentiment
        if count == 0:
            status = "no_data"
        else:
            status = classify_news_status(
                score=sentiment,
                message_count=count,
                direction=direction,
                upper_threshold=upper_threshold,
                lower_threshold=lower_threshold,
                min_message_count=min_message_count,
            )
        if status == "no_data":
            senti_score = news_score = None
        else:
            senti_score = sentiment_alignment(float(sentiment), direction)
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
    Quantile q của các giá trị > 0 (mốc có tín hiệu). Mốc = 0 là "không
    có tín hiệu", gộp vào sẽ kéo ngưỡng về 0 và biến mọi tín hiệu thành lệnh.
    """
    if not 0.0 < q < 1.0:
        raise ValueError(f"q phải trong (0, 1): {q}")
    positive = confidence[confidence > 0]
    if positive.empty:
        raise ValueError("không có mốc tín hiệu nào trong đoạn hiệu chỉnh")
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
    """
    Tỉ lệ mốc CÓ HƯỚNG (long/short) có sentiment đủ tin. Thấp → 3 nhánh gần
    như trùng nhau.
    """
    rows = components[components["direction"] != "neutral"]
    if rows.empty:
        return 0.0
    ok = (rows["message_count"] >= max(1, min_message_count)).mean()
    return float(ok)


def _backtest_two_sided(
    close: pd.Series,
    confidence: pd.Series,
    direction: pd.Series,
    entry_threshold: float,
    exit_threshold: float,
    sl_pct: float,
    tp_pct: float,
    fees: float,
    timeframe: str,
) -> dict:
    """
    Backtest 2 chiều bằng vectorbt. Tín hiệu shift(1) như N12: tính xong ở
    nến T thì chỉ được vào lệnh ở nến T+1.

    Vào long: hướng (đã shift) = long và confidence >= entry_threshold.
    `>=` chứ không `>`: trần CONFIDENCE_CAP ép mọi điểm >= 0.85 về đúng 0.85,
    tạo một khối giá trị trùng nhau. Khi quantile rơi đúng vào khối đó,
    `>` không bao giờ đúng → 0 lệnh (bug thật ở lần chạy thử 2026-10-08).
    Thoát long: hướng không còn long, hoặc confidence < exit_threshold.
    Short đối xứng. Entry và exit cùng chiều không thể cùng True vì
    exit_threshold < entry_threshold.
    """
    import vectorbt as vbt

    from technical_analysis.backtest import _safe_float

    conf = confidence.shift(1)
    side = direction.shift(1)
    long_entries = (side == "long") & (conf >= entry_threshold)
    long_exits = (side != "long") | (conf < exit_threshold)
    short_entries = (side == "short") & (conf >= entry_threshold)
    short_exits = (side != "short") | (conf < exit_threshold)

    portfolio = vbt.Portfolio.from_signals(
        close=close,
        entries=long_entries,
        exits=long_exits,
        short_entries=short_entries,
        short_exits=short_exits,
        sl_stop=sl_pct,
        tp_stop=tp_pct,
        fees=fees,
        freq=timeframe,
    )
    stats = portfolio.stats()
    return {
        "win_rate": _safe_float(stats.get("Win Rate [%]")),
        "sharpe_ratio": _safe_float(stats.get("Sharpe Ratio")),
        "max_drawdown": _safe_float(stats.get("Max Drawdown [%]")),
        "profit_factor": _safe_float(stats.get("Profit Factor")),
        "total_trades": int(_safe_float(stats.get("Total Trades"))),
        "total_return": _safe_float(stats.get("Total Return [%]")),
        "long_entry_signals": int(long_entries.sum()),
        "short_entry_signals": int(short_entries.sum()),
    }


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
    fees: float = 0.001,
    sides: tuple[str, ...] = SIDES_BOTH,
    timeframe: str = "1h",
) -> dict:
    """
    Chạy một nhánh cho một coin. Trả metric backtest (đoạn đánh giá) cùng
    ngưỡng đã chọn (đoạn hiệu chỉnh).

    fees: tỉ lệ phí mỗi lần khớp (0.001 = 0.1%, phí taker spot Binance).
    Baseline N12 không tính phí; ablation so các nhánh với nhau nên chỉ cần
    các nhánh cùng phí.
    """
    if exit_quantile >= entry_quantile:
        raise ValueError(
            f"exit_quantile {exit_quantile} phải < entry_quantile {entry_quantile}"
        )
    if fees < 0:
        raise ValueError(f"fees âm: {fees}")
    conf = branch_confidence(
        components,
        branch.weights,
        upper_threshold,
        lower_threshold,
        min_message_count,
        sides=sides,
    )
    calib, evaluation = split_calibration(conf, calib_frac)
    entry_th = quantile_threshold(calib, entry_quantile)
    exit_th = quantile_threshold(calib, exit_quantile)

    idx = evaluation.index
    direction = components["direction"].loc[idx]
    metrics = _backtest_two_sided(
        close=components["close"].loc[idx],
        confidence=evaluation,
        direction=direction.where(direction.isin(sides), "neutral"),
        entry_threshold=entry_th,
        exit_threshold=exit_th,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        fees=fees,
        timeframe=timeframe,
    )
    return {
        **metrics,
        "entry_threshold": entry_th,
        "exit_threshold": exit_th,
        "eval_candles": int(len(evaluation)),
        "eval_start": str(idx[0]),
        "eval_end": str(idx[-1]),
    }
