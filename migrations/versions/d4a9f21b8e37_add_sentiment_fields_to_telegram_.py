"""add sentiment fields to telegram_messages

Revision ID: d4a9f21b8e37
Revises: c658bb605a81
Create Date: 2026-08-07 00:00:00.000000

Ngày 19 — Engagement-Weighted Sentiment
=========================================
Thêm 4 cột lưu kết quả sentiment CẤP MESSAGE vào telegram_messages
(quyết định: KHÔNG tách bảng riêng — quan hệ 1-1 với message).

- sentiment_label:          'negative' | 'neutral' | 'positive' | NULL
- sentiment_score:          RAW score đã normalize signed (-1.000 -> 1.000).
                             CHƯA nhân engagement weight — weighted score
                             tính runtime lúc aggregate, KHÔNG lưu cứng ở đây
                             (views/forwards tăng dần theo thời gian, lưu
                             cứng weighted score sẽ stale ngay lập tức).
- sentiment_model_version:  vd 'phobert-v2', 'finbert-en', 'xlmr-fallback'
                             (để trace + so sánh khi model được retrain).
- sentiment_analyzed_at:    thời điểm phân tích — dùng để audit lag giữa
                             ingested_at và lúc thực sự được xử lý.

Index is_processed: pipeline Ngày 19 sẽ query cột này liên tục
(WHERE is_processed = FALSE) mỗi lần DAG chạy — cột đã tồn tại từ
migration đầu nhưng chưa có index, thêm ở đây luôn.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a9f21b8e37"
down_revision: Union[str, Sequence[str], None] = "c658bb605a81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "telegram_messages",
        sa.Column("sentiment_label", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "telegram_messages",
        sa.Column("sentiment_score", sa.DECIMAL(precision=4, scale=3), nullable=True),
    )
    op.add_column(
        "telegram_messages",
        sa.Column("sentiment_model_version", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "telegram_messages",
        sa.Column("sentiment_analyzed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_telegram_messages_is_processed",
        "telegram_messages",
        ["is_processed"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_telegram_messages_is_processed", table_name="telegram_messages")
    op.drop_column("telegram_messages", "sentiment_analyzed_at")
    op.drop_column("telegram_messages", "sentiment_model_version")
    op.drop_column("telegram_messages", "sentiment_score")
    op.drop_column("telegram_messages", "sentiment_label")
