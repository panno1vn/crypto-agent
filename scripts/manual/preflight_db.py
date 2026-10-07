"""
scripts/manual/preflight_db.py

Kiểm tra `localhost:5432` (theo .env) đúng là container Postgres của dự án,
trước khi chạy script/test chạm DB.

Bug thật 2026-10-08: một PostgreSQL 18 cài native trong WSL (service
`postgresql`, tự bật lúc khởi động) chiếm 127.0.0.1:5432. Container
`crypto_postgres` (Postgres 15) vẫn "healthy" nhưng Docker Desktop không
chuyển tiếp được cổng sang WSL. Mọi kết nối từ WSL rơi vào DB native và báo
`password authentication failed` — trông giống sai mật khẩu, thực chất là
sai máy chủ.

Chạy:
    PYTHONPATH=. python scripts/manual/preflight_db.py
"""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from rag.config import get_db_dsn

EXPECTED_MAJOR = "15"  # image postgres:15-alpine trong docker-compose.yml

HINT = (
    "Kiểm tra: `ss -ltnp | grep :5432` và `systemctl is-active postgresql`. "
    "Nếu có Postgres native: `sudo systemctl stop postgresql` (thêm `disable` "
    "để khỏi tự bật), rồi `docker compose restart postgres` để Docker đăng ký "
    "lại cổng."
)


async def _check() -> str:
    engine = create_async_engine(
        get_db_dsn().replace("postgresql://", "postgresql+asyncpg://", 1)
    )
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
            tables = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name IN ('ohlcv', 'telegram_messages')"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    if not version.startswith(EXPECTED_MAJOR + "."):
        raise RuntimeError(
            f"Postgres version {version}, mong đợi {EXPECTED_MAJOR}.x — "
            f"đang nối nhầm máy chủ. {HINT}"
        )
    if tables != 2:
        raise RuntimeError(f"Thiếu bảng ohlcv/telegram_messages — sai DB? {HINT}")
    return version


def main() -> None:
    try:
        version = asyncio.run(_check())
    except Exception as exc:
        print(f"PREFLIGHT FAIL: {exc}\n{HINT}", file=sys.stderr)
        raise SystemExit(1)
    print(f"PREFLIGHT OK: Postgres {version}, đủ bảng dự án")


if __name__ == "__main__":
    main()
