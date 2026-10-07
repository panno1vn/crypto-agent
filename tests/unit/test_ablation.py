"""
tests/unit/test_ablation.py

Ngày 32 — Test phần thuần của agent/ablation.py trên dữ liệu TỔNG HỢP.
vectorbt chạy thật. build_component_series() patch 3 hàm chạm DB.

Đây KHÔNG phải kết quả ablation: số thật phải chạy
scripts/manual/run_day32_ablation.py trên DB local.
"""

import math
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from agent.ablation import (
    Branch,
    branch_confidence,
    build_component_series,
    default_branches,
    describe_distribution,
    quantile_threshold,
    run_branch,
    sentiment_coverage,
    split_calibration,
    validate_components,
)
from agent.signal_aggregator import CONFIDENCE_CAP, TA_ONLY_WEIGHTS, SignalWeights

FULL = SignalWeights(technical=0.70, sentiment=0.15, news=0.15)
TH = dict(upper_threshold=0.1, lower_threshold=-0.1, min_message_count=1)


def _components(n=400, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    close = 30000 + np.cumsum(rng.normal(0, 50, n))
    direction = rng.choice(["long", "short", "neutral"], size=n, p=[0.5, 0.3, 0.2])
    strength = rng.uniform(0, 1, n)
    count = rng.integers(0, 4, n)
    sentiment = np.where(count > 0, rng.uniform(-1, 1, n), np.nan)
    return pd.DataFrame(
        {
            "close": close,
            "direction": direction,
            "strength": strength,
            "sentiment": sentiment,
            "message_count": count,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Nhánh
# ---------------------------------------------------------------------------
def test_default_branches_nhanh_2_chi_khac_nhanh_3_o_phan_news():
    ta, ta_s, full = default_branches(FULL)
    assert ta.weights == TA_ONLY_WEIGHTS
    assert ta_s.weights.sentiment == full.weights.sentiment
    assert ta_s.weights.news == 0.0
    assert ta_s.weights.technical == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# branch_confidence
# ---------------------------------------------------------------------------
def test_moc_neutral_bang_0_moc_short_co_confidence():
    df = _components()
    conf = branch_confidence(df, FULL, **TH)
    assert (conf[df["direction"] == "neutral"] == 0.0).all()
    assert (conf[df["direction"] == "short"] > 0).any()


def test_sides_chi_long_thi_short_bang_0():
    df = _components()
    conf = branch_confidence(df, FULL, **TH, sides=("long",))
    assert (conf[df["direction"] != "long"] == 0.0).all()


def test_sides_khong_hop_le_thi_raise():
    with pytest.raises(ValueError):
        branch_confidence(_components(10), FULL, **TH, sides=("buy",))


def test_short_doi_xung_voi_long():
    idx = pd.date_range("2026-01-01", periods=2, freq="1h")
    df = pd.DataFrame(
        {
            "close": [100.0, 100.0],
            "direction": ["long", "short"],
            "strength": [0.7, 0.7],
            "sentiment": [0.4, -0.4],
            "message_count": [3, 3],
        },
        index=idx,
    )
    conf = branch_confidence(df, FULL, **TH)
    assert conf.iloc[0] == pytest.approx(conf.iloc[1])


def test_ta_only_bang_strength_bi_chan_cap():
    df = _components()
    conf = branch_confidence(df, TA_ONLY_WEIGHTS, **TH)
    mask = df["direction"] != "neutral"
    expected = df.loc[mask, "strength"].clip(upper=CONFIDENCE_CAP)
    np.testing.assert_allclose(conf[mask], expected)


def test_moc_khong_co_sentiment_thi_moi_nhanh_bang_ta():
    df = _components()
    no_senti = (df["direction"] != "neutral") & (df["message_count"] == 0)
    assert no_senti.any()
    a = branch_confidence(df, FULL, **TH)[no_senti]
    b = branch_confidence(df, TA_ONLY_WEIGHTS, **TH)[no_senti]
    np.testing.assert_allclose(a, b)


def test_mot_moc_tinh_tay_khop_aggregator():
    idx = pd.date_range("2026-01-01", periods=1, freq="1h")
    df = pd.DataFrame(
        {
            "close": [100.0],
            "direction": ["long"],
            "strength": [0.8],
            "sentiment": [0.6],
            "message_count": [5],
        },
        index=idx,
    )
    # 0.70*0.8 + 0.15*0.8 + 0.15*1.0 (confirmed vì 0.6 > 0.1) = 0.83
    assert branch_confidence(df, FULL, **TH).iloc[0] == pytest.approx(0.83)


def test_min_message_count_cao_bien_moc_thanh_no_data():
    idx = pd.date_range("2026-01-01", periods=1, freq="1h")
    df = pd.DataFrame(
        {
            "close": [100.0],
            "direction": ["long"],
            "strength": [0.5],
            "sentiment": [0.9],
            "message_count": [2],
        },
        index=idx,
    )
    th = dict(TH, min_message_count=3)
    assert branch_confidence(df, FULL, **th).iloc[0] == pytest.approx(0.5)


def test_validate_components_bat_loi():
    df = _components(10)
    with pytest.raises(ValueError):
        validate_components(df.drop(columns=["sentiment"]))
    with pytest.raises(ValueError):
        validate_components(df.iloc[::-1])
    bad = df.copy()
    bad.iloc[0, bad.columns.get_loc("direction")] = "buy"
    with pytest.raises(ValueError):
        validate_components(bad)


# ---------------------------------------------------------------------------
# Hiệu chỉnh ngưỡng
# ---------------------------------------------------------------------------
def test_split_calibration_theo_thoi_gian_khong_chong_lan():
    df = _components(100)
    calib, ev = split_calibration(df, 0.3)
    assert len(calib) == 30 and len(ev) == 70
    assert calib.index.max() < ev.index.min()


@pytest.mark.parametrize("frac", [0.0, 1.0, -0.1])
def test_split_calibration_frac_sai(frac):
    with pytest.raises(ValueError):
        split_calibration(_components(10), frac)


def test_quantile_threshold_bo_qua_so_0():
    s = pd.Series([0.0] * 90 + [0.5] * 10)
    # Gộp số 0 vào thì quantile 0.75 = 0 → mọi tín hiệu thành lệnh.
    assert quantile_threshold(s, 0.75) == pytest.approx(0.5)


def test_quantile_threshold_khong_co_long_thi_raise():
    with pytest.raises(ValueError):
        quantile_threshold(pd.Series([0.0, 0.0]), 0.5)


def test_nguong_chi_phu_thuoc_doan_hieu_chinh():
    df = _components(400)
    br = Branch("ta_only", TA_ONLY_WEIGHTS)
    kw = dict(
        **TH,
        calib_frac=0.5,
        entry_quantile=0.75,
        exit_quantile=0.25,
        sl_pct=0.02,
        tp_pct=0.04,
    )
    base = run_branch(df, br, **kw)
    # Đổi mạnh đoạn đánh giá: ngưỡng KHÔNG được đổi (không nhìn tương lai).
    tampered = df.copy()
    tampered.iloc[200:, tampered.columns.get_loc("strength")] = 0.01
    after = run_branch(tampered, br, **kw)
    assert after["entry_threshold"] == base["entry_threshold"]
    assert after["exit_threshold"] == base["exit_threshold"]
    assert base["eval_candles"] == 200


def test_run_branch_tra_metric_va_huu_han():
    res = run_branch(
        _components(400),
        Branch("full", FULL),
        **TH,
        calib_frac=0.5,
        entry_quantile=0.75,
        exit_quantile=0.25,
        sl_pct=0.02,
        tp_pct=0.04,
    )
    for k in ("win_rate", "sharpe_ratio", "total_return", "entry_threshold"):
        assert math.isfinite(res[k])
    assert res["total_trades"] >= 0
    assert res["exit_threshold"] < res["entry_threshold"]


def _trend_components(direction: str, n=200) -> pd.DataFrame:
    """Giá đi một chiều đều đặn, TA luôn báo cùng hướng, strength đổi đều."""
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    step = 1.0 if direction == "long" else -1.0
    return pd.DataFrame(
        {
            "close": 1000.0 + step * np.arange(n),
            "direction": [direction] * n,
            "strength": np.tile([0.2, 0.5, 0.9, 0.6], n // 4),
            "sentiment": [np.nan] * n,
            "message_count": [0] * n,
        },
        index=idx,
    )


@pytest.mark.parametrize("direction", ["long", "short"])
def test_backtest_hai_chieu_loi_khi_dung_huong(direction):
    res = run_branch(
        _trend_components(direction),
        Branch("ta_only", TA_ONLY_WEIGHTS),
        **TH,
        calib_frac=0.5,
        entry_quantile=0.75,
        exit_quantile=0.25,
        sl_pct=0.5,
        tp_pct=0.5,
        fees=0.0,
    )
    assert res["total_trades"] > 0
    assert res["total_return"] > 0
    key = "long_entry_signals" if direction == "long" else "short_entry_signals"
    other = "short_entry_signals" if direction == "long" else "long_entry_signals"
    assert res[key] > 0 and res[other] == 0


def test_backtest_chi_long_tren_chuoi_toan_short_thi_raise():
    # sides=("long",) trên chuỗi toàn short: đoạn hiệu chỉnh không có tín
    # hiệu nào → raise, không âm thầm trả 0 lệnh.
    with pytest.raises(ValueError):
        run_branch(
            _trend_components("short"),
            Branch("ta_only", TA_ONLY_WEIGHTS),
            **TH,
            calib_frac=0.5,
            entry_quantile=0.75,
            exit_quantile=0.25,
            sl_pct=0.5,
            tp_pct=0.5,
            sides=("long",),
        )


def test_phi_lam_giam_loi_nhuan():
    kw = dict(
        **TH,
        calib_frac=0.5,
        entry_quantile=0.75,
        exit_quantile=0.25,
        sl_pct=0.5,
        tp_pct=0.5,
    )
    br = Branch("ta_only", TA_ONLY_WEIGHTS)
    free = run_branch(_trend_components("long"), br, **kw, fees=0.0)
    paid = run_branch(_trend_components("long"), br, **kw, fees=0.01)
    assert paid["total_return"] < free["total_return"]


def test_run_branch_exit_quantile_phai_nho_hon_entry():
    with pytest.raises(ValueError):
        run_branch(
            _components(100),
            Branch("x", FULL),
            **TH,
            calib_frac=0.5,
            entry_quantile=0.5,
            exit_quantile=0.5,
            sl_pct=0.02,
            tp_pct=0.04,
        )


# ---------------------------------------------------------------------------
# Thống kê
# ---------------------------------------------------------------------------
def test_describe_distribution():
    d = describe_distribution(pd.Series([0.0, 0.0, 0.5, 0.7]))
    assert d["n_positive"] == 2
    assert d["share_positive"] == 0.5
    assert d["share_gt_0_65"] == 0.5


def test_sentiment_coverage():
    df = _components(10)
    df["direction"] = "long"
    df["message_count"] = [0, 1, 2, 3, 0, 1, 2, 3, 0, 1]
    assert sentiment_coverage(df, 1) == pytest.approx(0.7)
    assert sentiment_coverage(df, 3) == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# build_component_series — patch biên DB, kiểm tra as_of và forward-fill
# ---------------------------------------------------------------------------
async def test_build_component_series_as_of_va_forward_fill():
    idx = pd.date_range("2026-01-01", periods=10, freq="1h")
    ohlcv = pd.DataFrame({"close": np.arange(10, dtype=float) + 100}, index=idx)
    ta_calls, senti_calls = [], []

    async def fake_fetch(session, coin, tf, limit, as_of=None):
        return ohlcv

    async def fake_confluence(session, coin, current_price=None, as_of=None):
        ta_calls.append(as_of)
        return SimpleNamespace(direction="long", strength=len(ta_calls) / 10)

    async def fake_aggregate(session, coin, window_hours, as_of=None):
        senti_calls.append(as_of)
        n = len(senti_calls)
        return SimpleNamespace(mean_weighted_score=0.2, message_count=n % 2)

    with patch(
        "technical_analysis.indicator_pipeline.fetch_ohlcv_from_db", new=fake_fetch
    ), patch(
        "technical_analysis.confluence.analyze_confluence", new=fake_confluence
    ), patch(
        "nlp.engagement_weighting.aggregate_coin_sentiment", new=fake_aggregate
    ):
        df = await build_component_series(
            None,
            "BTCUSDT",
            start=idx[2].to_pydatetime(),
            end=idx[9].to_pydatetime(),
            sentiment_window_hours=6,
            recompute_every_candles=3,
        )

    # 8 nến trong [idx2, idx9], recompute tại nến 0, 3, 6 của cửa sổ.
    assert len(df) == 8
    assert ta_calls == [idx[2], idx[5], idx[8]]
    assert senti_calls == ta_calls
    assert list(df["strength"]) == [0.1] * 3 + [0.2] * 3 + [0.3] * 2
    # message_count = 1 → có sentiment; = 0 → NaN (không giả 0.0)
    assert df["sentiment"].iloc[0] == 0.2
    assert math.isnan(df["sentiment"].iloc[3])


async def test_build_component_series_khong_co_nen_thi_raise():
    async def fake_fetch(session, coin, tf, limit, as_of=None):
        return pd.DataFrame()

    with patch(
        "technical_analysis.indicator_pipeline.fetch_ohlcv_from_db", new=fake_fetch
    ):
        with pytest.raises(ValueError):
            await build_component_series(
                None,
                "BTCUSDT",
                start=datetime(2026, 1, 1),
                end=datetime(2026, 1, 1) + timedelta(days=1),
                sentiment_window_hours=6,
            )


def test_khoi_gia_tri_dung_tran_van_vao_lenh():
    # Bug thật lần chạy thử 2026-10-08: >25% mốc bị trần 0.85 ép về đúng 0.85,
    # quantile 0.75 = 0.85, điều kiện `conf > 0.85` không bao giờ đúng → 0 lệnh.
    df = _trend_components("short")
    df["strength"] = np.tile([0.3, 0.5, 1.0, 1.0], len(df) // 4)
    res = run_branch(
        df,
        Branch("ta_only", TA_ONLY_WEIGHTS),
        **TH,
        calib_frac=0.5,
        entry_quantile=0.75,
        exit_quantile=0.25,
        sl_pct=0.5,
        tp_pct=0.5,
        fees=0.0,
    )
    assert res["entry_threshold"] == CONFIDENCE_CAP
    assert res["short_entry_signals"] > 0
    assert res["total_trades"] > 0


def test_cache_csv_round_trip_chinh_xac_tung_bit(tmp_path):
    # Giá trị THẬT từ cache XRP 2026-10-08: bộ đọc mặc định của pandas làm
    # tròn 0.44000000000000006 thành 0.44 → kết quả backtest đổi (2.435% so
    # với 1.875% từ DB) vì confidence dồn đúng tại ngưỡng.
    from agent.ablation import read_components_csv, write_components_csv

    df = _components(8)
    df.iloc[0, df.columns.get_loc("strength")] = 0.44000000000000006
    df.iloc[1, df.columns.get_loc("sentiment")] = -0.09880046339522146
    df.iloc[1, df.columns.get_loc("message_count")] = 1
    path = tmp_path / "c.csv"
    write_components_csv(df, path)
    back = read_components_csv(path)

    for col in ("close", "strength", "sentiment"):
        a, b = df[col].to_numpy(), back[col].to_numpy()
        same = (a == b) | (np.isnan(a) & np.isnan(b))
        assert same.all(), col
    assert (back["direction"] == df["direction"]).all()
    assert (back.index == df.index).all()
