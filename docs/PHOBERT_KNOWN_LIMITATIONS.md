# PhoBERT Sentiment — Known Limitations (tính đến hết Ngày 19)

> File này ghi lại vì sao project tiến hành Ngày 19+ dù PhoBERT v2 CHƯA đạt
> ngưỡng F1 macro >0.70 trong roadmap gốc — kèm số liệu thật, giả thuyết
> chưa xác nhận, và điểm cần quay lại khi có bằng chứng thực nghiệm.

## 1. Kết quả thật (Ngày 17-18, model v2)

| Metric | Test set (297 mẫu) | Val set |
|---|---|---|
| F1 macro | **0.6385** | 0.6360 |
| Accuracy | 0.7172 | 0.7205 |
| F1 negative | 0.5634 | 0.6230 |
| F1 neutral | 0.8094 | 0.8128 |
| F1 positive | 0.5429 | 0.4724 |

Ngưỡng roadmap yêu cầu: **F1 macro > 0.70**. Chưa đạt — thiếu ~0.06.
Test và val chênh nhau chỉ 0.003 → đây là mức hiệu năng thật, ổn định,
không phải nhiễu của 1 lần split may rủi.

## 2. Đã thử để cải thiện (5 ngày, nhiều hướng)

- **v1** (`class_weight="balanced"`): negative bị overcorrect nặng
  (precision chỉ 0.34, model đoán "negative" tràn lan).
- **v2** (`sqrt_balanced` weighting, thay balanced thuần): negative cải
  thiện rõ rệt (F1 0.563, tỷ lệ đoán-thừa negative giảm từ 2.5x → 1.37x
  so với v1) — nhưng đánh đổi: **positive tụt thành class yếu nhất**
  (F1 0.543, thấp hơn cả negative).

## 3. Lỗi chính hiện tại: KHÔNG còn ở negative — nằm ở ranh giới neutral↔positive

Đọc confusion matrix (test, 297 mẫu):
- 28 mẫu true=neutral bị đoán positive
- 25 mẫu true=positive bị đoán neutral
- Tổng **53/297 (17.8%)** rơi vào đúng 1 cặp nhầm lẫn này — lớn hơn nhiều
  so với tổng mọi lỗi liên quan đến negative cộng lại (31 mẫu).

**Giả thuyết (CHƯA xác nhận bằng script, quyết định dừng diagnose sâu để
giữ tiến độ):** hiện tượng "tone vs price-impact" — tin tài chính/vĩ mô
chung, không trực tiếp liên quan 5 coin track, bị model đoán theo giọng
điệu chung chung thay vì đúng theo tác động giá thật. Nếu đúng, đây là
giới hạn cấu trúc (model chỉ đọc raw text, không có tín hiệu tường minh
về mức độ liên quan tới coin) — không tự fix được bằng cách thêm data
thô, cần đưa tín hiệu "có liên quan coin hay không" vào pipeline.

## 4. Caveat còn treo — CHƯA xử lý, ảnh hưởng tới độ tin cậy của toàn bộ số liệu ở trên

Từ `GHI_CHU_NGAY16.md` mục 8: **review consistency 30-100 message label
CHƯA từng được làm.** Nghĩa là F1 macro 0.6385 có thể mang một phần nhiễu
từ chất lượng label gốc, không hoàn toàn phản ánh giới hạn của model.
Chưa biết mức độ ảnh hưởng lớn hay nhỏ — cần làm trước khi tin tưởng
tuyệt đối vào bất kỳ con số F1 nào ở trên.

## 5. Quyết định: tiến hành Ngày 19+ với model hiện tại — kèm 2 biện pháp giảm rủi ro

Thay vì tiếp tục tune model (không có bằng chứng chắc chắn sẽ cải thiện
sau 5 ngày đã thử nhiều hướng), Ngày 19 áp dụng 2 cơ chế ở tầng
*consume* để giảm tác động của giới hạn model, không sửa model:

1. **Relevance gate qua `coins_mentioned`:** `aggregate_coin_sentiment()`
   (`nlp/engagement_weighting.py`) chỉ tính vào aggregate của 1 coin nếu
   message thực sự nhắc tới coin đó (`ARRAY.any()`). Nếu giả thuyết
   "tone vs price-impact" đúng — lỗi tập trung ở message KHÔNG nhắc coin
   cụ thể — cơ chế này tự động loại bớt phần data nhiễu nhất khỏi signal
   cuối, dù không sửa được model.
2. **Signal Aggregator (Ngày 31) chỉ weight sentiment 25%**, và
   `confidence` bị hard-cap 0.85 — thiết kế downstream đã có sẵn khả năng
   chịu được 1 nguồn tín hiệu hơi nhiễu, không để 1 mình sentiment quyết
   định toàn bộ.

## 6. Số liệu production thật (Ngày 19, sau khi `dag_sentiment_pipeline` chạy ổn định)

Trên toàn bộ `telegram_messages` đã backfill:

| sentiment_label | count |
|---|---|
| neutral | 12,871 |
| positive | 2,965 |
| negative | 2,013 |
| NULL (không xác định ngôn ngữ) | 493 |
| **Tổng đã xử lý** | **18,342** |

Tỷ lệ neutral:negative ≈ 6.4:1 — khớp hợp lý với imbalance ratio 6.7:1
đã ghi nhận ở bộ label gốc (`GHI_CHU_NGAY17_18.md` mục 9.1). Không có
dấu hiệu bất thường so với đặc điểm đã biết của model.

## 7. `ENGAGEMENT_SCALE_FACTOR` — đã calibrate dựa trên data thật (không còn là số đoán mò)

Percentile views/forwards thật (18,342 message):

| | median | p90 | p99 |
|---|---|---|---|
| views | 1,672 | 60,371 | 574,826 |
| forwards | 1 | 43 | 166 |

Chọn `ENGAGEMENT_SCALE_FACTOR = 40` — neo tại mốc "tin viral (p99) được
boost khoảng +50%":

| | boost |
|---|---|
| Tin trung vị | +22.0% |
| Tin p90 | +39.7% |
| Tin p99 (viral) | +48.7% |

So với mặc định ban đầu `100` (viral chỉ +19.5%, gần như vô nghĩa do
`log1p()` nén khoảng cách rất mạnh) và cực đoan `20` (viral +97%, dễ để
1 tin đơn lẻ áp đảo cả window aggregate).

**CHƯA qua backtest thật** (Ngày 32 — so sánh full system TA+sentiment
vs baseline TA thuần) — con số này dựa trên phân tích thống kê phân
phối, không dựa trên bằng chứng "cải thiện signal chất lượng thật".
Có thể cần điều chỉnh khi có kết quả backtest.

## 8. Điểm quay lại thật — không phải ngưỡng 0.70 áng chừng

Ngưỡng 0.70 trong roadmap được viết TRƯỚC khi có data thật. Bài test
đáng tin hơn: **Ngày 32** — nếu full system (TA + sentiment) tốt hơn
baseline TA thuần trong backtest, model đủ dùng thực tế dù F1 chỉ 0.64.
Nếu không tốt hơn → quay lại đây, với bằng chứng backtest cụ thể để
quyết định hướng fix (relabel, feature coin-relevance tường minh, hay
thu thập thêm data targeted từ 3 kênh tin tức đã xác định).