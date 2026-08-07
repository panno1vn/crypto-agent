"""
nlp/phobert_analyzer.py

Load PhoBERT đã fine-tune (Ngày 17-18, v2, f1_macro=0.639) từ đĩa local,
predict sentiment cho text tiếng Việt.

⚠️ QUAN TRỌNG: PHẢI word-segment bằng pyvi TRƯỚC khi tokenize — đúng
hệt bước tiền xử lý lúc train (xem CELL 3 trong notebook Kaggle). Bỏ
qua bước này sẽ làm model dự đoán sai lệch nghiêm trọng so với lúc
train/eval, dù không có lỗi nào hiện ra — bug âm thầm nguy hiểm nhất.
"""

from __future__ import annotations

from pathlib import Path

import torch
import torch.nn.functional as F
from pyvi import ViTokenizer
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from data_pipeline.logger import get_logger
from nlp.schemas import SentimentResult

logger = get_logger(__name__)

MODEL_PATH = (
    Path(__file__).resolve().parent.parent / "models" / "phobert-crypto" / "best_model"
)
MAX_LENGTH = 256  # PHẢI khớp đúng giá trị lúc train (notebook CELL 2)
ID2LABEL = {0: "negative", 1: "neutral", 2: "positive"}


class PhoBERTAnalyzer:
    def __init__(self, model_path: Path = MODEL_PATH, device: str = "cpu"):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy PhoBERT model tại {model_path}. "
                f"Đã tải model từ Kaggle về đúng chỗ chưa? Xem docs/WEEK3.md mục 9."
            )
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_path), use_fast=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
        self.model.to(device)
        self.model.eval()
        logger.info(f"[PHOBERT] Đã load model từ {model_path}, device={device}")

    def _segment(self, text: str) -> str:
        try:
            return ViTokenizer.tokenize(text)
        except Exception as e:
            logger.warning(f"[PHOBERT] Segment fail cho '{text[:50]}...': {e}")
            return text

    def analyze(self, text: str) -> SentimentResult:
        return self.analyze_batch([text])[0]

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Batch inference — dùng cho throughput cao hơn (vd script so sánh 100 message)."""
        segmented = [self._segment(t) for t in texts]

        inputs = self.tokenizer(
            segmented,
            truncation=True,
            max_length=MAX_LENGTH,
            padding=True,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            logits = self.model(**inputs).logits
            probs = F.softmax(logits, dim=-1)

        results = []
        for prob in probs:
            pred_idx = int(torch.argmax(prob).item())
            label = ID2LABEL[pred_idx]
            confidence = float(prob[pred_idx].item())

            score = 0.0
            if label == "positive":
                score = confidence
            elif label == "negative":
                score = -confidence

            results.append(
                SentimentResult(
                    label=label,
                    score=score,
                    confidence=confidence,
                    language="vi",
                    model_used="phobert-crypto-v2",
                )
            )
        return results


if __name__ == "__main__":
    analyzer = PhoBERTAnalyzer()
    samples = [
        "BTC vừa được niêm yết thêm trên sàn Binance, giá tăng mạnh.",
        "Sàn giao dịch bị hack, mất 50 triệu USD.",
        "Hôm nay thời tiết đẹp, thích hợp đi cafe.",
    ]
    for s in samples:
        result = analyzer.analyze(s)
        print(f"[{result.label:8s} score={result.score:+.3f}] {s}")
