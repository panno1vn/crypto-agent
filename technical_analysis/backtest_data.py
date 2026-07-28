"""
technical_analysis/backtest_data.py

Ngày 13 (bổ sung) — Xây chuỗi 'strength' lịch sử cho backtest
======================================================================
Vấn đề gốc: analyze_confluence() chỉ tính 1 signal TẠI THỜI ĐIỂM HIỆN
TẠI (dùng 200 nến gần nhất mỗi timeframe). backtest_confluence_signal()
(Ngày 12) lại cần cột 'strength' cho TỪNG NẾN trong suốt khoảng backtest
(vd 6 tháng). Chưa từng có hàm nào nối 2 việc này lại.

Cách làm (Hướng A đã chọn — ưu tiên độ chính xác, chấp nhận chậm):
  - Duyệt qua chuỗi nến 1h trong khoảng [start, end].
  - Cứ mỗi `recompute_every_candles` nến, gọi lại analyze_confluence()
    THẬT với as_of=thời điểm nến đó -> ra 1 signal tại đúng lúc đó,
    KHÔNG nhìn thấy data tương lai.
  - Giữa 2 lần tính, forward-fill giá trị signal gần nhất (giống việc
    một trader không re-evaluate mỗi phút, mà đánh giá lại mỗi vài giờ).
  - Chỉ giữ strength khi direction=='long' (đưa về 0 nếu 'short'/'neutral')
    vì backtest_confluence_signal() hiện tại là chiến lược MỘT CHIỀU
    (chỉ long, có SL/TP) — không có logic short. Đây là giới hạn CÓ
    CHỦ ĐÍCH của Ngày 12, không phải thiếu sót của hàm này.

⚠️ CẢNH BÁO HIỆU NĂNG: mỗi lần recompute gọi analyze_confluence() thật,
tức là 4 lượt query DB + tính indicator (1 lượt/timeframe). Với 6 tháng
data 1h (~4380 nến) và recompute_every_candles=4, sẽ có ~1095 lượt gọi
analyze_confluence(), mỗi lượt ~4 query DB -> có thể mất VÀI PHÚT đến
VÀI CHỤC PHÚT tùy tốc độ máy. Luôn test với khoảng thời gian NGẮN
(vd 2 tuần) trước khi chạy full 6 tháng.
"""

from datetime import datetime

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from data_pipeline.logger import get_logger
from technical_analysis.confluence import analyze_confluence
from technical_analysis.indicator_pipeline import fetch_ohlcv_from_db

logger = get_logger(__name__)


async def build_confluence_signal_series(
    session: AsyncSession,
    coin: str,
    start: datetime,
    end: datetime,
    timeframe: str = "1h",
    recompute_every_candles: int = 4,
) -> pd.DataFrame:
    """
    Trả về DataFrame index=open_time, columns=['close', 'strength'],
    sẵn sàng truyền thẳng vào backtest_confluence_signal(df).

    Args:
        start, end: khoảng thời gian backtest (dùng để giới hạn nến
            base '1h' sẽ duyệt qua — KHÔNG giới hạn data đọc cho từng
            lần tính confluence, vì mỗi lần tính vẫn cần 200 nến LÙI
            VỀ TRƯỚC as_of, có thể vượt ra ngoài [start, end]).
        recompute_every_candles: số nến 1h giữa 2 lần tính confluence
            thật. Giá trị càng nhỏ càng chính xác nhưng càng chậm.
            4 = tính lại mỗi 4 tiếng (hợp lý cho swing trading).

    Raises:
        ValueError: nếu không có nến '1h' nào trong khoảng [start, end]
            (không có gì để backtest).
    """
    base_df = await fetch_ohlcv_from_db(session, coin, timeframe, limit=100000)

    if base_df.empty:
        raise ValueError(
            f"Không có OHLCV '{timeframe}' nào cho {coin} — không thể backtest."
        )

    base_df = base_df[(base_df.index >= start) & (base_df.index <= end)]

    if base_df.empty:
        raise ValueError(
            f"Không có nến '{timeframe}' nào cho {coin} trong khoảng "
            f"[{start}, {end}]."
        )

    strengths: list[float] = []
    last_strength = 0.0
    total_candles = len(base_df)
    recompute_count = 0

    logger.info(
        f"[BACKTEST_DATA] Bắt đầu xây chuỗi signal cho {coin}: "
        f"{total_candles} nến, recompute mỗi {recompute_every_candles} nến "
        f"(~{total_candles // recompute_every_candles} lượt gọi analyze_confluence)"
    )

    for i, (candle_time, row) in enumerate(base_df.iterrows()):
        if i % recompute_every_candles == 0:
            try:
                result = await analyze_confluence(
                    session,
                    coin,
                    current_price=float(row["close"]),
                    as_of=candle_time,
                )
                last_strength = result.strength if result.direction == "long" else 0.0
                recompute_count += 1
            except Exception as e:
                # Không để 1 điểm lỗi làm hỏng cả chuỗi backtest — giữ
                # nguyên giá trị trước đó và log rõ để biết chỗ nào bị bỏ qua.
                logger.warning(
                    f"[BACKTEST_DATA] Lỗi tính confluence tại {candle_time}: {e}. "
                    f"Giữ nguyên giá trị trước đó ({last_strength})."
                )

            if recompute_count % 50 == 0:
                logger.info(
                    f"[BACKTEST_DATA] Tiến độ: {i + 1}/{total_candles} nến "
                    f"({recompute_count} lượt recompute thật)"
                )

        strengths.append(last_strength)

    result_df = base_df.copy()
    result_df["strength"] = strengths

    logger.info(
        f"[BACKTEST_DATA] Hoàn tất: {total_candles} nến, "
        f"{recompute_count} lượt recompute thật, "
        f"strength trung bình={sum(strengths) / len(strengths):.3f}"
    )

    return result_df[["close", "strength"]]
