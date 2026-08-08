"""
scripts/manual/run_day20_signal_validation.py

Ngày 20 — Chạy validation cho BTCUSDT (combined/vi/en), log MLflow,
vẽ rolling correlation 30 ngày.

⚠️ Phần tạo session (get_session) viết TẠM theo pattern SQLAlchemy
async chuẩn — không có file db.py thật trong context lúc viết. Nếu
project đã có sẵn factory riêng (dùng ở dag_sentiment_pipeline.py hay
tương tự), THAY get_session() bên dưới bằng import từ đó, đừng để 2
nơi cấu hình connection lệch nhau (đúng lớp lỗi #4 đã gặp Ngày 19).

⚠️ EXPERIMENT_NAME = "signal_validation" là tên tự đặt, chưa biết
convention thật của backtest.py — đổi cho khớp nếu khác.

Chạy: python -m scripts.manual.run_day20_signal_validation
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from contextlib import (  # noqa: E402  -- phải chạy sau load_dotenv() để đọc đúng env var
    asynccontextmanager,
)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mlflow  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data_pipeline.logger import get_logger  # noqa: E402
from nlp.signal_validation import (  # noqa: E402
    rolling_correlation,
    validate_sentiment_signal,
)

logger = get_logger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL_ASYNC",
    f"postgresql+asyncpg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'localhost')}:5432/"
    f"{os.environ.get('POSTGRES_DB', 'crypto_agent')}",
)
_engine = create_async_engine(DATABASE_URL)
_SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session():
    async with _SessionLocal() as session:
        yield session


COIN = "BTCUSDT"
LOOKBACK_DAYS = 30
WINDOW_HOURS = 4
EXPERIMENT_NAME = "signal_validation"  # TODO Pan: verify convention thật


async def main() -> None:
    mlflow.set_experiment(EXPERIMENT_NAME)

    async with get_session() as session:
        reports = {}
        for label, lang in [("combined", None), ("vi", "vi"), ("en", "en")]:
            try:
                reports[label] = await validate_sentiment_signal(
                    session,
                    coin=COIN,
                    lookback_days=LOOKBACK_DAYS,
                    language=lang,
                    window_hours=WINDOW_HOURS,
                )
            except ValueError as e:
                logger.warning(f"[DAY20] Bỏ qua lang={label}: {e}")

        with mlflow.start_run(run_name=f"signal_validation_{COIN}"):
            mlflow.log_param("coin", COIN)
            mlflow.log_param("lookback_days", LOOKBACK_DAYS)
            mlflow.log_param("window_hours", WINDOW_HOURS)
            mlflow.log_param("price_return_method", "pct_change")  # xem note

            for label, report in reports.items():
                mlflow.log_metric(f"correlation_{label}", report.correlation)
                mlflow.log_metric(f"p_value_{label}", report.p_value)
                mlflow.log_metric(f"sample_size_{label}", report.sample_size)
                logger.info(
                    f"[DAY20] {label}: corr={report.correlation:.4f} "
                    f"p={report.p_value:.4f} n={report.sample_size} "
                    f"significant={report.is_significant}"
                )

            try:
                rc = await rolling_correlation(
                    session,
                    coin=COIN,
                    lookback_days=LOOKBACK_DAYS,
                    language=None,
                    window_hours=WINDOW_HOURS,
                    rolling_window_days=7,
                )
                fig, ax = plt.subplots(figsize=(10, 4))
                rc.plot(ax=ax)
                ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
                ax.set_title(f"{COIN} — Rolling 7-day sentiment/price correlation")
                ax.set_ylabel("Pearson r")
                fig.tight_layout()
                fig_path = "/tmp/rolling_correlation.png"
                fig.savefig(fig_path)
                mlflow.log_artifact(fig_path)
                plt.close(fig)
            except Exception:
                logger.error("[DAY20] Vẽ rolling correlation thất bại", exc_info=True)

    logger.info("[DAY20] Hoàn tất — xem MLflow UI.")


if __name__ == "__main__":
    asyncio.run(main())
