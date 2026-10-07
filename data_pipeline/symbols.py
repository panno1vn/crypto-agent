"""
data_pipeline/symbols.py

Ngày 31 — Chuẩn hóa symbol coin (trả nợ #2).

Hai miền dữ liệu dùng hai dạng symbol khác nhau, và đây là QUY ƯỚC CHÍNH
THỨC của repo:

  - Miền Telegram (telegram_messages.coins_mentioned, metadata `coins` của
    Chroma): dạng NGẮN, "BTC". Nguồn: extract_coins() trong
    data_pipeline/telegram/historical_scraper.py khớp theo CRYPTO_ENTITIES.
  - Miền Binance (ohlcv.coin, technical_indicators.coin, ConfluentSignal.coin):
    dạng CẶP, "BTCUSDT". Nguồn: COINS trong data_pipeline/binance/ohlcv_pipeline.py.

Mọi chỗ nối hai miền phải đi qua module này. Trước N31 có 2 bản stopgap
(`_to_short_symbol` ở nlp/signal_validation.py, `_to_base_symbol` ở
rag/news_confirmation.py) lệch nhau ở 2 điểm: bản đầu không upper(), bản sau
có thêm USDC. Hai bản song song là đúng kiểu bug "lệch nhau âm thầm", nên cả
hai đã bị xóa và thay bằng to_base_symbol() dưới đây.

Fail loudly: chuỗi rỗng, không phải str, hoặc chỉ là tên quote ("USDT")
thì raise SymbolError, không trả về chuỗi rỗng để query ra 0 dòng.
"""

from __future__ import annotations

# Quote asset của mọi cặp mà pipeline Binance đang tải.
QUOTE_ASSET = "USDT"

# Hậu tố quote được chấp nhận khi chuyển cặp -> dạng ngắn. Giữ cả các quote
# không dùng trong pipeline hiện tại để không âm thầm trả "ETHBUSD" nguyên
# văn (sẽ không khớp coins_mentioned nào). THỨ TỰ CÓ Ý NGHĨA: "BUSD" kết
# thúc bằng "USD", nên nếu thử "USD" trước thì "ETHBUSD" -> "ETHB" (sai).
# Quy tắc: quote dài đứng trước quote là hậu tố của nó. Có test pin.
_KNOWN_QUOTES = ("USDT", "BUSD", "USDC", "USD")


class SymbolError(ValueError):
    """Symbol không hợp lệ, không chuẩn hóa được."""


def _clean(symbol: object) -> str:
    if not isinstance(symbol, str):
        raise SymbolError(f"symbol phải là str, nhận {type(symbol).__name__}")
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise SymbolError("symbol rỗng")
    return cleaned


def to_base_symbol(symbol: str) -> str:
    """
    Đưa symbol về dạng ngắn của miền Telegram: "BTCUSDT" -> "BTC",
    "btc" -> "BTC". Idempotent: to_base_symbol(to_base_symbol(x)) == to_base_symbol(x).

    Raises:
        SymbolError: rỗng, không phải str, hoặc chỉ gồm tên quote ("USDT").
    """
    cleaned = _clean(symbol)
    if cleaned in _KNOWN_QUOTES:
        raise SymbolError(f"{symbol!r} là quote asset, không phải coin")
    for quote in _KNOWN_QUOTES:
        if cleaned.endswith(quote):
            return cleaned[: -len(quote)]
    return cleaned


def to_pair_symbol(symbol: str, quote: str = QUOTE_ASSET) -> str:
    """
    Đưa symbol về dạng cặp của miền Binance: "BTC" -> "BTCUSDT".
    Nhận cả dạng cặp đầu vào ("BTCUSDT" -> "BTCUSDT").
    """
    return to_base_symbol(symbol) + _clean(quote)
