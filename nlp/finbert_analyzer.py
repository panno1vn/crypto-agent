"""
nlp/finbert_analyzer.py

FinBERT (ProsusAI/finbert) cho sentiment tin tức tài chính tiếng Anh.
Model pretrained sẵn, KHÔNG fine-tune riêng cho crypto — dùng thẳng
theo đúng roadmap Ngày 18.
"""

from __future__ import annotations

from transformers import pipeline

from data_pipeline.logger import get_logger
from nlp.schemas import SentimentResult

logger = get_logger(__name__)


class FinBERTAnalyzer:
    def __init__(self, device: int = -1):
        """device=-1 nghĩa là CPU, khớp đúng snippet roadmap Ngày 18."""
        self.pipe = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=device,
        )
        logger.info("[FINBERT] Đã load ProsusAI/finbert.")

    def analyze(self, text: str) -> SentimentResult:
        return self.analyze_batch([text])[0]

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        raw_results = self.pipe(texts, truncation=True, max_length=512)

        results = []
        for raw in raw_results:
            # FinBERT trả label: "positive" | "negative" | "neutral" (đã lowercase sẵn)
            label = raw["label"].lower()
            confidence = float(raw["score"])

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
                    language="en",
                    model_used="finbert",
                )
            )
        return results


if __name__ == "__main__":
    analyzer = FinBERTAnalyzer()
    samples = [
        "Bitcoin surges after ETF approval news.",
        "Exchange hacked, losing $50 million in user funds.",
        "The weather today is nice for a walk.",
    ]
    for s in samples:
        result = analyzer.analyze(s)
        print(f"[{result.label:8s} score={result.score:+.3f}] {s}")
