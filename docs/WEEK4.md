# WEEK4.md — Tổng kết Tuần 4 (Ngày 22–28)

> Template — mọi ô `<...>` PHẢI được điền bằng số thật + nguồn (1 MLflow
> run hoặc 1 file `GHI_CHU_NGAYxx.md`), theo đúng quy tắc đã đặt ra ở
> `crypto_agent_roadmap_v2.md`: "không ghi bất kỳ con số nào nếu không
> trỏ được về nguồn". Ô nào chưa có nguồn → để nguyên `<TODO>`, không
> làm tròn/đoán.

## 1. Mục tiêu tuần 4 vs thực tế

- N22-23: metadata enrichment + benchmark embedding
- N24-25: cross-encoder reranker + metadata filtering
- N26: retrieval evaluation (IR metrics, viết lại hoàn toàn so với v1)
- N27: news-technical confirmation
- N28: review + đóng nợ + test end-to-end

<TODO: 2-3 câu tóm tắt độ lệch lớn nhất giữa kế hoạch ban đầu và thực tế
trong tuần — theo mẫu các GHI_CHU trước, mục "Mục tiêu ban đầu vs thực tế">

## 2. Số liệu — bắt buộc trỏ nguồn

| Hạng mục | Số đo | Nguồn | Trạng thái |
|---|---|---|---|
| IR baseline P@5 / R@5 / MRR | 1.000 / 0.963 / 1.000 | MLflow `Crypto_Agent_Retrieval_Eval_Day26` + `GHI_CHU_NGAY26.md` §4 | ✅ |
| Regression gate negative control | ⚠️ ledger ghi "7 câu" (N24-25), nhưng N26 chỉ chạy 6/6, ngưỡng đổi từ <0.14 → <0.20 | `GHI_CHU_NGAY26.md` §4 Bộ B vs `roadmap_v2.md` (`[x] Negative control 7 câu`) | ❌ **CHƯA ĐỐI CHIẾU — xem mục 3** |
| Nợ #1 (truncation, N26) | gap = -0.066 (hit-rate tin dài CAO hơn tin ngắn) | `GHI_CHU_NGAY26.md` §4 Bộ C | ✅ ĐÓNG |
| Nợ #4 (docstring `upsert()`, N27) | "đã có sẵn từ patch 2026-08-13" | `GHI_CHU_NGAY27.md` §1 — **chưa có grep/diff trích dẫn** | ⚠️ **CHƯA CÓ BẰNG CHỨNG CỨNG — xem mục 3** |
| Unit test toàn project | **131/131 pass** (chạy lại hôm nay, không phải số cũ N27) | Output pytest phiên N28, `4.39s`, 11 warning (đều là deprecation của `binance`/`websockets`/`chromadb`, không phải lỗi) | ✅ |
| Test end-to-end (real DB/Chroma) | Đã tạo `tests/integration/test_news_confirmation_e2e.py`, 5 test | — | ⚠️ ĐANG CHẠY, xem dưới |
| Test end-to-end — vòng 1 | 1/5 pass, 4/5 fail — 1 nguyên nhân gốc: `TelegramChannel` không có field `channel_name`, tên thật là `username` (xác nhận qua `data_pipeline/models.py`) | Output pytest phiên N28 | ✅ đã sửa trong file |
| Test end-to-end — vòng 2 (sau sửa) | **5/5 pass**, 19.24s. 3/5 test có warning `chromadb` (legacy embedding function config) — xác nhận các test đó THẬT SỰ chạm Chroma qua `search_telegram_news()` (nhánh không rơi vào `no_data`), không chỉ Postgres | Output pytest phiên N28, 5 passed, 3 warnings in 19.24s | ✅ |

## 3. Vấn đề phát hiện khi review N28 (mới, chưa có ở note trước)

