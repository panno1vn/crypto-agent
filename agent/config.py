"""
agent/config.py

Ngày 31 — Cấu hình tầng agent (Signal Aggregator).

⚠️ MỌI GIÁ TRỊ Ở ĐÂY LÀ PLACEHOLDER CHỜ ĐO Ở N32 (backtest ablation).
- Trọng số mặc định {TA 0.70, sentiment 0.15, news 0.15} là mức "thận
  trọng" của roadmap v2, KHÔNG phải số đo. Bằng chứng chống lại việc tin
  sentiment: PhoBERT F1=0.6385, correlation sentiment-giá -0.018 (p=0.87)
  (docs/context/06_results_and_metrics.md).
- Ngưỡng should_trade 0.65 lấy từ safety check tuần 7 của roadmap, chưa đo.

Đọc từ env để N32 đổi được mà không sửa code. Giá trị sai (không phải số,
âm, tổng trọng số != 1) raise ngay lúc load_signal_weights(), không âm thầm
chuẩn hóa lại.
"""

import os

from dotenv import load_dotenv

load_dotenv()

SIGNAL_WEIGHT_TECHNICAL: float = float(os.getenv("SIGNAL_WEIGHT_TECHNICAL", "0.70"))
SIGNAL_WEIGHT_SENTIMENT: float = float(os.getenv("SIGNAL_WEIGHT_SENTIMENT", "0.15"))
SIGNAL_WEIGHT_NEWS: float = float(os.getenv("SIGNAL_WEIGHT_NEWS", "0.15"))

SIGNAL_MIN_CONFIDENCE_TO_TRADE: float = float(
    os.getenv("SIGNAL_MIN_CONFIDENCE_TO_TRADE", "0.65")
)
