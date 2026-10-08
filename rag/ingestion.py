"""
rag/ingestion.py

Ngày 22 — Pipeline: PostgreSQL → enrich → embed → ChromaDB.

Nguyên tắc thiết kế:

  - Dùng `upsert()` chứ không phải `add()`. `add()` với id trùng bị bỏ qua
    trong IM LẶNG (đã kiểm chứng: không lỗi, count không đổi, document cũ
    giữ nguyên). Nghĩa là chạy lại sau khi sửa enricher sẽ KHÔNG cập nhật
    gì cả mà vẫn báo thành công. `upsert()` ghi đè, idempotent thật.

  - CẢNH BÁO (Ngày 24, xác nhận thực nghiệm trên chromadb==1.5.9):
    `upsert()` lên ID ĐÃ TỒN TẠI không xóa key metadata cũ nếu bản ghi
    mới thiếu key đó — hành vi merge/patch, không phải PUT/replace
    hoàn toàn. Pipeline hiện tại AN TOÀN vì watermark
    (`get_last_embedded_id_for_channel`) chỉ tiến, không bao giờ
    re-upsert lên ID cũ. Nhưng nếu tương lai cần re-embed hàng loạt (VD
    sau khi sửa `extract_coins()` khiến `coins_mentioned` đổi từ có
    thành rỗng), PHẢI `reset_collection()` trước hoặc xóa key tường
    minh — không thể tin `upsert()` tự dọn. Xem
    test_upsert_khong_xoa_key_cu_khi_metadata_moi_thieu_key trong
    tests/unit/test_rag_vector_store.py.

  - Phân trang theo `id > last_id ORDER BY id` thay vì OFFSET. Resume được
    sau khi ngắt, và không bị trượt trang khi có insert đồng thời từ
    `realtime_listener.py`.

  - KHÔNG dùng checkpoint file. Bài học Ngày 15: `checkpoint.save()` trong
    khối `finally` chạy bất kể insert thành công hay không. Ở đây trạng
    thái tiến độ nằm ngay trong Chroma (`collection.count()`), là nguồn
    sự thật duy nhất.

  - CẬP NHẬT 2026-08-13 — BUG NGHIÊM TRỌNG ĐÃ SỬA: watermark trước đây
    là `MAX(msg_id)` TOÀN CỤC gộp mọi kênh. `msg_id` là ID nội bộ của
    Telegram — đánh số ĐỘC LẬP theo từng kênh, KHÔNG liên quan gì tới
    thời gian hay ID của kênh khác. Hệ quả thực tế đã xác nhận: kênh
    `chatdautucrypto` (msg_id ~45754) đẩy watermark toàn cục vượt qua
    kênh `bd_ventures` (dừng ở msg_id=13784) — mọi tin MỚI của
    `bd_ventures` sau đó bị bỏ qua VĨNH VIỄN, im lặng, DAG vẫn báo
    "success" (vì với watermark toàn cục, "tin mới nhất" theo `id` luôn
    thuộc kênh có dải ID cao nhất). Quét toàn bộ 13 kênh: 5 kênh bị ảnh
    hưởng, 117 tin bị kẹt (`scripts/manual/diag_watermark_blast_radius.py`).

    Fix: watermark tính RIÊNG theo từng kênh (`get_last_embedded_id_for_channel`),
    `fetch_messages` lọc thêm `channel_name = $1`, `run_ingestion` lặp
    tuần tự qua từng kênh. Không cần schema migration — `channel` và
    `msg_id` đã có sẵn trong metadata Chroma từ Ngày 22. Chạy lại
    `run_ingestion()` sau fix này TỰ ĐỘNG vét sạch 117 tin đang kẹt,
    không cần backfill script riêng.

Về `async`: chỉ phần đọc Postgres là async thật (asyncpg, theo pattern
sẵn có của project). `encode()` và `upsert()` là đồng bộ và chặn event
loop — chấp nhận được vì đây là batch job, không phải server. Bọc chúng
trong `run_in_executor` chỉ thêm phức tạp mà không nhanh hơn.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg

from data_pipeline.logger import get_logger
from data_pipeline.telegram.historical_scraper import TelegramMessage
from rag.config import CHROMA_UPSERT_BATCH, INGEST_PAGE_SIZE, get_db_dsn
from rag.embedder import get_embedder
from rag.enricher import build_metadata, enrich_message_for_embedding
from rag.vector_store import get_collection

logger = get_logger(__name__)


# Chọn tường minh từng cột thay vì SELECT * — thêm cột mới vào bảng sẽ
# không âm thầm làm đổi thứ tự/nội dung record.
#
# LỌC THEO channel_name (thêm 2026-08-13): watermark giờ tính riêng
# theo từng kênh, nên trang đọc cũng phải giới hạn đúng 1 kênh — nếu
# không, `ORDER BY id` sẽ lại trộn ID của nhiều kênh không so sánh
# được với nhau, tái diễn đúng bug đã sửa.
_FETCH_SQL = """
    SELECT id, channel_name, message_text, language,
           views, forwards, coins_mentioned, created_at,
           has_media, reply_count
    FROM telegram_messages
    WHERE message_text IS NOT NULL
      AND channel_name = $1
      AND id > $2
    ORDER BY id
    LIMIT $3
