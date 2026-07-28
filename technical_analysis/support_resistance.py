"""
technical_analysis/support_resistance.py

Ngày 9 — Support/Resistance Detector (đã fix sau review)
============================================================
Fixes so với bản trước:
  1. cluster_levels(): neo so sánh theo điểm ĐẦU cluster thay vì điểm
     CUỐI — tránh bug "chaining" khiến các mức giá tăng dần đều bị gộp
     sai thành 1 cluster dù biên độ đầu-cuối vượt xa tolerance.
  2. cluster_levels(): guard chia cho 0 nếu có level = 0 (dữ liệu bẩn).
  3. find_swing_points(): log khi df quá ngắn thay vì im lặng trả rỗng.
"""

from typing import Dict, List, Tuple

import pandas as pd

from data_pipeline.logger import get_logger

logger = get_logger(__name__)


def cluster_levels(levels: List[float], tolerance: float = 0.005) -> List[float]:
    """
    Gộp các mức giá gần nhau (trong biên độ tolerance) thành 1 mức trung bình.

    QUAN TRỌNG: so sánh mỗi level mới với điểm ĐẦU TIÊN của cluster hiện tại
    (clusters[-1][0]), KHÔNG phải điểm cuối cùng vừa thêm vào (clusters[-1][-1]).

    Lý do: nếu neo theo điểm cuối, một chuỗi giá tăng dần đều với mỗi bước
    < tolerance (vd: 40000 -> 40150 -> 40300 -> 40450 -> 40600, mỗi bước ~0.37%)
    sẽ bị "chaining" gộp hết thành 1 cluster, dù biên độ đầu-cuối (40000-40600,
    ~1.5%) đã vượt xa tolerance ban đầu. Neo theo điểm đầu đảm bảo mọi thành
    viên trong cùng 1 cluster đều nằm trong tolerance so với mức neo gốc.
    """
    if not levels:
        return []

    # Lọc bỏ level <= 0 (dữ liệu bẩn) để tránh chia cho 0
    clean_levels = [lv for lv in levels if lv > 0]
    if len(clean_levels) < len(levels):
        logger.warning(
            f"[S/R] Dropped {len(levels) - len(clean_levels)} invalid level(s) <= 0"
        )
    if not clean_levels:
        return []

    sorted_levels = sorted(clean_levels)
    clusters = [[sorted_levels[0]]]

    for level in sorted_levels[1:]:
        anchor = clusters[-1][0]  # neo theo điểm ĐẦU cluster, không phải cuối
        if (level - anchor) / anchor < tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    return [sum(c) / len(c) for c in clusters]


def find_swing_points(df: pd.DataFrame, window: int = 5) -> Dict[str, List]:
    """Tìm các đỉnh (swing highs) và đáy (swing lows) cục bộ."""
    highs: List[Tuple[pd.Timestamp, float]] = []
    lows: List[Tuple[pd.Timestamp, float]] = []

    if df is None or len(df) <= window * 2:
        logger.warning(
            f"[S/R] DataFrame too short for window={window} "
            f"(len={0 if df is None else len(df)}). Returning empty result."
        )
        return {"resistance": [], "support": [], "swing_highs": [], "swing_lows": []}

    for i in range(window, len(df) - window):
        if df["high"].iloc[i] == df["high"].iloc[i - window : i + window + 1].max():
            highs.append((df.index[i], float(df["high"].iloc[i])))

        if df["low"].iloc[i] == df["low"].iloc[i - window : i + window + 1].min():
            lows.append((df.index[i], float(df["low"].iloc[i])))

    return {
        "resistance": cluster_levels([h[1] for h in highs])[-3:],
        "support": cluster_levels([lvl[1] for lvl in lows])[:3],
        "swing_highs": highs,
        "swing_lows": lows,
    }
