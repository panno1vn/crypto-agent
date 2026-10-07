# dags/dag_binance_ohlcv_sync.py
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def run_binance_sync():
    """
    Gọi pipeline OHLCV thật — trước đây chỉ print(), chưa từng lấy dữ
    liệu gì (bug phát hiện Ngày 20, xem docs/GHI_CHU_NGAY20.md).

    (2026-10-08, nợ #14) Gọi sync_recent(): mỗi cặp coin/timeframe quét lùi
    từ nến cuối đã có trong DB, tối thiểu 2 ngày. Bản cũ quét cố định
    days_back=2 nên khi stack tắt 2026-08-19 → 2026-10-07, lỗ 47 ngày không
    bao giờ được lấp. sync_recent() raise nếu có cặp lỗi → Airflow FAILED
    + retry, thay vì báo success khi không lấy được gì.

    Import bên trong hàm (không để đầu file) — tránh Airflow scheduler
    phải load toàn bộ dependency nặng (sqlalchemy, binance client...)
    mỗi lần parse lại DAG file, làm chậm cả UI Airflow không cần thiết.
    """
    import asyncio

    from data_pipeline.binance.ohlcv_pipeline import sync_recent

    print("Bắt đầu lấy dữ liệu nến (OHLCV) từ Binance...")
    asyncio.run(sync_recent(min_days_back=2))
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
