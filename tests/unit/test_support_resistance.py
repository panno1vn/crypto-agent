"""
tests/test_support_resistance.py

Ngày 9 — Unit Tests (đã fix + bổ sung sau review)
=====================================================
Thêm so với bản trước:
  - test_cluster_levels_no_chaining     : bug chính đã fix
  - test_cluster_levels_zero_division   : guard chia cho 0
  - test_cluster_levels_single_value    : edge case 1 phần tử
  - test_find_swing_points_multiple_sr  : nhiều đỉnh/đáy, kiểm tra thứ tự lấy [-3:]/[:3]
"""

import pandas as pd
import pytest

from technical_analysis.support_resistance import cluster_levels, find_swing_points


# ---------------------------------------------------------------------------
# TESTS CHO cluster_levels
# ---------------------------------------------------------------------------
def test_cluster_levels_empty():
    """Input rỗng phải trả về list rỗng."""
    assert cluster_levels([]) == []


def test_cluster_levels_grouping():
    """Các mức giá gần nhau (<0.5%) phải bị gộp lại."""
    levels = [40000, 40100, 45000]
    result = cluster_levels(levels, tolerance=0.005)

    assert len(result) == 2
    assert result[0] == 40050.0
    assert result[1] == 45000.0


def test_cluster_levels_no_chaining():
    """
    BUG FIX TEST: chuỗi giá tăng dần đều, mỗi bước < tolerance so với bước
    liền trước, nhưng biên độ đầu-cuối vượt xa tolerance.

    40000 -> 40150 -> 40300 -> 40450 -> 40600
    Mỗi bước ~0.37% (< 0.5%) nhưng biên độ tổng (40000->40600) là 1.5%.

    Với bug "chaining" (neo theo điểm cuối): tất cả gộp thành 1 cluster.
    Sau khi fix (neo theo điểm đầu): phải tách thành nhiều cluster vì
    40300 cách neo gốc 40000 đã là 0.75% (> 0.5%).
    """
    levels = [40000, 40150, 40300, 40450, 40600]
    result = cluster_levels(levels, tolerance=0.005)

    # Không được gộp hết thành 1 cluster duy nhất
    assert len(result) > 1, (
        "cluster_levels bị chaining: các mức tăng dần đều bị gộp sai "
        "thành 1 cluster dù biên độ vượt tolerance"
    )


def test_cluster_levels_zero_division_guard():
    """Level = 0 hoặc âm phải bị lọc bỏ, không được crash ZeroDivisionError."""
    levels = [0, 40000, 40100, -500]
    result = cluster_levels(levels, tolerance=0.005)

    # Không raise exception, và 0/-500 bị loại khỏi kết quả
    assert all(lv > 0 for lv in result)
    assert len(result) == 1  # 40000 và 40100 gộp lại


def test_cluster_levels_single_value():
    """Edge case: chỉ 1 phần tử."""
    assert cluster_levels([12345.0]) == [12345.0]


def test_cluster_levels_all_invalid():
    """Edge case: toàn bộ level <= 0 → trả về rỗng, không crash."""
    assert cluster_levels([0, -1, -100]) == []


# ---------------------------------------------------------------------------
# TESTS CHO find_swing_points
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_df():
    """DataFrame giả lập với mô hình sóng đơn giản (1 đỉnh, 1 đáy)."""
    dates = pd.date_range("2023-01-01", periods=15, freq="D")
    data = {
        "high": [20, 25, 30, 40, 45, 50, 45, 40, 35, 30, 20, 25, 30, 35, 40],
        "low": [15, 20, 25, 35, 40, 45, 40, 35, 30, 20, 10, 20, 25, 30, 35],
    }
    return pd.DataFrame(data, index=dates)


@pytest.fixture
def mock_df_multi_peaks():
    """
    DataFrame với NHIỀU đỉnh/đáy tách biệt rõ ràng, để kiểm tra
    logic lấy [-3:] (resistance) và [:3] (support) đúng thứ tự.
    3 đỉnh: 50, 60, 70 (resistance nên lấy đúng 3 mức cao nhất theo thứ tự tăng dần)
    3 đáy: 5, 10, 15 (support nên lấy đúng 3 mức thấp nhất theo thứ tự tăng dần)
    """
    n = 60
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    high = [20] * n
    low = [20] * n

    peak_positions = [10, 30, 50]
    peak_values = [50, 60, 70]
    trough_positions = [20, 40, 55]
    trough_values = [15, 10, 5]

    for pos, val in zip(peak_positions, peak_values):
        for offset in range(-3, 4):
            idx = pos + offset
            if 0 <= idx < n:
                high[idx] = val - abs(offset) * 2  # tạo hình chóp quanh đỉnh

    for pos, val in zip(trough_positions, trough_values):
        for offset in range(-3, 4):
            idx = pos + offset
            if 0 <= idx < n:
                low[idx] = val + abs(offset) * 2  # tạo hình đáy quanh trough

    return pd.DataFrame({"high": high, "low": low}, index=dates)


def test_find_swing_points_basic(mock_df):
    """Tìm đúng đỉnh cao nhất và đáy thấp nhất với window nhỏ."""
    result = find_swing_points(mock_df, window=3)

    assert len(result["swing_highs"]) > 0
    assert len(result["swing_lows"]) > 0
    assert result["swing_highs"][0][1] == 50
    assert result["swing_lows"][0][1] == 10


def test_find_swing_points_extracts_sr(mock_df):
    """Hàm phải trả về list S/R sau khi đã cluster."""
    result = find_swing_points(mock_df, window=3)

    assert isinstance(result["resistance"], list)
    assert isinstance(result["support"], list)
    assert len(result["resistance"]) == 1
    assert result["resistance"][0] == 50.0
    assert result["support"][0] == 10.0


def test_find_swing_points_short_df():
    """Edge case: DataFrame quá ngắn không đủ window."""
    dates = pd.date_range("2023-01-01", periods=5, freq="D")
    short_df = pd.DataFrame(
        {"high": [1, 2, 3, 4, 5], "low": [0, 1, 2, 3, 4]}, index=dates
    )

    result = find_swing_points(short_df, window=5)

    assert result["resistance"] == []
    assert result["support"] == []
    assert result["swing_highs"] == []
    assert result["swing_lows"] == []


def test_find_swing_points_none_df():
    """Edge case: df=None không được crash."""
    result = find_swing_points(None, window=5)
    assert result["resistance"] == []
    assert result["swing_highs"] == []


def test_find_swing_points_multiple_sr(mock_df_multi_peaks):
    """
    Kiểm tra logic lấy [-3:] cho resistance và [:3] cho support khi có
    NHIỀU đỉnh/đáy — đây là phần dễ sai nhất (nhầm lấy sai đầu danh sách).
    """
    result = find_swing_points(mock_df_multi_peaks, window=3)

    # resistance nên có 3 mức, tăng dần: 50, 60, 70
    assert len(result["resistance"]) == 3
    assert result["resistance"] == sorted(result["resistance"])
    assert result["resistance"][-1] > result["resistance"][0]

    # support nên có 3 mức thấp nhất, tăng dần: 5, 10, 15
    assert len(result["support"]) == 3
    assert result["support"] == sorted(result["support"])
    assert result["support"][0] < result["support"][-1]
