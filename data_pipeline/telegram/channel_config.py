# data_pipeline/telegram/channel_config.py
"""
data_pipeline/telegram/channel_config.py

Load danh sách channel từ config/channels.yaml thay vì hardcode rải rác
trong historical_scraper.py và realtime_listener.py.

Chuẩn hóa: luôn bỏ dấu '@' ở đầu username. Telethon chấp nhận cả 2 dạng
('whale_alert' và '@whale_alert') nhưng CheckpointManager dùng username
làm tên file — nếu không chuẩn hóa, đổi qua lại có/không '@' sẽ tạo ra
2 file checkpoint khác nhau cho cùng 1 channel và làm mất tiến độ resume.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from data_pipeline.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = Path("config/channels.yaml")

# Kênh vẫn còn dữ liệu cũ trong telegram_messages nhưng KHÔNG được catch-up
# nữa. DAG telegram_realtime_sync lấy danh sách kênh từ
# `SELECT DISTINCT channel_name`, nên không xóa kênh khỏi DB được (mất lịch
# sử) — loại trừ ở đây. Đặt trong data_pipeline/ (đã mount vào container
# Airflow), không đặt trong config/channels.yaml (config/ KHÔNG được mount).
# Mỗi kênh phải kèm lý do + ngày để còn xét lại.
EXCLUDED_CHANNELS: dict[str, str] = {
    "RIC_Capital_Channel": (
        "2026-10-08: UsernameInvalidError ('Nobody is using this username'), "
        "làm DAG đỏ mỗi 15 phút"
    ),
}


def filter_excluded(
    channels: list[str], excluded: Optional[dict[str, str]] = None
) -> list[str]:
    """
    Bỏ các kênh trong EXCLUDED_CHANNELS khỏi danh sách, giữ nguyên thứ tự.

    Log từng kênh bị bỏ (kèm lý do) để việc loại trừ không bao giờ im lặng.
    Kênh nằm trong danh sách loại trừ nhưng không có trong `channels` cũng
    được log WARNING: có thể tên sai chính tả, loại trừ không có tác dụng.
    """
    excluded = EXCLUDED_CHANNELS if excluded is None else excluded
    kept = [ch for ch in channels if ch not in excluded]
    for ch, reason in excluded.items():
        if ch in channels:
            logger.info(f"[CHANNEL_CONFIG] Bỏ qua kênh {ch}: {reason}")
        else:
            logger.warning(
                f"[CHANNEL_CONFIG] Kênh loại trừ {ch} không có trong danh sách "
                f"— sai tên? Loại trừ này không có tác dụng."
            )
    return kept


def _normalize(username: str) -> str:
    """Bỏ dấu '@' ở đầu nếu có, strip khoảng trắng thừa."""
    return username.strip().lstrip("@")


def load_channels(config_path: Optional[Path] = None) -> dict[str, list[str]]:
    """
    Đọc config/channels.yaml, trả về dict {'english': [...], 'vietnamese': [...]}
    với mọi username đã chuẩn hóa bỏ '@'.

    Raises:
        FileNotFoundError: nếu file config không tồn tại — fail loud thay vì
            âm thầm trả list rỗng, vì thiếu channel sẽ làm backfill/listener
            chạy im lặng mà không có dữ liệu, rất khó phát hiện.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy {path}. Tạo file config/channels.yaml trước khi chạy."
        )

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    raw = data.get("channels", {})
    normalized = {
        group: [_normalize(ch) for ch in channels] for group, channels in raw.items()
    }

    for group, channels in normalized.items():
        logger.info(f"[CHANNEL_CONFIG] {group}: {len(channels)} channels")

    return normalized


def all_channels(config_path: Optional[Path] = None) -> list[str]:
    """Trả về danh sách phẳng toàn bộ channel (english + vietnamese + mọi group khác)."""
    grouped = load_channels(config_path)
    flat: list[str] = []
    for channels in grouped.values():
        flat.extend(channels)
    return flat


def vietnamese_channels(config_path: Optional[Path] = None) -> list[str]:
    """Chỉ lấy nhóm 'vietnamese' — tiện cho các tác vụ chỉ cần kênh tiếng Việt."""
    return load_channels(config_path).get("vietnamese", [])


def english_channels(config_path: Optional[Path] = None) -> list[str]:
    """Chỉ lấy nhóm 'english'."""
    return load_channels(config_path).get("english", [])
