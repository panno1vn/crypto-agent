"""
tests/unit/test_engagement_weighting.py

Ngày 19 — Unit tests cho engagement_weighted_score() (pure function,
KHÔNG cần DB, chạy được ở mọi môi trường CI).

Aggregate functions (aggregate_coin_sentiment/aggregate_channel_sentiment)
cần DB thật — xem tests/integration/test_sentiment_pipeline.py.
"""

import pytest

from nlp.engagement_weighting import engagement_weighted_score

# ---------------------------------------------------------------------------
# Group 1: Hành vi cơ bản
# ---------------------------------------------------------------------------


def test_zero_engagement_returns_score_unchanged():
    """views=0, forwards=0 -> weight=1.0 -> output = sentiment_score gốc."""
    result = engagement_weighted_score(
        sentiment_score=0.5, views=0, forwards=0, channel_credibility=1.0
    )
    assert result == pytest.approx(0.5)


def test_neutral_score_stays_zero_regardless_of_engagement():
    """
    sentiment_score=0.0 (neutral) -> 0.0 * weight luôn = 0.0, bất kể
    engagement cao tới đâu. Đảm bảo message viral nhưng trung tính
    không bị "bơm" thành có ý nghĩa.
    """
    result = engagement_weighted_score(
        sentiment_score=0.0, views=1_000_000, forwards=50_000
    )
    assert result == 0.0


def test_positive_engagement_increases_magnitude():
    """Engagement cao hơn -> |output| lớn hơn base (cho score dương)."""
    base = engagement_weighted_score(sentiment_score=0.5, views=0, forwards=0)
    boosted = engagement_weighted_score(sentiment_score=0.5, views=5000, forwards=500)
    assert boosted > base


def test_negative_sentiment_stays_negative_with_engagement():
    """Score âm nhân với weight dương vẫn phải ra âm, không đổi dấu."""
    result = engagement_weighted_score(sentiment_score=-0.4, views=2000, forwards=100)
    assert result < 0


# ---------------------------------------------------------------------------
# Group 2: Clipping — luôn trong [-1.0, 1.0]
# ---------------------------------------------------------------------------


def test_clips_to_upper_bound():
    result = engagement_weighted_score(
        sentiment_score=0.9,
        views=10_000_000,
        forwards=1_000_000,
        channel_credibility=5.0,
    )
    assert result == 1.0


def test_clips_to_lower_bound():
    result = engagement_weighted_score(
        sentiment_score=-0.9,
        views=10_000_000,
        forwards=1_000_000,
        channel_credibility=5.0,
    )
    assert result == -1.0


# ---------------------------------------------------------------------------
# Group 3: channel_credibility phải thực sự có tác dụng
# ---------------------------------------------------------------------------


def test_higher_credibility_increases_magnitude():
    """
    Đây là test QUAN TRỌNG NHẤT trong file — verify credibility không
    còn bị "khai báo nhưng không dùng" như lỗi đã audit trong
    pseudocode gốc (channel_credibility declared but never passed).
    """
    low = engagement_weighted_score(
        sentiment_score=0.5, views=5000, forwards=500, channel_credibility=0.5
    )
    high = engagement_weighted_score(
        sentiment_score=0.5, views=5000, forwards=500, channel_credibility=1.5
    )
    assert high > low


def test_default_credibility_is_one():
    """Không truyền channel_credibility -> mặc định 1.0, không phải 0."""
    with_default = engagement_weighted_score(
        sentiment_score=0.5, views=1000, forwards=100
    )
    with_explicit_one = engagement_weighted_score(
        sentiment_score=0.5, views=1000, forwards=100, channel_credibility=1.0
    )
    assert with_default == pytest.approx(with_explicit_one)


# ---------------------------------------------------------------------------
# Group 4: Input validation
# ---------------------------------------------------------------------------


def test_negative_views_raises_value_error():
    with pytest.raises(ValueError, match="không được âm"):
        engagement_weighted_score(sentiment_score=0.5, views=-1, forwards=0)


def test_negative_forwards_raises_value_error():
    with pytest.raises(ValueError, match="không được âm"):
        engagement_weighted_score(sentiment_score=0.5, views=0, forwards=-1)
