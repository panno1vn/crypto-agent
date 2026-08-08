# dags/dag_binance_ohlcv_sync.py
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def run_binance_sync():
    """
    Gọi backfill_all() thật — trước đây chỉ print(), chưa từng lấy dữ
    liệu gì (bug phát hiện Ngày 20, xem docs/GHI_CHU_NGAY20.md).

    days_back=2: DAG chạy mỗi giờ, chỉ cần quét lùi đủ bắt kịp nếu lỡ
    1-2 lần chạy (mạng lỗi, container restart...). Không dùng 90 (số
    dùng cho full backfill 1 lần) vì mỗi giờ chạy lại sẽ tốn API call +
    thời gian không cần thiết — bulk_insert_ohlcv() có ON CONFLICT DO
    NOTHING nên quét dư không sai dữ liệu, chỉ lãng phí.

    Import bên trong hàm (không để đầu file) — tránh Airflow scheduler
    phải load toàn bộ dependency nặng (sqlalchemy, binance client...)
    mỗi lần parse lại DAG file, làm chậm cả UI Airflow không cần thiết.
    """
    import asyncio

    from data_pipeline.binance.ohlcv_pipeline import backfill_all

    print("Bắt đầu lấy dữ liệu nến (OHLCV) từ Binance...")
    asyncio.run(backfill_all(days_back=2))
    print("Lấy dữ liệu hoàn tất!")


default_args = {
    "owner": "pan",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 2,  # Tăng số lần thử lại nếu API Binance bị lỗi mạng
    "retry_delay": timedelta(minutes=1),
}

with DAG(
    dag_id="binance_ohlcv_hourly_sync",
    default_args=default_args,
    description="Lấy nến OHLCV từ Binance mỗi giờ",
    schedule_interval="0 * * * *",  # Chạy tròn mỗi giờ (VD: 1:00, 2:00)
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["crypto", "binance", "ohlcv"],
) as dag:
    task_sync_binance = PythonOperator(
        task_id="sync_ohlcv",
        python_callable=run_binance_sync,
    )

    task_sync_binance
