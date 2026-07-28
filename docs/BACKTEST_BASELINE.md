
# Backtest Baseline — Ngày 13 (Confluence thật, không còn mock)

## Trạng thái: Baseline thật đầu tiên — CẦN ĐỌC PHẦN CẢNH BÁO TRƯỚC KHI DÙNG SỐ LIỆU

Đây là lần đầu tiên `backtest_confluence_signal()` chạy với signal THẬT
từ `analyze_confluence()` (đã nối data thật ở Ngày 13), thay vì mock cố
định của Ngày 12. Baseline dưới đây phản ánh confluence logic hiện tại
(Ngày 8-13), TRƯỚC khi có sentiment (Tuần 3-4), risk manager (Tuần 5),
LLM reasoning (Tuần 6).

## ⚠️ CẢNH BÁO QUAN TRỌNG: Baseline cực kỳ nhạy với điểm kết thúc cửa sổ

**Phát hiện qua thực nghiệm:** chạy backtest 90 ngày với cửa sổ
`end = datetime.now()` hai lần cách nhau ~20 phút (chỉ lệch 1 nến,
2153 → 2152 nến) cho ra kết quả ĐẢO NGƯỢC hoàn toàn:

| Cửa sổ | Win Rate | Sharpe | Total Return | Trades |
|---|---|---|---|---|
| Kết thúc tại nến 2153 | 41.67% | +0.30 | +0.82% | 24 |
| Kết thúc tại nến 2152 (lệch 1 nến) | 26.09% | **-1.61** | **-6.08%** | 23 |

Sau khi cố định cửa sổ tuyệt đối (`end = 2026-07-12 00:00:00`), chạy
lại 5 lần độc lập cho kết quả **giống hệt nhau tuyệt đối** ở mọi chữ
số thập phân — xác nhận hệ thống hoàn toàn deterministic, KHÔNG có bug
non-determinism nào. Nguồn dao động ở trên hoàn toàn đến từ việc walk
sang 1 nến khác trong cửa sổ 90 ngày.

**Kết luận:** với chỉ ~23-24 lệnh trong 90 ngày, baseline là MỘT ĐIỂM
DỮ LIỆU DUY NHẤT, không phải ước lượng ổn định. Một vài giao dịch đổi
kết quả (thắng↔thua) đủ sức lật dấu Sharpe Ratio. **Không nên dùng con
số dưới đây như thước đo tuyệt đối "chiến lược tốt hay tệ"** — chỉ nên
dùng để so sánh TƯƠNG ĐỐI khi cải tiến hệ thống (Tuần 3 trở đi), và
phải chạy lại trên CÙNG cửa sổ cố định để so sánh công bằng.

## Baseline (cửa sổ cố định: 2026-04-13 → 2026-07-12, BTCUSDT, 1h)

```json
{
  "win_rate": 26.09,
  "sharpe_ratio": -1.61,
  "max_drawdown": 8.71,
  "profit_factor": 0.52,
  "total_trades": 23,
  "total_return": -6.08
}
```

Params: `sl_pct=0.02, tp_pct=0.04, strength_threshold=0.65, exit_threshold=0.30`

Logged vào MLflow: experiment `Crypto_Agent_Backtesting`, run
`Baseline_90days_Fixed_Run1` đến `Run5` (5 lần chạy giống hệt, dùng để
verify determinism, không phải 5 kết quả khác nhau).

## Đối chiếu với ngưỡng tối thiểu (Milestone Tuần 2)

| Tiêu chí | Ngưỡng | Kết quả | Đạt? |
|---|---|---|---|
| Win Rate | > 45% | 26.09% | ❌ |
| Sharpe Ratio | > 1.0 | -1.61 | ❌ |
| Max Drawdown | < 20% | 8.71% | ✅ |

**Đánh giá trung thực:** Confluence logic hiện tại (chỉ dùng TA đa
khung, chưa có sentiment/news/risk manager) CHƯA đạt ngưỡng production.
Đây là kỳ vọng hợp lý ở giai đoạn này — roadmap dành riêng Tuần 3-6 để
cải thiện chất lượng tín hiệu. Điều quan trọng là con số này THẬT,
không phải mock, và phương pháp đo đã được kiểm chứng deterministic.

## Việc cần làm trước khi tin baseline này (không bắt buộc ngay, ghi nhận nợ kỹ thuật)

- [ ] Walk-forward validation trên NHIỀU cửa sổ 90 ngày khác nhau
      (không chỉ 1 cửa sổ) để có khoảng tin cậy thay vì 1 điểm