"""

_CHANNELS_SQL = """
    SELECT DISTINCT channel_name
    FROM telegram_messages
    WHERE message_text IS NOT NULL
    ORDER BY channel_name
"""


# ---------------------------------------------------------------------------
# Đọc từ Postgres
# ---------------------------------------------------------------------------
async def get_channels(pool: asyncpg.Pool) -> list[str]:
    """
    Danh sách kênh distinct — dùng để lặp watermark THEO TỪNG KÊNH.

    Thứ tự alphabet (ORDER BY channel_name) để lần chạy nào cũng xử lý
    theo cùng thứ tự, dễ so sánh log giữa các lần chạy.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_CHANNELS_SQL)
    return [r["channel_name"] for r in rows]


async def fetch_messages(
    pool: asyncpg.Pool,
    channel: str,
    after_id: int = 0,
    limit: int = INGEST_PAGE_SIZE,
) -> tuple[list[TelegramMessage], int, int]:
    """
    Đọc một trang tin nhắn từ `telegram_messages`, giới hạn đúng 1 kênh.

    Dòng nào không dựng được thành `TelegramMessage` (ví dụ text quá ngắn
    theo validator Pydantic) sẽ bị bỏ qua nhưng LUÔN được log ở mức
    WARNING kèm id — không nuốt exception trong im lặng.

    Args:
        pool:     Connection pool asyncpg.
        channel:  Chỉ lấy tin của kênh này (xem cảnh báo watermark đầu file).
        after_id: Chỉ lấy các dòng có id lớn hơn giá trị này (watermark
                  của CHÍNH kênh này, không phải watermark toàn cục).
        limit:    Số dòng tối đa mỗi trang.

    Returns:
        Tuple (danh sách TelegramMessage đã validate, id lớn nhất của
        trang GỐC, số dòng thô đọc được).

        Trả về id lớn nhất của trang gốc — không phải của danh sách đã
        lọc — là chủ ý. Nếu cả trang đều fail validation mà con trỏ
        không tiến, vòng lặp sẽ đọc lại đúng trang đó vô hạn.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_FETCH_SQL, channel, after_id, limit)

    if not rows:
        return [], after_id, 0

    max_raw_id = max(row["id"] for row in rows)
    messages: list[TelegramMessage] = []
    skipped = 0

    for row in rows:
        try:
            messages.append(
                TelegramMessage(
                    id=row["id"],
                    channel_name=row["channel_name"],
                    message_text=row["message_text"],
                    language=row["language"],
                    views=row["views"] or 0,
                    forwards=row["forwards"] or 0,
                    created_at=row["created_at"],
                    coins_mentioned=list(row["coins_mentioned"] or []),
                    has_media=row["has_media"],
                    reply_count=row["reply_count"] or 0,
                )
            )
        except Exception as e:
            skipped += 1
            logger.warning(f"[INGEST] Bỏ qua id={row['id']}: {e}")

    if skipped:
        logger.warning(f"[INGEST] Trang này bỏ qua {skipped}/{len(rows)} dòng")

    return messages, max_raw_id, len(rows)


async def count_candidates(pool: asyncpg.Pool) -> int:
    """Đếm tổng số tin nhắn đủ điều kiện embed (để log tiến độ)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM telegram_messages WHERE message_text IS NOT NULL"
        )


