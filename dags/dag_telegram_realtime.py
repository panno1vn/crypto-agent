# dags/dag_telegram_realtime.py
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def run_telegram_sync():
    """
    Gọi scraper thật — trước đây chỉ print(), là nguyên nhân gốc của
    khoảng trống 47 ngày vừa lấp (xem docs/GHI_CHU_NGAY20.md).

    ⚠️ ĐỌC TRƯỚC KHI BẬT DAG NÀY:
    realtime_listener.py chạy nền riêng (nghe real-time qua Telethon)
    và DAG này KHÔNG được dùng chung 1 session Telethon — 2 client cùng
    lúc mở 1 file session SQLite dễ xung đột ("database is locked").
    Dùng tên session RIÊNG ("crypto_session_dag_catchup", khác
    "crypto_session" mà listener đang dùng).

    DAG này là LƯỚI AN TOÀN (catch-up), KHÔNG thay thế realtime_listener.py
    — nếu listener chết âm thầm (đúng sự cố 22/06→08/08, không ai biết
    suốt 47 ngày vì chạy tay, không có gì giám sát), DAG chạy mỗi 15
    phút sẽ tự bắt kịp phần bị bỏ lỡ thay vì im lặng mất dữ liệu.

    Lấy điểm resume trực tiếp từ DB (MAX(id) mỗi channel) — KHÔNG dùng
    checkpoint file (checkpoints/telegram/*.json), vì file đó dùng cho
    mục đích khác (đánh dấu đã quét NGƯỢC tới đâu ở lần backfill sâu
    ban đầu) — dùng sai mục đích từng gây quét dư/quét thiếu.
    """
    import asyncio
    import os

    import asyncpg
    from dotenv import load_dotenv
    from telethon import TelegramClient

    from data_pipeline.telegram.historical_scraper import (
        DatabaseWriter,
        scrape_channel_history,
    )

    load_dotenv()

    API_ID = int(os.environ["TELEGRAM_API_ID"])
    API_HASH = os.environ["TELEGRAM_API_HASH"]
    DB_DSN = (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )

    async def _sync():
        conn = await asyncpg.connect(DB_DSN)
        try:
            channel_rows = await conn.fetch(
                "SELECT DISTINCT channel_name FROM telegram_messages"
            )
            channels = [r["channel_name"] for r in channel_rows]
            max_id_rows = await conn.fetch(
                "SELECT channel_name, MAX(id) AS max_id "
                "FROM telegram_messages GROUP BY channel_name"
            )
            max_ids = {r["channel_name"]: r["max_id"] for r in max_id_rows}
        finally:
            await conn.close()

        client = TelegramClient("crypto_session_dag_catchup", API_ID, API_HASH)
        failed: list[str] = []
        db_writer = DatabaseWriter(dsn=DB_DSN, batch_size=100)

        async with client:
            await db_writer.connect()
            try:
                for channel in channels:
                    try:
                        count = 0
                        async for msg in scrape_channel_history(
                            client=client,
                            channel=channel,
                            limit=500,  # nhỏ — chạy mỗi 15p, không cần
                            # quét sâu như backfill 1 lần
                            offset_date=None,
                            min_message_id=max_ids.get(channel),
                            # Cũ nhất trước — xem docstring scrape_channel_history
                            # (nợ #14): newest-first + limit làm mất tin ở giữa.
                            oldest_first=True,
                        ):
                            await db_writer.write(msg)
                            count += 1
                        await db_writer.flush_remaining()
                        if count:
                            print(f"[TELEGRAM_SYNC] {channel}: {count} tin mới")
                    except Exception as e:
                        # 1 channel lỗi không chặn channel còn lại — cùng
                        # nguyên tắc FIX 2 đã áp dụng ở ohlcv_pipeline.py.
                        print(f"[TELEGRAM_SYNC] Lỗi ở {channel}: {e}")
                        failed.append(channel)
            finally:
                await db_writer.close()
        # (2026-10-08) Fail loudly: trước đây lỗi chỉ được print, DAG vẫn
        # success kể cả khi mọi kênh lỗi.
        if failed:
            raise RuntimeError(f"[TELEGRAM_SYNC] {len(failed)} kênh lỗi: {failed}")

    print("Bắt đầu đồng bộ dữ liệu Telegram (catch-up)...")
    asyncio.run(_sync())
    print("Đồng bộ hoàn tất!")


default_args = {
    "owner": "pan",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),  # Nếu lỗi, đợi 2 phút chạy lại
}

with DAG(
    dag_id="telegram_realtime_sync",
    default_args=default_args,
    description="Đồng bộ tin nhắn Telegram mỗi 15 phút (lưới an toàn cho realtime_listener.py)",
    schedule_interval="*/15 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["crypto", "telegram"],
) as dag:
    task_sync_telegram = PythonOperator(
        task_id="sync_messages",
        python_callable=run_telegram_sync,
    )

    task_sync_telegram