- [ ] Backtest trên nhiều coin (hiện chỉ test BTCUSDT) để tránh
      overfitting vào 1 coin cụ thể
- [ ] Tăng recompute_every_candles xuống thấp hơn (hiện =4, tính lại
      mỗi 4 tiếng) để xem độ nhạy của kết quả với tần suất đánh giá lại

## Đã verify (giữ nguyên từ Ngày 12, vẫn đúng)

- [x] `backtest_confluence_signal()` chạy được qua `VectorBT.Portfolio.from_signals` với signal THẬT
- [x] `entries`/`exits` dùng `signal.shift(1)` — tránh look-ahead bias
- [x] MLflow ghi nhận đúng params + metrics
- [x] MLflow tracking URI hoạt động đúng cả trong Airflow container lẫn WSL2 local (`.env`)
- [x] MLflow persistence qua `docker compose down -v && up`
- [x] `_safe_float()` guard NaN/Inf trước khi log metric
- [x] Cold-start `docker compose down -v && up --build` thành công (container Python 3.8/Airflow)
- [x] **MỚI (Ngày 13):** xác nhận hệ thống deterministic qua 5 lần chạy độc lập cùng cửa sổ cố định

## Gotcha đã gặp và fix (giữ nguyên, vẫn đáng tham khảo)

1. **`vectorbt` crash ngay lúc `import`** nếu không ghim version `plotly`.
   `vectorbt==0.26.2` đăng ký theme màu dùng trace type `heatmapgl` lúc
   import — Plotly xoá hẳn trace type này từ bản 6.0 trở đi. Fix: ghim
   `plotly==5.24.1` trong `requirements-airflow.txt`.
2. **Docker build cache có thể "che" việc thiếu dependency mới** — nếu
   sửa `requirements-airflow.txt` mà build vẫn báo `CACHED` cho bước
   `pip install`, nghĩa là file chưa thực sự đổi hoặc cần
   `docker compose build --no-cache <service>`.
3. **`mlflow_data` bị root chiếm quyền** vì container MLflow chạy dưới
   root mặc định. Fix: thêm `user: "${UID}:${GID}"` cho service mlflow
   trong docker-compose.yml, và set UID/GID thật vào `.env`.
4. **MỚI:** `mlflow` (bản full) xung đột `protobuf` với các bản mới
   (`google.protobuf.service` bị loại bỏ ở protobuf 5+). Fix: pin
   `protobuf>=3.20,<5`, hoặc chỉ cài `mlflow-skinny` thay vì `mlflow` đầy đủ.
5. **MỚI:** Backtest dùng `datetime.now()` làm điểm cuối cửa sổ →
   KHÔNG reproducible giữa các lần chạy (xem cảnh báo ở trên). Với
   mọi backtest cần so sánh, LUÔN cố định `end` bằng giá trị tuyệt đối.

# Backtest Baseline — Ngày 12

## Trạng thái: Infra verified, chưa có baseline chiến lược thật

Ngày 12 tập trung vào việc dựng hạ tầng backtesting (VectorBT + MLflow),
**chưa có signal thật** từ ConfluenceAnalyzer (việc của Ngày 11, sẽ nối
vào Ngày 13). Vì vậy các con số dưới đây **KHÔNG phải baseline chiến
lược** — chỉ là kết quả từ signal ngẫu nhiên, dùng để verify luồng kỹ
thuật hoạt động đúng.

## Đã verify

- [x] `backtest_mock_signal()` chạy được qua `VectorBT.Portfolio.from_signals`
- [x] `entries`/`exits` dùng `signal.shift(1)` — tránh look-ahead bias khi
      nối signal thật vào sau này
- [x] MLflow ghi nhận đúng params + metrics qua `mlflow.log_params()` /
      `mlflow.log_metrics()`
- [x] MLflow tracking URI đọc từ env var, hoạt động đúng cả khi chạy
      trong container Airflow (`http://mlflow:5000`) lẫn chạy local từ
      WSL2 (`http://localhost:5000` qua `.env`)
- [x] MLflow persistence: chạy 1 run → `docker compose down -v` →
      `up` lại → run vẫn còn trên UI (`--backend-store-uri` +
      `--default-artifact-root` trỏ đúng vào `/mlflow` đã mount)
- [x] `_safe_float()` guard NaN/Inf trước khi log metric
- [x] Cold-start `docker compose down -v && up --build` chạy thành công
      trong container Python 3.8 (Airflow), đã fix conflict version
      `vectorbt==0.26.2` với `plotly` (ghim `plotly==5.24.1`, vì
      `heatmapgl` bị Plotly xoá từ bản 6.0 — xem chi tiết bên dưới)