1. **Regression gate 7 vs 6**: N24-25 chốt 7 câu negative control, `roadmap_v2.md`
   tick `[x]`. N26 chỉ chạy 6/6 với ngưỡng <0.20 (khác <0.14 đã chốt).
   **Đã đối chiếu (2026-10-06): loại có chủ đích, không phải bỏ sót.**
   Bộ 7 câu = 5 câu mới (`GHI_CHU_NGAY24_25.md` §8) + 2 câu cũ
   ("công thức nấu phở bò", "weather forecast tomorrow"). Câu bị bỏ là
   **"weather forecast tomorrow"**: §3 của cùng file ghi câu này "không
   sạch" vì kênh `coin369channel` có đăng tin thời tiết thật. Quyết định
   bỏ được ghi ngay trong code, `scripts/manual/eval_retrieval_day26.py`
   dòng 112-114 ("ĐÃ BỎ ... (2026-08-13)"). 6 câu còn lại khớp đúng
   từng chữ với danh sách N24-25.
   Về ngưỡng: 0.14 **không phải ngưỡng đã chốt** mà là mức điểm cao nhất
   quan sát được của mmarco (`GHI_CHU_NGAY24_25.md` §9: "giữ dưới 0.14
   mọi câu"). 0.20 là ngưỡng gate đặt có biên an toàn
   (`LOW_SCORE_THRESHOLD`, dòng 132). Ghi chú N28 trước đó đọc nhầm
   thành "đổi ngưỡng". Giới hạn còn lại: `GHI_CHU_NGAY26.md` chỉ ghi
   "6/6 dưới 0.20", không ghi điểm từng câu, nên chưa biết lần chạy N26
   có còn nằm dưới 0.14 hay không (cần xem MLflow
   `Crypto_Agent_Retrieval_Eval_Day26`).
2. **Nợ #4 chưa có bằng chứng cứng**: cần chạy
   `grep -n "upsert" -A 5 rag/ingestion.py` (hoặc tương đương) và dán
   đoạn docstring thật vào đây trước khi tick nợ #4 là đóng.
3. **Roadmap tracking lệch thực tế**: bảng milestone trong
   `roadmap_v2.md` còn để trống `[ ]` cho "IR metrics baseline (N26)",
   "Kết luận chunking (N26)", "News-technical confirmation (N27)" dù cả
   3 đã xong. Bảng "Tiêu Chuẩn Chất Lượng" còn ghi unit test 117/117
   thay vì 131/131 (N27). Cần sửa trực tiếp trong `roadmap_v2.md`, không
   chỉ trong `WEEK4.md` này.
4. **Test end-to-end thật chưa từng chạy**: 14 test N27 đều mock
   `aggregate_coin_sentiment` + `search_telegram_news`. Chưa có test nào
   chạm Postgres/Chroma thật cho `correlate_news_with_technical()` —
   kể cả case `direction='neutral'` (tự ghi là việc mở ở
   `GHI_CHU_NGAY27.md` §9). **N28**: đã viết test, chạy thử BLOCKED ở
   bước seed do đoán sai tên field `TelegramChannel` — chưa xác nhận
   được logic thật vì chưa qua nổi bước dựng dữ liệu.
5. **Phát hiện mới, chưa rõ ý nghĩa**: `tests/unit/test_signal_validation.py`
   có `_to_short_symbol()` — khác với `_to_base_symbol()` stopgap trong
   `rag/news_confirmation.py` (nợ #4 docstring, không phải nợ #2). Chưa
   rõ đây có phải bước đầu xử lý nợ #2 (BTC/BTCUSDT, deadline N31) làm
   sớm hay là việc khác trùng tên. Cần xác nhận trước N31 — nếu đúng là
   chuẩn hóa chính thức, `_to_base_symbol()` phải gọi lại nó thay vì
   giữ 2 bản logic song song.

## 4. Việc phải làm trước khi coi N28 là xong

Đã xong: unit test (131/131), test end-to-end mới (5/5), nợ #4 (bằng
chứng cứng trong `rag/ingestion.py`), nợ #1 (đã đóng từ N26).

Regression gate: đã đối chiếu xong (2026-10-06, mục 3.1).

**(Đã đóng 2026-10-08, xem mục 6.)** Việc chặn cũ: xác minh `dag_embed_messages` GREEN. Việc này cần
stack docker đang chạy; lúc kiểm tra ngày 2026-10-06, lệnh `docker`
không có trong WSL.

## 5. Cập nhật áp dụng vào `crypto_agent_roadmap_v2.md`

- [ ] Tick `[x]` cho "IR metrics baseline (N26)", "Kết luận chunking (N26)",
      "News-technical confirmation (N27)" trong bảng milestone
- [ ] Bảng "Nợ Kỹ Thuật Đang Treo": đánh dấu #1 ĐÓNG (N26, gap=-0.066),
      #4 ĐÓNG (N27) **chỉ sau khi có bằng chứng cứng mục 3.2**
- [ ] Bảng "Tiêu Chuẩn Chất Lượng": Unit test pass 117/117 → số thật N28
- [x] Ghi rõ quyết định về câu negative control thứ 7 (mục 3.1)

## 6. Milestone Tuần 4 — trạng thái thật (không làm tròn)

- [x] IR metrics đo được, có baseline trong MLflow
- [x] Metadata filtering đúng theo coin + timeframe (N24-25)
- [x] Cross-Encoder reranking hoạt động
- [x] Regression gate: 6/6 dưới 0.20 (`GHI_CHU_NGAY26.md` Bộ B); câu thứ 7 bị loại có chủ đích (mục 3.1)
- [x] News-technical confirmation hoạt động, phân biệt `no_data` vs `neutral` — xác nhận thêm bởi test end-to-end N28 (5/5 pass, chạm cả Postgres lẫn Chroma thật)
- [x] `dag_embed_messages` GREEN — **xác minh 2026-10-08, sau khi sửa bug**: DAG ImportError mọi lần chạy từ 2026-08-13 (`get_last_embedded_id` đã bị xóa khỏi `rag/ingestion.py`). Sau khi sửa: run `manual__2026-10-07T18:58:17` success, upsert 3043; Chroma 30351 = Postgres 30351. Xem `docs/nhat-ky/2026-10-08_bug_dag-embed-messages-importerror-moi-lan-chay-tu-2026-08-13.md`
- [x] RAGAS > 0.70 dời N34-35 (đúng kế hoạch, không phải trượt)

## 7. Commit

<TODO: hash commit + message sau khi hoàn tất mục 4>