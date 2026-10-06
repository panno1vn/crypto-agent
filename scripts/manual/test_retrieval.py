"""
scripts/manual/test_retrieval.py

Ngày 23 — Test retrieval có số liệu, thay cho "BTC pump → đúng không?"
mơ hồ trong checklist gốc. Chấm tay precision@5 cho từng query, ghi kết
quả vào GHI_CHU_NGAY23.md.

Chạy: python scripts/manual/test_retrieval.py
"""

from dotenv import load_dotenv

load_dotenv()

from rag.embedder import get_embedder  # noqa: E402
from rag.vector_store import get_collection  # noqa: E402

# Bộ query: on-topic VN, on-topic EN, cross-lingual, negative control.
# "expect_coin"/"expect_lang" chỉ để in gợi ý khi chấm tay, KHÔNG dùng để
# tự động pass/fail — chấm bằng mắt vẫn là cách đáng tin nhất ở quy mô này.
QUERIES = [
    {"text": "Bitcoin tăng giá mạnh", "note": "VN on-topic"},
    {"text": "BTC pump", "note": "EN on-topic (ví dụ gốc roadmap)"},
    {"text": "Binance CEO từ chức", "note": "VN on-topic, tin CZ resignation"},
    {
        "text": "Binance CEO resignation scandal",
        "note": "EN -> kỳ vọng ra tin VN (cross-lingual)",
    },
    {"text": "Ethereum giảm giá", "note": "VN on-topic, coin khác BTC"},
    {"text": "altcoin season đang tới", "note": "VN, thuật ngữ tiếng Anh chèn giữa"},
    {"text": "SEC quy định crypto", "note": "VN, chủ đề pháp lý"},
    {
        "text": "SEC crypto regulation",
        "note": "EN -> kỳ vọng ra tin VN (cross-lingual)",
    },
    {
        "text": "công thức nấu phở bò",
        "note": "NEGATIVE CONTROL: hoàn toàn không liên quan crypto",
    },
    {
        "text": "weather forecast tomorrow",
        "note": "NEGATIVE CONTROL: EN, không liên quan",
    },
]


def run():
    collection = get_collection()
    embedder = get_embedder()

    print(f"Collection count: {collection.count()}\n")
    print("=" * 100)

    for q in QUERIES:
        query_vec = embedder.encode([q["text"]])[0].tolist()

        results = collection.query(
            query_embeddings=[query_vec],
            n_results=5,
        )

        print(f"\nQUERY: {q['text']!r}  ({q['note']})")
        print("-" * 100)

        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        if not ids:
            print("  (không có kết quả)")
            continue

        for i, (doc, meta, dist) in enumerate(zip(docs, metas, dists), start=1):
            coins = meta.get("coins", [])
            channel = meta.get("channel", "?")
            snippet = doc[:80].replace("\n", " ")
            print(
                f"  [{i}] dist={dist:.4f}  channel={channel}  coins={coins}\n"
                f"      {snippet}..."
            )

    print("\n" + "=" * 100)

    # --- Test where clause: $contains trên coins (mảng) ---
    print("\n[TEST WHERE] coins $contains 'BTC', không kèm vector search:")
    r = collection.get(
        where={"coins": {"$contains": "BTC"}},
        limit=3,
        include=["metadatas"],
    )
    print(f"  Số record: {len(r['ids'])}")
    for meta in r["metadatas"][:3]:
        print(f"  - coins={meta.get('coins')} channel={meta.get('channel')}")

    # --- Test where clause: created_ts $gte (bắt buộc dùng epoch int) ---
    print("\n[TEST WHERE] created_ts $gte (24h gần nhất tính từ tin mới nhất):")
    import time

    cutoff = int(time.time()) - 24 * 3600
    r2 = collection.get(
        where={"created_ts": {"$gte": cutoff}},
        limit=3,
        include=["metadatas"],
    )
    print(f"  Số record trong 24h qua (tính từ lúc chạy script): {len(r2['ids'])}")


if __name__ == "__main__":
    run()
