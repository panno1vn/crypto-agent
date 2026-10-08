"""
scripts/manual/backfill_telegram_collisions.py

Nợ #15 (2026-10-08): lấy lại tin bị bỏ vì trùng msg_id với kênh khác.

Trước migration e7c2a9d41f05, khóa chính telegram_messages là (id), insert
ON CONFLICT (id) DO NOTHING → tin kênh B trùng msg_id với kênh A bị bỏ im
lặng. Dấu vết: id trong dải [min, max] của kênh B mà B không có, nhưng một
kênh KHÁC đang có (cùng định nghĩa "lo_bi_kenh_khac_chiem" của
scripts/manual/check_telegram_id_collisions.py). Đó là CẬN TRÊN: một số id
là lỗ tự nhiên (tin đã xóa, tin không text) — Telegram trả None hoặc tin
không qua validator, không có gì được ghi.

Các id này rải rác, không thành khoảng liền, nên lấy theo DANH SÁCH id
(scrape_message_ids), không dùng min_id/max_id.

Bỏ qua kênh trong EXCLUDED_CHANNELS (kênh chết, vd RIC_Capital_Channel).

SAU KHI CHẠY: chạy scripts/manual/embed_missing.py (tin mới nằm dưới
watermark Chroma). Pause DAG telegram_realtime_sync nếu dùng chung session.

Chạy (từ gốc repo):
    PYTHONPATH=. python scripts/manual/backfill_telegram_collisions.py --dry-run
    PYTHONPATH=. python scripts/manual/backfill_telegram_collisions.py \\
        --session crypto_session_dag_catchup [--channel nghiencryptochannel]
"""

import argparse
import asyncio
import os
from collections import defaultdict
from pathlib import Path

import asyncpg
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

from data_pipeline.logger import get_logger  # noqa: E402
from data_pipeline.telegram.channel_config import filter_excluded  # noqa: E402
from data_pipeline.telegram.historical_scraper import (  # noqa: E402
    DatabaseWriter,
    scrape_message_ids,
)
from rag.config import get_db_dsn  # noqa: E402

logger = get_logger(__name__)


def collision_holes(keys: set[tuple[str, int]]) -> dict[str, list[int]]:
    """
    {kênh: [id trong dải của kênh, kênh không có, kênh khác đang có]}.
    """
    by_ch: dict[str, set[int]] = defaultdict(set)
    for ch, i in keys:
        by_ch[ch].add(i)
    all_ids = {i for _, i in keys}
    out: dict[str, list[int]] = {}
    for ch, own in by_ch.items():
        lo, hi = min(own), max(own)
        holes = sorted(i for i in all_ids if lo < i < hi and i not in own)
        if holes:
            out[ch] = holes
    return out


async def _load_keys(dsn: str) -> set[tuple[str, int]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT channel_name, id FROM telegram_messages")
    finally:
        await conn.close()
    return {(r["channel_name"], r["id"]) for r in rows}


async def run(session: "str | None", only_channel: "str | None", dry_run: bool):
    dsn = get_db_dsn()
    holes = collision_holes(await _load_keys(dsn))
    channels = filter_excluded(sorted(holes))
    if only_channel:
        if only_channel not in holes:
            raise ValueError(f"Kênh {only_channel!r} không có id va chạm nào")
        channels = filter_excluded([only_channel])
    for ch in channels:
        logger.info(f"[COLLISION_BACKFILL] {ch}: {len(holes[ch])} id cần thử")
    if dry_run:
        return

    if session is None:
        raise ValueError("Cần --session khi không --dry-run")
    # Telethon không thấy file session sẽ tạo session RỖNG rồi treo chờ OTP.
    session_file = Path(f"{session}.session")
    if not session_file.is_file():
        raise FileNotFoundError(
            f"Không thấy {session_file.resolve()} — chạy từ gốc repo, kiểm tên session"
        )

    client = TelegramClient(
        session, int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"]
    )
    db_writer = DatabaseWriter(dsn=dsn, batch_size=100)
    async with client:
        if not await client.is_user_authorized():
            raise RuntimeError(f"Session {session} chưa đăng nhập — không chạy tiếp")
        await db_writer.connect()
        try:
            for ch in channels:
                fetched = 0
                async for msg in scrape_message_ids(client, ch, holes[ch]):
                    await db_writer.write(msg)
                    fetched += 1
                await db_writer.flush_remaining()
                logger.info(
                    f"[COLLISION_BACKFILL] {ch}: {len(holes[ch])} id thử, "
                    f"{fetched} tin hợp lệ đã ghi (ON CONFLICT DO NOTHING)"
                )
        finally:
            await db_writer.flush_remaining()
            await db_writer.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--session")
    p.add_argument("--channel")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.session, a.channel, a.dry_run))


if __name__ == "__main__":
    main()
