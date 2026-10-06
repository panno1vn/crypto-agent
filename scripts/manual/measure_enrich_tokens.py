#!/usr/bin/env python3
"""
scripts/manual/measure_enrich_tokens.py

Ngày 22 — Đo xem prefix của MessageEnricher ăn mất bao nhiêu phần trăm
ngân sách 128 token của model.

Vì sao cần đo: `paraphrase-multilingual-mpnet-base-v2` có
max_seq_length = 128. Mọi token sau vị trí 128 bị CẮT BỎ trong im lặng —
không warning, không lỗi. Nếu prefix `[Channel: ...] [Time: ...]
[Coins: ...] [Engagement: ...]` chiếm 40 token thì phần nội dung thật của
tin nhắn chỉ còn 88 token để embed.

Script này lấy dữ liệu THẬT từ Postgres và so sánh 3 cấu hình enricher,
để quyết định bằng số chứ không phải bằng cảm tính.

Cách dùng:
    python scripts/manual/measure_enrich_tokens.py --sample 300
"""

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

load_dotenv()

from data_pipeline.telegram.historical_scraper import TelegramMessage  # noqa: E402,E501
from rag.config import EMBEDDING_MODEL, get_db_dsn  # noqa: E402
from rag.enricher import enrich_message_for_embedding  # noqa: E402

SAMPLE_SQL = """
    SELECT id, channel_name, message_text, language,
           views, forwards, coins_mentioned, created_at,
           has_media, reply_count
    FROM telegram_messages
    WHERE message_text IS NOT NULL
    ORDER BY random()
    LIMIT $1
"""

CONFIGS = {
    "roadmap (Channel+Time+Coins+Engagement)": dict(
        include_time=True, include_engagement=True
    ),
    "bo Engagement                          ": dict(
        include_time=True, include_engagement=False
    ),
    "chi Channel+Coins                      ": dict(
        include_time=False, include_engagement=False
    ),
}


async def load_sample(n: int) -> list[TelegramMessage]:
    pool = await asyncpg.create_pool(get_db_dsn(), min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(SAMPLE_SQL, n)
    finally:
        await pool.close()

    messages = []
    for row in rows:
        try:
            messages.append(TelegramMessage(**dict(row)))
        except Exception:
            continue
    return messages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=300)
    args = parser.parse_args()

    messages = asyncio.run(load_sample(args.sample))
    if not messages:
        print("Không lấy được tin nhắn nào từ DB.")
        return

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
    tokenizer = model.tokenizer
    max_len = model.max_seq_length

    print(f"Model          : {EMBEDDING_MODEL}")
    print(f"max_seq_length : {max_len}")
    print(f"Sample         : {len(messages)} tin nhắn thật\n")

    body_lens = [len(tokenizer.tokenize(m.message_text or "")) for m in messages]
    print(
        f"Độ dài nội dung gốc (token): "
        f"trung vị {statistics.median(body_lens):.0f}, "
        f"trung bình {statistics.mean(body_lens):.1f}, "
        f"tối đa {max(body_lens)}"
    )
    over = sum(1 for x in body_lens if x > max_len)
    print(
        f"  → {over}/{len(body_lens)} tin "
        f"({over / len(body_lens) * 100:.1f}%) đã vượt {max_len} token "
        f"NGAY CẢ KHI KHÔNG có prefix\n"
    )

    print(
        f"{'Cấu hình enricher':42s} "
        f"{'prefix':>8s} {'tổng':>8s} {'bị cắt':>8s} {'còn lại':>9s}"
    )
    print("-" * 80)

    for label, kwargs in CONFIGS.items():
        prefix_lens = []
        total_lens = []
        for msg, body_len in zip(messages, body_lens):
            full = enrich_message_for_embedding(msg, **kwargs)
            total = len(tokenizer.tokenize(full))
            total_lens.append(total)
            prefix_lens.append(total - body_len)

        truncated = sum(1 for x in total_lens if x > max_len)
        median_prefix = statistics.median(prefix_lens)
        # Số token nội dung thật còn sống sót sau khi cắt
        survived = [
            min(body, max(0, max_len - pre))
            for body, pre in zip(body_lens, prefix_lens)
        ]

        print(
            f"{label} "
            f"{median_prefix:8.0f} "
            f"{statistics.median(total_lens):8.0f} "
            f"{truncated / len(total_lens) * 100:7.1f}% "
            f"{statistics.median(survived):9.0f}"
        )

    print(
        "\nCột 'còn lại' = số token NỘI DUNG THẬT trung vị mà model "
        "thực sự nhìn thấy.\nChênh lệch giữa dòng 1 và dòng 3 chính là "
        "cái giá của Time + Engagement."
    )


if __name__ == "__main__":
    main()
