# scripts/test_backtest_data.py (file tạm, không cần present_files)
import asyncio
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from technical_analysis.backtest import backtest_confluence_signal, run_experiment
from technical_analysis.backtest_data import build_confluence_signal_series

DB_DSN = "postgresql+asyncpg://admin:admin123@localhost:5432/crypto_agent"  # dùng đúng DSN dev thật của bạn

import os  # noqa: E402

run_suffix = os.environ.get("RUN_TAG", "1")


async def main():
    engine = create_async_engine(DB_DSN)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    end = datetime(2026, 7, 12, 0, 0, 0)
    start = end - timedelta(days=90)

    async with session_factory() as session:
        df = await build_confluence_signal_series(
            session, "BTCUSDT", start, end, recompute_every_candles=4
        )
        print(df.describe())
        print(df[df["strength"] > 0.65].shape[0], "nến vượt ngưỡng entry")

        metrics = backtest_confluence_signal(df, coin="BTCUSDT")
        print(metrics)

        run_experiment(
            df,
            params={
                "coin": "BTCUSDT",
                "timeframe": "1h",
                "sl_pct": 0.02,
                "tp_pct": 0.04,
                "strength_threshold": 0.65,
                "exit_threshold": 0.30,
            },
            run_name=f"Baseline_90days_Fixed_Run{run_suffix}",
            use_mock=False,  # QUAN TRỌNG: dùng backtest_confluence_signal(), không phải mock
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
