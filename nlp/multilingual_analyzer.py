"""
nlp/multilingual_analyzer.py

Route text tới đúng model theo ngôn ngữ:
  vi -> PhoBERT (fine-tuned riêng cho crypto tiếng Việt)
  en -> FinBERT (pretrained cho tài chính tiếng Anh)
  khác -> XLM-RoBERTa (fallback đa ngôn ngữ, tổng quát)

⚠️ GHI CHÚ THỰC TẾ: dùng lại detect_language() từ
data_pipeline/telegram/historical_scraper.py (đã có unit test từ Ngày
5) — hàm này CHỈ trả về 'vi'/'en'/None (None khi text <20 ký tự hoặc
ngôn ngữ khác không hỗ trợ). Nghĩa là nhánh "khác -> XLM-RoBERTa" bên
dưới trong THỰC TẾ hiếm khi được gọi tới qua pipeline hiện tại — hầu
hết message ngôn ngữ lạ đã bị lọc bỏ (return None) TRƯỚC khi tới đây,
theo đúng logic Ngày 4. Giữ nhánh else để an toàn/tương lai (vd nếu
sau này detect_language được mở rộng hỗ trợ thêm ngôn ngữ), không phải
sai sót.
"""

from typing import Optional

from data_pipeline.logger import get_logger
from data_pipeline.telegram.historical_scraper import detect_language
from nlp.finbert_analyzer import FinBERTAnalyzer
from nlp.phobert_analyzer import PhoBERTAnalyzer
from nlp.schemas import SentimentResult
from nlp.xlmr_analyzer import XLMRAnalyzer

logger = get_logger(__name__)


class MultilingualSentimentAnalyzer:
    def __init__(self, lazy_load: bool = True):
        """
        lazy_load=True: chỉ load model khi thực sự cần (tiết kiệm RAM/thời
        gian khởi động nếu chỉ dùng 1 ngôn ngữ). Mặc định True vì PhoBERT +
        FinBERT + XLM-R cùng load 1 lúc khá nặng cho máy dev thông thường.
        """
        self._phobert: Optional[PhoBERTAnalyzer] = None
        self._finbert: Optional[FinBERTAnalyzer] = None
        self._xlmr: Optional[XLMRAnalyzer] = None
        self.lazy_load = lazy_load

        if not lazy_load:
            self._load_all()

    def _load_all(self):
        self.phobert
        self.finbert
        self.xlmr

    @property
    def phobert(self) -> PhoBERTAnalyzer:
        if self._phobert is None:
            self._phobert = PhoBERTAnalyzer()
        return self._phobert

    @property
    def finbert(self) -> FinBERTAnalyzer:
        if self._finbert is None:
            self._finbert = FinBERTAnalyzer()
        return self._finbert

    @property
    def xlmr(self) -> XLMRAnalyzer:
        if self._xlmr is None:
            self._xlmr = XLMRAnalyzer()
        return self._xlmr

    def analyze(self, text: str) -> Optional[SentimentResult]:
        """
        Trả None nếu detect_language() không xác định được ngôn ngữ
        (text quá ngắn hoặc không hỗ trợ) — KHÔNG âm thầm đoán mò, để
        caller tự quyết định skip message đó (đúng hành vi pipeline
        hiện tại, xem realtime_listener.py).
        """
        lang = detect_language(text)

        if lang == "vi":
            return self.phobert.analyze(text)
        elif lang == "en":
            return self.finbert.analyze(text)
        elif lang is None:
            logger.debug(
                f"[MULTILINGUAL] Không xác định ngôn ngữ, skip: '{text[:50]}...'"
            )
            return None
        else:
            # Nhánh dự phòng — xem ghi chú đầu file
            logger.info(f"[MULTILINGUAL] Ngôn ngữ lạ '{lang}', dùng XLM-R fallback.")
            return self.xlmr.analyze(text)


if __name__ == "__main__":
    analyzer = MultilingualSentimentAnalyzer(lazy_load=True)
    samples = [
        "BTC vừa được niêm yết thêm trên sàn Binance, giá tăng mạnh trong phiên hôm nay.",
        "Bitcoin surges after ETF approval news from the SEC.",
        "OK",  # quá ngắn, kỳ vọng None
    ]
    for s in samples:
        result = analyzer.analyze(s)
        if result:
            print(
                f"[{result.model_used:20s} {result.label:8s} score={result.score:+.3f}] {s}"
            )
        else:
            print(f"[SKIPPED - không xác định ngôn ngữ] {s}")
