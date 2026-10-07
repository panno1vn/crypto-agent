"""
agent/config.py

Ngày 31 — Cấu hình tầng agent (Signal Aggregator).

Trọng số mặc định — CHỐT Ở N32 (2026-10-08) bằng backtest ablation, MLflow
experiment `Crypto_Agent_Ablation_Day32`, cửa sổ cố định 2026-06-06 →
2026-08-16, 5 coin, 2 chiều, phí 0.1%, 80 lệnh tổng (chi tiết:
docs/GHI_CHU_NGAY32.md):
  run 15a7cc6f… ta_only            mean_total_return -1.645%
  run 070d6b1e… TA .85/sent .15    mean_total_return -2.160%
  run 6b1afc73… TA .70/.15/.15     mean_total_return -3.381%
- Sentiment không cải thiện ở mọi quantile/trọng số đã thử → hạ về 0.05
  theo điều kiện roadmap. TA-only đo được tốt nhất; giữ 0.05 (không phải 0)
  vì 80 lệnh/~35 ngày đánh giá chưa đủ để kết luận sentiment vô dụng.
- News = 0.0: kém hơn TA-only ở 14/15 cặp (quantile, coin) và trùng nguồn
  với sentiment (nợ #11). Có dấu hiệu, chưa có kiểm định thống kê.
- Ngưỡng should_trade 0.65 lấy từ safety check tuần 7 của roadmap, CHƯA đo.

Đọc từ env để N32 đổi được mà không sửa code. Giá trị sai (không phải số,
âm, tổng trọng số != 1) raise ngay lúc load_signal_weights(), không âm thầm
chuẩn hóa lại.
"""

import os

from dotenv import load_dotenv

load_dotenv()

SIGNAL_WEIGHT_TECHNICAL: float = float(os.getenv("SIGNAL_WEIGHT_TECHNICAL", "0.95"))
SIGNAL_WEIGHT_SENTIMENT: float = float(os.getenv("SIGNAL_WEIGHT_SENTIMENT", "0.05"))
SIGNAL_WEIGHT_NEWS: float = float(os.getenv("SIGNAL_WEIGHT_NEWS", "0.00"))

SIGNAL_MIN_CONFIDENCE_TO_TRADE: float = float(
    os.getenv("SIGNAL_MIN_CONFIDENCE_TO_TRADE", "0.65")
)
