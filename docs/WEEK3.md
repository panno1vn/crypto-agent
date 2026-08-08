# WEEK3.md — Tuần 3: PhoBERT Fine-tuning + NLP Stack

## 1. Tóm tắt trạng thái Milestone Tuần 3

| Tiêu chí | Trạng thái | Ghi chú |
|---|---|---|
| PhoBERT fine-tuned F1 macro > 0.70 | ❌ CHƯA ĐẠT | v2 = 0.6385. Xem mục 3 — quyết định proceed có lý do rõ ràng, không phải bỏ qua |
| FinBERT tiếng Anh hoạt động | ✅ Đạt | Tích hợp trong `nlp/finbert_analyzer.py`, dùng trong `MultilingualSentimentAnalyzer` |
| Engagement-weighted sentiment tự động | ✅ Đạt | `nlp/engagement_weighting.py`, `ENGAGEMENT_SCALE_FACTOR=40`, xử lý 18,342 messages (Ngày 19) |
| Sentiment-price correlation report trong MLflow | ✅ Đạt | Experiment `signal_validation` — xem mục 4 |
| Airflow DAG `dag_sentiment_pipeline` GREEN | ✅ Đạt | Xác nhận 2026-08-08: `paused=False`, manual trigger + scheduled run đều `success` trên code hiện tại (sau bug fix timezone) |

**Kết luận trung thực:** 4/5 tiêu chí đạt. Tiêu chí F1 không đạt ngưỡng gốc, nhưng có quyết định kỹ thuật rõ ràng để tiếp tục sang Tuần 4 thay vì tiếp tục tuning — chi tiết mục 3.

---

## 2. PhoBERT Fine-tuning — Kết quả

| Version | F1 macro | Ghi chú |
|---|---|---|
| v1 | 0.576 | `class_weight="balanced"` — overcorrect, model đoán negative tràn lan (precision 0.34) |
| v2 | 0.6385 | Đổi sang `sqrt_balanced` — giảm overcorrect (tỷ lệ đoán-thừa negative: 2.5x → 1.37x) |

Dataset: 996 valid records sau split, phân phối nhãn neutral 56% / positive 30.6% / negative 13.4%.

**[CHÈN SCREENSHOT: MLflow — so sánh 2 run v1 vs v2, metrics F1/accuracy]**

---

## 3. Quyết định: Proceed dù F1 chưa đạt 0.70

Sai số 0.70 - 0.6385 = 0.0615, không phải khoảng cách nhỏ nhưng cũng không phải thất bại toàn diện. Phân tích lỗi cho thấy nguyên nhân chính là **nhầm lẫn ranh giới neutral↔positive** (~18% test samples) — một vấn đề cấu trúc: model chỉ đọc raw text, không có tín hiệu tường minh về mức độ liên quan tới coin cụ thể ("tone vs price-impact"). Vấn đề này lặp lại giống hệt ở cả v1 và v2, cho thấy nhiều khả năng không phải do thiếu data mà do thiếu feature.

**Mitigation đã lên kế hoạch** (không thuộc phạm vi Tuần 3, dời sang Tuần 5):
- Relevance gate qua `coins_mentioned` — lọc tin không liên quan trực tiếp coin track trước khi tính sentiment
- Signal Aggregator (Ngày 31) cap trọng số sentiment ở mức tối đa 25% tổng signal — hạn chế tác động của 1 nguồn tín hiệu chưa đủ tin cậy

**Không tiếp tục tuning PhoBERT ngay bây giờ** vì: (1) chi phí tuning thêm (GPU Kaggle, thời gian) không đảm bảo vượt threshold do bản chất vấn đề là cấu trúc chứ không phải hyperparameter; (2) Ngày 32 (full system backtest) mới là điểm đánh giá thật sự có ý nghĩa — biết PhoBERT ở mức 0.6385 có đủ tốt cho toàn hệ thống hay không, quan trọng hơn việc đuổi theo con số 0.70 tách biệt.

**[CHÈN SCREENSHOT: MLflow — confusion matrix v2, classification report]**

---

## 4. Signal Validation — Sentiment vs Price Correlation (Ngày 20)

Pearson correlation giữa `weighted_sentiment` (mỗi window 4h) và `price_return` BTC cùng khung, 30 ngày gần nhất:

| Nhóm | n | Correlation | p-value | Ý nghĩa thống kê |
|---|---|---|---|---|
| Combined | 81 | -0.018 | 0.872 | Không |
| Tiếng Việt | 30 | -0.311 | 0.094 | Không (gần ngưỡng 0.05) |
| Tiếng Anh | 57 | 0.046 | 0.737 | Không |