class LegacyChromaIdError(RuntimeError):
    """Collection còn id kiểu cũ (msg_id trần) — xem chroma_id()."""


def chroma_id(channel: str, msg_id: int) -> str:
    """
    Id Chroma của một tin: "{channel}:{msg_id}".

    msg_id Telegram chỉ duy nhất trong 1 kênh (nợ #15). Id trần str(msg_id)
    làm upsert của kênh B GHI ĐÈ tin kênh A cùng msg_id, ngay khi Postgres
    đã lưu được cả hai (khóa (channel_name, id)).
    """
    return f"{channel}:{msg_id}"


def get_last_embedded_id_for_channel(collection, channel: str) -> int:
    """
    Watermark THẬT từ Chroma, tính RIÊNG cho từng kênh (đúng nguyên tắc
    "nguồn sự thật là chính đích đến" — nhưng phải scope đúng theo kênh,
    xem cảnh báo bug ở đầu file).

    Trước đây: `max(msg_id)` toàn cục — SAI, vì msg_id là ID nội bộ
    Telegram, không so sánh chéo kênh được. Kênh có dải ID cao đẩy
    watermark vượt qua kênh có dải ID thấp, tin mới của kênh ID thấp
    bị bỏ qua vĩnh viễn. Xác nhận thực tế 2026-08-13: 5/13 kênh, 117
    tin bị kẹt trước khi sửa.

    Hệ quả quan trọng (giữ nguyên như thiết kế gốc): nếu task DAG bị
    timeout/kill giữa chừng khi đang xử lý 1 kênh, lần chạy kế tiếp tự
    động resume đúng chỗ của CHÍNH kênh đó — không cần xử lý retry đặc
    biệt, không cần lưu state riêng ngoài Chroma.
    """
    result = collection.get(where={"channel": channel}, include=[])
    ids = result["ids"]
    prefix = f"{channel}:"
    legacy = [i for i in ids if not i.startswith(prefix)]
    if legacy:
        # Collection embed trước nợ #15 (id = msg_id trần). Trộn 2 kiểu id
        # thì tin cũ không bị thay mà nằm song song → RAG trả trùng.
        raise LegacyChromaIdError(
            f"Kênh '{channel}' có {len(legacy)} id Chroma không theo dạng "
            f"'{{channel}}:{{msg_id}}' (vd {legacy[:3]}). Collection embed "
            f"trước migration e7c2a9d41f05 — phải xóa collection và embed lại."
        )
    return max((int(i[len(prefix) :]) for i in ids), default=0)


