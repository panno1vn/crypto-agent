"""
tests/unit/test_signal_aggregator.py

Ngày 31 — Test cho agent/signal_aggregator.py.

Dùng dataclass THẬT (ConfluentSignal, NewsConfirmation), không stub. Chỉ
generate_final_signal() patch 2 hàm chạm DB/Chroma ở cuối file.
"""

import math
from unittest.mock import AsyncMock, patch

import pytest

from agent import config as agent_config
from agent.signal_aggregator import (
    CONFIDENCE_CAP,
    TA_ONLY_WEIGHTS,
    SignalInputError,
    SignalWeights,
    aggregate_signal,
    combine_scores,
    generate_final_signal,
    load_signal_weights,
    sentiment_alignment,
)
from rag.news_confirmation import NewsConfirmation
from technical_analysis.confluence import ConfluentSignal

DEFAULT = SignalWeights(technical=0.70, sentiment=0.15, news=0.15)


def _ta(direction="long", strength=0.8, coin="BTCUSDT") -> ConfluentSignal:
    return ConfluentSignal(
        coin=coin,
        direction=direction,
        strength=strength,
        timeframes_aligned=3,
        entry_zone=(0.0, 0.0),
        key_sr_levels={"support": [], "resistance": []},
        atr_1h=100.0,
        fib_levels={},
        reasoning={},
    )


def _news(status="confirmed", score=0.6, count=5, coin="BTC") -> NewsConfirmation:
    return NewsConfirmation(
        coin=coin,
        status=status,
        sentiment_score=score,
        message_count=count,
        confidence_adjustment=0.0,
    )


# ---------------------------------------------------------------------------
# Trọng số
# ---------------------------------------------------------------------------
def test_load_signal_weights_khop_config_va_hop_le():
    w = load_signal_weights()
    assert (w.technical, w.sentiment, w.news) == (
        agent_config.SIGNAL_WEIGHT_TECHNICAL,
        agent_config.SIGNAL_WEIGHT_SENTIMENT,
        agent_config.SIGNAL_WEIGHT_NEWS,
    )


@pytest.mark.parametrize(
    "t, s, n",
    [
        (0.7, 0.15, 0.5),  # tổng 1.35: env gõ nhầm, không được tự chuẩn hóa
        (0.7, 0.15, 0.1),  # tổng 0.95
        (1.2, -0.1, -0.1),  # âm
        (float("nan"), 0.5, 0.5),
        (float("inf"), 0.0, 0.0),
    ],
)
def test_signal_weights_sai_thi_raise(t, s, n):
    with pytest.raises(SignalInputError):
        SignalWeights(technical=t, sentiment=s, news=n)


# ---------------------------------------------------------------------------
# Công thức
# ---------------------------------------------------------------------------
def test_long_confirmed_tinh_dung_cong_thuc():
    # 0.70*0.8 + 0.15*((0.6+1)/2) + 0.15*1.0 = 0.56 + 0.12 + 0.15 = 0.83
    sig = aggregate_signal(_ta("long", 0.8), _news("confirmed", 0.6), weights=DEFAULT)
    assert sig.confidence == pytest.approx(0.83)
    assert sig.technical_score == 0.8
    assert sig.sentiment_score == pytest.approx(0.8)
    assert sig.news_score == 1.0
    assert sig.sentiment_available is True
    assert sig.weights_used == DEFAULT


def test_confidence_bi_chan_o_085():
    sig = aggregate_signal(_ta("long", 1.0), _news("confirmed", 1.0), weights=DEFAULT)
    assert sig.confidence == CONFIDENCE_CAP
    assert "trần" in sig.reasoning


def test_short_doi_xung_voi_long():
    long_sig = aggregate_signal(
        _ta("long", 0.7), _news("confirmed", 0.4), weights=DEFAULT
    )
    short_sig = aggregate_signal(
        _ta("short", 0.7), _news("confirmed", -0.4), weights=DEFAULT
    )
    assert short_sig.confidence == pytest.approx(long_sig.confidence)
    assert short_sig.sentiment_score == pytest.approx(long_sig.sentiment_score)


def test_sentiment_nguoc_chieu_khong_lat_huong():
    sig = aggregate_signal(_ta("long", 0.8), _news("conflicted", -1.0), weights=DEFAULT)
    assert sig.direction == "long"
    # 0.70*0.8 + 0.15*0 + 0.15*0 = 0.56
    assert sig.confidence == pytest.approx(0.56)


# ---------------------------------------------------------------------------
# sentiment_available=False → dồn về TA, KHÔNG coi là 0.5
# ---------------------------------------------------------------------------
def test_no_data_don_trong_so_ve_ta():
    sig = aggregate_signal(_ta("long", 0.8), _news("no_data", None, 0), weights=DEFAULT)
    assert sig.sentiment_available is False
    assert sig.confidence == pytest.approx(0.8)
    assert sig.sentiment_score is None
    assert sig.news_score is None
    assert sig.weights_used == TA_ONLY_WEIGHTS


