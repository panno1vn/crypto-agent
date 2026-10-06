"""
scripts/manual/dump_sentiment_distribution.py

Ngày 27 — Dump phân phối thật của CoinSentimentSummary.mean_weighted_score
trên cửa sổ trượt 30 ngày, để lấy percentile (p33/p67) thay cho ngưỡng
±0.1 bịa của roadmap v1.

TẠI SAO KHÔNG DÙNG search_telegram_news() hay aggregate_coin_sentiment()
TRỰC TIẾP CHO 30 NGÀY LỊCH SỬ:
    Cả hai hàm đều neo "bây giờ" — search_telegram_news() lọc hours_ago
    tính từ time.time() thật, còn aggregate_coin_sentiment() (bản gốc,
    trước patch as_of) tính cutoff từ _utc_naive_now(). Gọi lặp lại nhiều
    lần HÔM NAY chỉ cho ra 1 điểm dữ liệu (bây giờ), không phải phân phối
    30 ngày qua.

    Cách giải quyết: patch aggregate_coin_sentiment() thêm tham số
    `as_of: Optional[datetime] = None` (None = giữ nguyên hành vi cũ),
    cho phép tái dùng ĐÚNG logic đã test (engagement weighting, join
    credibility, relevance gate qua coins_mentioned) tại một mốc thời
    gian bất kỳ trong quá khứ — thay vì viết SQL riêng, dễ lệch khỏi
    logic gốc theo thời gian (đúng kiểu bug symbol mismatch đã có trong
    ledger).

    ⚠️ CẦN PATCH nlp/engagement_weighting.py TRƯỚC KHI CHẠY SCRIPT NÀY —
    xem hướng dẫn patch đi kèm (2 chỗ sửa, không phải viết lại file).

Cách chạy:
    PYTHONPATH=. python scripts/manual/dump_sentiment_distribution.py \\
        --coins BTC ETH SOL BNB XRP --days 30 --window-hours 6

Cửa sổ KHÔNG chồng lấn (tumbling, bước = window_hours) — tránh 1 tin
được đếm lặp lại ở nhiều cửa sổ liền kề, sẽ làm méo phân phối (đánh giá
quá cao mật độ mẫu ở vùng có nhiều tin).

Cửa sổ có message_count=0 bị SKIP khỏi phân phối, không tính là 0.0 —
đúng nguyên tắc đã áp dụng ở Ngày 26 ("đừng tính recall=0 giả tạo" cho
tổ hợp không có ground truth). 1 coin ít được nhắc tới sẽ tự nhiên có ít
mẫu hơn trong phân phối, đó là phản ánh đúng thực tế, không phải lỗi.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from data_pipeline.logger import get_logger
from nlp.engagement_weighting import aggregate_coin_sentiment
from rag.config import get_db_dsn

logger = get_logger(__name__)


def _to_asyncpg_dsn(dsn: str) -> str:
    """
    get_db_dsn() trả về "postgresql://..." (dùng cho asyncpg trực tiếp ở
    rag/ingestion.py). SQLAlchemy async cần dialect tường minh
    "postgresql+asyncpg://...".

    GIẢ ĐỊNH CHƯA XÁC NHẬN: script này tự tạo engine/session thay vì tái
    dùng 1 session factory có sẵn, vì chưa thấy factory đó ở đâu trong
    phiên làm việc này (aggregate_coin_sentiment() nhận session làm tham
    số, không tự tạo). Nếu project đã có sẵn factory dùng chung (vd
    data_pipeline/db.py), NÊN đổi sang dùng lại factory đó thay vì bản
    tự tạo dưới đây, để không có 2 nguồn cấu hình engine lệch nhau.
    """
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


def _utc_naive(dt: datetime) -> datetime:
    """Chuẩn hóa về naive-UTC, khớp quy ước naive=UTC của DB (engagement_weighting.py)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


async def dump_distribution(
    session: AsyncSession,
    coins: list[str],
    days: int,
    window_hours: float,
) -> dict[str, list[float]]:
    """
    Trả về dict {coin: [mean_weighted_score của từng cửa sổ có dữ liệu]}.
    """
    now = _utc_naive(datetime.now(timezone.utc))
    total_hours = days * 24
    n_windows = int(total_hours // window_hours)

    per_coin_scores: dict[str, list[float]] = {c: [] for c in coins}
    skipped_empty = 0

    for coin in coins:
        for w in range(n_windows):
            # Cửa sổ thứ w kết thúc tại (now - w * window_hours), không
            # chồng lấn cửa sổ w+1.
            as_of = now - timedelta(hours=w * window_hours)
            summary = await aggregate_coin_sentiment(
                session,
                coin=coin,
                window_hours=window_hours,
                as_of=as_of,
            )
            if summary.message_count == 0:
                skipped_empty += 1
                continue
            per_coin_scores[coin].append(summary.mean_weighted_score)

        logger.info(
            f"[DUMP] coin={coin} cửa sổ có dữ liệu={len(per_coin_scores[coin])}"
            f"/{n_windows}"
        )

    logger.info(f"[DUMP] Tổng cửa sổ rỗng bị skip: {skipped_empty}")
    return per_coin_scores


def print_percentiles(per_coin_scores: dict[str, list[float]]) -> None:
    all_scores: list[float] = []
    for coin, scores in per_coin_scores.items():
        all_scores.extend(scores)
        if not scores:
            print(f"{coin}: 0 cửa sổ có dữ liệu — bỏ qua")
            continue
        arr = np.array(scores)
        print(
            f"{coin}: n={len(arr)} mean={arr.mean():.4f} std={arr.std():.4f} "
            f"p10={np.percentile(arr, 10):.4f} p33={np.percentile(arr, 33):.4f} "
            f"p50={np.percentile(arr, 50):.4f} p67={np.percentile(arr, 67):.4f} "
            f"p90={np.percentile(arr, 90):.4f}"
        )

    if not all_scores:
        print(
            "\nKHÔNG có cửa sổ nào có dữ liệu trên toàn bộ coin — "
            "kiểm tra lại window_hours/days hoặc dữ liệu sentiment thật."
        )
        return

    arr = np.array(all_scores)
    print(
        f"\nTỔNG HỢP (mọi coin): n={len(arr)} mean={arr.mean():.4f} "
        f"std={arr.std():.4f}"
    )
    print(
        f"  → NEWS_CONFIRMATION_LOWER_THRESHOLD (đề xuất, p33) = "
        f"{np.percentile(arr, 33):.4f}"
    )
    print(
        f"  → NEWS_CONFIRMATION_UPPER_THRESHOLD (đề xuất, p67) = "
        f"{np.percentile(arr, 67):.4f}"
    )
    print(
        "\nLƯU Ý: đây là ĐỀ XUẤT dựa trên phân phối thật, không phải giá trị "
        "tự động áp dụng. Đọc qua trước khi ghi vào .env — đặc biệt nếu "
        "n quá nhỏ hoặc lệch hẳn về 1 coin có nhiều tin hơn hẳn."
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coins", nargs="+", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--window-hours", type=float, default=6)
    args = parser.parse_args()

    dsn = _to_asyncpg_dsn(get_db_dsn())
    engine = create_async_engine(dsn)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async with session_maker() as session:
        per_coin_scores = await dump_distribution(
            session, args.coins, args.days, args.window_hours
        )

    await engine.dispose()
    print_percentiles(per_coin_scores)


if __name__ == "__main__":  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(main())
