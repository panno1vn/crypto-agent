"""
rag/retriever.py

Ngày 24 — Entrypoint retrieval duy nhất cho mọi consumer sau này
(Signal Aggregator Ngày 31, agent ReAct Ngày 33+, RAGAS Ngày 26).

Pipeline: vector search (có metadata filter) → Cross-Encoder rerank.

Khác với code roadmap gốc, đã kiểm chứng thực nghiệm ở Ngày 22-24:

  - `where` dùng "$and" khi có >=2 điều kiện — Chroma yêu cầu cú pháp
    này; gán 2 key top-level như roadmap gốc là sai cú pháp.
  - Lọc thời gian bằng `created_ts` (int, epoch UTC), KHÔNG dùng
    `created_at` (chuỗi ISO) — Chroma $gte/$lte chỉ nhận số.
  - Tính "bây giờ" bằng `time.time()`, KHÔNG dùng `datetime.utcnow()`
    hay `datetime.now()` rồi tự trừ. Process chạy ở TZ +07 (xác nhận
    qua benchmark Ngày 24: `process TZ: +07`) — hai hàm datetime đó rất
    dễ viết nhầm và lệch 7 giờ mà KHÔNG có traceback nào báo, đúng kiểu
    bug đã từng ngốn cả buổi Ngày 15/23. time.time() luôn là epoch UTC,
    không có chỗ để viết sai.
  - `coin` lọc bằng `{"coins": {"$contains": coin}}` — chỉ đúng NHỜ
    Ngày 22 lưu `coins` dạng list[str] (không phải chuỗi nối dấu phẩy
    như roadmap gốc) và chromadb>=1.5.0 hỗ trợ $contains trên mảng.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from data_pipeline.logger import get_logger
from rag.config import RETRIEVAL_CANDIDATE_POOL, RETRIEVAL_TOP_K
from rag.embedder import get_embedder
from rag.reranker import get_reranker
from rag.vector_store import get_collection

logger = get_logger(__name__)


def _build_where(
    coin: Optional[str],
    hours_ago: Optional[float],
    require_coin_mentioned: bool,
) -> Optional[dict[str, Any]]:
    """Dựng where clause hợp lệ với Chroma >= 1.5. Trả None nếu không có điều kiện."""
    clauses: list[dict[str, Any]] = []

    if hours_ago is not None:
        cutoff_ts = int(time.time() - hours_ago * 3600)
        clauses.append({"created_ts": {"$gte": cutoff_ts}})

    if coin:
        clauses.append({"coins": {"$contains": coin.upper()}})

    if require_coin_mentioned:
        clauses.append({"coins_count": {"$gt": 0}})

    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def search_telegram_news(
    query: str,
    coin: Optional[str] = None,
    hours_ago: Optional[float] = 24,
    top_k: int = RETRIEVAL_TOP_K,
    candidate_pool: int = RETRIEVAL_CANDIDATE_POOL,
    require_coin_mentioned: bool = False,
    rerank: bool = True,
) -> list[dict[str, Any]]:
    """
    Truy hồi tin nhắn Telegram liên quan tới query, có lọc metadata và
    rerank bằng Cross-Encoder.

    Args:
        query: Câu hỏi tiếng Việt hoặc tiếng Anh.
        coin: Mã coin viết hoa (VD "BTC"). None = không lọc theo coin.
        hours_ago: Chỉ lấy tin trong N giờ gần nhất. None = không lọc
                   thời gian.
        top_k: Số kết quả cuối cùng trả về.
        candidate_pool: Số ứng viên lấy từ vector search trước rerank.
                        Phải >= top_k. Mặc định 20 (giá trị benchmark).
        require_coin_mentioned: True = chỉ lấy tin có coins_count > 0.
        rerank: False = bỏ qua Cross-Encoder, trả thẳng kết quả vector
                search. Dùng để so sánh A/B ở RAGAS Ngày 26.

    Returns:
        List dict: msg_id, document, metadata, distance, rerank_score
        (None nếu rerank=False). Sort theo rerank_score nếu có rerank,
        ngược lại theo distance tăng dần.

    Raises:
        ValueError: candidate_pool < top_k.
    """
    if candidate_pool < top_k:
        raise ValueError(f"candidate_pool ({candidate_pool}) phải >= top_k ({top_k})")

    where = _build_where(coin, hours_ago, require_coin_mentioned)

    collection = get_collection()
    embedder = get_embedder()

    t0 = time.perf_counter()
    query_embedding = embedder.encode([query])[0].tolist()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=candidate_pool,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    vector_ms = (time.perf_counter() - t0) * 1000

    candidates = [
        {
            "msg_id": meta.get("msg_id"),
            "document": doc,
            "metadata": meta,
            "distance": float(dist),
            "rerank_score": None,
        }
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        )
    ]

    if not candidates:
        logger.info(f"[RETRIEVER] query={query!r} where={where} → 0 ứng viên")
        return []

    if not rerank:
        return candidates[:top_k]

    t1 = time.perf_counter()
    reranked = get_reranker().rerank(query, candidates, top_k=top_k)
    rerank_ms = (time.perf_counter() - t1) * 1000

    logger.info(
        f"[RETRIEVER] query={query!r} coin={coin} hours_ago={hours_ago} "
        f"candidates={len(candidates)}→{len(reranked)} "
        f"vector_ms={vector_ms:.0f} rerank_ms={rerank_ms:.0f} "
        f"top1_score={reranked[0]['rerank_score']:.3f}"
    )

    return reranked


def warm_up() -> None:
    """
    Ép load embedder + reranker ngay lập tức, tách chi phí load model
    (~11s + ~9s, đo thực tế ở Ngày 24) ra khỏi lần gọi search đầu tiên.

    Gọi 1 lần lúc khởi động service SỐNG LÂU (Airflow task, FastAPI app,
    agent ReAct Ngày 33+). KHÔNG gọi trong hot path của mỗi request.

    Nếu bỏ qua bước này: lần search_telegram_news() đầu tiên của mỗi
    process mới sẽ có vector_ms/rerank_ms bị thổi phồng do lazy load —
    xem log thực tế Ngày 24 (vector_ms=19110 lần đầu vs 56 lần sau).
    Đọc log lúc đó dễ tưởng nhầm vector search chậm.

    Câu hỏi kiến trúc CHƯA CHỐT cho Ngày 33+: nếu agent chạy như process
    ngắn hạn (spin up mỗi lần gọi tool thay vì service sống lâu), chi
    phí ~20s này sẽ lặp lại MỖI LẦN gọi — không chấp nhận được cho vòng
    lặp ReAct nhiều bước. Phải quyết định kiến trúc trước khi tới đó.
    """
    logger.info("[RETRIEVER] Warm-up: loading embedder + reranker...")
    t0 = time.perf_counter()
    get_embedder().encode(["warmup"])
    get_reranker().rerank("warmup", [{"document": "warmup"}], top_k=1)
    logger.info(f"[RETRIEVER] Warm-up done in {time.perf_counter() - t0:.1f}s")
