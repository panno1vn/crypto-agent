"""
tests/unit/test_risk_manager.py

Ngày 30 — Test cho agent/risk_manager.py

Logic thuần, không cần DB. Số kỳ vọng tính tay từ RISK_CONFIG, nên khi
đổi RISK_CONFIG thì các test số cụ thể cũng phải đổi theo (cố ý: buộc
người đổi cấu hình nhìn thấy hệ quả).
"""

import math
from decimal import Decimal

import pytest

from agent.risk_manager import RISK_CONFIG, RiskInputError, RiskLevel, calculate_sl_tp

ENTRY = 60000.0
ATR = 600.0
BIG_BALANCE = 1_000_000.0


def _setup(direction="long", level=RiskLevel.MEDIUM, **kw):
    args = dict(
        entry_price=ENTRY,
        direction=direction,
        atr=ATR,
        risk_level=level,
        support_levels=[],
        resistance_levels=[],
        account_balance=BIG_BALANCE,
    )
    args.update(kw)
    return calculate_sl_tp(**args)


# ---------------------------------------------------------------------------
# 6 kịch bản cơ bản: Long/Short × 3 risk level
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("level", list(RiskLevel))
def test_long_dung_chieu_va_dung_khoang_cach(level):
    p = RISK_CONFIG[level]
    s = _setup("long", level)
    assert s.stop_loss < s.entry_price < s.take_profit
    assert s.stop_loss == pytest.approx(ENTRY - p.sl_atr_mult * ATR)
    assert s.take_profit == pytest.approx(ENTRY + p.tp_rr * p.sl_atr_mult * ATR)
    assert s.rr_ratio == p.tp_rr


@pytest.mark.parametrize("level", list(RiskLevel))
def test_short_dung_chieu_va_dung_khoang_cach(level):
    p = RISK_CONFIG[level]
    s = _setup("short", level)
    assert s.take_profit < s.entry_price < s.stop_loss
    assert s.stop_loss == pytest.approx(ENTRY + p.sl_atr_mult * ATR)
    assert s.take_profit == pytest.approx(ENTRY - p.tp_rr * p.sl_atr_mult * ATR)


@pytest.mark.parametrize("level", list(RiskLevel))
def test_khoi_luong_theo_rui_ro_khi_khong_bi_chan(level):
    # Không bị chặn khi sl_distance >= risk_pct × entry. Với entry 60000,
    # HIGH cần 2×ATR >= 3000, nên dùng ATR 3000 (5% giá) cho cả 3 mức.
    p = RISK_CONFIG[level]
    s = _setup("long", level, atr=3000.0)
    assert not s.capped_by_balance
    # Chạm SL thì mất đúng risk_pct × balance.
    assert s.risk_amount == pytest.approx(BIG_BALANCE * p.risk_pct)
    assert s.quantity * s.sl_distance == pytest.approx(BIG_BALANCE * p.risk_pct)


# ---------------------------------------------------------------------------
# Sizing bị chặn ở balance (ví dụ Pan đã duyệt 2026-10-06)
# ---------------------------------------------------------------------------
def test_notional_bi_chan_o_balance():
    s = _setup("long", RiskLevel.LOW, account_balance=1000.0)
    # Mục tiêu: 20$ / 600 = 0.0333 BTC = 2000$ > 1000$ → chặn.
    assert s.capped_by_balance
    assert s.position_notional == pytest.approx(1000.0)
    assert s.quantity == pytest.approx(1000.0 / ENTRY)
    # Rủi ro thật = 0.016667 × 600 = 10$, KHÔNG phải 20$ mục tiêu.
    assert s.risk_amount == pytest.approx(10.0)


@pytest.mark.parametrize("level", list(RiskLevel))
def test_chan_balance_khong_phu_thuoc_do_lon_balance(level):
    # Điều kiện chặn: risk_pct × entry / sl_distance > 1, không chứa balance.
    # ATR = 1% giá (mức thường gặp ở khung 1h) → mọi mức đều bị chặn, rủi
    # ro thật = sl_distance/entry × balance, thấp hơn risk_pct. Test này ghi
    # lại hành vi đó để ai đổi RISK_CONFIG cũng thấy (xem nhật ký N29).
    p = RISK_CONFIG[level]
    for balance in (100.0, 1_000_000.0):
        s = _setup("long", level, account_balance=balance)
        assert s.capped_by_balance
        assert s.risk_amount == pytest.approx(balance * p.sl_atr_mult * ATR / ENTRY)
        assert s.risk_amount < balance * p.risk_pct


