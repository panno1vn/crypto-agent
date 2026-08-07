# WEEK 3 — PhoBERT Fine-tuning cho Crypto Sentiment (Ngày 15–21)

> Tài liệu này mô tả toàn bộ quá trình label, audit, và fine-tune PhoBERT
> cho sentiment tiếng Việt. Viết để bất kỳ ai (người hoặc AI) đọc vào có
> thể hiểu đúng data và model đang ở trạng thái nào, không cần đọc lại
> lịch sử hội thoại/commit.

## 1. Tổng quan kết quả

| Version | Train size | f1_macro (test) | Ghi chú |
|---|---|---|---|
| v1 | 697 | 0.576 | Baseline, label lần đầu, chưa audit consistency |
| **v2** | **1384** | **0.639** | Sau audit + relabel toàn bộ + `sqrt_balanced` class weight |

**Chưa đạt** ngưỡng `f1_macro > 0.70` của project. Nguyên nhân đã xác
định rõ (mục 5), không phải do lỗi kỹ thuật mà do **giới hạn nguồn dữ
liệu** (thiếu tin tức tiêu cực thật trong các kênh Telegram đã scrape).

## 2. Quy trình label — 2 vòng

### Vòng 1 (Ngày 15–17)
- Label tay 1000 message qua Label Studio, tiêu chí "price impact,
  không phải tone"
- Kết quả: 996 hợp lệ, phân phối 56.0% neutral / 30.6% positive /
  13.4% negative (ratio 4.2:1)
- Fine-tune lần 1: `f1_macro = 0.576` trên test set 150 mẫu

### Phát hiện vấn đề (sau Ngày 18 lần 1)
Confusion matrix lần 1 lộ 2 vấn đề:
1. **Model đoán "negative" tràn lan** — 50 lần dự đoán cho 20 mẫu thật
   (2.5x), do `class_weight="balanced"` overcorrect trên ratio 4.2:1
2. **Nhãn không nhất quán** — tự review lại 1000 message đầu phát hiện
   nhiều case label theo cảm tính, không theo tiêu chí cố định (điển
   hình: tin tức công nghệ/AI chung không liên quan trực tiếp coin
   track, đôi khi bị gán positive/negative theo tone thay vì đúng theo
   price-impact)

### Vòng 2 (Ngày 18) — Audit + Relabel
- Xây quy trình quyết định **3 bước tuần tự**, thay cho đánh giá cảm
  tính:
  1. Tin có nhắc trực tiếp 1/5 coin track hoặc thị trường chung không?
  2. Tin có mô tả sự kiện cụ thể ảnh hưởng giá không?
  3. Sự kiện đẩy giá lên hay xuống?
  ("Phân vân ở bước nào → lùi về neutral")
- Dùng Claude API (`claude-haiku-4-5`) áp cùng quy trình 3 bước lên
  toàn bộ 2000 message (996 đã label trước + ~1000 reserve), so sánh
  với nhãn người, xuất Excel highlight các cặp bất đồng theo mức độ
  nghiêm trọng (đổi cực positive↔negative = HIGH, liên quan neutral =
  LOW)
- Tự review toàn bộ case HIGH, sửa lại nhãn sai qua Label Studio UI
- Label nốt phần reserve còn thiếu theo đúng quy trình 3 bước mới

**Kết quả cuối:** 1978 message hợp lệ (22 task bị skip do lỗi/rỗng
trong export)

## 3. Phân phối nhãn — vấn đề cốt lõi của project này

```
                v1 (996 mẫu)    v2 (1978 mẫu)
neutral         56.0%           66.6%
positive        30.6%           23.5%
negative        13.4%           10.0%
ratio           4.2 : 1         6.7 : 1
```

**Ratio TỆ HƠN sau khi gấp đôi data** — đây là phát hiện quan trọng
nhất của Ngày 18, không phải thất bại. Phân tích theo kênh cho thấy lý
do rõ ràng:

| Kênh | %negative | Loại kênh |
|---|---|---|
| `bitcoin_vietnam_news` | 25.1% | Tin tức tổng hợp |
| `bd_ventures` | 16.9% | Tin tức tổng hợp |
| `RIC_Capital_Channel` | 13.9% | Tin tức tổng hợp |
| `coin369channel` | 9.8% | Hỗn hợp |
| `nghiencryptochannel` | 6.4% | Hỗn hợp |
| `thichcheatair` | 2.3% | Kênh kèo trade |
| `crypto_musk1m` | 0.8% | Kênh kèo trade |
| `Tradecoinspeed` | 0.0% | Kênh kèo trade |

**Kết luận:** kênh kèo trade (đăng tín hiệu mua/bán) gần như không bao
giờ đưa tin tiêu cực — bản chất nội dung chỉ có "vào lệnh"/"chốt lời"
(positive) hoặc thông tin trung tính. Kênh tin tức tổng hợp mới là
nguồn negative thật. Vì dataset hiện tại có nhiều kênh kèo trade hơn
kênh tin tức, thêm data ngẫu nhiên chỉ pha loãng thêm neutral/positive.

