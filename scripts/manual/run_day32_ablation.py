"""
scripts/manual/run_day32_ablation.py

Ngày 32 — Chạy backtest ablation 3 nhánh trên DB thật, log vào MLflow.
Logic nằm ở agent/ablation.py (có unit test); script này chỉ nối DB,
cache và MLflow.

Chạy ở LOCAL (cần Postgres có OHLCV + telegram_messages đã có sentiment,
và MLflow):

    MLFLOW_TRACKING_URI=http://localhost:5000 PYTHONPATH=. \\
    python scripts/manual/run_day32_ablation.py \\
        --start 2026-05-01 --end 2026-08-15 \\
        --coins BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT

Cố định endpoint: --start/--end là BẮT BUỘC, không có mặc định kiểu "90 ngày
tới bây giờ" (chạy hôm sau ra cửa sổ khác, không so sánh được). Chọn cửa
sổ nằm TRONG khoảng có sentiment, xem query ở docs/GHI_CHU_NGAY32.md.

Cache: chuỗi thành phần mỗi coin lưu ở --cache-dir (CSV). Chạy lại với
ngưỡng/quantile khác không cần gọi lại DB. Đổi start/end/recompute thì
tên file cache đổi theo, không dùng nhầm cache cũ.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.ablation import (
    build_component_series,
    default_branches,
    describe_distribution,
    run_branch,
    sentiment_coverage,
    validate_components,
)
from agent.signal_aggregator import load_signal_weights
from data_pipeline.logger import get_logger
from rag.config import (
    NEWS_CONFIRMATION_LOWER_THRESHOLD,
    NEWS_CONFIRMATION_MIN_MESSAGE_COUNT,
    NEWS_CONFIRMATION_UPPER_THRESHOLD,
    NEWS_CONFIRMATION_WINDOW_HOURS,
    get_db_dsn,
)

logger = get_logger(__name__)

EXPERIMENT = "Crypto_Agent_Ablation_Day32"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Thiếu env {name} (vd http://localhost:5000 khi chạy từ WSL)"
        )
    return value


def _cache_path(cache_dir: Path, coin: str, args) -> Path:
    key = (
        f"{coin}_{args.start:%Y%m%d}_{args.end:%Y%m%d}"
        f"_r{args.recompute_every}_w{args.sentiment_window_hours:g}"
    )
    return cache_dir / f"components_{key}.csv"


async def _load_components(coin: str, args) -> pd.DataFrame:
    path = _cache_path(Path(args.cache_dir), coin, args)
    if path.exists() and not args.refresh:
        logger.info(f"[ABLATION] Dùng cache {path}")
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        validate_components(df)
        return df

    engine = create_async_engine(
        get_db_dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            df = await build_component_series(
                session,
                coin,
                args.start,
                args.end,
                sentiment_window_hours=args.sentiment_window_hours,
                recompute_every_candles=args.recompute_every,
            )
    finally:
        await engine.dispose()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, type=datetime.fromisoformat)
    p.add_argument("--end", required=True, type=datetime.fromisoformat)
    p.add_argument(
        "--coins",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
    )
    p.add_argument("--recompute-every", type=int, default=4)
    p.add_argument(
        "--sentiment-window-hours", type=float, default=NEWS_CONFIRMATION_WINDOW_HOURS
    )
    p.add_argument("--calib-frac", type=float, default=0.5)
    p.add_argument("--entry-quantile", type=float, default=0.75)
    p.add_argument("--exit-quantile", type=float, default=0.25)
    p.add_argument("--sl-pct", type=float, default=0.02)
    p.add_argument("--tp-pct", type=float, default=0.04)
    p.add_argument("--cache-dir", default="data/ablation_cache")
    p.add_argument("--refresh", action="store_true", help="bỏ qua cache, gọi lại DB")
    args = p.parse_args()

    import mlflow

    mlflow.set_tracking_uri(_require_env("MLFLOW_TRACKING_URI"))
    mlflow.set_experiment(EXPERIMENT)

    components = {c: asyncio.run(_load_components(c, args)) for c in args.coins}
    branches = default_branches(load_signal_weights())

    common_params = {
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "coins": ",".join(args.coins),
        "recompute_every": args.recompute_every,
        "sentiment_window_hours": args.sentiment_window_hours,
        "calib_frac": args.calib_frac,
        "entry_quantile": args.entry_quantile,
        "exit_quantile": args.exit_quantile,
        "sl_pct": args.sl_pct,
        "tp_pct": args.tp_pct,
        "news_upper_threshold": NEWS_CONFIRMATION_UPPER_THRESHOLD,
        "news_lower_threshold": NEWS_CONFIRMATION_LOWER_THRESHOLD,
        "news_min_message_count": NEWS_CONFIRMATION_MIN_MESSAGE_COUNT,
    }

    summary_rows = []
    for branch in branches:
        with mlflow.start_run(run_name=branch.name):
            mlflow.log_params(common_params)
            mlflow.log_params(
                {
                    "branch": branch.name,
                    "w_technical": branch.weights.technical,
                    "w_sentiment": branch.weights.sentiment,
                    "w_news": branch.weights.news,
                }
            )
            total_trades = 0
            returns = []
            for coin, df in components.items():
                cov = sentiment_coverage(df, NEWS_CONFIRMATION_MIN_MESSAGE_COUNT)
                dist = describe_distribution(
                    df["strength"].where(df["direction"] == "long", 0.0)
                )
                res = run_branch(
                    df,
                    branch,
                    upper_threshold=NEWS_CONFIRMATION_UPPER_THRESHOLD,
                    lower_threshold=NEWS_CONFIRMATION_LOWER_THRESHOLD,
                    min_message_count=NEWS_CONFIRMATION_MIN_MESSAGE_COUNT,
                    calib_frac=args.calib_frac,
                    entry_quantile=args.entry_quantile,
                    exit_quantile=args.exit_quantile,
                    sl_pct=args.sl_pct,
                    tp_pct=args.tp_pct,
                )
                numeric = {
                    f"{coin}_{k}": v
                    for k, v in res.items()
                    if isinstance(v, (int, float))
                }
                numeric[f"{coin}_sentiment_coverage"] = cov
                numeric.update({f"{coin}_strength_{k}": v for k, v in dist.items()})
                mlflow.log_metrics(numeric)
                total_trades += res["total_trades"]
                returns.append(res["total_return"])
                summary_rows.append(
                    {
                        "branch": branch.name,
                        "coin": coin,
                        "trades": res["total_trades"],
                        "return_%": round(res["total_return"], 3),
                        "win_%": round(res["win_rate"], 2),
                        "sharpe": round(res["sharpe_ratio"], 3),
                        "entry_th": round(res["entry_threshold"], 4),
                        "coverage": round(cov, 3),
                    }
                )
            mlflow.log_metrics(
                {
                    "total_trades_all": total_trades,
                    "mean_total_return": sum(returns) / len(returns),
                }
            )
            run_id = mlflow.active_run().info.run_id
            print(f"[{branch.name}] run_id={run_id} trades={total_trades}")

    print(pd.DataFrame(summary_rows).to_string(index=False))


if __name__ == "__main__":
    main()
