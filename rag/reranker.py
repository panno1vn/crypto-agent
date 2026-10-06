"""
rag/reranker.py

Ngày 24 — Cross-Encoder reranker.

QUYẾT ĐỊNH MODEL: xem rag/config.py và GHI_CHU_NGAY24.md. Tóm tắt:
msmarco-en (thuần Anh) thắng mmarco (đa ngôn ngữ) trên 2/3 tiêu chí đo
được (paraphrase robustness, latency); mmarco chỉ thắng ở margin thô,
vốn đo trên cỡ mẫu negative-control quá nhỏ (n=1) để tin tuyệt đối.

RỦI RO CHƯA GIẢI QUYẾT — đọc trước khi coi số của model này là chân lý:
  - Chưa có negative control sạch với cỡ mẫu đủ lớn.
  - Chưa có ground truth cho tiếng lóng crypto, chỉ đọc bằng mắt.
  - Điểm số bão hòa gần 1.0 cho match tốt → KHÔNG dùng ngưỡng tuyệt đối
    cố định. Dùng margin tương đối, hoặc để tầng gọi tự quyết theo
    ngữ cảnh (Signal Aggregator Ngày 31).
  → Việc treo Ngày 25: bộ negative control sạch + test tiếng lóng có nhãn.
"""

from __future__ import annotations

import threading
from typing import Any

from data_pipeline.logger import get_logger
from rag.config import RERANKER_DEVICE, RERANKER_MAX_LENGTH, RERANKER_MODEL

logger = get_logger(__name__)


class Reranker:
    """Wrapper quanh CrossEncoder, cùng pattern lazy-load với rag/embedder.py."""

    def __init__(
        self,
        model_name: str = RERANKER_MODEL,
        device: str = RERANKER_DEVICE,
        max_length: int = RERANKER_MAX_LENGTH,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._max_length = max_length
        self._model = None
        self._lock = threading.Lock()

    @property
    def model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    import torch
                    from sentence_transformers import CrossEncoder

                    logger.info(
                        f"[RERANKER] Loading {self._model_name} on {self._device}..."
                    )
                    # activation_fn=Sigmoid: model MS MARCO trả logit thô,
                    # không phải xác suất 0-1. Không đổi thứ hạng, chỉ đưa
                    # về thang chung để đọc/so sánh được.
                    self._model = CrossEncoder(
                        self._model_name,
                        max_length=self._max_length,
                        activation_fn=torch.nn.Sigmoid(),
                        device=self._device,
                    )
                    logger.info("[RERANKER] Loaded.")
        return self._model

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int,
        document_key: str = "document",
    ) -> list[dict[str, Any]]:
        """
        Chấm điểm và sắp xếp lại candidates theo mức liên quan với query.

        Args:
            query: Câu hỏi/truy vấn gốc.
            candidates: List dict, mỗi dict phải có key `document_key`.
                        Trả về nguyên vẹn kèm key 'rerank_score' mới.
            top_k: Số kết quả trả về sau khi rerank.
            document_key: Tên key chứa text để chấm điểm.

        Returns:
            List candidates đã sort giảm dần theo 'rerank_score', cắt còn
            top_k. Rỗng nếu candidates rỗng (không raise).
        """
        if not candidates:
            return []

        pairs = [(query, c[document_key]) for c in candidates]
        scores = self.model.predict(pairs, show_progress_bar=False)

        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)

        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_k]


_reranker: "Reranker | None" = None
_reranker_lock = threading.Lock()


def get_reranker() -> Reranker:
    """Instance dùng chung cho cả process — tránh load lại model mỗi lần gọi."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                _reranker = Reranker()
    return _reranker
