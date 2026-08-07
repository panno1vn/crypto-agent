# data_pipeline/models.py
"""
SQLAlchemy ORM models.

⚠️ BẮT BUỘC dùng cú pháp LEGACY (Column(), declarative_base()).
KHÔNG được đổi sang Mapped[]/mapped_column()/DeclarativeBase — đó là
API chỉ có ở SQLAlchemy 2.0+. Airflow 2.9.2 pin SQLAlchemy 1.4.x ở
core, nên cú pháp 2.0-only sẽ làm MỌI DAG import module này crash
ngay lúc import (đã xảy ra thật — xem git history commit fix ngày
19, lỗi: "cannot import name 'DeclarativeBase' from 'sqlalchemy.orm'").

.venv local dùng SQLAlchemy 2.0.x nên legacy syntax vẫn chạy đúng ở
CẢ HAI môi trường — đây là lý do duy nhất chọn legacy, không phải sở
thích cá nhân. Trước khi sửa file này lần sau, LUÔN verify bằng:

    docker compose exec airflow python -c "from data_pipeline import models; print('OK')"

Xem thêm: docs/WEEK2.md mục 3 (bảng phân biệt 2 môi trường Python).
"""

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import DECIMAL

Base = declarative_base()


# ==========================================
# 1. TELEGRAM LAYER
# ==========================================


class TelegramChannel(Base):
    __tablename__ = "telegram_channels"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    title = Column(String(200), nullable=True)
    language = Column(String(10), nullable=True)  # 'en' | 'vi'
    credibility = Column(DECIMAL(3, 2), default=1.0, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationship (1-N) với TelegramMessage
    messages = relationship("TelegramMessage", back_populates="channel")


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"

    id = Column(BigInteger, primary_key=True)
    channel_id = Column(BigInteger, ForeignKey("telegram_channels.id"), nullable=True)
    channel_name = Column(String(100), nullable=False)
    message_text = Column(Text, nullable=True)
    language = Column(String(10), nullable=True)
    has_media = Column(Boolean, default=False, nullable=False)

    # Metrics
    views = Column(Integer, default=0, nullable=False)
    forwards = Column(Integer, default=0, nullable=False)
    reply_count = Column(Integer, default=0, nullable=False)

    # Lưu danh sách coin dưới dạng mảng chuỗi (ví dụ: ['BTC', 'ETH'])
    coins_mentioned = Column(ARRAY(String(20)), nullable=True)

    created_at = Column(DateTime, nullable=False)
    ingested_at = Column(DateTime, default=func.now(), nullable=False)
    is_processed = Column(Boolean, default=False, nullable=False)

    # --- Ngày 19: Engagement-Weighted Sentiment ---
    # Khớp đúng migration d4a9f21b8e37_add_sentiment_fields_to_telegram_.py
    # Lưu ý: sentiment_score là RAW score đã normalize signed (-1.000 -> 1.000),
    # CHƯA nhân engagement weight. Weighted score tính runtime lúc aggregate.
    sentiment_label = Column(String(10), nullable=True)
    sentiment_score = Column(DECIMAL(4, 3), nullable=True)
    sentiment_model_version = Column(String(50), nullable=True)
    sentiment_analyzed_at = Column(DateTime, nullable=True)

    # Relationship quay ngược lại TelegramChannel
    channel = relationship("TelegramChannel", back_populates="messages")


# ==========================================
# 2. MARKET DATA LAYER (BINANCE)
# ==========================================


class OHLCV(Base):
    __tablename__ = "ohlcv"
    __table_args__ = (
        # Chống trùng lặp dữ liệu nến: Cùng 1 coin, 1 khung giờ, 1 thời điểm chỉ có 1 record
        UniqueConstraint(
            "coin", "timeframe", "open_time", name="uq_ohlcv_coin_timeframe_time"
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    open_time = Column(DateTime, nullable=False)

    open = Column(DECIMAL(20, 8), nullable=True)
    high = Column(DECIMAL(20, 8), nullable=True)
    low = Column(DECIMAL(20, 8), nullable=True)
    close = Column(DECIMAL(20, 8), nullable=True)
    volume = Column(DECIMAL(20, 8), nullable=True)


# ==========================================
# 3. ANALYSIS LAYER (TECHNICAL INDICATORS)
# ==========================================


class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"
    __table_args__ = (
        UniqueConstraint(
            "coin", "timeframe", "calculated_at", name="uq_tech_ind_coin_timeframe_time"
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    calculated_at = Column(DateTime, nullable=False)

    rsi_14 = Column(DECIMAL(10, 4), nullable=True)
    macd_line = Column(DECIMAL(20, 8), nullable=True)
    macd_signal = Column(DECIMAL(20, 8), nullable=True)
    macd_histogram = Column(DECIMAL(20, 8), nullable=True)
    bb_upper = Column(DECIMAL(20, 8), nullable=True)
    bb_middle = Column(DECIMAL(20, 8), nullable=True)
    bb_lower = Column(DECIMAL(20, 8), nullable=True)
    ema_20 = Column(DECIMAL(20, 8), nullable=True)
    ema_50 = Column(DECIMAL(20, 8), nullable=True)
    atr_14 = Column(DECIMAL(20, 8), nullable=True)

    # Array lưu các mức hỗ trợ / kháng cự (S/R)
    support_levels = Column(ARRAY(DECIMAL(20, 8)), nullable=True)
    resistance_levels = Column(ARRAY(DECIMAL(20, 8)), nullable=True)

    confluence_score = Column(DECIMAL(5, 4), nullable=True)
    trend_direction = Column(String(20), nullable=True)
