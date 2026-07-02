from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


# Đây là hàm Python sẽ được thực thi.
# Tạm thời anh để print, em sẽ import hàm thật từ thư mục data_pipeline của em vào đây sau.
def run_telegram_sync():
    print("Bắt đầu đồng bộ dữ liệu Telegram...")
    # Ví dụ:
    # from data_pipeline.telegram.historical_scraper import run_sync
    # run_sync()
    print("Đồng bộ hoàn tất!")


# default_args định nghĩa các cấu hình cơ bản cho mọi Task trong DAG
default_args = {
    "owner": "pan",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),  # Nếu lỗi, đợi 2 phút chạy lại
}

# Khởi tạo DAG
with DAG(
    dag_id="telegram_realtime_sync",
    default_args=default_args,
    description="Đồng bộ tin nhắn Telegram mỗi 15 phút",
    schedule_interval="*/15 * * * *",  # Chạy mỗi 15 phút
    start_date=datetime(2024, 1, 1),
    catchup=False,  # Không chạy bù các ngày trong quá khứ
    tags=["crypto", "telegram"],
) as dag:
    # Khởi tạo Task từ khuôn PythonOperator
    task_sync_telegram = PythonOperator(
        task_id="sync_messages",
        python_callable=run_telegram_sync,
    )

    # Vì hiện tại chỉ có 1 task nên ta gọi nó ra,
    # nếu có task2 thì sẽ là: task_sync_telegram >> task2
    task_sync_telegram
