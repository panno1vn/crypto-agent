# script_test_btc.py
import asyncio

from technical_analysis.confluence import analyze_confluence


async def main():
    print("Đang tính toán Confluence cho BTCUSDT...")
    result = analyze_confluence("BTCUSDT")  # Gọi hàm thật gọi DB
    print("\n--- KẾT QUẢ ---")
    print(f"Coin: {result.coin}")
    print(f"Hướng giao dịch (Direction): {result.direction}")
    print(f"Sức mạnh tín hiệu (Strength): {result.strength:.2f}/1.0")
    print(f"Đồng thuận TF (Aligned): {result.timeframes_aligned}/4")
    print(f"Vùng Entry: {result.entry_zone}")
    print(f"S/R quan trọng: {result.key_sr_levels}")


# Mở Terminal và chạy (nếu sử dụng asyncio)
# python script_test_btc.py
if __name__ == "__main__":
    asyncio.run(main())
