"""
scripts/manual/embed_missing.py

Embed mọi tin có trong Postgres mà chưa có trong Chroma (so theo
"{kênh}:{msg_id}"). Chạy SAU scripts/manual/backfill_telegram_gap.py: tin
backfill nằm dưới watermark Chroma nên dag_embed_messages không embed chúng.
Chạy lại an toàn (upsert, chỉ lấy phần còn thiếu).

Chạy (từ gốc repo):
    PYTHONPATH=. python scripts/manual/embed_missing.py
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from rag.ingestion import run_embed_missing  # noqa: E402

if __name__ == "__main__":
    print(asyncio.run(run_embed_missing()))
