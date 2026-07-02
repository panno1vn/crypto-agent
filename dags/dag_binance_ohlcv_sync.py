from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def run_binance_sync():
    print("Bắt đầu lấy dữ liệu nến (OHLCV) từ Binance...")
    # Ví dụ import hàm từ ngày 6:
    # from data_pipeline.binance.ohlcv_pipeline import fetch_ohlcv_for_all_coins
    # fetch_ohlcv_for_all_coins()
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
