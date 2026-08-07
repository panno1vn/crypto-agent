"""
nlp/schemas.py

Schema chung cho kết quả sentiment — mọi analyzer (PhoBERT, FinBERT,
XLM-RoBERTa) đều trả về đúng format này để downstream (Signal
Aggregator, engagement weighting Ngày 19) không cần biết model nào
đứng sau.
"""

from typing import Literal

from pydantic import BaseModel, Field


class SentimentResult(BaseModel):
    label: Literal["positive", "negative", "neutral"]
    score: float = Field(
        ge=-1.0,
        le=1.0,
        description="Điểm có dấu: +confidence nếu positive, -confidence nếu "
        "negative, 0.0 nếu neutral. Dùng trực tiếp cho "
        "engagement_weighted_score() ở Ngày 19.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Độ tự tin gốc của model (0-1)"
    )
    language: str
    model_used: str
