# WEEK 2 — Technical Analysis Engine (Ngày 8–14)

> Tài liệu này mô tả kiến trúc, luồng dữ liệu, và trạng thái thật của
> project tính đến hết Tuần 2. Viết để bất kỳ ai (người hoặc AI) đọc
> vào có thể hiểu đúng hệ thống đang hoạt động thế nào, không cần đọc
> lại toàn bộ lịch sử commit hay hội thoại.

## 1. Tổng quan kiến trúc

```
Binance API ──► ohlcv_pipeline.py ──► PostgreSQL (bảng ohlcv)
                                              │
                                              ▼
                              indicator_pipeline.py
                    (fetch_ohlcv_from_db → calculate_all →
                     find_swing_points → analyze_confluence)
                                              │
                                              ▼
                          PostgreSQL (bảng technical_indicators)
                                              │
                                              ▼
                    backtest_data.py (build_confluence_signal_series)
                                              │
                                              ▼
                      backtest.py (backtest_confluence_signal)
                                              │
                                              ▼
                                    MLflow (metrics + params)
```

Toàn bộ pipeline trên được Airflow điều phối qua 2 DAG nối tiếp nhau:
`binance_ohlcv_hourly_sync` (lấy nến mới mỗi giờ) →
`dag_calculate_indicators` (tính lại indicator cho 5 coin × 4 timeframe,
trigger tự động qua `TriggerDagRunOperator`).

## 2. Cấu trúc thư mục (những phần đã có code thật)

```
crypto-agent/
├── data_pipeline/
│   ├── binance/
│   │   └── ohlcv_pipeline.py      # fetch, validate, backfill OHLCV từ Binance
│   ├── telegram/
│   │   ├── historical_scraper.py  # scrape lịch sử + checkpoint resume
│   │   └── realtime_listener.py   # lắng nghe tin nhắn mới qua Telethon
│   ├── models.py                  # SQLAlchemy models (xem mục 4)
│   └── logger.py                  # get_logger() dùng chung toàn project
├── technical_analysis/
│   ├── indicators.py              # calculate_all() — RSI/MACD/BB/EMA/ATR
│   ├── support_resistance.py      # find_swing_points(), cluster_levels()
│   ├── trend.py                   # detect_trend() — EMA crossover + RSI
│   ├── fibonacci.py               # calculate_fib_levels(), find_recent_swing()
│   ├── confluence.py              # analyze_confluence() — multi-timeframe vote
│   ├── indicator_pipeline.py      # nối DB ↔ các module tính toán ở trên
│   ├── backtest.py                # backtest qua VectorBT + log MLflow
│   └── backtest_data.py           # build chuỗi 'strength' lịch sử cho backtest
├── dags/
│   ├── dag_binance_ohlcv_sync.py  # DAG chính: sync OHLCV mỗi giờ
│   ├── dag_calculate_indicators.py# DAG: tính indicator, trigger bởi DAG trên
│   └── dag_telegram_realtime.py   # DAG: telegram realtime (Tuần 1)
├── migrations/                    # Alembic — nguồn sự thật cho DB schema
├── tests/
│   ├── unit/                      # 61 tests, không cần DB
│   └── integration/                # 6 tests, cần DB (dùng conftest.py)
├── scripts/
│   ├── test_backtest_data.py      # script chạy thử build + backtest 1 coin
│   └── manual/                    # script thử nghiệm tay, KHÔNG bị pytest quét
├── docs/
│   ├── WEEK1.md
│   ├── WEEK2.md                   # file này
│   └── BACKTEST_BASELINE.md       # baseline thật + cảnh báo thống kê
├── docker-compose.yml
├── Dockerfile.airflow
├── requirements.txt               # dependency cho .venv local
├── requirements-airflow.txt       # dependency RIÊNG cho container Airflow
├── conftest.py                    # fixture db_session dùng chung mọi integration test
└── pytest.ini                     # asyncio_mode = auto
```

## 3. Hai môi trường Python tách biệt — điều quan trọng nhất cần hiểu

Project chạy trên **2 môi trường Python khác nhau**, mỗi bên có version
dependency riêng, và code phải viết sao cho chạy đúng ở CẢ HAI:

| | `.venv` local (WSL2) | Container Airflow |
|---|---|---|
| Dùng để | `pytest`, chạy script tay (`python -m scripts...`) | Chạy DAG production |
| SQLAlchemy | `2.0.x` (mới nhất từ `requirements.txt`) | `1.4.52` (bị Airflow 2.9.2 core ép, xem mục 6) |
| `POSTGRES_HOST` | `localhost` | `postgres` (tên service Docker) |
| `MLFLOW_TRACKING_URI` | `http://localhost:5000` | `http://mlflow:5000` |
| File cấu hình | `.env` | biến `environment:` trong `docker-compose.yml` — **KHÔNG tự kế thừa `.env`**, phải khai báo tường minh từng biến |

