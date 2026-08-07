"""
nlp/xlmr_analyzer.py

XLM-RoBERTa đa ngôn ngữ (cardiffnlp/twitter-xlm-roberta-base-sentiment)
— model TỔNG QUÁT, train trên tweet đa ngôn ngữ, KHÔNG chuyên biệt cho
crypto hay tiếng Việt. Dùng cho 2 việc:
  1. Fallback trong MultilingualSentimentAnalyzer khi detect_language()
     trả về ngôn ngữ khác vi/en (thực tế hiếm xảy ra — xem ghi chú
     trong multilingual_analyzer.py)
  2. So sánh với PhoBERT (Ngày 18 buổi chiều) — trả lời câu hỏi
     "PhoBERT có vượt trội hơn model tổng quát không, ở đâu?"
"""

from __future__ import annotations

from transformers import pipeline

from data_pipeline.logger import get_logger
from nlp.schemas import SentimentResult

logger = get_logger(__name__)

MODEL_NAME = "cardiffnlp/twitter-xlm-roberta-base-sentiment"


class XLMRAnalyzer:
    def __init__(self, device: int = -1):
        self.pipe = pipeline(
            "sentiment-analysis",
            model=MODEL_NAME,
            tokenizer=MODEL_NAME,
            device=device,
        )
        logger.info(f"[XLMR] Đã load {MODEL_NAME}.")

    def analyze(self, text: str) -> SentimentResult:
        return self.analyze_batch([text])[0]

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        raw_results = self.pipe(texts, truncation=True, max_length=512)

        results = []
        for raw in raw_results:
            # Label thật của model này CHƯA verify trực tiếp — code này tự
            # nhận diện nhiều format phổ biến (Positive/Neutral/Negative,
            # LABEL_0/1/2, positive/neutral/negative) thay vì giả định 1
            # format cố định. Nếu gặp format lạ, raise rõ ràng thay vì
            # âm thầm map sai — chạy __main__ bên dưới để verify trước khi
            # dùng cho việc quan trọng.
            raw_label = raw["label"].lower()
            if "pos" in raw_label or raw_label == "label_2":
                label = "positive"
            elif "neg" in raw_label or raw_label == "label_0":
                label = "negative"
            elif "neu" in raw_label or raw_label == "label_1":
                label = "neutral"
            else:
                raise ValueError(
                    f"[XLMR] Label lạ không nhận diện được: '{raw['label']}'. "
                    f"Kiểm tra lại model card {MODEL_NAME} trên HuggingFace, "
                    f"cập nhật logic map ở trên."
                )

            confidence = float(raw["score"])
            score = (
                confidence
                if label == "positive"
                else (-confidence if label == "negative" else 0.0)
            )

            results.append(
                SentimentResult(
                    label=label,
                    score=score,
                    confidence=confidence,
                    language="multi",
                    model_used="xlm-roberta-twitter",
                )
            )
        return results


if __name__ == "__main__":
    # CHẠY FILE NÀY TRƯỚC khi dùng trong script khác — verify label mapping đúng
    analyzer = XLMRAnalyzer()
    samples = [
        ("BTC tăng giá mạnh sau tin ETF được duyệt.", "vi, kỳ vọng positive"),
        ("Sàn giao dịch bị hack, mất trắng tiền.", "vi, kỳ vọng negative"),
        ("Bitcoin surges after ETF approval.", "en, kỳ vọng positive"),
    ]
    for text, expect in samples:
        result = analyzer.analyze(text)
        print(f"[{result.label:8s} score={result.score:+.3f}] ({expect}) {text}")
