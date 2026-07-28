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

from pathlib import Path
from typing import Optional

import yaml

from data_pipeline.logger import get_logger

logger = get_logger(__name__)

DEFAULT_CONFIG_PATH = Path("config/channels.yaml")


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