**Hệ quả trực tiếp lên cách viết code:**
- `data_pipeline/models.py` viết theo cú pháp SQLAlchemy **legacy**
  (`Column()`, `declarative_base()`) — tương thích cả 1.4 và 2.0. KHÔNG
  dùng `Mapped[]`/`mapped_column()`/`DeclarativeBase` (chỉ có ở 2.0+,
  sẽ làm Airflow crash lúc import).
- `dags/dag_calculate_indicators.py` dùng
  `sessionmaker(engine, class_=AsyncSession)` — tương thích cả 2 bản.
  `conftest.py` và `scripts/test_backtest_data.py` (chỉ chạy ở `.venv`
  local) được phép dùng `async_sessionmaker` (chỉ có ở SQLAlchemy ≥2.0.13)
  vì chúng không bao giờ chạy trong container Airflow.

## 4. Database schema (nguồn sự thật: `migrations/versions/`, không phải mô tả này)

5 bảng, DB `crypto_agent` (dev) và `crypto_agent_test` (test, được
`conftest.py` tự tạo/xoá bảng mỗi lần chạy qua `Base.metadata.create_all`/`drop_all`):

- `telegram_channels`, `telegram_messages` — Tuần 1, chưa đổi ở Tuần 2
- `ohlcv` — coin, timeframe, open_time (naive datetime, quy ước = UTC), open/high/low/close/volume. UNIQUE(coin, timeframe, open_time)
- `technical_indicators` — coin, timeframe, calculated_at, rsi_14, macd_*, bb_*, ema_20/50, atr_14, support_levels[], resistance_levels[], confluence_score, trend_direction. UNIQUE(coin, timeframe, calculated_at)

⚠️ **Gotcha timezone:** cột `open_time`/`calculated_at` là `DateTime`
naive. Binance trả về timestamp aware UTC — `bulk_insert_ohlcv()` chủ
động `.replace(tzinfo=None)` trước khi insert. Toàn hệ thống ngầm định
"naive datetime = UTC". Đừng phá quy ước này khi thêm code mới.

## 5. Luồng xử lý chính — theo đúng thứ tự thực thi

1. `backfill_all()` (`ohlcv_pipeline.py`) gọi Binance API cho 5 coin
   (`BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT`) × 4 timeframe
   (`15m, 1h, 4h, 1d`), validate giá/volume, ghi vào `ohlcv`.
2. `calculate_and_save_all()` (`indicator_pipeline.py`) loop qua cùng
   5×4 tổ hợp, với mỗi tổ hợp:
   - `fetch_ohlcv_from_db()` đọc 200 nến gần nhất
   - `calculate_all()` tính RSI/MACD/BB/EMA/ATR (tự guard NaN, KHÔNG
     raise exception — trả `IndicatorSet` toàn NaN nếu thiếu data)
   - `find_swing_points()` tính S/R
   - **Chỉ ở khung `1h`**: gọi `analyze_confluence()` — hàm này tự
     fetch cả 4 timeframe (15m/1h/4h/1d) qua `fetch_ohlcv_from_db`,
     tính `detect_trend()` + `calculate_fib_levels()` cho từng khung,
     rồi `weighted_vote()` để ra `direction` + `strength`
   - `upsert_indicators()` ghi 1 record vào `technical_indicators`
     (ON CONFLICT DO UPDATE — update ĐỦ mọi cột, không update dở dang)
3. `build_confluence_signal_series()` (`backtest_data.py`) — dùng cho
   BACKTEST LỊCH SỬ, KHÔNG dùng trong pipeline production ở bước 2.
   Vì `analyze_confluence()` chỉ tính 1 điểm tại thời điểm gọi, hàm
   này duyệt qua chuỗi nến 1h trong quá khứ, cứ mỗi
   `recompute_every_candles` (mặc định 4 = mỗi 4 tiếng) lại gọi
   `analyze_confluence(..., as_of=candle_time)` thật (dùng tham số
   `as_of` để không nhìn thấy data tương lai), forward-fill giữa
   2 lần tính.
4. `backtest_confluence_signal()` (`backtest.py`) nhận DataFrame có
   cột `strength` (từ bước 3), chạy qua VectorBT
   (`entries = signal.shift(1) > 0.65`, tránh look-ahead bias), trả
   về `win_rate`, `sharpe_ratio`, `max_drawdown`, `profit_factor`,
   `total_trades`, `total_return`.
5. `run_experiment()` log toàn bộ params + metrics vào MLflow,
   experiment `Crypto_Agent_Backtesting`.

## 6. Nợ kỹ thuật & giới hạn đã biết (đọc trước khi mở rộng)

