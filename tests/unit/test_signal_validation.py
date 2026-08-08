"""Test thuần, không cần DB — chỉ test logic chuẩn hoá symbol."""

from nlp.signal_validation import _to_short_symbol


def test_to_short_symbol_usdt():
    assert _to_short_symbol("BTCUSDT") == "BTC"


def test_to_short_symbol_already_short():
    assert _to_short_symbol("BTC") == "BTC"


def test_to_short_symbol_other_suffix():
    assert _to_short_symbol("ETHBUSD") == "ETH"


def test_to_short_symbol_short_but_matches_suffix_only():
    # "USD" không phải suffix hợp lệ nếu nó LÀ toàn bộ chuỗi
    assert _to_short_symbol("USD") == "USD"