## Gotcha đã gặp và fix (đáng giữ lại cho tương lai)

1. **`vectorbt` crash ngay lúc `import`** nếu không ghim version `plotly`.
   `vectorbt==0.26.2` đăng ký theme màu dùng trace type `heatmapgl` lúc
   import — Plotly xoá hẳn trace type này từ bản 6.0 trở đi. Fix: ghim
   `plotly==5.24.1` trong `requirements-airflow.txt`.
2. **Docker build cache có thể "che" việc thiếu dependency mới** — nếu
   sửa `requirements-airflow.txt` mà build vẫn báo `CACHED` cho bước
   `pip install`, nghĩa là file chưa thực sự đổi hoặc cần
   `docker compose build --no-cache <service>`.
3. **`mlflow_data` bị root chiếm quyền** vì container MLflow chạy dưới
   root mặc định. Fix: thêm `user: "${UID}:${GID}"` cho service mlflow
   trong docker-compose.yml, và set UID/GID thật (từ `id -u`/`id -g`)
   vào `.env`.

## Việc còn lại (Ngày 13 trở đi)

- [ ] Nối `ConfluenceAnalyzer` (Ngày 11) vào `backtest_confluence_signal()`
      để có signal thật thay vì mock
- [ ] Chạy backtest thật trên data BTC 6 tháng, ghi baseline thật vào
      file này (thay thế toàn bộ nội dung phía trên)
- [ ] So sánh Win Rate / Sharpe / Max Drawdown với ngưỡng tối thiểu
      (Win Rate > 45%, Sharpe > 1.0, Max Drawdown < 20%)

# Backtest Baseline — Ngày 13 (Confluence thật, không còn mock)

## Trạng thái: Baseline thật đầu tiên — CẦN ĐỌC PHẦN CẢNH BÁO TRƯỚC KHI DÙNG SỐ LIỆU

Đây là lần đầu tiên `backtest_confluence_signal()` chạy với signal THẬT
từ `analyze_confluence()` (đã nối data thật ở Ngày 13), thay vì mock cố
định của Ngày 12. Baseline dưới đây phản ánh confluence logic hiện tại
(Ngày 8-13), TRƯỚC khi có sentiment (Tuần 3-4), risk manager (Tuần 5),
LLM reasoning (Tuần 6).

## ⚠️ CẢNH BÁO QUAN TRỌNG: Baseline cực kỳ nhạy với điểm kết thúc cửa sổ

**Phát hiện qua thực nghiệm:** chạy backtest 90 ngày với cửa sổ
`end = datetime.now()` hai lần cách nhau ~20 phút (chỉ lệch 1 nến,
2153 → 2152 nến) cho ra kết quả ĐẢO NGƯỢC hoàn toàn:

| Cửa sổ | Win Rate | Sharpe | Total Return | Trades |
|---|---|---|---|---|
| Kết thúc tại nến 2153 | 41.67% | +0.30 | +0.82% | 24 |
| Kết thúc tại nến 2152 (lệch 1 nến) | 26.09% | **-1.61** | **-6.08%** | 23 |

Sau khi cố định cửa sổ tuyệt đối (`end = 2026-07-12 00:00:00`), chạy
lại 5 lần độc lập cho kết quả **giống hệt nhau tuyệt đối** ở mọi chữ
số thập phân — xác nhận hệ thống hoàn toàn deterministic, KHÔNG có bug
non-determinism nào. Nguồn dao động ở trên hoàn toàn đến từ việc walk
sang 1 nến khác trong cửa sổ 90 ngày.

**Kết luận:** với chỉ ~23-24 lệnh trong 90 ngày, baseline là MỘT ĐIỂM
DỮ LIỆU DUY NHẤT, không phải ước lượng ổn định. Một vài giao dịch đổi
kết quả (thắng↔thua) đủ sức lật dấu Sharpe Ratio. **Không nên dùng con
số dưới đây như thước đo tuyệt đối "chiến lược tốt hay tệ"** — chỉ nên
dùng để so sánh TƯƠNG ĐỐI khi cải tiến hệ thống (Tuần 3 trở đi), và
phải chạy lại trên CÙNG cửa sổ cố định để so sánh công bằng.

## Baseline (cửa sổ cố định: 2026-04-13 → 2026-07-12, BTCUSDT, 1h)

