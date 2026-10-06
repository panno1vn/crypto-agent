from __future__ import annotations

import time
import uuid
from typing import Any

import chromadb
import pytest

from rag.retriever import _build_where, search_telegram_news


# ---------------------------------------------------------------------------
# Fakes — thay model thật, giữ interface giống hệt
# ---------------------------------------------------------------------------
class FakeEmbedder:
    def encode(self, texts: list[str]):
        import numpy as np

        return np.array([[0.1, 0.2, 0.3, 0.4] for _ in texts])


class FakeReranker:
    def rerank(self, query, candidates, top_k, document_key="document"):
        reversed_candidates = list(reversed(candidates))
        for i, c in enumerate(reversed_candidates):
            c["rerank_score"] = float(len(reversed_candidates) - i)
        return reversed_candidates[:top_k]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def ephemeral_collection():
    """
    Collection Chroma tạm trong RAM, config cosine giống production.

    QUAN TRỌNG: chromadb.EphemeralClient() KHÔNG cô lập theo lần gọi như
    tên gọi "ephemeral" gợi ý. Chroma cache System theo hash(settings) —
    nhiều client tạo trong CÙNG process với settings mặc định giống hệt
    nhau sẽ CHIA SẺ chung 1 System ngầm bên dưới. Xác nhận bằng thực
    nghiệm Ngày 24: test đầu tiên tạo collection "test_retriever" thành
    công, mọi test sau đó (client MỚI, tưởng là cô lập) đều
    `InternalError: Collection already exists`.

    Fix: tên collection random mỗi test (uuid4) + xóa ở teardown, không
    dựa vào EphemeralClient tự cô lập.
    """
    client = chromadb.EphemeralClient()
    name = f"test_retriever_{uuid.uuid4().hex[:8]}"
    collection = client.create_collection(
        name=name,
        configuration={"hnsw": {"space": "cosine"}},
    )
    yield collection
    try:
        client.delete_collection(name)
    except Exception:
        pass  # test đã fail giữa chừng thì collection có thể chưa tồn tại


def _seed(collection, records: list[dict[str, Any]]) -> None:
    collection.upsert(
        ids=[r["id"] for r in records],
        embeddings=[r["embedding"] for r in records],
        documents=[r["document"] for r in records],
        metadatas=[r["metadata"] for r in records],
    )


def _meta(
    msg_id: int,
    coins: "list[str] | None" = None,
    hours_ago: float = 1.0,
    channel: str = "test_channel",
) -> dict[str, Any]:
    ts = int(time.time() - hours_ago * 3600)
    m = {
        "msg_id": msg_id,
        "channel": channel,
        "language": "vi",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_ts": ts,
        "views": 0,
        "forwards": 0,
        "coins_count": len(coins or []),
    }
    if coins:
        m["coins"] = coins
    return m


