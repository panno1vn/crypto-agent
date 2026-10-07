"""
agent/risk_manager.py

Ngày 29 — Risk Manager: từ (entry, hướng, ATR, mức rủi ro, S/R, balance)
ra một TradeSetup có SL, TP và khối lượng lệnh.

Quyết định thiết kế (chi tiết + phương án thay thế: docs/nhat-ky/, task N29):

  1. SL/TP CHỈ dựa vào ATR + R:R. S/R KHÔNG dịch chuyển SL/TP.
     Roadmap không nêu luật "snap SL theo support" nào, và chưa có số đo
     nào cho thấy luật đó tốt hơn. Theo luật "đo trước khi quyết", S/R ở
     đây chỉ dùng để BÁO: mức S/R gần nhất mỗi phía, và mức kháng cự/hỗ
     trợ nằm chắn giữa entry và TP (`tp_obstructed_by`). Việc có dịch TP
     hay không để backtest quyết định sau.

  2. `risk_pct` = % balance chấp nhận MẤT nếu chạm SL (Pan chốt
     2026-10-06). Khối lượng = risk_amount / khoảng cách SL. Tổng giá trị
     lệnh bị chặn ở balance (spot, không đòn bẩy); khi bị chặn,
     `capped_by_balance=True` và `risk_amount` là số mất THẬT, không phải
     số mục tiêu.

  3. Fail loudly. Input không hợp lệ thì raise RiskInputError, không bao
     giờ trả setup với giá trị mặc định. Đặc biệt `atr <= 0`:
     `analyze_confluence()` gán `atr_1h = 0.0` khi ATR là NaN hoặc thiếu
     khung 1h (technical_analysis/confluence.py), nên 0 ở đây nghĩa là
     "không có dữ liệu", không phải "thị trường đứng yên".

  4. S/R được lọc lại theo vị trí so với entry, KHÔNG tin nhãn
     "support"/"resistance" của đầu vào. find_swing_points() trả các đáy
     cũ, có thể đã nằm TRÊN giá hiện tại sau một cú sập; một "support"
     như vậy thực chất là kháng cự.

Các con số trong RISK_CONFIG là điểm xuất phát lấy từ roadmap, CHƯA ĐO.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Optional

from data_pipeline.logger import get_logger

logger = get_logger(__name__)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RiskParams:
    sl_atr_mult: float  # khoảng cách SL = sl_atr_mult × ATR
    tp_rr: float  # khoảng cách TP = tp_rr × khoảng cách SL
    risk_pct: float  # % balance mất nếu chạm SL


# CHƯA ĐO — giá trị gợi ý từ crypto_agent_roadmap_v2.md (N29).
RISK_CONFIG: dict[RiskLevel, RiskParams] = {
    RiskLevel.LOW: RiskParams(sl_atr_mult=1.0, tp_rr=1.5, risk_pct=0.02),
    RiskLevel.MEDIUM: RiskParams(sl_atr_mult=1.5, tp_rr=2.0, risk_pct=0.03),
    RiskLevel.HIGH: RiskParams(sl_atr_mult=2.0, tp_rr=3.0, risk_pct=0.05),
}


class RiskInputError(ValueError):
    """Input của Risk Manager không hợp lệ — không được tạo lệnh."""


@dataclass(frozen=True)
class TradeSetup:
    direction: Literal["long", "short"]
    risk_level: RiskLevel
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    sl_distance: float
    rr_ratio: float
    quantity: float
    position_notional: float
    risk_amount: float
    capped_by_balance: bool
    nearest_support: Optional[float]
    nearest_resistance: Optional[float]
    tp_obstructed_by: Optional[float]


def _finite_positive(name: str, value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise RiskInputError(f"{name} phải là số, nhận {value!r}") from exc
    if not math.isfinite(v):
        raise RiskInputError(f"{name} không hữu hạn: {v}")
    if v <= 0:
        raise RiskInputError(f"{name} phải > 0, nhận {v}")
    return v


def _clean_levels(name: str, levels: Iterable) -> list[float]:
    if levels is None:
        raise RiskInputError(f"{name} là None; không có S/R thì truyền list rỗng")
    return [_finite_positive(f"{name}[{i}]", lv) for i, lv in enumerate(levels)]


def calculate_sl_tp(
    entry_price: float,
    direction: str,
    atr: float,
    risk_level: RiskLevel,
    support_levels: Iterable[float],
    resistance_levels: Iterable[float],
    account_balance: float,
) -> TradeSetup:
    """
    Tính SL, TP và khối lượng lệnh.

    Bất biến của kết quả: long → SL < entry < TP; short → TP < entry < SL;
    mọi giá > 0; position_notional <= account_balance.

    Raises:
        RiskInputError: hướng không phải long/short, risk_level lạ, hoặc
            entry/atr/balance/mức S/R không phải số hữu hạn > 0, hoặc SL/TP
            tính ra <= 0 (ATR quá lớn so với giá).
    """
    if direction not in ("long", "short"):
        raise RiskInputError(
            f"direction phải là 'long' hoặc 'short', nhận {direction!r} "
            "(tín hiệu 'neutral' không được tạo lệnh)"
        )
    if not isinstance(risk_level, RiskLevel):
        raise RiskInputError(f"risk_level phải là RiskLevel, nhận {risk_level!r}")

    entry = _finite_positive("entry_price", entry_price)
    atr_v = _finite_positive("atr", atr)
    balance = _finite_positive("account_balance", account_balance)
    supports = _clean_levels("support_levels", support_levels)
    resistances = _clean_levels("resistance_levels", resistance_levels)

    params = RISK_CONFIG[risk_level]
    sl_distance = params.sl_atr_mult * atr_v
    tp_distance = params.tp_rr * sl_distance

    if direction == "long":
        stop_loss = entry - sl_distance
        take_profit = entry + tp_distance
    else:
        stop_loss = entry + sl_distance
        take_profit = entry - tp_distance

    if stop_loss <= 0 or take_profit <= 0:
        raise RiskInputError(
            f"SL={stop_loss} / TP={take_profit} <= 0: ATR={atr_v} quá lớn so với "
            f"entry={entry} ở mức {risk_level.value}"
        )

    # Phân loại lại theo vị trí thật so với entry (quyết định thiết kế 4).
    all_levels = supports + resistances
    below = sorted(lv for lv in all_levels if lv < entry)
    above = sorted(lv for lv in all_levels if lv > entry)
    nearest_support = below[-1] if below else None
    nearest_resistance = above[0] if above else None

    if direction == "long":
        blockers = [lv for lv in above if lv < take_profit]
        tp_obstructed_by = blockers[0] if blockers else None
    else:
        blockers = [lv for lv in below if lv > take_profit]
        tp_obstructed_by = blockers[-1] if blockers else None

    target_risk = balance * params.risk_pct
    quantity = target_risk / sl_distance
    capped = quantity * entry > balance
    if capped:
        quantity = balance / entry
    position_notional = quantity * entry
    risk_amount = quantity * sl_distance

    if capped:
        logger.info(
            f"[RISK] notional chặn ở balance={balance}: rủi ro thật "
            f"{risk_amount:.4f} < mục tiêu {target_risk:.4f}"
        )

    return TradeSetup(
        direction=direction,
        risk_level=risk_level,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr=atr_v,
        sl_distance=sl_distance,
        rr_ratio=params.tp_rr,
        quantity=quantity,
        position_notional=position_notional,
        risk_amount=risk_amount,
        capped_by_balance=capped,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        tp_obstructed_by=tp_obstructed_by,
    )
