"""
tests/unit/test_channel_config.py

2026-10-08: RIC_Capital_Channel không còn tồn tại (UsernameInvalidError) làm
DAG telegram_realtime_sync đỏ mỗi 15 phút. DAG lấy kênh từ DB nên cần danh
sách loại trừ tường minh thay vì xóa dữ liệu cũ của kênh.
"""

from data_pipeline.telegram.channel_config import EXCLUDED_CHANNELS, filter_excluded


def test_bo_kenh_bi_loai_tru_giu_nguyen_thu_tu():
    kept = filter_excluded(["a", "ric", "b"], excluded={"ric": "lý do"})
    assert kept == ["a", "b"]


def test_kenh_loai_tru_khong_co_trong_danh_sach_khong_lam_hong():
    assert filter_excluded(["a", "b"], excluded={"sai_ten": "lý do"}) == ["a", "b"]


def test_mac_dinh_loai_ric_capital_channel():
    assert "RIC_Capital_Channel" in EXCLUDED_CHANNELS
    assert filter_excluded(["whale_alert", "RIC_Capital_Channel"]) == ["whale_alert"]


def test_moi_kenh_loai_tru_phai_co_ly_do():
    # Loại trừ không lý do = không ai biết khi nào được bỏ ra.
    assert all(reason.strip() for reason in EXCLUDED_CHANNELS.values())