@pytest.fixture(autouse=True)
def patch_models(monkeypatch):
    monkeypatch.setattr("rag.retriever.get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr("rag.retriever.get_reranker", lambda: FakeReranker())


@pytest.fixture
def patch_collection(monkeypatch, ephemeral_collection):
    monkeypatch.setattr("rag.retriever.get_collection", lambda: ephemeral_collection)
    return ephemeral_collection


# ---------------------------------------------------------------------------
# _build_where
# ---------------------------------------------------------------------------
def test_build_where_no_filters_returns_none():
    assert _build_where(coin=None, hours_ago=None, require_coin_mentioned=False) is None


def test_build_where_only_hours_ago():
    where = _build_where(coin=None, hours_ago=24, require_coin_mentioned=False)
    assert set(where.keys()) == {"created_ts"}
    assert "$gte" in where["created_ts"]
    expected = time.time() - 24 * 3600
    assert abs(where["created_ts"]["$gte"] - expected) < 5


def test_build_where_only_coin_uppercases():
    where = _build_where(coin="eth", hours_ago=None, require_coin_mentioned=False)
    assert where == {"coins": {"$contains": "ETH"}}


def test_build_where_multiple_conditions_uses_and():
    where = _build_where(coin="BTC", hours_ago=24, require_coin_mentioned=False)
    assert "$and" in where
    assert len(where["$and"]) == 2
    assert {"coins": {"$contains": "BTC"}} in where["$and"]


def test_build_where_require_coin_mentioned_adds_clause():
    where = _build_where(coin=None, hours_ago=None, require_coin_mentioned=True)
    assert where == {"coins_count": {"$gt": 0}}


def test_build_where_three_conditions_all_anded():
    where = _build_where(coin="SOL", hours_ago=6, require_coin_mentioned=True)
    assert len(where["$and"]) == 3


# ---------------------------------------------------------------------------
# search_telegram_news
# ---------------------------------------------------------------------------
def test_search_empty_collection_returns_empty_no_crash(patch_collection):
    result = search_telegram_news("query bất kỳ", hours_ago=None)
    assert result == []


def test_search_candidate_pool_less_than_top_k_raises(patch_collection):
    with pytest.raises(ValueError):
        search_telegram_news("q", top_k=10, candidate_pool=5)


def test_search_filters_by_coin(patch_collection):
    _seed(
        patch_collection,
        [
            {
                "id": "1",
                "document": "tin về BTC",
                "metadata": _meta(1, coins=["BTC"]),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "2",
                "document": "tin về ETH",
                "metadata": _meta(2, coins=["ETH"]),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
        ],
    )
    result = search_telegram_news(
        "test", coin="ETH", hours_ago=None, top_k=5, candidate_pool=5
    )
    assert len(result) == 1
    assert result[0]["metadata"]["msg_id"] == 2


def test_search_filters_by_time(patch_collection):
    _seed(
        patch_collection,
        [
            {
                "id": "old",
                "document": "tin cũ",
                "metadata": _meta(1, hours_ago=48),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "new",
                "document": "tin mới",
                "metadata": _meta(2, hours_ago=1),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
        ],
    )
    result = search_telegram_news("test", hours_ago=6, top_k=5, candidate_pool=5)
    assert len(result) == 1
    assert result[0]["metadata"]["msg_id"] == 2


def test_search_require_coin_mentioned_excludes_zero_coins(patch_collection):
    _seed(
        patch_collection,
        [
            {
                "id": "no_coin",
                "document": "tin chung chung",
                "metadata": _meta(1, coins=None),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "has_coin",
                "document": "tin về BTC",
                "metadata": _meta(2, coins=["BTC"]),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
        ],
    )
    result = search_telegram_news(
        "test", hours_ago=None, require_coin_mentioned=True, top_k=5, candidate_pool=5
    )
    assert len(result) == 1
    assert result[0]["metadata"]["msg_id"] == 2


def test_search_rerank_false_skips_reranker(patch_collection):
    _seed(
        patch_collection,
        [
            {
                "id": "1",
                "document": "a",
                "metadata": _meta(1),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "2",
                "document": "b",
                "metadata": _meta(2),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
        ],
    )
    result = search_telegram_news(
        "test", hours_ago=None, top_k=5, candidate_pool=5, rerank=False
    )
    assert all(r["rerank_score"] is None for r in result)


def test_search_rerank_true_reorders_by_reranker_not_vector(patch_collection):
    _seed(
        patch_collection,
        [
            {
                "id": str(i),
                "document": f"doc {i}",
                "metadata": _meta(i),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            }
            for i in range(1, 4)
        ],
    )
    result = search_telegram_news(
        "test", hours_ago=None, top_k=3, candidate_pool=3, rerank=True
    )
    assert all(r["rerank_score"] is not None for r in result)
    scores = [r["rerank_score"] for r in result]
    assert scores == sorted(scores, reverse=True)


def test_search_no_where_when_all_filters_none(patch_collection):
    _seed(
        patch_collection,
        [
            {
                "id": "1",
                "document": "a",
                "metadata": _meta(1),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "2",
                "document": "b",
                "metadata": _meta(2),
                "embedding": [0.1, 0.2, 0.3, 0.4],
            },
        ],
    )
    result = search_telegram_news(
        "test",
        coin=None,
        hours_ago=None,
        require_coin_mentioned=False,
        top_k=5,
        candidate_pool=5,
    )
    assert len(result) == 2
