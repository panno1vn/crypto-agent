# test_indicators.py
import matplotlib.pyplot as plt
import pandas as pd
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands

from technical_analysis.indicators import calculate_all


def fetch_btc_4h_data() -> pd.DataFrame:
    print("Đang tải dữ liệu BTCUSDT 4H...")
    client = Client()
    klines = client.get_historical_klines(
        "BTCUSDT", Client.KLINE_INTERVAL_4HOUR, "1 Jan, 2024"
    )

    df = pd.DataFrame(
        klines,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
            "ignore",
        ],
    )

    numeric_cols = ["open", "high", "low", "close", "volume"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, axis=1)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def plot_indicators(df: pd.DataFrame):
    print("Đang tính toán chỉ báo cho toàn bộ chuỗi để vẽ chart...")

    # Tính toán chỉ báo bằng thư viện 'ta'
    df["RSI_14"] = RSIIndicator(close=df["close"], window=14).rsi()

    macd = MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["MACD_line"] = macd.macd()
    df["MACD_signal"] = macd.macd_signal()
    df["MACD_hist"] = macd.macd_diff()

    bb = BollingerBands(close=df["close"], window=20, window_dev=2)
    df["BB_upper"] = bb.bollinger_hband()
    df["BB_middle"] = bb.bollinger_mavg()
    df["BB_lower"] = bb.bollinger_lband()

    df["EMA_20"] = EMAIndicator(close=df["close"], window=20).ema_indicator()
    df["EMA_50"] = EMAIndicator(close=df["close"], window=50).ema_indicator()

    df_plot = df.tail(150)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(14, 12), gridspec_kw={"height_ratios": [3, 1, 1]}
    )
    fig.suptitle("BTCUSDT 4H - Technical Indicators", fontsize=16, fontweight="bold")

    # Tầng 1: Giá + BB + EMAs
    ax1.plot(
        df_plot.index,
        df_plot["close"],
        label="Close Price",
        color="black",
        linewidth=1.5,
    )
    ax1.plot(
        df_plot.index,
        df_plot["BB_upper"],
        label="BB Upper",
        color="red",
        linestyle="--",
        alpha=0.5,
    )
    ax1.plot(
        df_plot.index,
        df_plot["BB_lower"],
        label="BB Lower",
        color="green",
        linestyle="--",
        alpha=0.5,
    )
    ax1.plot(df_plot.index, df_plot["EMA_20"], label="EMA 20", color="blue", alpha=0.8)
    ax1.plot(
        df_plot.index, df_plot["EMA_50"], label="EMA 50", color="orange", alpha=0.8
    )
    ax1.fill_between(
        df_plot.index, df_plot["BB_upper"], df_plot["BB_lower"], color="gray", alpha=0.1
    )
    ax1.set_ylabel("Price (USDT)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # Tầng 2: RSI
    ax2.plot(df_plot.index, df_plot["RSI_14"], label="RSI (14)", color="purple")
    ax2.axhline(70, color="red", linestyle="--", alpha=0.5)
    ax2.axhline(30, color="green", linestyle="--", alpha=0.5)
    ax2.axhline(50, color="gray", linestyle=":", alpha=0.5)
    ax2.set_ylabel("RSI")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, 100)

    # Tầng 3: MACD
    ax3.plot(df_plot.index, df_plot["MACD_line"], label="MACD Line", color="blue")
    ax3.plot(df_plot.index, df_plot["MACD_signal"], label="Signal Line", color="orange")

    colors = ["green" if val >= 0 else "red" for val in df_plot["MACD_hist"]]
    ax3.bar(
        df_plot.index, df_plot["MACD_hist"], color=colors, alpha=0.5, label="Histogram"
    )

    ax3.set_ylabel("MACD")
    ax3.legend(loc="upper left")
    ax3.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("btc_indicators_chart.png", dpi=300, bbox_inches="tight")
    print(
        "✅ Đã lưu biểu đồ thành công! Hãy mở file 'btc_indicators_chart.png' trong VSCode để xem."
    )


if __name__ == "__main__":
    df_btc = fetch_btc_4h_data()

    current_indicators = calculate_all(df_btc)
    print("\n--- KẾT QUẢ CHỈ BÁO NẾN HIỆN TẠI ---")
    print(f"RSI 14:     {current_indicators.rsi:.2f}")
    print(f"MACD Line:  {current_indicators.macd_line:.2f}")
    print(f"BB Upper:   {current_indicators.bb_upper:.2f}")
    print(f"EMA 20:     {current_indicators.ema_20:.2f}")
    print("------------------------------------\n")

    plot_indicators(df_btc)