```json
{
  "win_rate": 26.09,
  "sharpe_ratio": -1.61,
  "max_drawdown": 8.71,
  "profit_factor": 0.52,
  "total_trades": 23,
  "total_return": -6.08
}
```

Params: `sl_pct=0.02, tp_pct=0.04, strength_threshold=0.65, exit_threshold=0.30`

Logged vào MLflow: experiment `Crypto_Agent_Backtesting`, run
`Baseline_90days_Fixed_Run1` đến `Run5` (5 lần chạy giống hệt, dùng để
verify determinism, không phải 5 kết quả khác nhau).

## Đối chiếu với ngưỡng tối thiểu (Milestone Tuần 2)

| Tiêu chí | Ngưỡng | Kết quả | Đạt? |
|---|---|---|---|
| Win Rate | > 45% | 26.09% | ❌ |
| Sharpe Ratio | > 1.0 | -1.61 | ❌ |
| Max Drawdown | < 20% | 8.71% | ✅ |

**Đánh giá trung thực:** Confluence logic hiện tại (chỉ dùng TA đa
khung, chưa có sentiment/news/risk manager) CHƯA đạt ngưỡng production.
Đây là kỳ vọng hợp lý ở giai đoạn này — roadmap dành riêng Tuần 3-6 để
cải thiện chất lượng tín hiệu. Điều quan trọng là con số này THẬT,
không phải mock, và phương pháp đo đã được kiểm chứng deterministic.

## Việc cần làm trước khi tin baseline này (không bắt buộc ngay, ghi nhận nợ kỹ thuật)

- [ ] Walk-forward validation trên NHIỀU cửa sổ 90 ngày khác nhau
      (không chỉ 1 cửa sổ) để có khoảng tin cậy thay vì 1 điểm
- [ ] Backtest trên nhiều coin (hiện chỉ test BTCUSDT) để tránh
      overfitting vào 1 coin cụ thể
- [ ] Tăng recompute_every_candles xuống thấp hơn (hiện =4, tính lại
      mỗi 4 tiếng) để xem độ nhạy của kết quả với tần suất đánh giá lại

## Đã verify (giữ nguyên từ Ngày 12, vẫn đúng)

- [x] `backtest_confluence_signal()` chạy được qua `VectorBT.Portfolio.from_signals` với signal THẬT
- [x] `entries`/`exits` dùng `signal.shift(1)` — tránh look-ahead bias
- [x] MLflow ghi nhận đúng params + metrics
- [x] MLflow tracking URI hoạt động đúng cả trong Airflow container lẫn WSL2 local (`.env`)
- [x] MLflow persistence qua `docker compose down -v && up`
- [x] `_safe_float()` guard NaN/Inf trước khi log metric
- [x] Cold-start `docker compose down -v && up --build` thành công (container Python 3.8/Airflow)
- [x] **MỚI (Ngày 13):** xác nhận hệ thống deterministic qua 5 lần chạy độc lập cùng cửa sổ cố định

## Gotcha đã gặp và fix (giữ nguyên, vẫn đáng tham khảo)

1. **`vectorbt` crash ngay lúc `import`** nếu không ghim version `plotly`.
   `vectorbt==0.26.2` đăng ký theme màu dùng trace type `heatmapgl` lúc
   import — Plotly xoá hẳn trace type này từ bản 6.0 trở đi. Fix: ghim
   `plotly==5.24.1` trong `requirements-airflow.txt`.
2. **Docker build cache có thể "che" việc thiếu dependency mới** — nếu
   sửa `requirements-airflow.txt` mà build vẫn báo `CACHED` cho bước
   `pip install`, nghĩa là file chưa thực sự đổi hoặc cần
   `docker compose build --no-cache <service>`.
3. **`mlflow_data` bị root chiếm quyền** vì container MLflow chạy dưới
   root mặc định. Fix: thêm `user: "${UID}:${GID}"` cho service mlflow
   trong docker-compose.yml, và set UID/GID thật vào `.env`.
4. **MỚI:** `mlflow` (bản full) xung đột `protobuf` với các bản mới
   (`google.protobuf.service` bị loại bỏ ở protobuf 5+). Fix: pin
   `protobuf>=3.20,<5`, hoặc chỉ cài `mlflow-skinny` thay vì `mlflow` đầy đủ.
5. **MỚI:** Backtest dùng `datetime.now()` làm điểm cuối cửa sổ →
   KHÔNG reproducible giữa các lần chạy (xem cảnh báo ở trên). Với
   mọi backtest cần so sánh, LUÔN cố định `end` bằng giá trị tuyệt đối.