- **`analyze_confluence()` được gọi TRỰC TIẾP trong production pipeline
  (bước 2) MỖI LẦN DAG chạy** — với 5 coin, mỗi lần tính khung 1h sẽ
  gọi lại 4 lượt fetch+tính cho cả 4 timeframe. Chưa có cache/tối ưu —
  chấp nhận được ở quy mô hiện tại (5 coin, chạy mỗi giờ), nhưng sẽ
  cần tối ưu nếu số coin tăng lên nhiều.
- **Backtest baseline cực kỳ nhạy với điểm kết thúc cửa sổ** — xem
  chi tiết + số liệu thật trong `docs/BACKTEST_BASELINE.md`. Với chỉ
  ~23 lệnh/90 ngày, ĐỪNG coi 1 lần chạy là kết luận cuối cùng.
- **Airflow 2.9.2 giới hạn SQLAlchemy <2.0** — nếu sau này nâng cấp
  Airflow lên 2.10+, có thể bỏ giới hạn này và viết lại `models.py`
  theo cú pháp `Mapped[]` hiện đại hơn nếu muốn — không bắt buộc, cú
  pháp legacy hiện tại hoạt động đúng trên cả 2 phiên bản.
- **`requirements-airflow.txt` là danh sách RIÊNG**, tách khỏi
  `requirements.txt` — mọi thư viện mới mà code trong `technical_analysis/`
  hoặc `data_pipeline/` cần, PHẢI thêm vào cả 2 file nếu muốn chạy
  được ở cả `.venv` local lẫn Airflow. Quên 1 trong 2 → lỗi
  `ModuleNotFoundError` chỉ xuất hiện ở 1 môi trường, dễ gây nhầm lẫn
  "chạy pytest thì ổn mà Airflow thì lỗi".
- **Threshold `strength_threshold=0.65` / `exit_threshold=0.30`** hiện
  hardcode trong `backtest_confluence_signal()` — đây là số ước lượng
  ban đầu từ roadmap, CHƯA được hiệu chỉnh dựa trên phân phối
  `strength` thật (mean≈0.23, std≈0.37 trên data 90 ngày BTCUSDT).

## 7. Trạng thái Milestone Tuần 2 (theo `crypto_agent_roadmap.md`)

| Tiêu chí | Trạng thái |
|---|---|
| Confluence score tự động, data thật (không mock) | ✅ Xong |
| Backtest baseline ghi nhận (số thật) | ✅ Xong — chưa đạt ngưỡng Win Rate>45%/Sharpe>1.0, đúng kỳ vọng ở baseline |
| Indicators lưu tự động qua Airflow (verify chạy thật, không chỉ code) | ✅ Xong — `dag_calculate_indicators` chạy 20/20 thành công trong Airflow UI |
| MLflow tracking | ✅ Xong — log thật, verify qua UI |
| >25 unit/integration tests pass | ✅ 67/67 pass |

## 8. Cách vận hành — lệnh thường dùng

```bash
# Chạy toàn bộ test (local, không đụng Airflow)
pytest tests/unit/ tests/integration/ -v

# Backfill OHLCV thủ công (90 ngày mặc định)
python -m data_pipeline.binance.ohlcv_pipeline

# Chạy thử build confluence series + backtest 1 coin (script tạm)
python -m scripts.test_backtest_data

# Cold-start toàn bộ hạ tầng (Postgres, MLflow, Airflow)
docker compose down -v && docker compose up --build
# (dùng "down" KHÔNG có "-v" nếu chỉ muốn rebuild image, giữ data)

# Trigger DAG thủ công qua CLI (không cần vào UI)
docker compose exec airflow airflow dags trigger dag_calculate_indicators

# Xem log của 1 task cụ thể
find logs/airflow -path "*<dag_id>*<task_id>*" -name "*.log" | sort | tail -1 | xargs cat
```

**Truy cập services:**
- Airflow UI: `http://localhost:8081` (lưu ý PORT 8081, map vào 8080 trong container)
- MLflow UI: `http://localhost:5000`
- Postgres: `localhost:5432`, user/pass theo `.env` (`POSTGRES_USER`/`POSTGRES_PASSWORD`)

## 9. Hướng tiếp theo (Tuần 3 — theo roadmap)

Tuần 3 bắt đầu nhánh **NLP** (PhoBERT fine-tuning cho sentiment tiếng
Việt) — độc lập với pipeline TA đã xong ở Tuần 2. Điểm nối giữa 2
nhánh sẽ xảy ra ở Tuần 5 (`Signal Aggregator`, `agent/signal_aggregator.py`),
khi confluence score (TA) và sentiment score (NLP) được cộng gộp có
trọng số. Tới lúc đó, `analyze_confluence()` và cấu trúc
`technical_indicators` ở tài liệu này vẫn là nguồn sự thật, không đổi.
