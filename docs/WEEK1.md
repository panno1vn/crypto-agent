# Tuần 1 — Data Pipeline (Ngày 1-7)

## Mục tiêu tuần 1
Telegram scraper + OHLCV pipeline chạy tự động, data validated vào PostgreSQL, Airflow orchestration hoạt động ổn định.

---

## Những gì đã hoàn thành

### Ngày 1-3: Setup môi trường
- WSL2 + Ubuntu, Docker Desktop, Python 3.11, VSCode
- Project structure chuẩn (`data_pipeline/`, `nlp/`, `technical_analysis/`, `agent/`, `execution/`, `api/`, `tests/`)
- PostgreSQL schema đầy đủ 4 bảng: `telegram_channels`, `telegram_messages`, `ohlcv`, `technical_indicators`
- Telethon setup, đăng ký Telegram App, list channel theo dõi

### Ngày 4: Historical Scraper
File: `data_pipeline/telegram/historical_scraper.py`
- `TelegramMessage` — Pydantic v2 schema, validate `message_text` tối thiểu 10 ký tự
- `detect_language()` — route message vào PhoBERT (vi) / FinBERT (en) downstream
- `scrape_channel_history()` — async generator, humanized throttling (ngủ ngẫu nhiên 35-65 msg), xử lý `FloodWaitError` với kill-switch
- `CheckpointManager` — resume scrape nếu bị interrupt, lưu `last_message_id` theo channel
- `DatabaseWriter` — async bulk upsert qua `asyncpg`, `ON CONFLICT DO NOTHING`
- `run_historical_backfill()` — orchestrator chạy tuần tự nhiều channel, tránh rate limit

### Ngày 5: Realtime Listener + Logging
File: `data_pipeline/telegram/realtime_listener.py`, `data_pipeline/logger.py`
- `get_logger()` — centralized logging, guard chống duplicate handlers, `propagate=False`
- Event handler `events.NewMessage` — parse, validate, save, log mỗi message mới
- `periodic_flush()` — task song song, flush buffer mỗi 5s dù chưa đủ `batch_size`
- 7 unit tests pass (`tests/unit/test_telegram_parser.py`): extract_coins, schema validation, detect_language

### Ngày 6: Binance OHLCV Pipeline
- `fetch_ohlcv()` — REST API `get_klines`, 4 timeframe (15m/1h/4h/1d) × 5 coin
- Validate: không giá âm, chấp nhận `volume=0` (hợp lệ cho pair thanh khoản thấp), check gap
- Backfill 90 ngày cho toàn bộ coin/timeframe

### Ngày 7: Airflow Orchestration
- `docker-compose.yml`: Postgres + Airflow, custom `Dockerfile.airflow`
- 2 DAG: `binance_ohlcv_hourly_sync` (mỗi giờ), `telegram_realtime_sync` (mỗi 15 phút)
- Cả 2 DAG trigger thành công, state `success`

---

## Bug đã gặp và cách fix (quan trọng — tránh lặp lại)

### 1. Timezone: `.replace(tzinfo=None)` là bug
PostgreSQL `TIMESTAMPTZ` cần datetime aware timezone. Đúng: `.replace(tzinfo=timezone.utc)` lúc construct record, không strip UTC.

### 2. Duplicate log handlers
Gọi `basicConfig` ở nhiều module → duplicate handler. Fix: `get_logger(__name__)` với guard `if not logger.handlers` + `propagate=False`.

### 3. Airflow: SequentialExecutor mặc định
Backend đổi sang Postgres KHÔNG tự đổi executor. Phải set tường minh:
```yaml
AIRFLOW__CORE__EXECUTOR: LocalExecutor
```

### 4. Airflow: `airflow_db` không tự tồn tại
`POSTGRES_DB` chỉ tạo 1 database. Airflow cần database metadata riêng, tách biệt schema business. Giải pháp: `scripts/init-multi-db.sh` — script chạy tự động bởi Postgres image lúc khởi tạo volume lần đầu, đọc biến `POSTGRES_MULTIPLE_DATABASES` để tạo nhiều DB.

**Gotcha trong chính script này:** `psql --username admin` không chỉ định `-d <database>` sẽ cố kết nối vào database *trùng tên user* (ở đây `admin`) — không tồn tại → lỗi `FATAL: database "admin" does not exist`. Fix: luôn chỉ định `--dbname postgres` khi chạy lệnh `CREATE DATABASE` khởi tạo.

### 5. Container Airflow thiếu dependencies của project
Image gốc `apache/airflow:2.8.0` không có `asyncpg`, `Telethon`, `pydantic`... Giải pháp: `Dockerfile.airflow` riêng, cài `requirements-airflow.txt`.

**Quan trọng — tách riêng 2 file requirements:**
- `requirements.txt` (gốc) — dùng cho `.venv` local, có cả dev tools (black, flake8, isort, pre-commit, mypy)
- `requirements-airflow.txt` (mới) — CHỈ chứa package DAG thực sự import lúc runtime

