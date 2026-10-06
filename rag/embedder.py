"""
rag/embedder.py

Ngày 22 — Wrapper quanh SentenceTransformer.

Hai quyết định kỹ thuật đáng chú ý:

1. Model được load LƯỜI (lazy). File này sẽ bị `import` lúc Airflow parse
   DAG ở Ngày 23; nếu load model ngay lúc import thì mỗi lần scheduler
   quét thư mục dags/ sẽ nạp ~1.1GB vào RAM.

2. `normalize_embeddings=True`. Xem docstring của `encode()`.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from data_pipeline.logger import get_logger
from rag.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DEVICE,
    EMBEDDING_DIM,
    EMBEDDING_MAX_SEQ_LEN,
    EMBEDDING_MODEL,
)

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

logger = get_logger(__name__)


class Embedder:
    """
    Sinh embedding đa ngôn ngữ cho text tiếng Việt lẫn tiếng Anh.

    Model `paraphrase-multilingual-mpnet-base-v2`:
      - Kiến trúc XLM-RoBERTa base, output 768 chiều
      - Hỗ trợ 50+ ngôn ngữ, có 'vi' và 'en'
      - max_seq_length mặc định = 128 token
      - KHÔNG được fine-tune cho thuật ngữ crypto. Nó hiểu "giá tăng mạnh"
        tốt hơn nhiều so với "kèo x5", "rug pull", "gom hàng vùng đáy".
        Đây là giới hạn đã biết, sẽ đo bằng RAGAS ở Ngày 26.
    """

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        device: str = EMBEDDING_DEVICE,
        batch_size: int = EMBEDDING_BATCH_SIZE,
        max_seq_length: "int | None" = EMBEDDING_MAX_SEQ_LEN,
    ) -> None:
        """
        Args:
            model_name:     Tên model trên HuggingFace Hub.
            device:         'cpu' hoặc 'cuda'.
            batch_size:     Số câu encode mỗi lượt forward.
            max_seq_length: Ghi đè độ dài tối đa. None = giữ mặc định (128).
        """
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._max_seq_length = max_seq_length
        self._model = None
        self._lock = threading.Lock()

    @property
    def model(self):
        """Load model lần đầu được gọi, các lần sau dùng lại."""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    # Import trong hàm: giữ cho việc `import rag.embedder`
                    # không kéo theo torch lúc Airflow parse DAG.
                    from sentence_transformers import SentenceTransformer

                    logger.info(
                        f"[EMBEDDER] Loading {self._model_name} "
                        f"on {self._device}..."
                    )
                    model = SentenceTransformer(self._model_name, device=self._device)
                    if self._max_seq_length is not None:
                        model.max_seq_length = self._max_seq_length
                    self._model = model
                    logger.info(
                        f"[EMBEDDER] Loaded. "
                        f"max_seq_length={model.max_seq_length} "
                        f"dim={model.get_embedding_dimension()}"
                    )
        return self._model

    @property
    def max_seq_length(self) -> int:
        """Số token tối đa model thực sự nhìn thấy."""
        return int(self.model.max_seq_length)

    def encode(self, texts: list[str]) -> "np.ndarray":
        """
        Encode danh sách text thành ma trận embedding đã chuẩn hóa L2.

        Vì sao `normalize_embeddings=True` là bắt buộc, không phải tùy chọn:

        Chroma mặc định dùng khoảng cách l2. Nếu collection lỡ được tạo
        với space sai (rất dễ xảy ra — xem `rag/vector_store.py`), vector
        đã chuẩn hóa vẫn cho THỨ TỰ xếp hạng y hệt cosine, vì với
        |a| = |b| = 1 thì d_l2 = 2 * d_cosine. Đã kiểm chứng bằng thực
        nghiệm: top-10 của hai space trùng khớp hoàn toàn khi vector
        được normalize, và khác nhau rõ rệt khi không normalize.

        Nói cách khác: normalize biến một lỗi cấu hình im lặng thành
        vô hại. Chỉ giá trị `distance` thay đổi (gấp đôi), còn kết quả
        retrieve thì không.

        Args:
            texts: Danh sách chuỗi cần encode. Không được rỗng.

        Returns:
            np.ndarray shape (len(texts), 768), mỗi hàng có norm = 1.

        Raises:
            ValueError: Nếu texts rỗng hoặc số chiều output khác 768.
        """
        if not texts:
            raise ValueError("encode() nhận danh sách rỗng")

        embeddings = self.model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        # Fail loudly: đổi model mà quên sửa EMBEDDING_DIM sẽ khiến Chroma
        # từ chối insert ở tận cuối pipeline với thông báo khó hiểu.
        if embeddings.shape[1] != EMBEDDING_DIM:
            raise ValueError(
                f"Model trả về {embeddings.shape[1]} chiều, "
                f"config khai báo EMBEDDING_DIM={EMBEDDING_DIM}"
            )

        return embeddings


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_embedder: "Embedder | None" = None
_embedder_lock = threading.Lock()


def get_embedder() -> Embedder:
    """
    Trả về Embedder dùng chung cho cả process.

    Tránh việc mỗi task/hàm tự tạo một instance rồi load lại 1.1GB model.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = Embedder()
    return _embedder
