"""
scripts/manual/check_telegram_id_collisions.py

Nợ #15 (2026-10-08) — đo read-only dấu vết va chạm msg_id giữa các kênh.

Trước migration e7c2a9d41f05, khóa chính telegram_messages là (id) nên tin
kênh B trùng msg_id với kênh A bị bỏ im lặng. Tin mất để lại "lỗ" trong dải
id của kênh B, và id của lỗ đó đang thuộc kênh khác. Script in, cho từng kênh:
  - lo: id trong [min, max] mà kênh không có
  - lo_bi_kenh_khac_chiem: cận trên số tin mất do va chạm
  - tỉ lệ lỗ trong vùng chồng dải với kênh khác so với ngoài vùng chồng.
    Lỗ tự nhiên (tin bị xóa, tin không có chữ) phải gần như nhau ở 2 vùng;
    vùng chồng lỗ nhiều hơn hẳn = dấu hiệu mất tin.
Và kiểm Chroma khớp Postgres theo (kênh, msg_id).

Sau migration, lỗ cũ KHÔNG tự lấp: phải lấy lại tin từ Telegram. Chạy lại
script sau khi lấy lại để thấy tỉ lệ lỗ vùng chồng tụt về mức tự nhiên.

Chạy:
    PYTHONPATH=. python scripts/manual/check_telegram_id_collisions.py
"""

import asyncio
from collections import defaultdict

import asyncpg
from dotenv import load_dotenv

load_dotenv()

from rag.config import get_db_dsn  # noqa: E402
from rag.vector_store import get_collection  # noqa: E402


async def _load_pg() -> set[tuple[str, int]]:
    conn = await asyncpg.connect(get_db_dsn())
    try:
        rows = await conn.fetch("SELECT channel_name, id FROM telegram_messages")
    finally:
        await conn.close()
    return {(r["channel_name"], r["id"]) for r in rows}


def report_postgres(keys: set[tuple[str, int]]) -> None:
    by_ch: dict[str, set[int]] = defaultdict(set)
    for ch, i in keys:
        by_ch[ch].add(i)
    owners: dict[int, set[str]] = defaultdict(set)
    for ch, i in keys:
        owners[i].add(ch)
    rng = {ch: (min(s), max(s)) for ch, s in by_ch.items()}

    print("=== Lỗ id trong dải mỗi kênh ===")
    print(
        f"{'kenh':24}{'so_tin':>7}{'lo':>7}{'lo_bi_kenh_khac_chiem':>23}"
        f"{'ty_le_lo_vung_chong':>21}{'ty_le_lo_ngoai':>16}"
    )
    can_tren = 0
    for ch, (lo, hi) in sorted(rng.items(), key=lambda x: x[1][0]):
        others = [
            (a, b) for c, (a, b) in rng.items() if c != ch and a <= hi and b >= lo
        ]

        def in_ov(i: int) -> bool:
            return any(a <= i <= b for a, b in others)

        holes = [i for i in range(lo, hi + 1) if i not in by_ch[ch]]
        taken = [i for i in holes if owners.get(i)]
        ov_total = sum(1 for i in range(lo, hi + 1) if in_ov(i))
        ov_holes = sum(1 for i in holes if in_ov(i))
        out_total = (hi - lo + 1) - ov_total
        out_holes = len(holes) - ov_holes
        can_tren += len(taken)
        r_ov = f"{ov_holes}/{ov_total}={ov_holes / ov_total:.0%}" if ov_total else "-"
        r_out = f"{out_holes / out_total:.0%}" if out_total else "-"
        print(
            f"{ch:24}{len(by_ch[ch]):>7}{len(holes):>7}{len(taken):>23}"
            f"{r_ov:>21}{r_out:>16}"
        )
    print(f"Cận trên tổng số tin có thể mất do va chạm: {can_tren}")
    dup = sum(1 for chs in owners.values() if len(chs) > 1)
    print(f"msg_id đang có ở >1 kênh (chỉ lưu được sau migration): {dup}")


def report_chroma(keys: set[tuple[str, int]]) -> None:
    col = get_collection()
    n = col.count()
    chroma_keys: set[tuple[str, int]] = set()
    bad_meta = 0
    for off in range(0, n, 5000):
        r = col.get(include=["metadatas"], limit=5000, offset=off)
        for cid, m in zip(r["ids"], r["metadatas"]):
            key = (m.get("channel"), m.get("msg_id"))
            if cid != f"{key[0]}:{key[1]}":
                bad_meta += 1
            chroma_keys.add(key)
    print("\n=== Chroma vs Postgres ===")
    print(
        f"Chroma={n}, Postgres={len(keys)}, "
        f"id lệch metadata={bad_meta}, "
        f"có ở Chroma không có ở PG={len(chroma_keys - keys)}, "
        f"có ở PG chưa embed={len(keys - chroma_keys)} "
        f"(gồm tin không qua được validate, vd text < 10 ký tự)"
    )


def main() -> None:
    keys = asyncio.run(_load_pg())
    report_postgres(keys)
    report_chroma(keys)


if __name__ == "__main__":
    main()