**Kết luận trung thực:** Không có tương quan có ý nghĩa thống kê ở cả 3 cách chia. Đây là kết quả HỢP LỆ theo đúng cảnh báo gốc của roadmap ("correlation thường 0.1-0.3, đôi khi không significant"), không phải thất bại của module. Nhóm tiếng Việt (`r=-0.311`) gần ngưỡng có ý nghĩa nhưng `n=30` quá nhỏ để diễn giải — không kết luận "sentiment VN có tín hiệu ngược" từ con số này, chờ Ngày 32 (dataset lớn hơn) mới đủ cơ sở.

`n=81` combined thấp hơn kỳ vọng lý thuyết (~180) do phần lớn khung 4h gần đây thiếu sentiment tương ứng trước khi backfill gap được xử lý.

**[CHÈN SCREENSHOT: MLflow — experiment `signal_validation`, log params/metrics]**

---

## 5. PhoBERT CPU Inference Benchmark (Ngày 21)

Benchmark trên 40 tin nhắn thật lấy ngẫu nhiên từ DB (tiếng Việt, độ dài 55–2040 ký tự, mean 393 ký tự), sau warm-up 1 lần (loại trừ chi phí init kernel lần gọi đầu):

| Metric | Giá trị |
|---|---|
| Mean | 53.0ms |
| P50 | 39.5ms |
| P95 | 122.4ms |
| Max | 130.4ms |
| Ngưỡng roadmap | < 200ms |
| Kết quả | ✅ PASS (P95 chỉ bằng ~61% ngưỡng) |

**Không cần implement batch inference** — roadmap chỉ yêu cầu batching nếu vượt ngưỡng; ở đây còn dư biên độ lớn, thêm batching lúc này là tối ưu sớm không cần thiết.

Script: `scripts/manual/benchmark_phobert_inference.py`, chạy lại bất cứ lúc nào bằng `python3 scripts/manual/benchmark_phobert_inference.py` (cần file mẫu `/tmp/sample_real_messages.jsonl`, tạo bằng query trong script hoặc export thủ công từ `telegram_messages`).

---

## 6. Nợ kỹ thuật xử lý trong tuần (không thuộc phạm vi gốc Tuần 3, phát hiện khi làm Ngày 20-21)

- **Bug timezone lặp lại (lần 2)**: `historical_scraper.py` gắn tzinfo thay vì strip khi ghi `created_at` — cùng loại lỗi đã note ở `ohlcv_pipeline.py` trước đây. Đã fix, nhưng xác nhận đây là mô thức lỗi tái diễn, cần rà soát các điểm insert datetime khác chưa được kiểm tra.
- **3 file deliverable Ngày 20 bị sót commit** (`nlp/signal_validation.py`, `scripts/manual/run_day20_signal_validation.py`, `tests/unit/test_signal_validation.py`) — tồn tại trên đĩa nhiều ngày nhưng chưa từng vào git. Đã commit bù trong session này.
- **1 unit test sai giả định schema DB** (`test_ohlcv_pipeline.py`, viết từ Ngày 7) — assert `open_time` phải timezone-aware trong khi cột DB thật là naive. Tồn tại 13+ ngày không bị phát hiện vì không có gì kích hoạt lại nó cho tới khi chỉnh sửa timezone logic lần này. Đã sửa và verify pass.
- **`requirements-airflow.txt` được review lại toàn bộ** — xác nhận đầy đủ `torch`, `transformers`, `sentencepiece`, `tokenizers`, `safetensors`, `numpy`, `pandas`, `huggingface-hub`, `langdetect`, `pyvi` cần cho `dag_sentiment_pipeline`. Verify bằng `docker compose build` thành công (không chỉ `pip install --dry-run` local — 2 môi trường có thể cho kết quả khác nhau).

## 7. Việc còn treo — cần xử lý trước Ngày 31

- [ ] **Bug `aggregate_coin_sentiment()` symbol mismatch**: hàm match `coins_mentioned` bằng short symbol (`'BTC'`) nhưng caller truyền full symbol (`'BTCUSDT'`) → silent zero returns. PHẢI fix trước Ngày 31 (Signal Aggregator phụ thuộc hàm này).
- [ ] `realtime_listener.py` chỉ monitor 4/13 channel — chưa điều tra nguyên nhân.
- [ ] `config/channels.yaml` outdated (chỉ khớp 4/13 channel thật) — không dùng cho pipeline logic, cần cập nhật hoặc xoá để tránh nhầm lẫn sau này.
- [ ] Setup doc còn thiếu: người clone repo mới cần tự tạo file `crypto_session_dag_catchup.session` (Telethon login thủ công) trước khi `docker compose up` — hiện chưa ghi ở đâu.

---

*Cập nhật lần cuối: 2026-08-08*
