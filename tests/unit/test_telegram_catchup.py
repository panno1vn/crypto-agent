"""
tests/unit/test_telegram_catchup.py

2026-10-08, nợ #14 — catch-up Telegram phải lấy tin CŨ NHẤT trước kể từ
watermark. Telethon là biên mạng nên dùng client GIẢ, ghi lại tham số
iter_messages và mô phỏng thứ tự trả về của Telethon.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from data_pipeline.telegram import historical_scraper
from data_pipeline.telegram.historical_scraper import (
    scrape_channel_history,
    scrape_message_ids,
)

TEXT = "BTC vượt kháng cự, thị trường đang rất sôi động hôm nay"


class FakeClient:
    """Mô phỏng iter_messages: newest-first mặc định, reverse=True thì tăng dần."""

    def __init__(self, ids):
        self.ids = sorted(ids)
        self.calls = []

    def iter_messages(self, channel, limit=None, **kw):
        self.calls.append(kw)
        ids = self.ids
        if "min_id" in kw:
            ids = [i for i in ids if i > kw["min_id"]]
        if "max_id" in kw:
            # Telethon: max_id KHÔNG gồm biên (message.id >= max_id thì dừng).
            ids = [i for i in ids if i < kw["max_id"]]
        ids = ids if kw.get("reverse") else list(reversed(ids))
        ids = ids[:limit]

        async def gen():
            for i in ids:
                yield SimpleNamespace(
                    id=i,
                    text=TEXT,
                    views=1,
                    forwards=0,
                    date=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )

        return gen()


@pytest.fixture(autouse=True)
def khong_ngu_that(monkeypatch):
    # Scraper ngủ 3.5-8.2s sau mỗi 35-65 tin (giả lập người thật). Unit test
    # không cần chờ thật.
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(historical_scraper.asyncio, "sleep", no_sleep)


async def _collect(client, **kw):
    return [m.id async for m in scrape_channel_history(client, "kenh", **kw)]


async def test_oldest_first_lay_ngay_sau_watermark_khong_bo_khuc_giua():
    # Watermark 100, có 1000 tin mới (101..1100), limit 500.
    client = FakeClient(range(1, 1101))
    got = await _collect(client, limit=500, min_message_id=100, oldest_first=True)
    assert got == list(range(101, 601))
    assert client.calls[0] == {"min_id": 100, "reverse": True}


async def test_newest_first_cu_mat_khuc_giua():
    # Tái hiện bug: newest-first + limit chỉ lấy 601..1100; 101..600 bị bỏ
    # vì lần sau watermark MAX(id)=1100.
    client = FakeClient(range(1, 1101))
    got = await _collect(client, limit=500, min_message_id=100)
    assert min(got) == 601


async def test_oldest_first_khong_co_watermark_thi_raise():
    with pytest.raises(ValueError):
        await _collect(FakeClient([1, 2]), limit=10, oldest_first=True)


class ErrorClient:
    def iter_messages(self, channel, limit=None, **kw):
        async def gen():
            raise RuntimeError("Nobody is using this username")
            yield  # pragma: no cover

        return gen()


async def test_loi_kenh_duoc_raise_khong_bi_nuot():
    # Bản cũ: except Exception → chỉ log, generator kết thúc bình thường →
    # DAG catch-up báo success dù kênh không tồn tại.
    with pytest.raises(RuntimeError):
        await _collect(ErrorClient(), limit=10, min_message_id=1, oldest_first=True)


async def test_khoang_id_lay_dung_lo_khong_gom_bien():
    # Backfill lỗ: tin 340009 và 358068 đã có trong DB, lấy đúng phần giữa.
    client = FakeClient(range(340000, 358100))
    got = await _collect(
        client,
        limit=None,
        min_message_id=340009,
        max_message_id=358068,
        oldest_first=True,
    )
    assert got == list(range(340010, 358068))
    assert client.calls[0] == {"min_id": 340009, "max_id": 358068, "reverse": True}


async def test_max_message_id_khong_oldest_first_thi_raise():
    with pytest.raises(ValueError):
        await _collect(FakeClient([1, 2]), limit=10, max_message_id=2)


class FakeIdsClient:
    """Mô phỏng iter_messages(ids=...): None cho id không có; tin không text."""

    def __init__(self, existing, no_text=()):
        self.existing = set(existing)
        self.no_text = set(no_text)
        self.calls = []

    def iter_messages(self, channel, ids=None, **kw):
        self.calls.append(ids)

        async def gen():
            for i in ids:
                if i not in self.existing:
                    yield None
                    continue
                yield SimpleNamespace(
                    id=i,
                    text="" if i in self.no_text else TEXT,
                    views=None,
                    forwards=0,
                    date=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )

        return gen()


async def test_scrape_message_ids_bo_id_khong_ton_tai_va_tin_khong_text():
    client = FakeIdsClient(existing=[3, 5, 9], no_text=[5])
    got = [m.id async for m in scrape_message_ids(client, "kenh", ids=[3, 4, 5, 9])]
    assert got == [3, 9]
    assert client.calls == [[3, 4, 5, 9]]
