"""
tests/unit/test_rag_chroma_ids.py

Nợ #15 (2026-10-08): id Chroma phải gồm kênh. Id trần str(msg_id) làm upsert
của kênh B ghi đè tin kênh A cùng msg_id. Collection embed trước migration
(id trần) phải bị chặn, không được trộn với id mới.
"""

import uuid

import pytest

from rag.ingestion import (
    LegacyChromaIdError,
    chroma_id,
    get_last_embedded_id_for_channel,
)

chromadb = pytest.importorskip("chromadb")


@pytest.fixture
def collection():
    # Xem docstring fixture `client` trong test_rag_vector_store.py: phải
    # clear_system_cache() + tên ngẫu nhiên để test không nhiễm chéo.
    from chromadb.api.client import SharedSystemClient

    SharedSystemClient.clear_system_cache()
    return chromadb.EphemeralClient().create_collection(
        f"test-ids-{uuid.uuid4().hex[:8]}"
    )


def _add(col, ids, channels):
    col.upsert(
        ids=ids,
        embeddings=[[0.1, 0.2, 0.3]] * len(ids),
        documents=["d"] * len(ids),
        metadatas=[{"channel": c} for c in channels],
    )


def test_chroma_id_gom_kenh():
    assert chroma_id("kenh_a", 4500) == "kenh_a:4500"


def test_hai_kenh_cung_msg_id_khong_ghi_de(collection):
    _add(
        collection,
        [chroma_id("kenh_a", 4500), chroma_id("kenh_b", 4500)],
        ["kenh_a", "kenh_b"],
    )
    assert collection.count() == 2


def test_watermark_theo_kenh_doc_tu_id_moi(collection):
    _add(
        collection,
        [chroma_id("kenh_a", 7), chroma_id("kenh_a", 120), chroma_id("kenh_b", 900)],
        ["kenh_a", "kenh_a", "kenh_b"],
    )
    assert get_last_embedded_id_for_channel(collection, "kenh_a") == 120
    assert get_last_embedded_id_for_channel(collection, "kenh_b") == 900
    assert get_last_embedded_id_for_channel(collection, "kenh_khac") == 0


def test_collection_con_id_cu_bi_chan(collection):
    _add(collection, ["4500"], ["kenh_a"])
    with pytest.raises(LegacyChromaIdError, match="embed lại"):
        get_last_embedded_id_for_channel(collection, "kenh_a")
