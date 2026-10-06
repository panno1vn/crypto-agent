# CLAUDE.md — crypto-agent

Hệ thống crypto market intelligence: Telegram (13 kênh vi/en) + Binance OHLCV → sentiment (PhoBERT/FinBERT) + Technical Analysis + RAG (ChromaDB) → Signal Aggregator → Risk Manager → LLM agent (Ollama, chưa làm) → Binance Testnet (chưa làm).

Chủ dự án: **Pan**, sinh viên IT, xây dự án này để luyện kỹ năng AI Engineer và làm portfolio. Dự án đi theo roadmap 70 ngày (`crypto_agent_roadmap_v2.md` ở gốc repo).

**Trước khi làm bất cứ việc gì: đọc `docs/context/00_README.md`**, rồi đọc file liên quan đến task. Bộ `docs/context/` là bản tổng hợp toàn bộ context từ các phiên làm việc trước. Nhật ký từng ngày nằm ở `docs/GHI_CHU_NGAY*.md`.

---

## 6 luật cứng (vi phạm = làm lại)

1. **Audit trước khi viết.** Đọc source thật trước khi sửa hoặc đề xuất code. Không bao giờ đoán tên field, số tham số, sync hay async. Lịch sử dự án có nhiều vòng sửa lãng phí chỉ vì đoán API (xem `docs/context/05_failure_patterns.md`).
2. **Fail loudly.** Thiếu giá trị thì phải raise, không được thay bằng default im lặng. Đây là lớp bug số 1 của repo này: env var, watermark, timezone, fingerprint collection, DAG stub đều từng dính.
   - Env bắt buộc: dùng `${VAR:?msg}` trong compose/shell, raise trong Python. Không dùng `:-` cho config bắt buộc.
   - Validate lúc khởi động, không đợi đến lúc lỗi runtime.
   - Bug mới debug xong thì phải kết thúc bằng một check thực thi được (test hoặc preflight), không chỉ thêm một dòng bài học vào markdown.
3. **Không tuyên bố khi không có bằng chứng.** Không đánh dấu "xong", "pass", "fixed" nếu không có output `pytest` nguyên văn, kết quả query DB, hoặc dòng log. Số liệu ghi vào README hay docs phải trỏ được về một MLflow run hoặc một file `GHI_CHU_NGAY*.md`.
4. **Không commit secret.** `*.session`, `*.session-journal`, `.env` không bao giờ vào git. Không in giá trị credential, chỉ kiểm tra độ dài (`${#VAR}`).
5. **Đo trước khi quyết.** Ngưỡng, trọng số, model đều phải chọn bằng số đo trên dữ liệu thật. Code mẫu trong roadmap chỉ là GỢI Ý, không phải SPEC (ngày 24 tìm ra 6 bug trong chính code mẫu của roadmap).
6. **Nói đúng mức độ chắc chắn.** "Có dấu hiệu, chưa xác nhận" khác "gần như chắc chắn". Kết luận sai mức tự tin giữa buổi debug từng làm mất nhiều thời gian.

## Quy ước kỹ thuật (tóm tắt, chi tiết ở `docs/context/04_conventions.md`)

- Python 3.11, `.venv`. Chạy test bằng `pytest`, không bao giờ `python test_file.py`. Mọi thư mục test cần `__init__.py`.
- Pre-commit: black, isort, flake8 phải sạch.
- Test dùng instance thư viện thật, không mock, trừ khi phụ thuộc cần network hoặc credential. Chroma unit test dùng `EphemeralClient` + `SharedSystemClient.clear_system_cache()` trong fixture.
- SQLAlchemy **chỉ cú pháp legacy** (`Column()`, `declarative_base()`). Airflow 2.8.0 pin SQLAlchemy 1.4.x. Ràng buộc vĩnh viễn cho tới khi nâng Airflow.
- Thời gian: naive datetime = UTC ở mọi cột `TIMESTAMP WITHOUT TIME ZONE`. Dùng `time.time()` cho `created_ts`. Không dùng `datetime.utcnow().timestamp()` (lệch +07 theo timezone process).
- `load_dotenv()` ở MODULE LEVEL trong file config, kèm `# noqa: E402` nếu import phải đứng sau.
- Logging: chỉ dùng `get_logger(__name__)` từ `data_pipeline/logger.py`. Không `basicConfig`, không nuốt exception.
- `requirements.txt` (local) và `requirements-airflow.txt` (container) là 2 file riêng. Thêm thư viện phải cập nhật cả 2. Cài xong luôn chạy `pip check`.
- Commit tách theo phạm vi nội dung.

## Bẫy im lặng đã biết (danh sách đầy đủ ở `docs/context/05_failure_patterns.md`)

- Chroma: `get_or_create_collection()` âm thầm bỏ tham số khi collection đã tồn tại. `add()` âm thầm bỏ qua ID trùng, nên dùng `upsert()`; nhưng `upsert()` MERGE metadata, không replace. `$gte` trên chuỗi ISO fail im lặng, phải lưu epoch số. Metadata `None` gây TypeError; mảng rỗng bị từ chối. `where` nhiều điều kiện phải bọc `$and`.
- Docker: `restart` không đọc lại `docker-compose.yml` và không làm sạch filesystem container. Đổi env hoặc volume thì dùng `up -d`. Đổi Dockerfile hoặc requirements thì dùng `build`.
- Telethon: không thấy file session thì tự tạo session RỖNG rồi treo chờ OTP.
- Airflow: DAG "success" không có nghĩa là làm đúng việc. Phải đối chiếu số liệu trong DB.
- Retrieval: `rerank_score` KHÔNG BAO GIỜ được dùng làm cổng lọc độc lập. Luôn kèm `require_coin_mentioned=True`.
- Watermark và cursor: `msg_id` của Telegram đánh số riêng theo từng kênh. Watermark phải tính theo kênh.

## Môi trường

- **Local (WSL2 Ubuntu trên Windows, project ở `~/crypto-agent`)**: có Docker Compose stack đầy đủ (Postgres, Airflow, ChromaDB, MLflow), có `.env` và Telegram session.
- **Cloud session**: KHÔNG có stack, KHÔNG có `.env`, KHÔNG có Telegram session, KHÔNG có khóa Binance, KHÔNG có GPU. Không cố khởi động docker-compose hay gọi API ngoài. Task cần những thứ đó thì dừng lại và ghi rõ trong PR: cần verify gì ở local, bằng lệnh nào.

## Cách làm việc với Pan (chi tiết ở `docs/context/10_working_with_pan.md`)

- Trả lời bằng **tiếng Việt**, thẳng thắn, nghiêm khắc. Sai thì nhận sai rõ ràng, không vòng vo.
- Pan muốn **hiểu**, không chỉ nhận code. Giải thích lý do của từng quyết định kỹ thuật.
- Thiếu file nào để audit thì hỏi Pan, đừng đoán.
- Mỗi phiên làm việc kết thúc bằng `docs/GHI_CHU_NGAYxx.md`: đã làm gì, quyết định gì, đi sai hướng ở đâu.

## Trạng thái khi bàn giao (2026-10-06)

Mốc cuối có tài liệu: **Ngày 28/70 (Review Tuần 4), 2026-08-19**. Sau đó Pan tạm dừng để học lại nền tảng (Docker, Postgres, SQLAlchemy, Airflow, RAG). Từ cuối tháng 8 đến nay không có ghi chép tiến độ. **Việc đầu tiên của phiên Claude Code đầu tiên là xác minh trạng thái thật**, theo checklist ở `docs/context/11_open_questions_to_verify.md`.
