# data_pipeline/models.py
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (ARRAY, BigInteger, Boolean, DateTime, ForeignKey,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import DECIMAL


class Base(DeclarativeBase):
    """Lớp nền tảng để tất cả các model kế thừa"""

    pass


# ==========================================
# 1. TELEGRAM LAYER
# ==========================================


class TelegramChannel(Base):
    __tablename__ = "telegram_channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(200))
    language: Mapped[Optional[str]] = mapped_column(String(10))  # 'en' | 'vi'
    credibility: Mapped[Optional[float]] = mapped_column(DECIMAL(3, 2), default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    # Relationship (1-N) với TelegramMessage
    messages: Mapped[List["TelegramMessage"]] = relationship(back_populates="channel")


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("telegram_channels.id")
    )
    channel_name: Mapped[str] = mapped_column(String(100), nullable=False)
    message_text: Mapped[Optional[str]] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(10))
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)

    # Metrics
    views: Mapped[int] = mapped_column(default=0)
    forwards: Mapped[int] = mapped_column(default=0)
    reply_count: Mapped[int] = mapped_column(default=0)

    # Lưu danh sách coin dưới dạng mảng chuỗi (ví dụ: ['BTC', 'ETH'])
    coins_mentioned: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(20)))

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationship quay ngược lại TelegramChannel
    channel: Mapped[Optional["TelegramChannel"]] = relationship(
        back_populates="messages"
    )


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

    # BIGSERIAL tự động tăng
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    coin: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    open: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    high: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    low: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    close: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    volume: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))


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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    coin: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    rsi_14: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 4))
    macd_line: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    macd_signal: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    macd_histogram: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    bb_upper: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    bb_middle: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    bb_lower: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    ema_20: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    ema_50: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))
    atr_14: Mapped[Optional[float]] = mapped_column(DECIMAL(20, 8))

    # Array lưu các mức hỗ trợ / kháng cự (S/R)
    support_levels: Mapped[Optional[list[float]]] = mapped_column(ARRAY(DECIMAL(20, 8)))
    resistance_levels: Mapped[Optional[list[float]]] = mapped_column(
        ARRAY(DECIMAL(20, 8))
    )

    confluence_score: Mapped[Optional[float]] = mapped_column(DECIMAL(5, 4))
    trend_direction: Mapped[Optional[str]] = mapped_column(String(20))