**Hướng khắc phục đã xác định, CHƯA thực hiện** (do đánh đổi thời gian
— xem mục 6): scrape có mục tiêu, sâu hơn về quá khứ, tập trung 3 kênh
tin tức tổng hợp (`bitcoin_vietnam_news`, `bd_ventures`,
`RIC_Capital_Channel`) thay vì scrape ngẫu nhiên/mở rộng kênh mới.

## 4. Kỹ thuật xử lý imbalance — bài học `sqrt_balanced`

`class_weight="balanced"` (sklearn) chuẩn với ratio 4.2:1 đã cho
trọng số negative ~2.5x, kết quả: model overcorrect, đoán negative
tràn lan (precision 0.34, xem confusion matrix v1 trong
`models/phobert-crypto-v1/confusion_matrix.png` nếu còn giữ — bản v1
đã bị ghi đè, chỉ còn số liệu trong Excel audit).

Với ratio 6.7:1 (v2), `balanced` chuẩn sẽ cho trọng số ~3.35x — rủi ro
overcorrect cao hơn. Đổi sang **căn bậc hai của balanced weight**
(`sqrt_balanced`): vẫn ưu tiên class hiếm nhưng không cực đoan.

**Kết quả xác nhận đúng hướng:** tỷ lệ dự đoán negative/mẫu negative
thật giảm từ 2.5x (v1) xuống ~1.37x (v2) — vẫn thiên nhẹ về phía
"cẩn trọng" (recall 0.667 > base rate), hợp lý cho nghiệp vụ (thà cảnh
báo nhầm còn hơn bỏ sót tin xấu), nhưng không còn tràn lan như trước.

```python
raw_weights = compute_class_weight(class_weight="balanced", ...)
class_weights_arr = np.sqrt(raw_weights)  # thay vì dùng raw_weights trực tiếp
```

## 5. Kết quả chi tiết v2 (test set 297 mẫu)

```
              precision    recall  f1-score   support
    negative     0.4878    0.6667    0.5634        30
     neutral     0.8378    0.7828    0.8094       198
    positive     0.5352    0.5507    0.5429        69
    accuracy                         0.7172       297
   macro avg     0.6203    0.6667    0.6385       297
```

- **neutral**: mạnh nhất, hợp lý vì chiếm 66.6% data
- **negative**: cải thiện rõ (f1 0.486→0.563), nhưng support chỉ 30
  mẫu trong test — mỗi mẫu sai lệch ~3.3% f1, cần thận trọng khi diễn
  giải con số này
- **positive**: cải thiện MẠNH NHẤT (f1 0.441→0.543, +0.102) — xác
  nhận giả thuyết v1: phần lớn lỗi trước đây là do model chưa phân
  biệt tốt positive/neutral, và audit + thêm data đã giải quyết đúng
  vấn đề này

## 6. Quyết định: không tiếp tục scrape thêm ở thời điểm này

Đã cân nhắc 3 phương án tại thời điểm phát hiện vấn đề kênh (mục 3):

| Phương án | Thời gian | Trade-off |
|---|---|---|
| A. Train với data hiện có | 0 | f1_macro dừng ở ~0.64, không đạt 0.70 |
| B. Scrape thêm từ 3 kênh tin tức trước khi train | +2-3 ngày | Có thể đạt 0.70+, chậm tiến độ roadmap 70 ngày |
| C. Train trước, scrape sau nếu cần | 0 (rồi +2-3 ngày sau) | Có baseline ngay, quyết định sau dựa trên số liệu thật |

**Chọn phương án A** — ưu tiên giữ tiến độ roadmap (đang ở Ngày 18/70,
còn RAG/Agent/Execution/Production phía trước). Quyết định hợp lý vì:
- Đã CHẨN ĐOÁN được nguyên nhân gốc rễ (không phải đoán mò)
- Cải thiện đã đạt được đáng kể (v1→v2: +0.063 f1_macro) chỉ bằng
  audit + relabel, không cần thêm data
- Có thể quay lại scrape kênh tin tức bất kỳ lúc nào sau này nếu
  `f1_macro=0.639` trở thành nút thắt thật sự ở downstream (Signal
  Aggregator Tuần 5, nơi sentiment score được dùng thật)

## 7. Công cụ đã xây trong quá trình này

- `scripts/audit_all_labels.py` — gọi Claude API áp quy trình 3 bước
  lên toàn bộ message đã label, so sánh với nhãn người, output JSON
- `scripts/export_audit_xlsx.py` — xuất Excel với highlight màu theo
  mức độ bất đồng (HIGH = đổi cực, LOW = liên quan neutral), giữ
  nguyên thứ tự Task ID (không sort) + AutoFilter để không mất khả
  năng đối chiếu ngược với Label Studio
- `scripts/split_dataset.py` — stratified split train/val/test, tự
  phát hiện file task rỗng/lỗi, in cảnh báo nếu ratio > 3.0

## 8. Trạng thái Milestone Tuần 3 (theo `crypto_agent_roadmap.md`)

