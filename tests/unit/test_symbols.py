"""
tests/unit/test_symbols.py

Ngày 31 — Pin quy ước symbol (nợ #2).

Thay cho tests/unit/test_signal_validation.py (chỉ test stopgap
`_to_short_symbol`, đã xóa). Ba nhóm test:
  1. Hành vi to_base_symbol / to_pair_symbol, kể cả ca fail loudly.
  2. Pin QUY ƯỚC giữa 2 miền: mọi cặp Binance pipeline tải về phải chuẩn
     hóa ra một coin mà extract_coins() nhận ra. Ai thêm coin vào COINS mà
     quên CRYPTO_ENTITIES (hoặc đổi dạng lưu coins_mentioned) thì test ĐỎ.
  3. Pin FIX GỐC: aggregate_coin_sentiment("BTCUSDT") phải query 'BTC'.
     Dùng session giả (FakeSession) chỉ để bắt câu SQL, vì DB thật không
     có trong unit test; bản chạy trên Postgres thật nằm ở
     tests/integration/test_news_confirmation_e2e.py.
"""

import pytest
from sqlalchemy.dialects import postgresql

from data_pipeline.binance.ohlcv_pipeline import COINS as BINANCE_PAIRS
from data_pipeline.symbols import (
    QUOTE_ASSET,
    SymbolError,
    to_base_symbol,
    to_pair_symbol,
)
from data_pipeline.telegram.historical_scraper import CRYPTO_ENTITIES, extract_coins
from nlp.engagement_weighting import aggregate_coin_sentiment
from technical_analysis.indicator_pipeline import COINS as INDICATOR_PAIRS


# ---------------------------------------------------------------------------
# 1. Hành vi
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BTCUSDT", "BTC"),
        ("BTC", "BTC"),
        ("btcusdt", "BTC"),
        ("  eth  ", "ETH"),
        ("ETHBUSD", "ETH"),  # BUSD phải thử trước USD, nếu không ra "ETHB"
        ("SOLUSDC", "SOL"),
        ("XRPUSD", "XRP"),
    ],
)
def test_to_base_symbol(raw, expected):
    assert to_base_symbol(raw) == expected


@pytest.mark.parametrize("raw", ["BTCUSDT", "BTC", "ethbusd"])
def test_to_base_symbol_idempotent(raw):
    once = to_base_symbol(raw)
    assert to_base_symbol(once) == once


@pytest.mark.parametrize("raw", ["", "   ", "USDT", "usd", "BUSD"])
def test_to_base_symbol_raise_khi_rong_hoac_chi_la_quote(raw):
    # Trước N31 stopgap trả "USD" nguyên văn -> query 0 dòng, không báo lỗi.
    with pytest.raises(SymbolError):
        to_base_symbol(raw)


@pytest.mark.parametrize("raw", [None, 123, ["BTC"]])
def test_to_base_symbol_raise_khi_khong_phai_str(raw):
    with pytest.raises(SymbolError):
        to_base_symbol(raw)


def test_symbol_error_la_value_error():
    # Nơi gọi cũ đang bắt ValueError vẫn bắt được.
    assert issubclass(SymbolError, ValueError)


@pytest.mark.parametrize(
    "raw, expected", [("BTC", "BTCUSDT"), ("BTCUSDT", "BTCUSDT"), ("eth", "ETHUSDT")]
)
def test_to_pair_symbol(raw, expected):
    assert to_pair_symbol(raw) == expected


# ---------------------------------------------------------------------------
# 2. Pin quy ước giữa 2 miền
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("pair", sorted(set(BINANCE_PAIRS) | set(INDICATOR_PAIRS)))
def test_moi_cap_binance_khop_mot_coin_telegram(pair):
    base = to_base_symbol(pair)
    assert base in CRYPTO_ENTITIES, (
        f"{pair} -> {base} không có trong CRYPTO_ENTITIES: tin Telegram về coin "
        f"này sẽ không bao giờ được gắn coins_mentioned, sentiment luôn no_data"
    )
    assert to_pair_symbol(base) == pair
    assert pair.endswith(QUOTE_ASSET)


@pytest.mark.parametrize("pair", sorted(set(BINANCE_PAIRS)))
def test_coins_mentioned_luu_dang_ngan(pair):
    # extract_coins() là nguồn ghi coins_mentioned. Nếu ai đổi nó sang lưu
    # dạng cặp, aggregate_coin_sentiment (lọc dạng ngắn) sẽ ra 0 tin.
    base = to_base_symbol(pair)
    coins = extract_coins(f"{pair} vừa phá kháng cự")
    assert base in coins
    assert pair not in coins


# ---------------------------------------------------------------------------
# 3. Pin fix gốc trong aggregate_coin_sentiment
# ---------------------------------------------------------------------------
class _EmptyResult:
    def all(self):
        return []


class FakeSession:
    """Session GIẢ: chỉ ghi lại câu lệnh, trả 0 dòng. Không phải DB."""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _EmptyResult()


def _bound_values(stmt) -> list:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return list(compiled.params.values())


@pytest.mark.parametrize("coin_in", ["BTCUSDT", "BTC", "btc"])
async def test_aggregate_coin_sentiment_query_dang_ngan(coin_in):
    session = FakeSession()
    summary = await aggregate_coin_sentiment(session, coin=coin_in, window_hours=6)

    values = _bound_values(session.statements[0])
    assert "BTC" in values
    assert "BTCUSDT" not in values
    assert summary.coin == "BTC"


async def test_aggregate_coin_sentiment_raise_khi_symbol_rong():
    with pytest.raises(SymbolError):
        await aggregate_coin_sentiment(FakeSession(), coin="", window_hours=6)
