"""
scripts/manual/benchmark_reranker_round2.py

Ngày 24 — Kiểm chứng 3 nghi vấn phát sinh từ round 1:

  1. msmarco-en thắng round 1 có phải nhờ hiểu tiếng Việt, hay chỉ nhờ
     token trùng (Bitcoin/SEC/CEO/Binance xuất hiện y hệt trong cả query
     lẫn document)? → cognate ablation: giữ nguyên candidate pool, đổi
     query sang bản diễn giải thuần Việt không chứa từ mượn, xem điểm
     rơi bao nhiêu.

  2. Corpus có thực sự chứa nội dung thời tiết (nghi vấn negative
     control "weather forecast tomorrow" bị sai nhãn)? → in full text
     của top-1 để đọc bằng mắt, không đoán qua 120 ký tự bị cắt.

  3. Latency thật của vector search (không tính chi phí load model
     lazy ở lần gọi đầu) là bao nhiêu? → warm-up trước khi đo.

Chạy:
    PYTHONPATH=. python scripts/manual/benchmark_reranker_round2.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from data_pipeline.logger import get_logger  # noqa: E402
from rag.embedder import get_embedder  # noqa: E402
from rag.vector_store import get_collection  # noqa: E402

logger = get_logger(__name__)

MODELS = {
    "msmarco-en": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "mmarco": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
}

# ---------------------------------------------------------------------------
# Cognate ablation — cùng 1 ý nghĩa, một bản có từ mượn tiếng Anh (khớp
# token trực tiếp với document), một bản diễn giải thuần Việt (không có
# từ nào trùng ký tự với "Bitcoin/SEC/CEO/Binance/altcoin").
# ---------------------------------------------------------------------------
COGNATE_PAIRS = [
    {
        "cognate": "Bitcoin tăng giá mạnh",
        "vi_only": "Đồng tiền số vốn hóa lớn nhất đang tăng giá mạnh",
    },
    {
        "cognate": "Ethereum giảm giá",
        "vi_only": "Đồng tiền số lớn thứ hai đang giảm giá",
    },
    {
        "cognate": "SEC quy định crypto",
        "vi_only": "Cơ quan quản lý Mỹ ra quy định về tiền điện tử",
    },
    {
        "cognate": "Binance CEO từ chức",
        "vi_only": "Sếp lớn của một sàn giao dịch tiền số từ chức",
    },
    {
        "cognate": "altcoin season đang tới",
        "vi_only": "Mùa các đồng tiền số vốn hóa nhỏ đang tới",
    },
]

# Query bản địa thuần Việt, không có ground truth — chỉ đọc bằng mắt để
# xem model có tìm ra doc hợp lý không, không tính margin định lượng.
NATIVE_SLANG_QUERIES = [
    "cá mập gom hàng vùng đáy",
    "kèo x5 sắp về bờ",
    "sập sàn rút tiền không được",
    "đu đỉnh xong giờ cắt lỗ",
]


def vector_search(collection, embedder, query: str, n: int = 20) -> list[dict]:
    emb = embedder.encode([query])[0].tolist()
    res = collection.query(
        query_embeddings=[emb],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"msg_id": m.get("msg_id"), "document": d, "distance": float(dist)}
        for d, m, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        )
    ]


def main() -> None:
    collection = get_collection()
    embedder = get_embedder()

    # --- Warm-up: loại lazy-load ra khỏi phép đo latency ---
    logger.info("[WARMUP] Loading embedder trước khi đo...")
    embedder.encode(["warmup"])
    t0 = time.perf_counter()
    for _ in range(5):
        embedder.encode(["Bitcoin tăng giá"])
    warm_latency_ms = (time.perf_counter() - t0) / 5 * 1000
    print(
        f"\n[LATENCY THẬT — sau warm-up] vector search 1 query ≈ {warm_latency_ms:.0f}ms\n"
    )

    import torch
    from sentence_transformers import CrossEncoder

    rerankers = {
        key: CrossEncoder(
            name, max_length=512, activation_fn=torch.nn.Sigmoid(), device="cpu"
        )
        for key, name in MODELS.items()
    }

    # ---------------- 2. Đọc thật negative control nghi ngờ ----------------
    print("=" * 78)
    print("KIỂM TRA NHÃN — 'weather forecast tomorrow' có thực sự off-topic?")
    print("=" * 78)
    cands = vector_search(collection, embedder, "weather forecast tomorrow", n=3)
    for c in cands:
        print(f"[dist={c['distance']:.3f}] {c['document'][:300]}\n")

    # ---------------- 1. Cognate ablation ----------------
    print("=" * 78)
    print(
        "COGNATE ABLATION — msmarco-en thắng nhờ hiểu tiếng Việt hay nhờ token trùng?"
    )
    print("=" * 78)
    ablation_results = []
    for pair in COGNATE_PAIRS:
        cands = vector_search(collection, embedder, pair["cognate"], n=20)
        docs = [c["document"] for c in cands]

        row = {"cognate_query": pair["cognate"], "vi_only_query": pair["vi_only"]}
        print(f"\n--- {pair['cognate']!r} vs {pair['vi_only']!r} ---")
        for key, model in rerankers.items():
            score_cognate = max(model.predict([(pair["cognate"], d) for d in docs]))
            score_vi_only = max(model.predict([(pair["vi_only"], d) for d in docs]))
            drop = score_cognate - score_vi_only
            drop_pct = (drop / score_cognate * 100) if score_cognate > 0 else 0
            row[key] = {
                "score_with_cognate": round(float(score_cognate), 4),
                "score_vi_only": round(float(score_vi_only), 4),
                "drop_pct": round(drop_pct, 1),
            }
            flag = "RƠI MẠNH — nghi khớp token" if drop_pct > 40 else "giữ được"
            print(
                f"  {key:<12} cognate={score_cognate:.4f}  vi_only={score_vi_only:.4f}  "
                f"drop={drop_pct:>5.1f}%  [{flag}]"
            )
        ablation_results.append(row)

    # ---------------- Bonus: query bản địa, đọc bằng mắt ----------------
    print("\n" + "=" * 78)
    print("QUERY BẢN ĐỊA (không ground truth — đọc bằng mắt)")
    print("=" * 78)
    for q in NATIVE_SLANG_QUERIES:
        cands = vector_search(collection, embedder, q, n=20)
        docs = [c["document"] for c in cands]
        print(f"\n--- {q!r} ---")
        for key, model in rerankers.items():
            scores = model.predict([(q, d) for d in docs])
            best_i = max(range(len(scores)), key=lambda i: scores[i])
            print(
                f"  {key:<12} top1_score={scores[best_i]:.4f}  "
                f"doc={docs[best_i][:140]}"
            )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warm_latency_ms_per_query": round(warm_latency_ms, 1),
        "cognate_ablation": ablation_results,
    }
    Path("docs/benchmark_reranker_round2.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )
    print("\n[DONE] docs/benchmark_reranker_round2.json")


if __name__ == "__main__":
    main()
