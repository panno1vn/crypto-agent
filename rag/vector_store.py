"""
rag/vector_store.py

Ngày 22 — Kết nối ChromaDB và quản lý collection.
Ngày 23 — Thêm verify enrich_fingerprint (cùng nguyên tắc với hnsw:space).

BẪY LỚN NHẤT CỦA NGÀY 22, đã kiểm chứng thực tế trên chromadb 1.5.9:

    a = client.create_collection("x")                    # space = l2
    b = client.get_or_create_collection(
            "x", configuration={"hnsw": {"space": "cosine"}})
    b.configuration["hnsw"]["space"]                     # -> 'l2'

`get_or_create_collection` BỎ QUA toàn bộ tham số cấu hình nếu collection
đã tồn tại, và không hề cảnh báo. Nghĩa là: chạy sai một lần rồi sửa code
cho đúng vẫn KHÔNG sửa được collection — nó vĩnh viễn là l2 cho tới khi
bị xóa đi tạo lại.

Vì vậy `get_collection()` bên dưới luôn đọc ngược lại cấu hình thật từ
server và ném lỗi nếu không phải cosine, thay vì tin vào tham số đã truyền.

Ghi chú về API: cả `configuration={"hnsw": {"space": "cosine"}}` (cách mới,
Chroma 1.x) lẫn `metadata={"hnsw:space": "cosine"}` (cách cũ) đều còn chạy
trên 1.5.9. Dùng cách mới vì đó là API được tài liệu hóa hiện hành.

NGÀY 23 — cùng một lỗ hổng loại "tham số truyền vào bị bỏ qua trong im
lặng" có thể xảy ra với cấu hình ENRICH (ENRICH_INCLUDE_TIME/ENGAGEMENT):
nếu container Airflow thiếu biến môi trường này, nó rơi về default khác
với .env local, và toàn bộ tin backfill qua DAG sẽ có định dạng text
khác với tin backfill qua CLI — không có lỗi, không có cảnh báo, chỉ có
retrieval chất lượng kém dần. `get_enrich_fingerprint()` (rag/config.py)
đóng gói cấu hình đó thành 1 chuỗi, lưu vào metadata CẤP COLLECTION
(khác `documents`/`metadatas` cấp record), và được xác minh ngược mỗi
lần `get_collection()` được gọi — đúng pattern đã dùng cho hnsw:space.
"""

from __future__ import annotations

from typing import Any

from data_pipeline.logger import get_logger
from rag.config import (
    CHROMA_COLLECTION,
    CHROMA_HOST,
    CHROMA_PORT,
    HNSW_SPACE,
    get_enrich_fingerprint,
)

logger = get_logger(__name__)


class VectorSpaceMismatchError(RuntimeError):
    """Collection tồn tại nhưng dùng hàm khoảng cách sai."""


