"""
scripts/manual/eval_retrieval_day26.py

Ngày 26 — Retrieval-only Evaluation
====================================
KHÔNG dùng RAGAS Faithfulness / Answer Relevancy hôm nay. Lý do: 2 metric
đó cần một câu trả lời ĐÃ ĐƯỢC LLM SINH RA từ (câu hỏi + context), nhưng
Ollama/LLM chưa được tích hợp cho tới Ngày 33-34 theo roadmap. Đo 2 metric
này bây giờ là đo một thứ chưa tồn tại. Chi tiết xem GHI_CHU_NGAY26.md.

Thay vào đó, file này đo CHẤT LƯỢNG RETRIEVAL thuần (thứ thực sự tồn tại
sau Ngày 24-25), bằng 2 bộ test KHÔNG cần LLM judge, KHÔNG cần OpenAI key:

  A. AUTO-LABELED — ground truth suy trực tiếp từ PostgreSQL, không cần
     Pan gán nhãn tay. Với mỗi (coin, hours_ago), tập "relevant" thật sự
     = mọi message trong telegram_messages có coin đó trong
     coins_mentioned VÀ created_at nằm trong cửa sổ thời gian. So khớp
     với ID mà search_telegram_news() trả về → Precision@k, Recall@k, MRR.
     Đây là cách kiểm chứng khách quan nhất hiện có cho tầng metadata
     filter, vì ground truth không phụ thuộc vào đánh giá chủ quan nào.

  B. HAND-LABELED — dùng lại nguyên bộ 7 câu negative-control + 5 câu
     tiếng lóng ĐÃ ĐƯỢC PAN CHẤM ở Ngày 24-25 (xem GHI_CHU_NGAY24_25.md
     mục 8), không tạo bộ mới. Kiểm tra rerank_score của các câu đó có
     còn đúng ngưỡng như lúc benchmark hay không (dùng để phát hiện
     regression nếu model/config đổi trong tương lai).

  C. NỢ #1 (truncation debt) — với mỗi document trong ground truth của Bộ A,
     phân loại "ngắn" (< 128 token theo tokenizer thật của
     paraphrase-multilingual-mpnet-base-v2) hay "dài/bị cắt" (>= 128 token).
     So sánh hit-rate (có được retrieve vào top-k hay không) giữa 2 nhóm.
     Nếu hit-rate nhóm dài thấp hơn rõ rệt → truncation đang thật sự làm
     mất recall → mở task chunking. Nếu tương đương → đóng nợ #1, không
     cần xây thêm hạ tầng.

So sánh rerank=True vs rerank=False cho cả 3 bộ, log MLflow để so
sánh lâu dài qua các lần chỉnh sửa retriever/reranker.

============================================================================
GIẢ ĐỊNH VỀ SIGNATURE search_telegram_news() — ĐÃ XÁC NHẬN qua
--smoke-test (Pan chạy 2026-08-13, xem log thật):

    search_telegram_news(
        query: str,
        coin: str | None = None,
        hours_ago: int = 24,
        top_k: int = 5,
        rerank: bool = True,
    ) -> list[dict]   # field ID = "msg_id" (int) — CONFIRMED
                       # field điểm = "rerank_score" (float) — CONFIRMED
                       # kèm thêm "document", "metadata", "distance" (chưa dùng)

CÒN LẠI CHƯA XÁC NHẬN (đánh dấu "# VERIFY"), nhưng rủi ro thấp vì cùng
module đã import thành công:
  - `warm_up()` có tồn tại trong rag/retriever.py, không cần argument
    (suy từ mô tả deliverable ở GHI_CHU_NGAY24_25.md mục 2, chưa gọi
    thử qua --smoke-test). Nếu sai tên/signature, lỗi sẽ raise NGAY khi
    chạy full eval (không phải lỗi âm thầm) — sửa 1 chỗ rồi chạy lại.
============================================================================
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import asyncpg
import mlflow

from rag.retriever import search_telegram_news, warm_up  # VERIFY: warm_up tồn tại?

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'admin')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'admin123')}@"
    f"{os.getenv('POSTGRES_HOST', 'localhost')}:5432/"
    f"{os.getenv('POSTGRES_DB', 'crypto_agent')}"
)

EVAL_COINS = ["BTC", "ETH", "SOL", "BNB", "XRP"]  # khớp 5 coin đang track
EVAL_WINDOWS_HOURS = [6, 24]
TOP_K = 5

QUERY_TEMPLATES = [
    "Tin tức về {coin} trong {h} giờ qua",
    "{coin} có diễn biến gì mới không",
]

# Bộ B — tách 2 nhóm, KHÔNG còn xử lý chung như trước (đã sai vì lẫn 2
# nguồn baseline khác nhau, phát hiện 2026-08-13):
#
#   NEGATIVE_CONTROLS: pass/fail TỰ ĐỘNG được, vì ngưỡng LOW_SCORE_THRESHOLD
#   đã verify TRỰC TIẾP với mmarco thật ở GHI_CHU_NGAY24_25.md mục 9
#   ("mmarco giữ dưới 0.14 mọi câu" — kể cả case guitar 0.0041).
#
#   SLANG_QUERIES: KHÔNG pass/fail tự động. Baseline lịch sử
#   (0.858/0.870/0.895/0.864/0.181) đo bằng msmarco-en, TRƯỚC khi đảo
#   sang mmarco — xác nhận từ Pan 2026-08-13. Không có baseline mmarco
#   thật cho 5 câu này. Script chỉ IN RA score + đoạn document top-1,
#   Pan tự đọc và chấm bằng mắt — đúng quy trình đã áp dụng cho
#   msmarco-en trước đây (GHI_CHU_NGAY24_25.md mục 10: "Test tiếng lóng
#   cần ground truth thật, Pan tự chấm, không thể để AI tự đánh giá").
#
# ĐÃ BỎ "weather forecast tomorrow" khỏi negative control (2026-08-13):
# GHI_CHU_NGAY24_25.md mục 3 ghi rõ câu này "không sạch" — corpus thật
# có tin thời tiết liên quan vĩ mô/hàng hoá (kênh macro).
NEGATIVE_CONTROLS: list[str] = [
    "cách nấu bún chả Hà Nội",
    "lịch chiếu phim rạp CGV cuối tuần",
    "review sách văn học kinh điển",
    "cách chăm sóc cây cảnh trong nhà",
    "hướng dẫn học guitar cơ bản cho người mới",
    "công thức nấu phở bò",
]

SLANG_QUERIES: list[str] = [
    "cá mập gom hàng vùng đáy",
    "kèo x5 sắp về bờ",
    "sập sàn rút tiền không được",
    "đu đỉnh xong giờ cắt lỗ",
    "fomo mua đỉnh rồi dính bẫy",
]

LOW_SCORE_THRESHOLD = 0.20  # dựa trên mmarco giữ dưới 0.14 trên negative control


# ---------------------------------------------------------------------------
# Smoke test — xác nhận nhanh signature search_telegram_news()
# (đã chạy 2026-08-13, field msg_id/rerank_score CONFIRMED — giữ hàm này
# lại để dùng lần sau nếu retriever.py đổi field name)
# ---------------------------------------------------------------------------


def run_smoke_test() -> None:
    print(
        "Gọi search_telegram_news(query='BTC', coin='BTC', hours_ago=24*30, "
        "top_k=2, rerank=True)...\n"
    )
    hits = search_telegram_news(
        query="BTC",
        coin="BTC",
        hours_ago=24 * 30,
        top_k=2,
        rerank=True,
    )
    if not hits:
        print(
            "→ Trả về rỗng. Không tự tin verify được field name từ đây — "
            "thử coin khác hoặc hours_ago lớn hơn, hoặc đọc thẳng "
            "rag/retriever.py."
        )
        return

    print(f"→ Trả về {len(hits)} kết quả. Item đầu tiên:\n")
    first = hits[0]
    print(f"  type: {type(first)}")
    if isinstance(first, dict):
        for k, v in first.items():
            print(f"  key={k!r:25s} type={type(v).__name__:10s} value={v!r}")
        print()
        print("Đối chiếu:")
        id_ok = "msg_id" in first
        score_ok = "rerank_score" in first
        print(f"  Field ID    giả định 'msg_id'      → {'CÓ' if id_ok else 'KHÔNG'}")
        print(
            f"  Field điểm  giả định 'rerank_score' → {'CÓ' if score_ok else 'KHÔNG'}"
        )
        if not id_ok or not score_ok:
            print(
                "  → field name lệch giả định. Tìm 'item[\"msg_id\"]' và "
                "'hits[0][\"rerank_score\"]' trong file này, sửa cho khớp key thật ở trên."
            )
    else:
        print(
            "  Không phải dict — cấu trúc trả về khác giả định hoàn toàn, "
            "cần đọc lại rag/retriever.py trước khi chạy full eval."
        )


# ---------------------------------------------------------------------------
# Bộ A + Bộ C — gộp chung 1 vòng lặp retrieval (tránh gọi search_telegram_news
# 2 lần cho cùng 1 (coin, h, template) — rerank_ms ~650-950ms/call ở steady
# state, gộp lại đỡ mất thêm ~40 lần gọi không cần thiết mỗi lượt rerank)
# ---------------------------------------------------------------------------


MsgKey = tuple[str, int]  # (channel_name, msg_id)


async def get_ground_truth_with_text(
    pool: asyncpg.Pool, coin: str, hours_ago: int
) -> list[tuple[MsgKey, str]]:
    """
    Ground truth khách quan: mọi message thật trong Postgres có nhắc
    `coin` trong cửa sổ `hours_ago` giờ gần nhất, kèm text để phân loại
    ngắn/dài cho Bộ C. Không cần LLM, không cần Pan gán nhãn tay.
    """
    since = datetime.utcnow() - timedelta(hours=hours_ago)
    rows = await pool.fetch(
        """
        SELECT channel_name, id, message_text FROM telegram_messages
        WHERE $1 = ANY(coins_mentioned) AND created_at >= $2
        """,
        coin,
        since,
    )
    # Khóa (kênh, msg_id) — nợ #15: msg_id chỉ duy nhất trong 1 kênh, so
    # khớp chỉ bằng id sẽ tính nhầm tin kênh khác cùng id là "relevant".
    return [((r["channel_name"], r["id"]), r["message_text"] or "") for r in rows]


def precision_at_k(retrieved_ids: list[MsgKey], relevant_ids: set[MsgKey]) -> float:
    if not retrieved_ids:
        return 0.0
    hits = sum(1 for i in retrieved_ids if i in relevant_ids)
    return hits / len(retrieved_ids)


def recall_at_k(retrieved_ids: list[MsgKey], relevant_ids: set[MsgKey]) -> float:
    if not relevant_ids:
        return float("nan")  # không có ground truth → không đo được, không phải 0
    hits = sum(1 for i in retrieved_ids if i in relevant_ids)
    return hits / len(relevant_ids)


def reciprocal_rank(retrieved_ids: list[MsgKey], relevant_ids: set[MsgKey]) -> float:
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_ids:
            return 1.0 / rank
    return 0.0


# Nợ #1 — model embedder thật dùng ở Ngày 22, KHÔNG phải model reranker.
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
MAX_SEQ_LENGTH = 128
TRUNCATION_DEBT_GAP_THRESHOLD = 0.15  # chênh hit-rate > mức này → cần chunking
ZERO_CANDIDATE_CONFOUND_RATIO = 0.30  # >30% combo 0-candidate → không tin Bộ C

_tokenizer = None  # lazy singleton, cùng pattern với rag/embedder.py


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer  # import trễ, tránh tải khi --smoke-test

        _tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
    return _tokenizer


def is_truncated(text: str) -> bool:
    """True nếu text vượt quá MAX_SEQ_LENGTH token theo đúng tokenizer
    của model embedding thật (không phải ước lượng bằng số từ)."""
    if not text:
        return False
    n_tokens = len(_get_tokenizer().encode(text, add_special_tokens=True))
    return n_tokens > MAX_SEQ_LENGTH


@dataclass
class SuiteAResult:
    precisions: list[float] = field(default_factory=list)
    recalls: list[float] = field(default_factory=list)
    rr: list[float] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    skipped_empty_ground_truth: int = 0
    zero_candidate_combos: int = 0  # có ground truth thật nhưng Chroma trả về 0 hits


@dataclass
class TruncationDebtResult:
    hits_short: int = 0
    total_short: int = 0
    hits_long: int = 0
    total_long: int = 0

    @property
    def hit_rate_short(self) -> float:
        return self.hits_short / self.total_short if self.total_short else float("nan")

    @property
    def hit_rate_long(self) -> float:
        return self.hits_long / self.total_long if self.total_long else float("nan")

    @property
    def gap(self) -> float:
        """hit_rate_short - hit_rate_long. Dương lớn = truncation đang gây hại thật."""
        if self.total_short == 0 or self.total_long == 0:
            return float("nan")
        return self.hit_rate_short - self.hit_rate_long


async def run_suite_a_and_c(
    pool: asyncpg.Pool, rerank: bool
) -> tuple[SuiteAResult, TruncationDebtResult]:
    suite_a = SuiteAResult()
    suite_c = TruncationDebtResult()

    for coin in EVAL_COINS:
        for h in EVAL_WINDOWS_HOURS:
            relevant = await get_ground_truth_with_text(pool, coin, h)
            if not relevant:
                # Không phải bug — nghĩa là corpus không có tin coin này
                # trong cửa sổ đó. Bỏ qua, không tính là recall=0 giả tạo.
                suite_a.skipped_empty_ground_truth += 1
                continue
            relevant_ids = {doc_id for doc_id, _ in relevant}

            for template in QUERY_TEMPLATES:
                query = template.format(coin=coin, h=h)

                t0 = time.perf_counter()
                hits = search_telegram_news(
                    query=query,
                    coin=coin,
                    hours_ago=h,
                    top_k=TOP_K,
                    rerank=rerank,
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000

                retrieved_ids = [
                    (item["metadata"]["channel"], item["msg_id"]) for item in hits
                ]
                if not retrieved_ids:
                    # Ground truth CÓ thật (đã qua check ở trên) nhưng
                    # Chroma trả về 0 candidate — dấu hiệu embedding lag
                    # (xem diag_ground_truth_gap.py), KHÔNG PHẢI bằng
                    # chứng "retriever xếp hạng kém". Đếm riêng để Bộ C
                    # không âm thầm bị nhiễu bởi số này.
                    suite_a.zero_candidate_combos += 1

                # Bộ A
                suite_a.precisions.append(precision_at_k(retrieved_ids, relevant_ids))
                suite_a.recalls.append(recall_at_k(retrieved_ids, relevant_ids))
                suite_a.rr.append(reciprocal_rank(retrieved_ids, relevant_ids))
                suite_a.latencies_ms.append(elapsed_ms)

                # Bộ C — dùng chung kết quả retrieval vừa gọi ở trên
                retrieved_id_set = set(retrieved_ids)
                for doc_id, text in relevant:
                    hit = doc_id in retrieved_id_set
                    if is_truncated(text):
                        suite_c.total_long += 1
                        suite_c.hits_long += int(hit)
                    else:
                        suite_c.total_short += 1
                        suite_c.hits_short += int(hit)

    return suite_a, suite_c


# ---------------------------------------------------------------------------
# Bộ B — Hand-labeled (kế thừa Ngày 24-25)
# ---------------------------------------------------------------------------


@dataclass
class SuiteBResult:
    n_correct: int = 0
    n_total: int = 0
    failures: list[str] = field(default_factory=list)
    skipped: bool = False
    # (query, score, đoạn document top-1) — không pass/fail, Pan tự chấm
    slang_probe: list[tuple[str, float, str]] = field(default_factory=list)


def run_suite_b(rerank: bool) -> SuiteBResult:
    result = SuiteBResult()

    if not rerank:
        # rerank_score = None khi rerank=False (đã xác nhận qua traceback
        # thật 2026-08-13). Bộ B đo HÀNH VI RERANKER cụ thể — không có ý
        # nghĩa khi tắt rerank.
        result.skipped = True
        return result

    for query in NEGATIVE_CONTROLS:
        hits = search_telegram_news(
            query=query,
            coin=None,
            hours_ago=24 * 30,  # mở rộng window, đây là test semantic không phải filter
            top_k=1,
            rerank=rerank,
        )
        top1_score = hits[0]["rerank_score"] if hits else 0.0

        result.n_total += 1
        if top1_score < LOW_SCORE_THRESHOLD:
            result.n_correct += 1
        else:
            result.failures.append(
                f"'{query}' → score={top1_score:.4f} (kỳ vọng thấp, đã verify "
                f"mmarco N24-25 mục 9)"
            )

    for query in SLANG_QUERIES:
        hits = search_telegram_news(
            query=query,
            coin=None,
            hours_ago=24 * 30,
            top_k=1,
            rerank=rerank,
        )
        if hits:
            score = hits[0]["rerank_score"]
            snippet = (hits[0]["document"] or "")[:100].replace("\n", " ")
        else:
            score, snippet = 0.0, "(không có kết quả)"
        result.slang_probe.append((query, score, snippet))

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _safe_mean(values: list[float]) -> float:
    clean = [v for v in values if v == v]  # lọc NaN
    return sum(clean) / len(clean) if clean else float("nan")


async def main() -> None:
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)

    mlflow.set_experiment("Crypto_Agent_Retrieval_Eval_Day26")

    # BẮT BUỘC: warm_up() trước khi đo latency, nếu không thì lần gọi đầu
    # tiên sẽ tính luôn cả thời gian lazy-load embedder+reranker (~25-30s,
    # đã thấy thật trong log --smoke-test: vector_ms=25767 rerank_ms=9878)
    # vào latency_ms_mean, làm con số vô nghĩa. Xem GHI_CHU_NGAY24_25.md
    # mục 4.1. Nếu warm_up() không tồn tại đúng tên này, lỗi sẽ raise rõ
    # ràng ngay đây — sửa tên hàm rồi chạy lại (VERIFY, xem đầu file).
    print("Đang warm_up() embedder + reranker (chỉ cần làm 1 lần)...")
    t_warmup = time.perf_counter()
    warm_up()
    print(f"warm_up() xong sau {(time.perf_counter() - t_warmup):.1f}s\n")

    try:
        for rerank in (True, False):
            run_name = f"rerank={rerank}"
            with mlflow.start_run(run_name=run_name):
                mlflow.log_param("rerank", rerank)
                mlflow.log_param("top_k", TOP_K)
                mlflow.log_param("eval_coins", ",".join(EVAL_COINS))

                suite_a, suite_c = await run_suite_a_and_c(pool, rerank)
                mlflow.log_metric("precision_at_k", _safe_mean(suite_a.precisions))
                mlflow.log_metric("recall_at_k", _safe_mean(suite_a.recalls))
                mlflow.log_metric("mrr", _safe_mean(suite_a.rr))
                mlflow.log_metric("latency_ms_mean", _safe_mean(suite_a.latencies_ms))
                mlflow.log_metric(
                    "n_skipped_empty_ground_truth", suite_a.skipped_empty_ground_truth
                )
                mlflow.log_metric(
                    "n_zero_candidate_combos", suite_a.zero_candidate_combos
                )

                suite_b = run_suite_b(rerank)
                if not suite_b.skipped:
                    accuracy_b = (
                        suite_b.n_correct / suite_b.n_total if suite_b.n_total else 0.0
                    )
                    mlflow.log_metric("negative_control_accuracy", accuracy_b)
                    mlflow.log_metric(
                        "negative_control_n_failures", len(suite_b.failures)
                    )
                    if suite_b.slang_probe:
                        slang_mean = sum(s for _, s, _ in suite_b.slang_probe) / len(
                            suite_b.slang_probe
                        )
                        mlflow.log_metric("slang_score_mean_no_baseline", slang_mean)

                mlflow.log_metric("hit_rate_short_docs", suite_c.hit_rate_short)
                mlflow.log_metric("hit_rate_truncated_docs", suite_c.hit_rate_long)
                mlflow.log_metric("n_short_docs", suite_c.total_short)
                mlflow.log_metric("n_truncated_docs", suite_c.total_long)

                print(f"\n=== rerank={rerank} ===")
                print(
                    f"Suite A (auto-labeled, n={len(suite_a.precisions)}): "
                    f"P@{TOP_K}={_safe_mean(suite_a.precisions):.3f}  "
                    f"R@{TOP_K}={_safe_mean(suite_a.recalls):.3f}  "
                    f"MRR={_safe_mean(suite_a.rr):.3f}  "
                    f"latency={_safe_mean(suite_a.latencies_ms):.0f}ms"
                )
                if suite_b.skipped:
                    print(
                        "Suite B: BỎ QUA (rerank=False → không có rerank_score, "
                        "regression gate này chỉ áp dụng khi rerank=True)."
                    )
                else:
                    print(
                        f"Suite B — negative control (n={suite_b.n_total}): "
                        f"accuracy={accuracy_b:.2%}"
                    )
                    if suite_b.failures:
                        print("  Regressions (đã verify mmarco N24-25):")
                        for f_ in suite_b.failures:
                            print(f"    - {f_}")
                    print(
                        "Suite B — tiếng lóng (CHƯA có baseline mmarco, "
                        "Pan tự chấm bằng mắt, không tự động pass/fail):"
                    )
                    for query, score, snippet in suite_b.slang_probe:
                        print(f"    '{query}' → score={score:.4f}  doc='{snippet}...'")
                n_combos = len(suite_a.precisions)
                zero_ratio = (
                    suite_a.zero_candidate_combos / n_combos if n_combos else 0.0
                )
                confounded = zero_ratio > ZERO_CANDIDATE_CONFOUND_RATIO
                mlflow.log_metric("zero_candidate_ratio", zero_ratio)
                mlflow.log_metric("suite_c_confounded", int(confounded))

                print(
                    f"Suite C — nợ #1 truncation debt: "
                    f"hit_rate(short, n={suite_c.total_short})="
                    f"{suite_c.hit_rate_short:.3f}  "
                    f"hit_rate(truncated, n={suite_c.total_long})="
                    f"{suite_c.hit_rate_long:.3f}  gap={suite_c.gap:.3f}"
                )
                if confounded:
                    print(
                        f"  ⚠️  {suite_a.zero_candidate_combos}/{n_combos} combo "
                        f"({zero_ratio:.0%}) có ground truth thật nhưng Chroma trả "
                        f"về 0 candidate (embedding lag / dag_embed_messages — xem "
                        f"diag_ground_truth_gap.py). KHÔNG kết luận chunking từ số "
                        f"gap phía trên — kết luận đang bị nhiễu bởi lag, không phải "
                        f"bằng chứng thật về truncation."
                    )
                elif suite_c.gap == suite_c.gap:  # not NaN
                    if suite_c.gap > TRUNCATION_DEBT_GAP_THRESHOLD:
                        print(
                            f"  → gap > {TRUNCATION_DEBT_GAP_THRESHOLD}: truncation "
                            f"ĐANG gây mất recall thật. Mở task chunking, đặt deadline."
                        )
                    else:
                        print(
                            f"  → gap <= {TRUNCATION_DEBT_GAP_THRESHOLD}: không thấy "
                            f"bằng chứng cần chunking. Có thể đóng nợ #1."
                        )
                else:
                    print(
                        "  → không đủ dữ liệu (thiếu nhóm short hoặc long) để kết luận."
                    )
    finally:
        await pool.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Chỉ gọi search_telegram_news() 1 lần để verify tên field, "
        "không chạy full eval, không cần Postgres.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.smoke_test:
        run_smoke_test()
        sys.exit(0)
    asyncio.run(main())
