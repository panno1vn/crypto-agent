"""
scripts/manual/benchmark_reranker.py

Ngày 24 — So sánh các Cross-Encoder reranker trên corpus thật.

Câu hỏi cần trả lời, theo đúng thứ tự ưu tiên:

  1. Có model nào tạo được NGƯỠNG CUTOFF tách sạch query on-topic khỏi
     query off-topic không? Ngày 23 đã chứng minh distance thô KHÔNG làm
     được (query "Binance CEO từ chức" dist 0.551 nằm lọt trong vùng
     negative control 0.49-0.58). Nếu reranker cũng không làm được thì
     kết luận đúng là BỎ reranker, không phải nhét nó vào cho khớp roadmap.

  2. Reranker có thực sự đổi thứ hạng không, hay chỉ chép lại thứ tự của
     vector search? Đo bằng Kendall tau. tau ~ 1.0 = model không đóng góp
     gì ngoài việc tốn thêm 1-3 giây.

  3. Latency có nằm trong ngân sách của agent ReAct (Ngày 33+) không?
     Một vòng ReAct 5 bước gọi retrieval 5 lần. 3s/lần = 15s chỉ để
     retrieve. Phải biết con số này TRƯỚC khi xây agent.

Không cần label thủ công: bộ query Ngày 23 đã có nhãn ở cấp query
(on-topic / negative control), đủ để trả lời cả 3 câu hỏi trên.

Chạy:
    cd ~/crypto-agent && source .venv/bin/activate
    PYTHONPATH=. python scripts/manual/benchmark_reranker.py
    PYTHONPATH=. python scripts/manual/benchmark_reranker.py --models mmarco
    PYTHONPATH=. python scripts/manual/benchmark_reranker.py --strip-prefix
"""

from __future__ import annotations

import argparse
import gc
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from data_pipeline.logger import get_logger  # noqa: E402
from rag.embedder import get_embedder  # noqa: E402
from rag.vector_store import get_collection  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Bộ query — lấy nguyên từ Ngày 23 để so sánh trực tiếp được với bảng cũ.
# 'topic' = có tin liên quan thật trong corpus.
# 'off'   = negative control, corpus KHÔNG có gì liên quan.
# ---------------------------------------------------------------------------
QUERIES: list[dict[str, str]] = [
    {"q": "Bitcoin tăng giá mạnh", "kind": "topic", "lang": "vi"},
    {"q": "Ethereum giảm giá", "kind": "topic", "lang": "vi"},
    {"q": "SEC quy định crypto", "kind": "topic", "lang": "vi"},
    {"q": "SEC crypto regulation", "kind": "topic", "lang": "en"},
    {"q": "altcoin season đang tới", "kind": "topic", "lang": "vi"},
    {"q": "BTC pump", "kind": "topic", "lang": "en"},
    # Hai query dưới là ca khó nhất của Ngày 23 — distance thất bại ở đây.
    {"q": "Binance CEO từ chức", "kind": "topic", "lang": "vi"},
    {"q": "Binance CEO resignation scandal", "kind": "topic", "lang": "en"},
    {"q": "công thức nấu phở bò", "kind": "off", "lang": "vi"},
    {"q": "weather forecast tomorrow", "kind": "off", "lang": "en"},
]

MODELS: dict[str, str] = {
    "msmarco-en": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "mmarco": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    "bge-m3": "BAAI/bge-reranker-v2-m3",
}

# Bỏ các khối "[Channel: x] [Coins: y] " ở đầu document do enricher thêm vào.
# Viết theo dạng tổng quát (mọi khối [...] liên tiếp ở đầu chuỗi) để không
# phụ thuộc vào format cụ thể của enrich_message_for_embedding().
_PREFIX_RE = re.compile(r"^(\[[^\]]*\]\s*)+")


def strip_enrich_prefix(doc: str) -> str:
    """Trả về phần nội dung tin nhắn, bỏ prefix metadata do enricher chèn."""
    return _PREFIX_RE.sub("", doc).strip()