# ---------------------------------------------------------------------------
# Enrich + embed + ghi vào Chroma
# ---------------------------------------------------------------------------
def embed_and_store(
    messages: list[TelegramMessage],
    collection=None,
    batch_size: int = CHROMA_UPSERT_BATCH,
) -> int:
    """
    Enrich → embed → upsert một lô tin nhắn vào ChromaDB.

    Id ghi vào Chroma là chroma_id(channel, msg_id) — PHẢI gồm kênh
    (nợ #15), nếu không tin 2 kênh cùng msg_id ghi đè nhau.

    Args:
        messages:   Tin nhắn cần embed. Rỗng thì không làm gì.
        collection: Collection Chroma. None = tự lấy theo config.
        batch_size: Số record mỗi lần upsert lên server.

    Returns:
        Số record đã upsert thành công.
    """
    if not messages:
        return 0

    if collection is None:
        collection = get_collection()

    texts = [enrich_message_for_embedding(m) for m in messages]
    metadatas: list[dict[str, Any]] = [build_metadata(m) for m in messages]
    ids = [chroma_id(m.channel_name, m.id) for m in messages]

    embeddings = get_embedder().encode(texts)

    total = 0
    for start in range(0, len(ids), batch_size):
        end = min(start + batch_size, len(ids))
        # Xem cảnh báo H2 ở docstring đầu file: upsert() lên ID cũ
        # không xóa key metadata thiếu trong bản ghi mới. An toàn ở
        # đây vì watermark chỉ tiến, không re-upsert ID cũ.
        collection.upsert(
            ids=ids[start:end],
            embeddings=embeddings[start:end].tolist(),
            documents=texts[start:end],
            metadatas=metadatas[start:end],
        )
        total += end - start

    logger.info(f"[INGEST] Upserted {total} records vào ChromaDB")
    return total


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
async def run_ingestion(
    dsn: "str | None" = None,
    max_messages: "int | None" = None,
    page_size: int = INGEST_PAGE_SIZE,
) -> dict[str, Any]:
    """
    Chạy toàn bộ pipeline embedding cho tin nhắn trong PostgreSQL.

    THAY ĐỔI 2026-08-13: lặp TUẦN TỰ qua từng kênh, mỗi kênh dùng
    watermark RIÊNG của chính nó (xem cảnh báo bug đầu file). Tham số
    `after_id` toàn cục đã BỎ — không còn ý nghĩa khi watermark scope
    theo kênh. Muốn resume từ giữa chừng, cứ chạy lại: mỗi kênh tự đọc
    watermark thật của nó từ Chroma, không cần truyền tay.

    Args:
        dsn:          DSN Postgres. None = dựng từ biến môi trường.
        max_messages: Dừng sau khi xử lý bấy nhiêu tin, CỘNG DỒN qua
                      mọi kênh. None = chạy hết toàn bộ backlog của mọi
                      kênh. Dùng số nhỏ (vd 100) để smoke test.
        page_size:    Số dòng đọc mỗi vòng lặp, tính riêng theo kênh.

    Returns:
        Dict thống kê: processed, upserted, last_id_by_channel,
        collection_count.
    """
    dsn = dsn or get_db_dsn()

    # Lấy collection TRƯỚC khi load model: nếu Chroma chưa chạy hoặc
    # space sai, fail ngay trong 1 giây thay vì sau 60 giây tải model.
    collection = get_collection()

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    processed = 0
    upserted = 0
    last_id_by_channel: dict[str, int] = {}

    try:
        total_available = await count_candidates(pool)
        channels = await get_channels(pool)
        logger.info(
            f"[INGEST] Bắt đầu. Tổng tin đủ điều kiện: {total_available}. "
            f"Chroma hiện có: {collection.count()}. Số kênh: {len(channels)}."
        )

        for channel in channels:
            if max_messages is not None and processed >= max_messages:
                logger.info(
                    f"[INGEST] Đạt max_messages={max_messages}, dừng trước "
                    f"khi xử lý kênh còn lại."
                )
                break

            channel_watermark = get_last_embedded_id_for_channel(collection, channel)
            last_id = channel_watermark
            logger.info(
                f"[INGEST] Kênh '{channel}': watermark hiện tại id={channel_watermark}"
            )

            while True:
                if max_messages is not None and processed >= max_messages:
                    break

                remaining = None if max_messages is None else max_messages - processed
                limit = page_size if remaining is None else min(page_size, remaining)

                messages, max_raw_id, raw_count = await fetch_messages(
                    pool, channel=channel, after_id=last_id, limit=limit
                )

                if raw_count == 0:
                    break

                # Con trỏ luôn tiến theo trang GỐC của kênh này. Nếu cả
                # trang fail validation thì vẫn phải nhảy qua, nếu
                # không sẽ lặp vô hạn.
                last_id = max_raw_id
                processed += raw_count

                if messages:
                    upserted += embed_and_store(messages, collection=collection)

                logger.info(
                    f"[INGEST] kênh={channel} processed={processed} "
                    f"(last_id kênh này={last_id})"
                )

            last_id_by_channel[channel] = last_id

    finally:
        await pool.close()

    stats: dict[str, Any] = {
        "processed": processed,
        "upserted": upserted,
        "last_id_by_channel": last_id_by_channel,
        "collection_count": collection.count(),
    }
    logger.info(f"[INGEST] Xong: {stats}")
    return stats


if __name__ == "__main__":  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(run_ingestion(max_messages=100))
