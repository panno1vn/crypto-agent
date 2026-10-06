"""
rag/enricher.py

Ngày 22 — MessageEnricher.

Hai việc, tách bạch rõ ràng:

1. `enrich_message_for_embedding()` → chuỗi TEXT sẽ được đem đi embed.
   Chỉ nên chứa thứ có giá trị NGỮ NGHĨA, vì model chỉ nhìn 128 token đầu.

2. `build_metadata()` → dict METADATA để lọc bằng `where` trong Chroma.
   Chứa mọi thứ cần lọc chính xác (thời gian, coin, kênh, engagement).

Đừng gộp hai việc này: nhét số liệu vào text làm loãng embedding,
còn để thời gian ở dạng chuỗi trong metadata thì không lọc được.

Các ràng buộc CỨNG của Chroma đã kiểm chứng thực tế (chromadb 1.5.9):
  - Giá trị metadata KHÔNG được là None       → TypeError
  - Mảng metadata KHÔNG được rỗng             → ValueError
  - `$gte`/`$lte` CHỈ nhận int/float          → ValueError nếu là chuỗi ISO
  - `$contains` CHỈ hoạt động trên mảng;
    dùng trên chuỗi thì trả về [] trong IM LẶNG (nguy hiểm nhất)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from data_pipeline.logger import get_logger
from data_pipeline.telegram.historical_scraper import TelegramMessage
from rag.config import ENRICH_INCLUDE_ENGAGEMENT, ENRICH_INCLUDE_TIME

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helper: chuẩn hóa timezone
# ---------------------------------------------------------------------------
def to_utc(dt: datetime) -> datetime:
    """
    Chuẩn hóa datetime về UTC aware.

    Quy ước của project (xem WEEK2.md): cột TIMESTAMP trong Postgres là
    naive và LUÔN mang nghĩa UTC. Vì vậy datetime naive đọc từ DB phải
    được gắn tzinfo=UTC, KHÔNG phải coi là giờ local.

    Nếu bỏ qua bước này, `datetime.timestamp()` trên giá trị naive sẽ
    dùng timezone của máy (VN = UTC+7) → `created_ts` lệch 25200 giây →
    bộ lọc "tin trong 6h qua" ở Ngày 24 sai lệch mà không báo lỗi.

    Args:
        dt: datetime naive (hiểu là UTC) hoặc aware.

    Returns:
        datetime aware ở múi giờ UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_coins(coins: "list[str] | None") -> list[str]:
    """
    Chuẩn hóa danh sách coin: bỏ None, bỏ chuỗi rỗng, viết hoa, khử trùng lặp
    nhưng GIỮ NGUYÊN thứ tự xuất hiện.

    Không dùng `set()` để khử trùng lặp rồi trả về trực tiếp — thứ tự của
    set không ổn định giữa các lần chạy, làm document/metadata khác nhau
    giữa hai lần embed cùng một tin nhắn.
    """
    if not coins:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for coin in coins:
        if not coin:
            continue
        upper = coin.strip().upper()
        if upper and upper not in seen:
            seen.add(upper)
            result.append(upper)
    return result


# ---------------------------------------------------------------------------
# 1. TEXT đem đi embed
# ---------------------------------------------------------------------------
def enrich_message_for_embedding(
    msg: TelegramMessage,
    include_time: bool = ENRICH_INCLUDE_TIME,
    include_engagement: bool = ENRICH_INCLUDE_ENGAGEMENT,
) -> str:
    """
    Thêm metadata dạng prefix vào text trước khi embed.

    Mục đích: câu hỏi về BTC vẫn retrieve được tin BTC dù phần text gốc
    ngắn hoặc không nhắc trực tiếp tên coin.

    Đánh đổi: model `paraphrase-multilingual-mpnet-base-v2` có
    max_seq_length = 128 token. Mỗi field prefix ăn vào ngân sách đó.
    `include_time` và `include_engagement` để mặc định True theo roadmap,
    nhưng cả hai đều đã có trong metadata và được lọc bằng `where` —
    giá trị ngữ nghĩa của chúng gần bằng 0. Hãy đo bằng
    `scripts/manual/measure_enrich_tokens.py` rồi tự quyết định.

    Args:
        msg:                Tin nhắn đã validate qua Pydantic.
        include_time:       Có chèn `[Time: ...]` vào text không.
        include_engagement: Có chèn `[Engagement: ...]` vào text không.

    Returns:
        Chuỗi text đã enrich, luôn là str (không bao giờ None).
    """
    coins = _normalize_coins(msg.coins_mentioned)

    parts: list[str] = [f"[Channel: {msg.channel_name}]"]

    if include_time:
        created = to_utc(msg.created_at)
        parts.append(f"[Time: {created.strftime('%Y-%m-%d %H:%M')}]")

    if coins:
        parts.append(f"[Coins: {', '.join(coins)}]")

    if include_engagement:
        parts.append(f"[Engagement: {msg.views or 0}v {msg.forwards or 0}f]")

    # message_text là Optional[str] trong schema. f-string trên None sẽ
    # tạo ra chuỗi "None" và đem đi embed — phải chặn tường minh.
    text = (msg.message_text or "").strip()

    return " ".join(parts) + " " + text


# ---------------------------------------------------------------------------
# 2. METADATA cho Chroma
# ---------------------------------------------------------------------------
def build_metadata(msg: TelegramMessage) -> dict[str, Any]:
    """
    Dựng dict metadata hợp lệ với ChromaDB.

    Khác biệt so với bản trong roadmap, và lý do:

      - `coins` lưu dạng LIST[str] thay vì chuỗi nối bằng dấu phẩy.
        `where={"coins": {"$contains": "BTC"}}` chỉ hoạt động trên mảng.
        Nếu lưu dạng chuỗi, Chroma trả về [] mà KHÔNG báo lỗi.

      - Key `coins` bị BỎ HẲN khi không có coin nào. Chroma từ chối mảng
        rỗng (`ValueError: Expected metadata list value ... to be non-empty`).
        Record không có key `coins` vẫn hợp lệ và tự động bị loại khỏi
        `$contains` — đúng hành vi mong muốn.

      - Thêm `created_ts` (Unix epoch, int) bên cạnh `created_at` (chuỗi ISO).
        Chroma chỉ so sánh `$gte`/`$lte` trên số. Lọc theo thời gian ở
        Ngày 24 BẮT BUỘC dùng `created_ts`; `created_at` chỉ để đọc/debug.

      - Thêm `coins_count` để làm relevance gate rẻ tiền:
        `where={"coins_count": {"$gt": 0}}`.

      - `language` ép về "unknown" khi None. Chroma không nhận giá trị None.

    Args:
        msg: Tin nhắn đã validate qua Pydantic.

    Returns:
        Dict metadata, đảm bảo không có giá trị None và không có mảng rỗng.
    """
    created = to_utc(msg.created_at)
    coins = _normalize_coins(msg.coins_mentioned)

    metadata: dict[str, Any] = {
        "msg_id": int(msg.id),
        "channel": msg.channel_name,
        "language": msg.language or "unknown",
        "created_at": created.isoformat(),
        "created_ts": int(created.timestamp()),
        "views": int(msg.views or 0),
        "forwards": int(msg.forwards or 0),
        "coins_count": len(coins),
    }

    if coins:
        metadata["coins"] = coins

    return metadata