class EnrichFingerprintMismatchError(RuntimeError):
    """Collection tồn tại nhưng được embed bằng cấu hình enrich khác."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def get_chroma_client(host: str = CHROMA_HOST, port: int = CHROMA_PORT):
    """
    Tạo HttpClient trỏ tới Chroma server trong Docker Compose.

    Args:
        host: Hostname. 'localhost' khi chạy từ WSL2, 'chroma' trong container.
        port: Cổng HTTP của Chroma server.

    Returns:
        chromadb.HttpClient đã kiểm tra kết nối bằng heartbeat.

    Raises:
        ConnectionError: Nếu không heartbeat được (thường là chưa
            `docker compose up -d chroma`).
    """
    import chromadb

    client = chromadb.HttpClient(host=host, port=port)
    try:
        client.heartbeat()
    except Exception as e:
        raise ConnectionError(
            f"Không kết nối được ChromaDB tại {host}:{port}. "
            f"Kiểm tra `docker compose ps chroma`. Lỗi gốc: {e}"
        ) from e

    logger.info(f"[CHROMA] Connected to {host}:{port}")
    return client


# ---------------------------------------------------------------------------
# Đọc ngược cấu hình thật — hnsw:space
# ---------------------------------------------------------------------------
def read_hnsw_space(collection) -> "str | None":
    """
    Đọc hàm khoảng cách THẬT mà server đang dùng cho collection.

    Không tin vào tham số đã truyền lúc tạo — đọc từ
    `collection.configuration` do server trả về.

    Args:
        collection: Đối tượng Collection của Chroma.

    Returns:
        'cosine' | 'l2' | 'ip', hoặc None nếu không xác định được
        (ví dụ collection dùng index SPANN thay vì HNSW).
    """
    config: Any = getattr(collection, "configuration", None)
    if not config:
        return None

    hnsw = config.get("hnsw") if hasattr(config, "get") else None
    if not hnsw:
        return None

    space = hnsw.get("space") if hasattr(hnsw, "get") else None
    return str(space) if space else None


# ---------------------------------------------------------------------------
# Đọc ngược cấu hình thật — enrich fingerprint (Ngày 23)
# ---------------------------------------------------------------------------
def read_enrich_fingerprint(collection) -> "str | None":
    """
    Đọc fingerprint cấu hình enrich đã lưu ở metadata CẤP COLLECTION
    (khác metadata cấp record trong build_metadata()).

    Returns:
        Chuỗi fingerprint đã lưu, hoặc None nếu collection chưa từng
        được gán (tạo trước khi có cơ chế này, hoặc modify() thất bại).
    """
    meta: Any = getattr(collection, "metadata", None)
    if not meta:
        return None
    return meta.get("enrich_fingerprint")


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def get_collection(
    client=None,
    name: str = CHROMA_COLLECTION,
    space: str = HNSW_SPACE,
    create: bool = True,
    verify_fingerprint: bool = True,
):
    """
    Lấy (hoặc tạo) collection, XÁC MINH hàm khoảng cách VÀ cấu hình enrich.

    `embedding_function=None` là chủ ý: project tự sinh embedding bằng
    SBERT 768 chiều. Nếu để Chroma tự gắn embedding function mặc định,
    mọi lời gọi `query_texts=` sẽ âm thầm dùng model 384 chiều khác hẳn.
    Quy tắc cứng của project: LUÔN truyền `embeddings=` khi add và
    `query_embeddings=` khi query, KHÔNG bao giờ dùng `documents=` đơn lẻ
    hay `query_texts=`.

    Args:
        client:             Chroma client. None = tự tạo HttpClient từ config.
        name:               Tên collection.
        space:              Hàm khoảng cách mong muốn.
        create:             True = tạo nếu chưa có. False = chỉ lấy, lỗi nếu chưa có.
        verify_fingerprint: True = xác minh enrich_fingerprint (Ngày 23).
                             Tắt khi cần thao tác thấp cấp (vd reset_collection).

    Returns:
        Collection đã xác minh dùng đúng `space` và đúng enrich_fingerprint.

    Raises:
        VectorSpaceMismatchError:    Nếu collection đã tồn tại với space khác.
        EnrichFingerprintMismatchError: Nếu collection đã tồn tại với cấu
            hình enrich khác (vd .env thiếu biến trong container).
    """
    if client is None:
        client = get_chroma_client()

    expected_fp = get_enrich_fingerprint()

    if create:
        collection = client.get_or_create_collection(
            name=name,
            configuration={"hnsw": {"space": space}},
            # `metadata` cũng bị BỎ QUA nếu collection đã tồn tại — giống
            # hệt bẫy của `configuration`. Không tin giá trị này, luôn
            # đọc ngược bằng read_enrich_fingerprint() bên dưới.
            metadata={"enrich_fingerprint": expected_fp},
            embedding_function=None,
        )
    else:
        collection = client.get_collection(name=name, embedding_function=None)

    # --- Xác minh hnsw:space ---
    actual_space = read_hnsw_space(collection)

    if actual_space is None:
        logger.warning(
            f"[CHROMA] Không đọc được hnsw space của '{name}'. "
            f"configuration={getattr(collection, 'configuration', None)}"
        )
    elif actual_space != space:
        raise VectorSpaceMismatchError(
            f"Collection '{name}' đang dùng space='{actual_space}', "
            f"cần '{space}'. get_or_create_collection() KHÔNG sửa được "
            f"cấu hình của collection đã tồn tại. Phải xóa và tạo lại:\n"
            f'    python -c "from rag.vector_store import '
            f"get_chroma_client; "
            f"get_chroma_client().delete_collection('{name}')\"\n"
            f"rồi chạy lại ingestion để embed lại từ đầu."
        )

    # --- Xác minh enrich_fingerprint (Ngày 23) ---
    if verify_fingerprint:
        actual_fp = read_enrich_fingerprint(collection)
        count = collection.count()

        if actual_fp is None:
            if count == 0:
                # Collection rỗng, chưa từng track fingerprint (thường là
                # do get_or_create_collection() bỏ qua metadata lần tạo
                # đầu tiên) -> gán ngay bằng modify(), an toàn vì chưa có
                # document nào embed bằng config nào cả.
                try:
                    collection.modify(metadata={"enrich_fingerprint": expected_fp})
                    logger.info(f"[CHROMA] Gán enrich_fingerprint: {expected_fp}")
                except Exception as e:
                    logger.warning(f"[CHROMA] Không gán được enrich_fingerprint: {e}")
            else:
                logger.warning(
                    f"[CHROMA] Collection '{name}' có {count} document "
                    f"nhưng KHÔNG có enrich_fingerprint (tạo trước khi có "
                    f"cơ chế này, vd smoke test Ngày 22). KHÔNG tự động "
                    f"tin đây là cấu hình đúng. Kiểm tra tay bằng "
                    f"`collection.peek(limit=1)` xem text có lẫn "
                    f"'[Time:'/'[Engagement:' không, hoặc gọi "
                    f"reset_collection('{name}') để bắt đầu sạch trước "
                    f"khi backfill toàn bộ."
                )
        elif actual_fp != expected_fp:
            raise EnrichFingerprintMismatchError(
                f"Collection '{name}' được embed bằng cấu hình enrich "
                f"khác:\n"
                f"  đã lưu  : {actual_fp}\n"
                f"  hiện tại: {expected_fp}\n"
                f"Nguyên nhân thường gặp: biến môi trường "
                f"ENRICH_INCLUDE_TIME/ENRICH_INCLUDE_ENGAGEMENT thiếu ở "
                f"nơi gọi (vd container Airflow không khai trong "
                f"`environment:` của docker-compose.yml), rơi về default "
                f"'true' khác với .env local 'false'. Sửa cấu hình cho "
                f"khớp rồi chạy lại. Nếu đổi có chủ đích: "
                f"reset_collection('{name}') rồi embed lại từ đầu."
            )

    logger.info(
        f"[CHROMA] Collection '{name}' ready "
        f"(space={actual_space}, count={collection.count()}, "
        f"fingerprint={'ok' if verify_fingerprint else 'skipped'})"
    )

    return collection


def reset_collection(client=None, name: str = CHROMA_COLLECTION):
    """
    Xóa và tạo lại collection với cấu hình đúng (space + enrich_fingerprint).

    Dùng khi `VectorSpaceMismatchError`/`EnrichFingerprintMismatchError`
    xảy ra, hoặc khi đổi model embedding (số chiều khác → phải embed lại
    toàn bộ).

    CẢNH BÁO: thao tác này xóa sạch dữ liệu, không hoàn tác được.
    """
    if client is None:
        client = get_chroma_client()

    try:
        client.delete_collection(name=name)
        logger.warning(f"[CHROMA] Đã XÓA collection '{name}'")
    except Exception as e:
        logger.info(f"[CHROMA] Không có collection '{name}' để xóa ({e})")

    return get_collection(client=client, name=name, create=True)