def kendall_tau(order_a: list[int], order_b: list[int]) -> float:
    """
    Kendall tau-a giữa 2 hoán vị, tự cài để không phụ thuộc scipy.

    n <= 50 nên O(n^2) hoàn toàn chấp nhận được.

    Args:
        order_a: Danh sách id theo thứ hạng của xếp hạng thứ nhất.
        order_b: Danh sách id theo thứ hạng của xếp hạng thứ hai.

    Returns:
        Giá trị trong [-1, 1]. 1.0 = hai thứ hạng trùng khớp hoàn toàn.
    """
    rank_b = {item: i for i, item in enumerate(order_b)}
    n = len(order_a)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a_i, a_j = order_a[i], order_a[j]
            if a_i not in rank_b or a_j not in rank_b:
                continue
            if rank_b[a_i] < rank_b[a_j]:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def vector_search(collection, embedder, query: str, n: int) -> list[dict[str, Any]]:
    """
    Lấy top-n ứng viên từ Chroma, KHÔNG áp where filter.

    Cố ý bỏ filter ở bước benchmark: mục tiêu là đo riêng chất lượng
    reranker. Trộn thêm metadata filter vào sẽ làm không phân biệt được
    cải thiện đến từ đâu.
    """
    emb = embedder.encode([query])[0].tolist()
    res = collection.query(
        query_embeddings=[emb],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )
    out = []
    for doc, meta, dist in zip(
        res["documents"][0], res["metadatas"][0], res["distances"][0]
    ):
        out.append(
            {
                "msg_id": meta.get("msg_id"),
                "document": doc,
                "metadata": meta,
                "distance": float(dist),
            }
        )
    return out


def load_reranker(model_name: str, max_length: int):
    """
    Load CrossEncoder với sigmoid activation.

    Vì sao cần sigmoid: model họ MS MARCO trả về LOGIT (khoảng giá trị có
    thể là -11 tới +11), không phải xác suất. Sigmoid không đổi thứ hạng
    (hàm đơn điệu tăng) nhưng đưa mọi model về chung thang 0-1, nhờ đó
    ngưỡng cutoff đọc được bằng mắt và so sánh được giữa các query.
    """
    import torch
    from sentence_transformers import CrossEncoder

    logger.info(f"[RERANK] Loading {model_name}...")
    t0 = time.perf_counter()
    model = CrossEncoder(
        model_name,
        max_length=max_length,
        activation_fn=torch.nn.Sigmoid(),
        device="cpu",
    )
    logger.info(f"[RERANK] Loaded in {time.perf_counter() - t0:.1f}s")
    return model