Lý do tách: container Airflow chạy Python 3.8, còn dev tools trong `requirements.txt` thường pin version mới nhất đòi hỏi Python ≥3.9/3.10 → gây `ResolutionImpossible` liên tục kiểu whack-a-mole (sửa 1 package lòi ra package khác).

**Gotcha thứ hai, tinh vi hơn:** không được pin cứng version của package mà **Airflow core tự phụ thuộc** (SQLAlchemy, Flask...). Đã gặp: pin `SQLAlchemy==2.0.25` đè lên bản `1.4.x` mà Airflow 2.8.0 yêu cầu (`sqlalchemy>=1.4.28,<2.0`), phá vỡ cách Airflow cấu hình dialect Postgres (`executemany_mode` không tồn tại ở SQLAlchemy 2.0 API). Verify bằng:
```bash
curl -s "https://pypi.org/pypi/apache-airflow/2.8.0/json" | python3 -c "
import json,sys
d = json.load(sys.stdin)
for req in d['info']['requires_dist']:
    if 'sqlalchemy' in req.lower(): print(req)
"
```

### 6. Postgres chưa healthy, Airflow đã start
`depends_on: [postgres]` chỉ đảm bảo container start, không đảm bảo Postgres sẵn sàng nhận connection. Fix: `healthcheck` + `depends_on: condition: service_healthy`.

### 7. Permission denied khi mount logs ra host
Mount `./logs/airflow:/opt/airflow/logs` — Docker tự tạo thư mục host thuộc `root`, nhưng Airflow chạy bằng user không phải root → không ghi được. Fix tạm: `chmod -R 777 logs/airflow`. Fix chuẩn hơn (chưa áp dụng, để tham khảo sau): set `AIRFLOW_UID` khớp UID host.

### 8. DAG stuck ở `queued` mãi không chạy
Không phải lỗi — DAG mặc định **paused** khi tạo lần đầu (`AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION` default `true`). Scheduler nhận trigger, tạo DagRun, nhưng không đẩy sang `running` vì DAG paused. Fix: `airflow dags unpause <dag_id>`, hoặc set `AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: 'false'` trong docker-compose cho môi trường dev.

### 9. Fernet key tự sinh random
Không set `AIRFLOW__CORE__FERNET_KEY` → standalone tự sinh mới mỗi lần container recreate → Connection/Variable đã lưu (vd: Binance API key ở tuần 7) không decrypt được sau restart. Fix: generate 1 lần, lưu cố định trong `.env`.

---

## Checklist verify reproducibility (đã pass)

```bash
docker-compose down -v          # xóa sạch, mô phỏng máy người khác clone repo
docker-compose up --build -d
sleep 30
docker-compose ps                                          # tất cả Up/healthy
docker exec crypto_airflow airflow config get-value core executor   # LocalExecutor
docker exec crypto_postgres psql -U admin -d postgres -l            # thấy cả airflow_db, crypto_agent
docker exec crypto_airflow airflow dags trigger <dag_id>
docker exec crypto_airflow airflow dags list-runs -d <dag_id>       # state: success
```

---

## Kết quả cuối tuần 1

- [x] Pipeline chạy tự động, không cần can thiệp tay sau cold-start
- [x] Schema PostgreSQL đầy đủ 4 bảng
- [x] Historical scraper + Realtime listener cho Telegram
- [x] OHLCV backfill 4 timeframe × 5 coin
- [x] Airflow: 2 DAG GREEN, `LocalExecutor`, reproducible từ `docker-compose down -v && up`
- [x] pytest: 7 unit tests pass
- [ ] > 5,000 messages trong `telegram_messages` — cần chạy backfill thật để verify (chưa confirm số liệu thực tế)

## Việc còn treo, mang sang tuần 2

- DAG `dag_calculate_indicators` (ngày 13) sẽ cần `pandas`, `pandas-ta` — đã comment sẵn trong `requirements-airflow.txt`, uncomment khi tới ngày 8
- Chưa setup `AIRFLOW_UID` matching host UID (đang dùng `chmod 777` tạm cho logs — đủ dùng cho dev, không phải giải pháp production)
- Chưa verify số lượng message thật > 5,000 theo milestone gốc — cần chạy `run_historical_backfill()` với channel thật (không phải chỉ test DAG skeleton)

---

## Bài học lớn nhất tuần này

Container hóa 1 project Python cho Airflow không đơn giản là "COPY code + pip install requirements.txt". Ba nguyên tắc rút ra:

1. **Tách biệt dependency theo môi trường chạy** — dev tools (black, flake8) không bao giờ nên vào image production/runtime.
2. **Không pin version đè lên package mà framework (Airflow) tự quản lý** — luôn verify qua `requires_dist` của chính framework trước khi pin.
3. **Test reproducibility bằng `down -v && up` thường xuyên**, không chỉ test "chạy được trên máy mình lúc này" — đây chính là câu hỏi phỏng vấn hay gặp: "bạn đảm bảo teammate mới clone repo chạy được không?"