| Tiêu chí | Trạng thái |
|---|---|
| PhoBERT fine-tuned, F1 macro > 0.70 | ❌ Chưa đạt (0.639) — nguyên nhân đã xác định, xem mục 3 |
| FinBERT cho tiếng Anh | ⏳ Chưa bắt đầu (kế tiếp) |
| Engagement-weighted sentiment | ⏳ Chưa bắt đầu |
| Sentiment-price correlation report | ⏳ Chưa bắt đầu |
| Airflow DAG `dag_sentiment_pipeline` | ⏳ Chưa bắt đầu |

**Quyết định:** tiếp tục sang FinBERT + MultilingualSentimentAnalyzer
(Ngày 18 buổi chiều theo roadmap) với model PhoBERT hiện tại
(`f1_macro=0.639`), không chặn tiến độ chờ đạt 0.70. Quay lại cải
thiện PhoBERT (scrape kênh tin tức mục tiêu) như 1 task riêng nếu
downstream cho thấy cần thiết.

## 10. So sánh PhoBERT vs XLM-RoBERTa (Ngày 18 buổi chiều)

Câu hỏi roadmap đặt ra: "PhoBERT có vượt trội không? Ở đâu?" — trả lời
bằng số liệu, trên 100 message tiếng Việt từ `test.json` (có ground
truth), so với `cardiffnlp/twitter-xlm-roberta-base-sentiment` (model
đa ngôn ngữ tổng quát, không chuyên biệt domain):

| | PhoBERT (fine-tuned) | XLM-RoBERTa (tổng quát) |
|---|---|---|
| accuracy | 0.750 | 0.630 |
| f1_macro | **0.664** | 0.366 |
| recall negative | 0.667 | **0.083** |
| recall positive | 0.500 | **0.150** |

**Kết luận: PhoBERT vượt trội rõ rệt** (+0.298 f1_macro). XLM-R gần
như bỏ sót hoàn toàn cả tin tích cực lẫn tiêu cực (recall <0.15-0.17),
có xu hướng đoán "neutral" tràn lan — model train trên tweet cảm xúc
cá nhân không nhận ra sắc thái tin tức tài chính khách quan (vd "SVB
không được cứu trợ", "Binance tuyên bố biến động thị trường" đọc rất
trung tính về ngôn từ nhưng PhoBERT hiểu đúng ý nghĩa tài chính).

**Phát hiện quan trọng hơn — lỗi còn lại của PhoBERT có pattern lặp
lại từ v1:** toàn bộ 5/5 case PhoBERT sai (trong nhóm XLM-R đúng) đều
là `true=neutral` bị đoán lệch — điển hình tin tài chính/vĩ mô CHUNG
không liên quan trực tiếp coin track (vd "Ngân hàng Trung ương Saudi
Arabia...", "S&P 500 giảm 0,5%...") nhưng PhoBERT vẫn suy luận theo
tone. Đây **cùng loại lỗi** đã phát hiện ở audit v1 (case Google đầu
tư $300M AI) — xuất hiện lại lần 2, xác nhận đây là **giới hạn cấu
trúc thật sự**: PhoBERT chỉ đọc raw text, không có tín hiệu tường minh
"tin này có liên quan trực tiếp coin đang track hay không".

**Hướng cải thiện đã xác định, CHƯA làm** (việc cho Tuần 5+, khi ghép
Signal Aggregator): `extract_coins()` (đã có từ Ngày 4) trích xuất
chính xác coin được nhắc tới trong message — có thể đưa làm feature
tường minh (vd concat vào input, hoặc rule-based pre-filter override
model khi 0 coin track được nhắc tới) thay vì chỉ dựa vào PhoBERT tự
suy luận từ raw text.

Chi tiết đầy đủ: `data/raw/phobert_vs_xlmr_comparison.json`, MLflow
experiment `Crypto_Agent_PhoBERT_Sentiment_v3`.

## 11. File quan trọng, vị trí

```
models/phobert-crypto/
├── best_model/              # weights + tokenizer (KHÔNG commit git, quá nặng)
├── val_metrics.json
├── test_metrics.json        # số liệu chính thức, xem mục 5
├── classification_report.txt
├── confusion_matrix.png
├── training_curves.png
└── misclassified.json       # toàn bộ ca sai trên test set, để phân tích thêm nếu cần

data/phobert_dataset/        # v2, 1384/297/297 — dùng train hiện tại
data/phobert_dataset_v1_backup/  # v1, giữ lại để đối chiếu nếu cần
data/raw/
├── final_labeled_export.json    # export gốc từ Label Studio sau relabel
├── audit_all_results.json       # kết quả đối chiếu Claude vs người, toàn bộ
├── audit_result.xlsx            # bản Excel highlight, dùng lúc review
└── phobert_vs_xlmr_comparison.json  # chi tiết case PhoBERT/XLM-R bất đồng

nlp/
├── schemas.py                   # SentimentResult — schema chung mọi analyzer
├── phobert_analyzer.py          # load model local, BẮT BUỘC word-segment trước tokenize
├── finbert_analyzer.py          # tiếng Anh, ProsusAI/finbert
├── xlmr_analyzer.py             # fallback đa ngôn ngữ + dùng cho so sánh
└── multilingual_analyzer.py     # router theo detect_language()
```