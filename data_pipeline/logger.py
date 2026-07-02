# Nên tạo file riêng: data_pipeline/logger.py
import logging
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    if not logger.handlers:  # chống duplicate
        handler_file = logging.FileHandler("logs/pipeline.log")
        handler_stream = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        handler_file.setFormatter(formatter)
        handler_stream.setFormatter(formatter)
        logger.addHandler(handler_file)
        logger.addHandler(handler_stream)
        logger.setLevel(logging.INFO)
    return logger
