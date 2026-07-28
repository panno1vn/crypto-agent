import mplfinance as mpf
import pandas as pd

from technical_analysis.support_resistance import find_swing_points


def plot_support_resistance(df: pd.DataFrame, window: int = 5):
    """Vẽ biểu đồ nến và đính kèm các đường Hỗ trợ/Kháng cự."""

    # 1. Chạy thuật toán tìm S/R
    sr_data = find_swing_points(df, window=window)
    resistances = sr_data["resistance"]
    supports = sr_data["support"]

    print(f"Resistances found: {resistances}")
    print(f"Supports found: {supports}")

    # 2. Gộp tất cả các đường thành danh sách hlines (horizontal lines)
    all_lines = resistances + supports

    # 3. Tạo màu sắc: Kháng cự (Đỏ), Hỗ trợ (Xanh lá)
    colors = ["r"] * len(resistances) + ["g"] * len(supports)

    # 4. Vẽ chart bằng mplfinance
    mpf.plot(
        df,
        type="candle",
        style="charles",
        title="BTC/USDT 4H - Support & Resistance",
        hlines=dict(hlines=all_lines, colors=colors, linestyle="dashed", alpha=0.6),
        figsize=(12, 6),
        savefig="sr_chart_test.png",
    )


# --- CÁCH CHẠY THỬ ---
if __name__ == "__main__":
    # Dùng data ảo từ DB (Giả lập) hoặc bạn có thể query thẳng từ PostgreSQL của bạn
    # Ở đây mình giả lập 1 dataframe ngẫu nhiên để script không bị lỗi
    import numpy as np

    dates = pd.date_range(start="2023-01-01", periods=100, freq="4h")
    df = pd.DataFrame(
        {
            "open": np.random.uniform(40000, 45000, 100),
            "high": np.random.uniform(45000, 46000, 100),
            "low": np.random.uniform(39000, 40000, 100),
            "close": np.random.uniform(40000, 45000, 100),
        },
        index=dates,
    )

    # Ghi đè vài nến để tạo S/R rõ ràng
    df.loc[dates[20], "high"] = 48000  # Swing High 1
    df.loc[dates[50], "high"] = 47900  # Swing High 2 (sẽ bị gom cụm)
    df.loc[dates[30], "low"] = 37000  # Swing Low 1

    plot_support_resistance(df, window=5)
