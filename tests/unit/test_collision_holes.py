"""
tests/unit/test_collision_holes.py

Nợ #15 (2026-10-08): chọn đúng id cần lấy lại — id trong dải của kênh, kênh
không có, kênh khác đang có. Lỗ không ai chiếm (tin đã xóa) và id ngoài dải
không được chọn.
"""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "manual"
    / "backfill_telegram_collisions.py"
)
_spec = importlib.util.spec_from_file_location("backfill_collisions", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
collision_holes = _mod.collision_holes


def test_chon_id_bi_kenh_khac_chiem_trong_dai():
    keys = {("a", 10), ("a", 14), ("b", 11), ("b", 12), ("b", 20), ("c", 5)}
    holes = collision_holes(keys)
    # a: dải 10..14; 11, 12 do b chiếm → chọn; 13 không ai có → bỏ.
    assert holes["a"] == [11, 12]
    # b: dải 11..20; 14 do a chiếm → chọn; 10 (a) và 5 (c) ngoài dải → bỏ.
    assert holes["b"] == [14]
    assert "c" not in holes