def benchmark_model(
    key: str,
    model_name: str,
    candidate_sets: dict[str, list[dict[str, Any]]],
    strip_prefix: bool,
    max_length: int,
) -> dict[str, Any]:
    """Chấm điểm toàn bộ query bằng 1 model, trả về thống kê."""
    model = load_reranker(model_name, max_length)
    per_query: list[dict[str, Any]] = []

    for spec in QUERIES:
        q = spec["q"]
        cands = candidate_sets[q]
        docs = [
            strip_enrich_prefix(c["document"]) if strip_prefix else c["document"]
            for c in cands
        ]
        pairs = [(q, d) for d in docs]

        t0 = time.perf_counter()
        scores = model.predict(pairs, show_progress_bar=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        scored = sorted(
            zip(cands, [float(s) for s in scores]),
            key=lambda x: x[1],
            reverse=True,
        )
        vector_order = [c["msg_id"] for c in cands]
        rerank_order = [c["msg_id"] for c, _ in scored]

        per_query.append(
            {
                "query": q,
                "kind": spec["kind"],
                "lang": spec["lang"],
                "top1_score": scored[0][1],
                "top5_scores": [round(s, 4) for _, s in scored[:5]],
                "top1_vector_dist": cands[0]["distance"],
                "tau_vs_vector": round(kendall_tau(vector_order, rerank_order), 3),
                "latency_ms": round(elapsed_ms, 1),
                "top1_doc": scored[0][0]["document"][:120],
                "moved_into_top5": sorted(
                    set(rerank_order[:5]) - set(vector_order[:5])
                ),
            }
        )

    on_top1 = [r["top1_score"] for r in per_query if r["kind"] == "topic"]
    off_top1 = [r["top1_score"] for r in per_query if r["kind"] == "off"]
    lat = sorted(r["latency_ms"] for r in per_query)

    stats = {
        "model": model_name,
        "min_on_topic_top1": round(min(on_top1), 4),
        "max_off_topic_top1": round(max(off_top1), 4),
        # Dương  = tồn tại một ngưỡng cutoff tách sạch. Đây là con số
        #          quyết định có dùng reranker hay không.
        # Âm     = model này thất bại đúng chỗ distance đã thất bại.
        "separation_margin": round(min(on_top1) - max(off_top1), 4),
        "mean_tau_vs_vector": round(
            sum(r["tau_vs_vector"] for r in per_query) / len(per_query), 3
        ),
        "latency_ms_mean": round(sum(lat) / len(lat), 1),
        "latency_ms_max": lat[-1],
        "per_query": per_query,
    }

    del model
    gc.collect()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Cross-Encoder reranker")
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(MODELS.keys()),
        choices=list(MODELS.keys()),
        help="Model nào cần đo. Mặc định: tất cả.",
    )
    parser.add_argument(
        "--top-n", type=int, default=20, help="Số ứng viên từ vector search"
    )
    parser.add_argument(
        "--max-length", type=int, default=512, help="max_length cho CrossEncoder"
    )
    parser.add_argument(
        "--strip-prefix",
        action="store_true",
        help="Bỏ prefix [Channel:...][Coins:...] trước khi đưa vào reranker",
    )
    parser.add_argument("--out", default="docs/benchmark_reranker.json")
    args = parser.parse_args()

    collection = get_collection()
    embedder = get_embedder()
    logger.info(f"[BENCH] Collection count = {collection.count()}")

    # Chạy vector search MỘT LẦN, dùng chung cho mọi model — nếu mỗi model
    # tự search lại thì ứng viên có thể khác nhau (DAG nền vẫn đang ghi
    # thêm tin mới), và mọi so sánh sau đó trở nên vô nghĩa.
    logger.info(f"[BENCH] Retrieving top-{args.top_n} for {len(QUERIES)} queries...")
    candidate_sets: dict[str, list[dict[str, Any]]] = {}
    vec_latency: list[float] = []
    for spec in QUERIES:
        t0 = time.perf_counter()
        candidate_sets[spec["q"]] = vector_search(
            collection, embedder, spec["q"], args.top_n
        )
        vec_latency.append((time.perf_counter() - t0) * 1000)

    # Baseline: distance thô có tách được on-topic/off-topic không?
    # Distance NHỎ là tốt, nên margin đảo dấu so với score.
    on_d = [
        candidate_sets[s["q"]][0]["distance"] for s in QUERIES if s["kind"] == "topic"
    ]
    off_d = [
        candidate_sets[s["q"]][0]["distance"] for s in QUERIES if s["kind"] == "off"
    ]
    baseline = {
        "model": "VECTOR ONLY (baseline)",
        "max_on_topic_top1_dist": round(max(on_d), 4),
        "min_off_topic_top1_dist": round(min(off_d), 4),
        "separation_margin": round(min(off_d) - max(on_d), 4),
        "latency_ms_mean": round(sum(vec_latency) / len(vec_latency), 1),
    }

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_n": args.top_n,
        "strip_prefix": args.strip_prefix,
        "collection_count": collection.count(),
        "baseline": baseline,
        "models": {},
    }

    for key in args.models:
        logger.info(f"[BENCH] ===== {key} =====")
        results["models"][key] = benchmark_model(
            key, MODELS[key], candidate_sets, args.strip_prefix, args.max_length
        )

    # ---------------- Báo cáo ----------------
    print("\n" + "=" * 78)
    print("BASELINE — vector distance thô (Ngày 23)")
    print("=" * 78)
    print(f"  on-topic  top1 dist tệ nhất : {baseline['max_on_topic_top1_dist']}")
    print(f"  off-topic top1 dist tốt nhất: {baseline['min_off_topic_top1_dist']}")
    print(
        f"  separation margin           : {baseline['separation_margin']}  "
        f"({'TÁCH ĐƯỢC' if baseline['separation_margin'] > 0 else 'CHỒNG LẤN'})"
    )
    print(f"  latency trung bình          : {baseline['latency_ms_mean']} ms")

    print("\n" + "=" * 78)
    print(
        f"{'model':<12} {'margin':>9} {'min_on':>8} {'max_off':>8} "
        f"{'tau':>6} {'lat_ms':>8} {'lat_max':>8}"
    )
    print("=" * 78)
    for key, s in results["models"].items():
        flag = "OK " if s["separation_margin"] > 0 else "FAIL"
        print(
            f"{key:<12} {s['separation_margin']:>9.4f} {s['min_on_topic_top1']:>8.4f} "
            f"{s['max_off_topic_top1']:>8.4f} {s['mean_tau_vs_vector']:>6.3f} "
            f"{s['latency_ms_mean']:>8.1f} {s['latency_ms_max']:>8.1f}  {flag}"
        )

    print("\nĐọc bảng trên:")
    print("  margin > 0  → tồn tại ngưỡng cutoff tách sạch on/off-topic")
    print("  tau  ~ 1.0  → reranker chỉ chép lại thứ tự vector, không đóng góp gì")
    print("  lat_max     → nhân với số bước ReAct để ra ngân sách Ngày 33+")

    for key, s in results["models"].items():
        print(f"\n--- {key} — chi tiết từng query ---")
        for r in s["per_query"]:
            mark = "!" if r["kind"] == "off" else " "
            print(
                f" {mark} {r['query'][:34]:<34} top1={r['top1_score']:.4f} "
                f"dist={r['top1_vector_dist']:.3f} tau={r['tau_vs_vector']:>6} "
                f"{r['latency_ms']:>7.0f}ms  moved={len(r['moved_into_top5'])}"
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n[BENCH] Đã ghi kết quả đầy đủ vào {out_path}")


if __name__ == "__main__":
    main()
