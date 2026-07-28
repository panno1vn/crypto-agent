"""
technical_analysis/backtest.py

Ngày 12 — Backtesting Signal Quality (đã fix sau review)
===========================================================
Fixes so với bản gốc:
  1. Tách rõ backtest MOCK (random signal, chỉ test luồng VectorBT/MLflow)
     khỏi backtest THẬT (nhận signal đã tính sẵn từ ConfluenceAnalyzer).
     -> Không còn chuyện âm thầm fake signal rồi vô tình coi kết quả là thật.
  2. entries/exits dùng signal.shift(1) — tránh look-ahead bias khi signal
     tại nến T được entry ngay tại giá close của nến T.
  3. mlflow.set_tracking_uri đọc từ biến môi trường MLFLOW_TRACKING_URI,
     mặc định trỏ tới service name "mlflow" (đúng network nội bộ Docker),
     không hardcode "localhost" (sẽ fail khi chạy trong container Airflow).
  4. Guard NaN/Inf cho mọi metric trước khi log vào MLflow.
"""

import logging
import os
from datetime import datetime

import mlflow
import numpy as np
import pandas as pd
import vectorbt as vbt
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env — QUAN TRỌNG: giá trị MLFLOW_TRACKING_URI khác nhau tùy nơi chạy:
#   - Chạy local từ WSL2 (ngoài Docker, vd: `python backtest.py` để test nhanh)
#     -> .env phải có MLFLOW_TRACKING_URI=http://localhost:5000
#   - Chạy trong container Airflow (qua DAG)
#     -> đã set thẳng MLFLOW_TRACKING_URI=http://mlflow:5000 trong
#        docker-compose.yml (env của service airflow), KHÔNG cần .env cho
#        trường hợp này, và biến do docker-compose set sẽ có hiệu lực vì
#        load_dotenv() mặc định KHÔNG ghi đè biến môi trường đã tồn tại.
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# TODO (Ngày 13): thay bằng hàm thật lấy OHLCV từ PostgreSQL
# from data_pipeline.database import get_ohlcv_from_db


