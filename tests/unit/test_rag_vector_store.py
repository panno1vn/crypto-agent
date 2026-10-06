"""
tests/unit/test_rag_vector_store.py

Ngày 22 — Tests cho rag/vector_store.py

Dùng `chromadb.EphemeralClient()` (in-memory), KHÔNG cần server đang chạy.
Yêu cầu package `chromadb` bản đầy đủ; bản `chromadb-client` không có
EphemeralClient nên các test này sẽ tự skip.
"""

import uuid

import pytest

chromadb = pytest.importorskip("chromadb")

if not hasattr(chromadb, "EphemeralClient"):  # pragma: no cover
    pytest.skip(
        "Cần chromadb bản đầy đủ (không phải chromadb-client)",
        allow_module_level=True,
    )


from rag.vector_store import (  # noqa: E402
    VectorSpaceMismatchError,
    get_collection,
    read_hnsw_space,
)


def _unique_name(prefix: str) -> str:
    """
    Tên collection duy nhất mỗi lần gọi, KHÔNG hardcode.

    Bắt buộc vì chromadb.EphemeralClient() không cô lập dữ liệu theo
    lần gọi trong cùng process pytest (Chroma cache System theo hash
    settings — xác nhận thực nghiệm Ngày 24). Kết hợp với hành vi
    upsert() không xóa key metadata cũ khi bản ghi mới thiếu key đó,
    tên collection cố định giữa các test gây rò rỉ dữ liệu chéo, dẫn
    tới assertion fail trông giống bug ở build_metadata()/_build_where()
    trong khi bản chất là ô nhiễm test.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client():
    """
    EphemeralClient() MỚI mỗi test, nhưng chromadb cache System dùng
    chung theo hash(settings) — mặc định giống hệt nhau nên nhiều
    client "mới" trong CÙNG process pytest vẫn chia sẻ chung 1 System
    ngầm bên dưới.

    Với 1-3 client trong 1 script rời, việc này vô hại (verify bằng
    repro tay Ngày 24 — 3 nhánh A/B/C đều pass). Nhưng trong 1 phiên
    pytest đầy đủ (~20+ EphemeralClient() được tạo qua các test khác
    nhau trước khi tới đây), state tích tụ đủ để $contains trên
    metadata mảng trả kết quả sai — dù mỗi test dùng TÊN collection
    ngẫu nhiên riêng (_unique_name()).

    clear_system_cache() là API chính thức chromadb cung cấp cho đúng
    tình huống này (dùng trong bộ test nội bộ của họ) — ép mỗi test
    nhận System hoàn toàn mới, không kế thừa gì từ test trước.
    """
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    return chromadb.EphemeralClient()


def test_collection_moi_dung_cosine(client):
    col = get_collection(client=client, name=_unique_name("test-cosine"))
    assert read_hnsw_space(col) == "cosine"


def test_get_collection_lan_hai_van_cosine(client):
    """Gọi lại lần hai không được làm hỏng cấu hình."""
    get_collection(client=client, name=_unique_name("test-idempotent"))
    col = get_collection(client=client, name=_unique_name("test-idempotent"))
    assert read_hnsw_space(col) == "cosine"


def test_phat_hien_collection_l2_co_san(client):
    """
    Đây là bẫy chính của Ngày 22.

    `get_or_create_collection` BỎ QUA tham số `configuration` nếu
    collection đã tồn tại — không lỗi, không cảnh báo. Một lần tạo nhầm
    với space mặc định (l2) là vĩnh viễn sai cho tới khi xóa đi tạo lại.

    `get_collection()` phải phát hiện và ném lỗi, không được im lặng.
    """
    unique_name = _unique_name("test-legacy-l2")
    client.create_collection(unique_name, embedding_function=None)

    with pytest.raises(VectorSpaceMismatchError, match="l2"):
        get_collection(client=client, name=unique_name)


def test_read_hnsw_space_tra_ve_none_khi_khong_doc_duoc():
    class FakeCollection:
        configuration = None

    assert read_hnsw_space(FakeCollection()) is None


def test_metadata_that_duoc_chroma_chap_nhan(client):
    """
    Kiểm tra `build_metadata()` sinh ra dict mà Chroma thật sự nhận,
    và `$contains` trên mảng coins hoạt động đúng.
    """
    from datetime import datetime, timezone

    from data_pipeline.telegram.historical_scraper import TelegramMessage
    from rag.enricher import build_metadata

    col = get_collection(client=client, name=_unique_name("test-metadata"))

    msgs = [
        TelegramMessage(
            id=1,
            channel_name="chan_a",
            message_text="BTC đang tăng mạnh trong phiên hôm nay nhé.",
            language="vi",
            created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            coins_mentioned=["BTC"],
        ),
        TelegramMessage(
            id=2,
            channel_name="chan_b",
            message_text="Tin thị trường chung không nhắc coin nào cả.",
            language=None,
            created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
            coins_mentioned=[],
        ),
    ]

    col.upsert(
        ids=[str(m.id) for m in msgs],
        embeddings=[[0.1] * 768, [0.2] * 768],
        documents=["doc1", "doc2"],
        metadatas=[build_metadata(m) for m in msgs],
    )

    assert col.count() == 2
    assert col.get(where={"coins": {"$contains": "BTC"}})["ids"] == ["1"]
    assert col.get(where={"coins_count": {"$gt": 0}})["ids"] == ["1"]

    ts = int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp())
    assert col.get(where={"created_ts": {"$gte": ts}})["ids"] == ["1"]


def test_upsert_ghi_de_con_add_thi_khong(client):
    """
    `add()` với id trùng bị bỏ qua trong im lặng — sửa enricher rồi chạy
    lại `add()` sẽ KHÔNG cập nhật gì mà vẫn báo thành công.
    Đó là lý do ingestion.py dùng `upsert()`.
    """
    col = get_collection(client=client, name=_unique_name("test-upsert"))
    emb = [[0.5] * 768]

    col.add(ids=["x"], embeddings=emb, documents=["ban cu"])
    col.add(ids=["x"], embeddings=emb, documents=["ban moi qua add"])
    assert col.get(ids=["x"])["documents"] == ["ban cu"]

    col.upsert(ids=["x"], embeddings=emb, documents=["ban moi qua upsert"])
    assert col.get(ids=["x"])["documents"] == ["ban moi qua upsert"]


def test_upsert_khong_xoa_key_cu_khi_metadata_moi_thieu_key(client):
    """
    PIN LẠI hành vi thật của chromadb==1.5.9, xác nhận thực nghiệm Ngày 24:
    upsert() lên ID ĐÃ TỒN TẠI không thay thế toàn bộ metadata theo kiểu
    PUT — nó merge/patch, giữ lại key cũ nếu metadata mới không có key đó.

    Rủi ro thật: nếu sau này re-embed 1 tin đã có trong Chroma (VD sau khi
    sửa lại extract_coins() và coins_mentioned đổi từ ["BTC"] thành []),
    key "coins" cũ sẽ SỐNG SÓT trong Chroma dù build_metadata() bản mới
    đúng đắn không đưa key đó vào — record trông như còn nhắc BTC dù
    logic hiện tại nói là không.

    Chính bug này (chưa có test pin lại) từng làm
    test_metadata_that_duoc_chroma_chap_nhan fail sai chỗ Ngày 24: 2 test
    dùng chung tên collection cố định "test-metadata" khiến id="2" của
    lần chạy trước rò rỉ sang lần chạy sau. Đã fix bằng _unique_name(),
    nhưng bug THẬT ở tầng Chroma vẫn còn — test này giữ nó không bị quên.

    Nếu chromadb đổi hành vi này ở bản sau (thành đúng PUT semantics),
    test này sẽ ĐỎ — đó là tín hiệu tốt, không phải regression.
    """
    col = get_collection(client=client, name=_unique_name("test-upsert-merge"))

    col.upsert(
        ids=["x"],
        embeddings=[[0.1] * 768],
        documents=["d"],
        metadatas=[{"coins": ["ETH"], "msg_id": 99}],
    )

    # Re-upsert CÙNG ID, metadata mới KHÔNG có "coins"
    col.upsert(
        ids=["x"], embeddings=[[0.1] * 768], documents=["d"], metadatas=[{"msg_id": 99}]
    )

    result = col.get(ids=["x"])["metadatas"][0]
    assert "coins" in result, (
        "chromadb đã đổi hành vi upsert() sang PUT semantics thật — "
        "cập nhật lại cảnh báo trong rag/ingestion.py, bug này không "
        "còn tồn tại."
    )