# ---------------------------------------------------------------------------
# Edge case bắt buộc (roadmap N30): atr = 0, NaN, không có S/R, balance = 0
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_atr", [0.0, -1.0, float("nan"), float("inf"), None])
def test_atr_khong_hop_le_phai_raise(bad_atr):
    # atr=0.0 là giá trị analyze_confluence() gán khi ATR NaN/thiếu khung 1h.
    with pytest.raises(RiskInputError):
        _setup(atr=bad_atr)


@pytest.mark.parametrize("bad_balance", [0.0, -100.0, float("nan")])
def test_balance_khong_hop_le_phai_raise(bad_balance):
    with pytest.raises(RiskInputError):
        _setup(account_balance=bad_balance)


@pytest.mark.parametrize("bad_entry", [0.0, -5.0, float("nan")])
def test_entry_khong_hop_le_phai_raise(bad_entry):
    with pytest.raises(RiskInputError):
        _setup(entry_price=bad_entry)


def test_khong_co_sr_van_tao_lenh():
    s = _setup("long", support_levels=[], resistance_levels=[])
    assert s.nearest_support is None
    assert s.nearest_resistance is None
    assert s.tp_obstructed_by is None


def test_sr_none_phai_raise():
    with pytest.raises(RiskInputError):
        _setup(support_levels=None)


def test_muc_sr_nan_phai_raise_khong_bo_qua_im_lang():
    with pytest.raises(RiskInputError):
        _setup(resistance_levels=[61000.0, float("nan")])


# ---------------------------------------------------------------------------
# Hướng / risk level không hợp lệ
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_dir", ["neutral", "LONG", "buy", ""])
def test_direction_khong_hop_le_phai_raise(bad_dir):
    with pytest.raises(RiskInputError):
        _setup(direction=bad_dir)


def test_risk_level_dang_chuoi_phai_raise():
    with pytest.raises(RiskInputError):
        _setup(level="medium")


# ---------------------------------------------------------------------------
# Giá âm: ATR quá lớn so với entry
# ---------------------------------------------------------------------------
def test_long_sl_am_phai_raise():
    # HIGH: SL = 100 - 2×60 = -20
    with pytest.raises(RiskInputError):
        _setup("long", RiskLevel.HIGH, entry_price=100.0, atr=60.0)


def test_short_tp_am_phai_raise():
    # HIGH: TP = 100 - 3×2×20 = -20, còn SL = 140 vẫn dương
    with pytest.raises(RiskInputError):
        _setup("short", RiskLevel.HIGH, entry_price=100.0, atr=20.0)


# ---------------------------------------------------------------------------
# S/R: phân loại lại theo vị trí, báo mức chắn TP
# ---------------------------------------------------------------------------
def test_support_nam_tren_gia_duoc_coi_la_khang_cu():
    # Đáy cũ 61000 nằm TRÊN entry (sau một cú sập) → thực chất là kháng cự.
    s = _setup("long", support_levels=[58000.0, 61000.0], resistance_levels=[])
    assert s.nearest_support == 58000.0
    assert s.nearest_resistance == 61000.0


def test_long_tp_bi_chan_boi_khang_cu_gan_nhat():
    # MEDIUM long: TP = 60000 + 2×900 = 61800. Kháng cự 61000 chắn giữa.
    s = _setup(
        "long",
        RiskLevel.MEDIUM,
        resistance_levels=[61000.0, 61500.0, 63000.0],
    )
    assert s.tp_obstructed_by == 61000.0
    # S/R không làm dịch TP (quyết định thiết kế 1).
    assert s.take_profit == pytest.approx(61800.0)


def test_short_tp_bi_chan_boi_ho_tro_gan_nhat():
    # MEDIUM short: TP = 60000 - 1800 = 58200. Hỗ trợ 59000 chắn giữa.
    s = _setup("short", RiskLevel.MEDIUM, support_levels=[57000.0, 59000.0])
    assert s.tp_obstructed_by == 59000.0


def test_khang_cu_ngoai_tp_khong_tinh_la_chan():
    s = _setup("long", RiskLevel.MEDIUM, resistance_levels=[65000.0])
    assert s.tp_obstructed_by is None
    assert s.nearest_resistance == 65000.0


# ---------------------------------------------------------------------------
# Kiểu dữ liệu từ DB: technical_indicators.atr_14 là DECIMAL(20, 8)
# ---------------------------------------------------------------------------
def test_nhan_decimal_tu_db():
    s = _setup(
        entry_price=Decimal("60000.00000000"),
        atr=Decimal("600.00000000"),
        support_levels=[Decimal("59000.5")],
    )
    assert isinstance(s.stop_loss, float)
    assert s.nearest_support == pytest.approx(59000.5)
    assert math.isfinite(s.quantity)