def test_no_data_khac_voi_trung_tinh_that():
    # "Không có dữ liệu" và "có dữ liệu, sentiment = 0" phải ra số khác nhau.
    no_data = aggregate_signal(
        _ta("long", 0.8), _news("no_data", None, 0), weights=DEFAULT
    )
    neutral = aggregate_signal(
        _ta("long", 0.8), _news("neutral", 0.0, 5), weights=DEFAULT
    )
    # neutral: 0.70*0.8 + 0.15*0.5 + 0.15*0.5 = 0.71
    assert neutral.confidence == pytest.approx(0.71)
    assert no_data.confidence != pytest.approx(neutral.confidence)


def test_no_data_do_min_message_count_bo_qua_score_that():
    # N27: dưới min_message_count status='no_data' nhưng sentiment_score vẫn
    # giữ giá trị thật để debug. Aggregator phải bỏ qua con số đó.
    sig = aggregate_signal(_ta("long", 0.5), _news("no_data", 0.9, 1), weights=DEFAULT)
    assert sig.sentiment_available is False
    assert sig.confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# should_trade
# ---------------------------------------------------------------------------
def test_neutral_khong_giao_dich():
    sig = aggregate_signal(_ta("neutral", 0.1), _news("neutral", 0.9), weights=DEFAULT)
    assert sig.confidence == 0.0
    assert sig.should_trade is False


def test_should_trade_nguong_strict():
    at = aggregate_signal(
        _ta("long", 0.65), _news("no_data", None, 0), min_confidence=0.65
    )
    above = aggregate_signal(
        _ta("long", 0.66), _news("no_data", None, 0), min_confidence=0.65
    )
    assert at.should_trade is False
    assert above.should_trade is True


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------
def test_coin_dang_cap_va_dang_ngan_khop_nhau_giu_dang_cap():
    sig = aggregate_signal(_ta(coin="BTCUSDT"), _news(coin="BTC"), weights=DEFAULT)
    assert sig.coin == "BTCUSDT"


def test_coin_lech_thi_raise():
    with pytest.raises(SignalInputError):
        aggregate_signal(_ta(coin="BTCUSDT"), _news(coin="ETH"), weights=DEFAULT)


# ---------------------------------------------------------------------------
# Fail loudly
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("strength", [float("nan"), float("inf"), -0.1, 1.01, None])
def test_strength_khong_hop_le_thi_raise(strength):
    with pytest.raises(SignalInputError):
        aggregate_signal(_ta(strength=strength), _news(), weights=DEFAULT)


def test_direction_la_thi_raise():
    with pytest.raises(SignalInputError):
        aggregate_signal(_ta(direction="buy"), _news(), weights=DEFAULT)


def test_status_la_thi_raise():
    with pytest.raises(SignalInputError):
        aggregate_signal(_ta(), _news(status="bullish"), weights=DEFAULT)


def test_status_co_du_lieu_ma_score_none_thi_raise():
    with pytest.raises(SignalInputError):
        aggregate_signal(_ta(), _news("confirmed", None), weights=DEFAULT)


@pytest.mark.parametrize("score", [1.5, -1.01, float("nan")])
def test_sentiment_ngoai_mien_thi_raise(score):
    with pytest.raises(SignalInputError):
        aggregate_signal(_ta(), _news("confirmed", score), weights=DEFAULT)


def test_sentiment_alignment_neutral_raise():
    with pytest.raises(SignalInputError):
        sentiment_alignment(0.3, "neutral")


def test_combine_scores_thieu_mot_trong_hai_thi_raise():
    with pytest.raises(SignalInputError):
        combine_scores(0.8, 0.5, None, DEFAULT)


def test_combine_scores_ket_qua_huu_han_trong_mien():
    for t in (0.0, 0.3, 1.0):
        for s in (0.0, 0.5, 1.0):
            for n in (0.0, 0.5, 1.0):
                conf, _ = combine_scores(t, s, n, DEFAULT)
                assert math.isfinite(conf)
                assert 0.0 <= conf <= CONFIDENCE_CAP


# ---------------------------------------------------------------------------
# generate_final_signal: chỉ nối dây. Patch 2 biên chạm DB/Chroma.
# ---------------------------------------------------------------------------
async def test_generate_final_signal_noi_dung_3_buoc():
    ta = _ta("long", 0.8)
    news = _news("confirmed", 0.6)
    with patch(
        "technical_analysis.confluence.analyze_confluence",
        new=AsyncMock(return_value=ta),
    ) as m_ta, patch(
        "rag.news_confirmation.correlate_news_with_technical",
        new=AsyncMock(return_value=news),
    ) as m_news:
        sig = await generate_final_signal(session=object(), coin="BTCUSDT")

    assert m_ta.await_args.args[1] == "BTCUSDT"
    assert m_news.await_args.args[2] is ta
    assert sig.coin == "BTCUSDT"
    assert sig.sentiment_available is True
