"""
rag/config.py

Ngày 22 — Cấu hình tập trung cho RAG pipeline.

Mọi giá trị đều đọc từ biến môi trường, có default an toàn cho local.
Lý do tách file riêng: `rag/ingestion.py`, `rag/vector_store.py` và DAG
Ngày 23 đều cần cùng bộ giá trị này — tránh hardcode 3 chỗ rồi lệch nhau.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# ChromaDB
# ---------------------------------------------------------------------------
# Trong container Airflow: CHROMA_HOST=chroma (tên service docker-compose)
# Chạy local từ WSL2:      CHROMA_HOST=localhost
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))

# Tên collection phải khớp regex của Chroma: 3-512 ký tự [a-zA-Z0-9._-],
# bắt đầu và kết thúc bằng chữ/số.
CHROMA_COLLECTION: str = os.getenv("CHROMA_COLLECTION", "telegram_messages")

# Hàm khoảng cách của HNSW index.
# QUAN TRỌNG: Chroma mặc định là "l2". Với sentence embeddings phải dùng
# "cosine". Xem thêm ghi chú trong rag/vector_store.py.
HNSW_SPACE: str = "cosine"


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------
EMBEDDING_MODEL: str = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
)

# XLM-RoBERTa base → 768 chiều. Dùng để assert, không phải để cấu hình.
EMBEDDING_DIM: int = 768

EMBEDDING_DEVICE: str = os.getenv("EMBEDDING_DEVICE", "cpu")
EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))

# SBERT set sẵn max_seq_length=128 cho model này. Đặt None để giữ nguyên.
# Nếu muốn thử 256 (XLM-R hỗ trợ tới 512), set env EMBEDDING_MAX_SEQ_LEN.
_max_seq = os.getenv("EMBEDDING_MAX_SEQ_LEN")
EMBEDDING_MAX_SEQ_LEN: "int | None" = int(_max_seq) if _max_seq else None


# ---------------------------------------------------------------------------
# Enricher — thành phần nào được nhét vào TEXT đem đi embed
# ---------------------------------------------------------------------------
# Cảnh báo: model chỉ nhìn 128 token đầu. Mỗi field prefix thêm vào là một
# phần ngân sách token bị lấy khỏi nội dung tin nhắn thật.
# Channel/Coins có giá trị ngữ nghĩa; Time/Engagement thì gần như không —
# chúng đã nằm trong metadata và được lọc bằng `where` rồi.
# Dùng scripts/manual/measure_enrich_tokens.py để đo trước khi quyết định.
ENRICH_INCLUDE_TIME: bool = os.getenv("ENRICH_INCLUDE_TIME", "true").lower() == "true"
ENRICH_INCLUDE_ENGAGEMENT: bool = (
    os.getenv("ENRICH_INCLUDE_ENGAGEMENT", "true").lower() == "true"
)


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
# Số dòng đọc từ Postgres mỗi vòng lặp (không phải batch size của embedder).
INGEST_PAGE_SIZE: int = int(os.getenv("INGEST_PAGE_SIZE", "500"))

# Số record đẩy lên Chroma mỗi lần upsert. Chroma có giới hạn payload,
# 500 là mức an toàn cho vector 768 chiều.
CHROMA_UPSERT_BATCH: int = int(os.getenv("CHROMA_UPSERT_BATCH", "500"))


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
def get_db_dsn() -> str:
    """
    Dựng DSN Postgres từ các biến môi trường rời rạc trong .env.

    Giữ đúng pattern đang dùng ở `historical_scraper.main()` và
    `realtime_listener.main()` để không phát sinh nguồn sự thật thứ hai.

    Returns:
        Chuỗi DSN dạng postgresql://user:pass@host:port/dbname
    """
    return (
        f"postgresql://{os.environ['POSTGRES_USER']}"
        f":{os.environ['POSTGRES_PASSWORD']}"
        f"@{os.environ.get('POSTGRES_HOST', 'localhost')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'crypto_agent')}"
    )


# ---------------------------------------------------------------------------
# Fingerprint enrich config — Ngày 23
# ---------------------------------------------------------------------------
# Bump thủ công khi ĐỔI LOGIC enrich (thêm/bớt field, đổi format chuỗi
# trong enrich_message_for_embedding). KHÔNG cần bump khi chỉ đổi cờ
# true/false — 2 cờ đó đã tự nằm trong fingerprint runtime bên dưới.
ENRICH_CODE_VERSION: str = "v1"


def get_enrich_fingerprint() -> str:
    """
    Chuỗi định danh cho TOÀN BỘ cấu hình ảnh hưởng tới nội dung text đem
    đi embed: model, các cờ include, max_seq_len, version code.

    Tự động phản ánh giá trị RUNTIME thật (đọc qua các hằng số ở trên tại
    thời điểm import) — không hardcode. Nếu nơi gọi (vd container Airflow)
    thiếu biến môi trường ENRICH_INCLUDE_TIME/ENRICH_INCLUDE_ENGAGEMENT
    và rơi về default "true" trong khi .env local là "false", fingerprint
    sẽ tự khác nhau. `get_collection()` dùng giá trị này để chặn việc
    trộn 2 định dạng text trong cùng collection, thay vì âm thầm cho qua.
    """
    return (
        f"{ENRICH_CODE_VERSION}"
        f"|model={EMBEDDING_MODEL}"
        f"|time={ENRICH_INCLUDE_TIME}"
        f"|eng={ENRICH_INCLUDE_ENGAGEMENT}"
        f"|maxlen={EMBEDDING_MAX_SEQ_LEN}"
    )


# ---------------------------------------------------------------------------
# Reranker — Ngày 24-25
# ---------------------------------------------------------------------------
# QUYẾT ĐỊNH ĐẢO 2 LẦN, đây là lần chốt cuối (Ngày 25) dựa trên bằng chứng
# đầy đủ nhất — xem GHI_CHU_NGAY24.md mục 8 để đọc toàn bộ quá trình,
# kể cả các lần AI đoán sai giữa chừng.
#
# Vòng 1-2: chọn ms-marco-MiniLM-L-6-v2 (thuần Anh) vì margin cao hơn và
# robust hơn với cognate ablation TRÊN BỘ TEST HẸP (2 negative control).
#
# Vòng 3 (Ngày 25): mở rộng lên 7 negative control đa dạng khung câu, phát
# hiện ms-marco-MiniLM-L-6-v2 cho điểm 0.9959 (gần tuyệt đối) trên câu HOÀN
# TOÀN không liên quan ("hướng dẫn học guitar cho người mới" khớp nhầm tin
# về học phân tích kỹ thuật do trùng KHUNG CÂU "...cho người mới học...",
# không phải trùng chủ đề). mmarco giữ điểm dưới 0.14 trên toàn bộ 7 câu.
#
# Đối chứng thêm: nghi vấn "mmarco lỗi recall khi paraphrase" (cognate
# ablation vòng 2) hóa ra KHÔNG phải nhược điểm riêng — msmarco-en đổi
# document y hệt (4/5 ca) khi test bằng đúng phép đo, chỉ khác là nó tự tin
# tuyệt đối (~0.95-1.0) dù đúng hay sai, trong khi mmarco tụt điểm theo khi
# kém chắc chắn — an toàn hơn cho một hệ thống cần biết khi nào nó KHÔNG
# chắc.
#
# Đánh đổi: chậm hơn ~400ms/query (953ms vs 540ms trung bình, đo Ngày 24).
# Cần đưa vào ngân sách latency khi thiết kế agent ReAct Ngày 33+.
#
# Phòng thủ BẮT BUỘC bất kể model nào: rerank_score KHÔNG được dùng làm
# cổng lọc độc lập theo ngưỡng tuyệt đối. Luôn kết hợp với
# require_coin_mentioned=True (filter coins_count > 0) làm điều kiện tiên
# quyết ở tầng gọi — đã verify thực nghiệm: document gây false positive
# 0.9959 của msmarco-en có coins_count=0, gate này chặn được.
RERANKER_MODEL: str = os.getenv(
    "RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
)
RERANKER_DEVICE: str = os.getenv("RERANKER_DEVICE", "cpu")
RERANKER_MAX_LENGTH: int = int(os.getenv("RERANKER_MAX_LENGTH", "512"))

# Số ứng viên lấy từ vector search TRƯỚC khi rerank.
RETRIEVAL_CANDIDATE_POOL: int = int(os.getenv("RETRIEVAL_CANDIDATE_POOL", "20"))
# Số kết quả trả về SAU khi rerank.
RETRIEVAL_TOP_K: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))
# ---------------------------------------------------------------------------
# News-Technical Confirmation — Ngày 27
# ---------------------------------------------------------------------------
# ⚠️ GIÁ TRỊ CHƯA CALIBRATE. Ngưỡng ±0.1 và confirm_boost/conflict_penalty
# +0.10/-0.15 là số BỊA của roadmap v1, giữ tạm làm default để chạy được —
# KHÔNG coi đây là số đã kiểm chứng.
#
# Trước khi tin dùng cho quyết định thật:
#   1. Chạy scripts/manual/dump_sentiment_distribution.py (30 ngày) để lấy
#      phân phối thật của mean_weighted_score, dùng percentile (p33/p67)
#      thay cho ±0.1 tròn.
#   2. confirm_boost/conflict_penalty: để N32 ablation quyết định giá trị
#      cuối (so sánh TA-only vs TA+sentiment vs TA+sentiment+news).
# Đọc từ env để đổi được mà không cần deploy lại code.
NEWS_CONFIRMATION_WINDOW_HOURS: float = float(
    os.getenv("NEWS_CONFIRMATION_WINDOW_HOURS", "6")
)
NEWS_CONFIRMATION_UPPER_THRESHOLD: float = float(
    os.getenv("NEWS_CONFIRMATION_UPPER_THRESHOLD", "0.1")
)
NEWS_CONFIRMATION_LOWER_THRESHOLD: float = float(
    os.getenv("NEWS_CONFIRMATION_LOWER_THRESHOLD", "-0.1")
)
NEWS_CONFIRMATION_CONFIRM_BOOST: float = float(
    os.getenv("NEWS_CONFIRMATION_CONFIRM_BOOST", "0.10")
)
NEWS_CONFIRMATION_CONFLICT_PENALTY: float = float(
    os.getenv("NEWS_CONFIRMATION_CONFLICT_PENALTY", "-0.15")
)
NEWS_CONFIRMATION_KEY_NEWS_COUNT: int = int(
    os.getenv("NEWS_CONFIRMATION_KEY_NEWS_COUNT", "3")
)
NEWS_CONFIRMATION_MIN_MESSAGE_COUNT: int = int(
    os.getenv("NEWS_CONFIRMATION_MIN_MESSAGE_COUNT", "1")
)
