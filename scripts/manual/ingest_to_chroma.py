#!/usr/bin/env python3
"""
scripts/manual/ingest_to_chroma.py

Ngày 22 — Chạy tay pipeline embedding: PostgreSQL → ChromaDB.

Cách dùng:

    # Smoke test 20 tin đầu tiên, rồi thử query luôn
    python scripts/manual/ingest_to_chroma.py --limit 20 --verify

    # Backfill toàn bộ (Ngày 23)
    python scripts/manual/ingest_to_chroma.py

    # Xóa sạch và làm lại từ đầu (khi đổi enricher hoặc đổi model)
    python scripts/manual/ingest_to_chroma.py --reset

    # Tiếp tục chỗ đã dừng: chạy lại không tham số. Watermark tính theo
    # từng kênh từ chính Chroma (rag/ingestion.py). --after-id đã bỏ
    # 2026-10-08: run_ingestion() không còn tham số after_id từ 2026-08-13.

Lưu ý: `load_dotenv()` phải chạy TRƯỚC khi import rag.* vì rag/config.py
đọc os.environ ngay lúc import.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# Cho phép chạy trực tiếp từ thư mục gốc project
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

load_dotenv()

from data_pipeline.logger import get_logger  # noqa: E402
from rag.config import CHROMA_COLLECTION  # noqa: E402
from rag.ingestion import run_ingestion  # noqa: E402
from rag.vector_store import get_collection, reset_collection  # noqa: E402

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed tin nhắn Telegram từ PostgreSQL vào ChromaDB"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Số tin tối đa xử lý. Bỏ trống = chạy hết.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="XÓA collection rồi tạo lại trước khi chạy. Không hoàn tác được.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Chạy một truy vấn mẫu sau khi ingest xong.",
    )
    return parser.parse_args()


def verify_query(query_text: str = "BTC tăng giá mạnh") -> None:
    """
    Truy vấn thử để xác nhận toàn bộ chuỗi hoạt động.

    Bắt buộc dùng `query_embeddings=`, KHÔNG dùng `query_texts=`.
    Collection được tạo với `embedding_function=None`; truyền text thô
    vào sẽ khiến Chroma dùng model mặc định 384 chiều, lệch hẳn với
    768 chiều của SBERT.
    """
    from rag.embedder import get_embedder

    collection = get_collection()
    embedding = get_embedder().encode([query_text])[0].tolist()

    results = collection.query(
        query_embeddings=[embedding],
        n_results=5,
    )

    print(f"\n=== Query: {query_text!r} ===")
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]

    if not docs:
        print("  (không có kết quả — collection rỗng?)")
        return

    for rank, (doc, meta, dist) in enumerate(zip(docs, metas, dists), 1):
        preview = doc[:150].replace("\n", " ")
        print(
            f"\n[{rank}] distance={dist:.4f}  "
            f"channel={meta.get('channel')}  "
            f"coins={meta.get('coins', [])}  "
            f"time={meta.get('created_at')}"
        )
        print(f"    {preview}...")


def main() -> None:
    args = parse_args()

    if args.reset:
        answer = input(
            f"XÓA TOÀN BỘ collection '{CHROMA_COLLECTION}'? " f"Gõ 'yes' để xác nhận: "
        )
        if answer.strip().lower() != "yes":
            print("Đã hủy.")
            return
        reset_collection()

    stats = asyncio.run(run_ingestion(max_messages=args.limit))

    print("\n=== Kết quả ===")
    for key, value in stats.items():
        print(f"  {key:20s}: {value}")

    if args.verify:
        verify_query()


if __name__ == "__main__":
    main()
