"""
scripts/manual/check_ohlcv_gaps.py

Kiểm lỗ GIỮA chuỗi OHLCV (nợ #14). Exit 1 nếu có lỗ trong khoảng kiểm.

Vì sao cần: sync_recent() quét lùi từ nến CUỐI. Nếu nến mới đã được ghi sau
một khoảng tắt (vd DAG bản cũ ghi 2026-10-05 → 10-07), lỗ nằm ở giữa và
watermark không nhìn thấy. Ngày 2026-10-08 phát hiện lỗ 47 ngày theo đúng
kiểu này. Chạy script này trước mọi backtest.

Chạy:
    PYTHONPATH=. python scripts/manual/check_ohlcv_gaps.py --since 2026-06-01
Lấp lỗ (trong container, có env Binance):
    docker compose exec airflow python -c "import asyncio; \\
      from data_pipeline.binance.ohlcv_pipeline import backfill_all; \\
      asyncio.run(backfill_all(days_back=N))"
"""

import argparse
import asyncio
import sys
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from rag.config import get_db_dsn

GAP_SQL = text(
    """
    WITH t AS (
        SELECT coin, timeframe, open_time,
               lead(open_time) OVER (
                   PARTITION BY coin, timeframe ORDER BY open_time
               ) AS nx
        FROM ohlcv
        WHERE open_time >= :since
    )
    SELECT coin, timeframe, open_time, nx
    FROM t
    WHERE nx - open_time > CASE timeframe
        WHEN '15m' THEN interval '15 minutes'
        WHEN '1h' THEN interval '1 hour'
        WHEN '4h' THEN interval '4 hours'
        WHEN '1d' THEN interval '1 day'
    END
    ORDER BY coin, timeframe, open_time
    """
)


async def find_gaps(since: datetime) -> list:
    engine = create_async_engine(
        get_db_dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.connect() as conn:
            return (await conn.execute(GAP_SQL, {"since": since})).all()
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True, type=datetime.fromisoformat)
    args = p.parse_args()

    gaps = asyncio.run(find_gaps(args.since))
    for coin, tf, start, nxt in gaps:
        print(f"LỖ {coin} {tf}: {start} → {nxt} ({nxt - start})")
    if gaps:
        print(f"FAIL: {len(gaps)} lỗ từ {args.since}", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: không có lỗ OHLCV từ {args.since}")


if __name__ == "__main__":
    main()
