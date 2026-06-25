from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TelegramMessageSchema(BaseModel):
    # Basic data fields
    id: int
    channel_name: str
    language: Optional[str] = None
    created_at: datetime

    # 1. Using Field() to  set up static rules
    views: int = Field(
        default=0, ge=0, description="The number of views must not be negative."
    )
    forwards: int = Field(
        default=0, ge=0, description="SThe number of shares must not be negative."
    )

    message_text: Optional[str] = None
    coins_mentioned: Optional[List[str]] = None

    # 2. Using @field_validator to set up dynamic rules (Logic)
    @field_validator("message_text")
    @classmethod
    def text_not_too_short(cls, value: Optional[str]) -> Optional[str]:
        # If text exists but the length after trimming whitespace is less than 10 characters -> Report an error.
        if value is not None and len(value.strip()) < 10:
            raise ValueError("Tin nhắn quá ngắn, không mang giá trị phân tích!")
        return value
