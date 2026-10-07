"""
dags/dag_embed_messages.py

Ngày 23 — DAG embed tin nhắn mới vào ChromaDB.

QUYẾT ĐỊNH KIẾN TRÚC:
Watermark (id cuối cùng đã embed, TÍNH THEO TỪNG KÊNH từ 2026-08-13) được
ĐỌC NGƯỢC từ chính Chroma mỗi lần task chạy
(`rag.ingestion.get_last_embedded_id_for_channel`, gọi bên trong
`run_ingestion()`), KHÔNG lưu vào Airflow Variable
hay bảng riêng. Cùng triết lý với `enrich_fingerprint`/`hnsw:space` ở
rag/vector_store.py: nguồn sự thật là đích đến, không phải state phụ có
thể lệch pha nếu task bị kill giữa chừng. Hệ quả: task fail/timeout thì
lần chạy kế tiếp tự resume đúng chỗ, không cần retry logic đặc biệt.

Vì `upsert()` (không phải `add()`) là idempotent theo id, chạy đè lên
range id đã embed rồi (vd do watermark tính chưa kịp cập nhật) là an
toàn — không tạo bản ghi trùng, không sai lệch.

Pattern giữ nguyên theo dag_sentiment_pipeline.py (Ngày 19):
  - Import nặng (asyncpg, torch/sentence-transformers qua rag.*) nằm
    TRONG hàm callable, không phải module level — tránh
    dagbag_import_timeout khi scheduler parse DAG mỗi ~30s.
  - catchup=False, max_active_runs=1.
  - try/except: log kèm exc_info=True rồi raise lại, để Airflow đánh
    dấu task FAILED đúng thay vì âm thầm "thành công" (đúng bài học
    Ngày 20: DAG xanh không đồng nghĩa đã làm việc thật).

TRƯỚC KHI UNPAUSE DAG NÀY:
  1. `docker compose build airflow` sau khi thêm chromadb-client +
     sentence-transformers vào requirements-airflow.txt.
  2. Pre-warm model vào huggingface_cache để lần chạy đầu không phải
     tải ~1.1GB giữa lúc task đang chạy:
       docker compose exec airflow python -c \
         "from rag.embedder import get_embedder; get_embedder()"
  3. Chạy tay 1 lần qua Airflow UI (Trigger DAG), verify
     collection.count() tăng đúng số tin mới, rồi mới bật schedule.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# 30 phút theo roadmap gốc. Không dày hơn dag_sentiment_pipeline (10
# phút) vì embed (mpnet, ~50ms/tin) chậm hơn PhoBERT (~14.5ms/tin) khá
# nhiều — 2 model transformer cùng lúc trên CPU WSL2 là rủi ro RAM thật,
# chưa đo, cần theo dõi `free -h` sau khi bật DAG này vài ngày.
SCHEDULE_INTERVAL = "*/30 * * * *"

# Nếu 1 lần chạy vượt mốc này, Airflow đánh FAILED và dừng — an toàn vì
# watermark đọc ngược từ Chroma, lần chạy kế tiếp (30 phút sau) tự resume
# đúng chỗ, không mất tiến độ đã upsert.
EXECUTION_TIMEOUT = timedelta(minutes=25)


def run_embed_messages():
    import asyncio

    from data_pipeline.logger import get_logger
    from rag.ingestion import run_ingestion

    logger = get_logger(__name__)

    async def _run():
        # (2026-10-08) Watermark tính THEO KÊNH bên trong run_ingestion()
        # (sửa bug 2026-08-13). Bản cũ gọi get_last_embedded_id() toàn cục
        # và run_ingestion(after_id=...) — cả hai đã bị xóa khỏi
        # rag/ingestion.py nhưng DAG không được sửa theo, nên DAG ImportError
        # mọi lần chạy từ đó. tests/unit/test_dag_imports.py chặn lặp lại.
        # run_ingestion() tự get_collection() trước khi load model, vẫn fail
        # nhanh nếu Chroma sai space/fingerprint.
        logger.info("[DAG] dag_embed_messages bắt đầu")
        stats = await run_ingestion()
        logger.info(f"[DAG] dag_embed_messages xong: {stats}")

    try:
        asyncio.run(_run())
    except Exception:
        logger.error("[DAG] dag_embed_messages thất bại.", exc_info=True)
        raise


with DAG(
    "dag_embed_messages",
    schedule_interval=SCHEDULE_INTERVAL,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["rag", "embedding", "chroma"],
) as embed_dag:
    embed_task = PythonOperator(
        task_id="embed_messages",
        python_callable=run_embed_messages,
        execution_timeout=EXECUTION_TIMEOUT,
    )
