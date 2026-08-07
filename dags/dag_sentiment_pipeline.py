"""
dags/dag_sentiment_pipeline.py

Ngày 19 — DAG xử lý sentiment cho message chưa được phân tích.

QUYẾT ĐỊNH KIẾN TRÚC (đã thống nhất khi audit pseudocode gốc):
Chạy theo LỊCH ĐỘC LẬP (schedule_interval cố định), KHÔNG trigger sau
dag_telegram_realtime — vì dag_telegram_realtime chạy 1 process lắng
nghe vĩnh viễn (client.run_until_disconnected(), xem
data_pipeline/telegram/realtime_listener.py), không bao giờ "hoàn
thành" theo nghĩa Airflow task hiểu, nên TriggerDagRunOperator/
ExternalTaskSensor sẽ không bao giờ được kích hoạt nếu chờ nó.

DAG này tự query is_processed=FALSE mỗi lần chạy — cùng triết lý với
periodic_flush() trong realtime_listener.py: đừng chờ tín hiệu, cứ
định kỳ tự kiểm tra.

Pattern giữ nguyên theo dag_calculate_indicators.py (Ngày 13) —
sessionmaker(engine, class_=AsyncSession), get_logger, asyncio.run()
bọc trong try/except raise lại để Airflow đánh dấu task FAILED đúng.
"""

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

# 10 phút — đủ nhanh để sentiment không trễ quá lâu so với lúc message
# vào DB, không dày tới mức tranh chấp CPU với DAG khác (indicators
# chạy mỗi giờ). Điều chỉnh nếu cần — không có ràng buộc cứng nào.
SCHEDULE_INTERVAL = "*/10 * * * *"


def run_sentiment_pipeline():
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from data_pipeline.logger import get_logger
    from nlp.sentiment_pipeline import process_unprocessed_messages

    logger = get_logger(__name__)

    DB_DSN = (
        f"postgresql+asyncpg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'postgres')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )

    async def _run():
        engine = create_async_engine(DB_DSN)
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            # NOTE: MultilingualSentimentAnalyzer(lazy_load=False) load
            # LẠI PhoBERT+FinBERT+XLM-R từ đĩa MỖI LẦN task này chạy
            # (mỗi 10 phút) — không có cơ chế giữ model "ấm" giữa các
            # lần chạy trong LocalExecutor hiện tại. Chấp nhận được ở
            # quy mô hiện tại, nhưng nếu load time trở thành vấn đề rõ
            # rệt (quan sát qua Airflow task duration), cân nhắc tách
            # thành 1 service inference thường trực (FastAPI nội bộ)
            # thay vì load lại trong mỗi PythonOperator invocation.
            count = await process_unprocessed_messages(session_factory)
            logger.info(f"[DAG] dag_sentiment_pipeline xử lý {count} message.")
        finally:
            await engine.dispose()

    try:
        asyncio.run(_run())
    except Exception:
        logger.error("[DAG] dag_sentiment_pipeline thất bại.", exc_info=True)
        raise


with DAG(
    "dag_sentiment_pipeline",
    schedule_interval=SCHEDULE_INTERVAL,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,  # tránh 2 lần chạy chồng nhau nếu 1 lần > 10 phút
    tags=["nlp", "sentiment"],
) as sentiment_dag:
    sentiment_task = PythonOperator(
        task_id="process_sentiment",
        python_callable=run_sentiment_pipeline,
    )