# ---------------------------------------------------------------------------
# Helper: guard NaN / Inf trước khi log vào MLflow
# ---------------------------------------------------------------------------
def _safe_float(value, default: float = 0.0) -> float:
    """
    Ép kiểu an toàn cho mọi metric trả về từ vectorbt.

    Lý do cần hàm này:
      - portfolio.stats() có thể trả NaN khi total_trades = 0
        (vd: Profit Factor không xác định khi chưa có lệnh nào).
      - NaN/Inf vẫn log được vào MLflow nhưng hiển thị vô nghĩa trên UI,
        và có thể làm hỏng biểu đồ so sánh giữa các run.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if np.isnan(f) or np.isinf(f):
        return default
    return f


# ---------------------------------------------------------------------------
# Core backtest — dùng chung cho cả mock và signal thật
# ---------------------------------------------------------------------------
def _run_vectorbt_backtest(
    price: pd.Series,
    signal: pd.Series,
    timeframe: str,
    sl_pct: float,
    tp_pct: float,
    entry_threshold: float,
    exit_threshold: float,
) -> dict:
    """
    Chạy VectorBT Portfolio từ 1 chuỗi signal strength (0.0 -> 1.0).

    QUAN TRỌNG — look-ahead bias:
    Signal tại thời điểm T thường được ConfluenceAnalyzer tính TỪ dữ liệu
    đã đóng nến tại T (rsi, macd, sr...). Nếu dùng signal đó để entry NGAY
    tại giá close của nến T, tức là bạn giả định "biết trước" giá đóng cửa
    trước khi nó thực sự xảy ra trong một hệ thống live.
    => Luôn shift(1): tín hiệu tính xong ở nến T chỉ được dùng để vào lệnh
       ở nến T+1 (giá open hoặc close của nến kế tiếp).
    """
    signal_shifted = signal.shift(1)

    entries = signal_shifted > entry_threshold
    exits = signal_shifted < exit_threshold

    # Hàng đầu tiên sau shift sẽ là NaN -> ép về False để vectorbt không lỗi
    entries = entries.fillna(False)
    exits = exits.fillna(False)

    portfolio = vbt.Portfolio.from_signals(
        close=price,
        entries=entries,
        exits=exits,
        sl_stop=sl_pct,
        tp_stop=tp_pct,
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
    }


# ---------------------------------------------------------------------------
# Backtest THẬT — nhận signal đã tính sẵn (Ngày 13 trở đi sẽ dùng hàm này)
# ---------------------------------------------------------------------------
def backtest_confluence_signal(
    df: pd.DataFrame,
    coin: str = "BTCUSDT",
    timeframe: str = "1h",
    sl_pct: float = 0.02,
    tp_pct: float = 0.04,
    strength_threshold: float = 0.65,
    exit_threshold: float = 0.30,
) -> dict:
    """
    Backtest chiến lược dựa trên cột 'strength' đã có sẵn trong df.

    Bắt buộc df phải có cột 'strength' (output từ ConfluenceAnalyzer,
    Ngày 11) — hàm này KHÔNG tự fake dữ liệu nữa. Nếu thiếu cột, raise
    lỗi rõ ràng thay vì âm thầm chạy random, để không ai nhầm kết quả
    random là kết quả thật.
    """
    if "strength" not in df.columns:
        raise ValueError(
            "df thiếu cột 'strength'. Đây là hàm backtest CHO SIGNAL THẬT — "
            "nếu bạn đang test luồng VectorBT/MLflow, dùng "
            "backtest_mock_signal() thay vì hàm này."
        )

    return _run_vectorbt_backtest(
        price=df["close"],
        signal=df["strength"],
        timeframe=timeframe,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        entry_threshold=strength_threshold,
        exit_threshold=exit_threshold,
    )


# ---------------------------------------------------------------------------
# Backtest MOCK — CHỈ để test luồng kỹ thuật, không phải kết quả thật
# ---------------------------------------------------------------------------
def backtest_mock_signal(
    df: pd.DataFrame,
    coin: str = "BTCUSDT",
    timeframe: str = "1h",
    sl_pct: float = 0.02,
    tp_pct: float = 0.04,
    strength_threshold: float = 0.65,
    exit_threshold: float = 0.30,
    seed: int = 42,
) -> dict:
    """
    Sinh signal 'strength' NGẪU NHIÊN để test luồng VectorBT + MLflow
    hoạt động đúng trước khi có ConfluenceAnalyzer thật (Ngày 11-13).

    ⚠️ CẢNH BÁO: mọi metric (win_rate, sharpe...) trả về từ hàm này là
    NHIỄU NGẪU NHIÊN, KHÔNG mang ý nghĩa chiến lược gì cả. Tuyệt đối
    không copy kết quả của hàm này vào docs/BACKTEST_BASELINE.md.
    Dùng seed cố định để kết quả reproducible khi debug pipeline.
    """
    logger.warning(
        "[MOCK] Đang chạy backtest với signal NGẪU NHIÊN (seed=%s). "
        "Kết quả CHỈ dùng để kiểm tra luồng kỹ thuật, không phải "
        "hiệu suất chiến lược thật.",
        seed,
    )
    rng = np.random.default_rng(seed)
    df = df.copy()
    df["strength"] = rng.uniform(0, 1, size=len(df))

    return _run_vectorbt_backtest(
        price=df["close"],
        signal=df["strength"],
        timeframe=timeframe,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        entry_threshold=strength_threshold,
        exit_threshold=exit_threshold,
    )


# ---------------------------------------------------------------------------
# MLflow experiment runner
# ---------------------------------------------------------------------------
def run_experiment(
    df: pd.DataFrame,
    params: dict,
    run_name: str = "baseline",
    use_mock: bool = False,
) -> dict:
    """
    Bọc hàm backtest lại và log kết quả lên MLflow.

    Args:
        use_mock: True => dùng backtest_mock_signal() (test luồng).
                  False => dùng backtest_confluence_signal() (signal thật,
                  df bắt buộc phải có cột 'strength').
    """
    # FIX: đọc tracking URI từ env var thay vì hardcode localhost.
    # Trong docker-compose, service MLflow tên là "mlflow" — khi script
    # này chạy bên trong container Airflow, "localhost" trỏ vào chính
    # container Airflow chứ không phải container MLflow => connection
    # refused. Set MLFLOW_TRACKING_URI=http://mlflow:5000 trong env
    # của service airflow ở docker-compose.yml.
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("Crypto_Agent_Backtesting")

    backtest_fn = backtest_mock_signal if use_mock else backtest_confluence_signal

    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(params)
        mlflow.log_param("is_mock_signal", use_mock)

        logger.info("Đang chạy backtest (mock=%s) với params: %s", use_mock, params)
        metrics = backtest_fn(df, **params)
        mlflow.log_metrics(metrics)

        logger.info(
            "Kết quả Backtest: Win Rate: %.2f%% | Sharpe: %.2f | MDD: %.2f%% | Trades: %d",
            metrics["win_rate"],
            metrics["sharpe_ratio"],
            metrics["max_drawdown"],
            metrics["total_trades"],
        )
        return metrics


# ---------------------------------------------------------------------------
# Entry point (manual run / test)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Tạo dữ liệu OHLCV giả lập để test luồng MLflow + VectorBT...")

    dates = pd.date_range(start=datetime(2023, 1, 1), periods=1000, freq="1h")
    rng = np.random.default_rng(0)
    mock_close = np.cumsum(rng.normal(0, 10, 1000)) + 30000
    df_mock = pd.DataFrame({"close": mock_close}, index=dates)

    baseline_params = {
        "coin": "BTCUSDT",
        "timeframe": "1h",
        "sl_pct": 0.02,
        "tp_pct": 0.04,
        "strength_threshold": 0.65,
    }

    # use_mock=True vì chưa có ConfluenceAnalyzer thật (đến Ngày 11-13).
    # Khi có signal thật, đổi use_mock=False và đảm bảo df có cột 'strength'.
    run_experiment(
        df_mock, baseline_params, run_name="Mock_Flow_Test_1H", use_mock=True
    )
