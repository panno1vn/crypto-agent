"""
nlp/sentiment_pipeline.py

Ngày 19 — Orchestrator: fetch message chưa xử lý -> chạy sentiment
analyzer -> lưu kết quả -> đánh dấu is_processed=TRUE.

Đây là 2 bước ĐẦU (fetch + analyze) hoàn toàn thiếu trong pseudocode
gốc của roadmap — aggregate_channel_sentiment() gốc giả định sentiment
đã có sẵn trong tay, module này là nơi tạo ra dữ liệu đó.

Độc lập với nlp/engagement_weighting.py — module này chỉ ghi RAW
sentiment_score (chưa nhân engagement weight) vào DB. Vì vậy KHÔNG
cần numpy, KHÔNG cần thêm gì vào requirements-airflow.txt ngoài
những gì các analyzer (PhoBERT/FinBERT/XLM-R) đã cần sẵn.
"""

from datetime import datetime, timezone
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_pipeline.logger import get_logger
from data_pipeline.models import TelegramMessage
from nlp.multilingual_analyzer import MultilingualSentimentAnalyzer

logger = get_logger(__name__)


def _utc_naive_now() -> datetime:
    """Giờ hiện tại, UTC, đã strip tzinfo — khớp quy ước naive=UTC của DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def process_unprocessed_messages(
    session_factory,
    analyzer: Optional[MultilingualSentimentAnalyzer] = None,
    batch_size: int = 100,
) -> int:
    """
    Xử lý TẤT CẢ message có is_processed=FALSE, theo batch.

    Hành vi lỗi (quan trọng):
      - Message phân tích lỗi (exception từ analyzer, DB constraint, ...)
        -> log đầy đủ, KHÔNG set is_processed=TRUE -> sẽ được thử lại
        ở lần chạy DAG kế tiếp. Không để 1 message hỏng làm mất data
        vĩnh viễn, cũng không để nó chặn các message khác trong cùng batch.
      - Message analyzer trả None (ngôn ngữ không xác định, đúng hành vi
        MultilingualSentimentAnalyzer.analyze() khi detect_language()
        trả None) -> VẪN set is_processed=TRUE (đây không phải lỗi,
        là kết quả hợp lệ "không phân tích được") -> tránh bị query lại
        vô thời hạn mỗi lần DAG chạy.
      - Trong CÙNG 1 lần gọi hàm này: message đã thử (dù thành công hay
        lỗi) được loại khỏi batch fetch tiếp theo qua `attempted_ids`,
        tránh vòng lặp vô hạn nếu 1 message luôn luôn lỗi (vd text lỗi
        encoding khiến model crash mỗi lần).

    Giới hạn CHƯA xử lý (biết trước, chấp nhận ở quy mô hiện tại):
      - Nếu 1 message lỗi VĨNH VIỄN (crash mọi lần, mọi run), nó sẽ
        chiếm 1 suất trong batch_size mỗi lần DAG chạy tới vô hạn.
        Chưa có cơ chế "thử N lần rồi bỏ qua" (cần thêm cột kiểu
        sentiment_error_count nếu vấn đề này thực sự xảy ra).

    Args:
        session_factory: callable trả về AsyncSession (async context
                          manager) — cùng pattern với dag_calculate_indicators.py.
        analyzer:         cho phép inject analyzer giả lập trong test
                          (KHÔNG load model thật). None -> tự tạo
                          MultilingualSentimentAnalyzer(lazy_load=False).
        batch_size:       số message fetch mỗi vòng.

    Returns:
        Tổng số message đã xử lý THÀNH CÔNG (không tính message lỗi).
    """
    if analyzer is None:
        analyzer = MultilingualSentimentAnalyzer(lazy_load=False)

    total_processed = 0
    total_failed = 0
    attempted_ids: Set[int] = set()

    while True:
        async with session_factory() as session:
            stmt = (
                select(TelegramMessage)
                .where(TelegramMessage.is_processed.is_(False))
                .where(TelegramMessage.message_text.is_not(None))
            )
            if attempted_ids:
                stmt = stmt.where(TelegramMessage.id.notin_(attempted_ids))
            stmt = stmt.order_by(TelegramMessage.id).limit(batch_size)

            try:
                result = await session.execute(stmt)
                messages = result.scalars().all()
            except Exception:
                logger.error("[SENTIMENT] Fetch batch thất bại.", exc_info=True)
                raise

            if not messages:
                break

            for msg in messages:
                try:
                    # NOTE: analyzer.analyze() là hàm SYNC (CPU/GPU-bound
                    # transformer inference), chạy trong coroutine này sẽ
                    # block event loop. Chấp nhận được ở quy mô hiện tại
                    # (batch tuần tự trong 1 DAG task riêng biệt, không có
                    # coroutine nào khác cần chạy song song cùng lúc).
                    # Nếu latency trở thành vấn đề (xem mục tiêu Ngày 21:
                    # <200ms/message), cân nhắc run_in_executor hoặc batch
                    # inference thật sự (đưa nhiều text vào 1 lần forward pass).
                    sentiment = analyzer.analyze(msg.message_text)

                    if sentiment is not None:
                        msg.sentiment_label = sentiment.label
                        msg.sentiment_score = sentiment.score
                        msg.sentiment_model_version = sentiment.model_used
                        msg.sentiment_analyzed_at = _utc_naive_now()
                    # sentiment is None: ngôn ngữ không xác định — đây là
                    # kết quả HỢP LỆ, không phải lỗi. sentiment_* cột giữ
                    # NULL (default), nhưng vẫn mark processed bên dưới.

                    msg.is_processed = True
                    total_processed += 1

                except Exception as e:
                    total_failed += 1
                    # Chỉ track id THẤT BẠI — message thành công đã tự
                    # loại khỏi batch sau nhờ is_processed=True (đã commit),
                    # không cần thêm vào đây. Nếu track cả thành công,
                    # attempted_ids phình to vô ích theo số message xử lý
                    # được, làm câu NOT IN(...) ngày càng chậm mà không
                    # mang lại lợi ích gì (đã quan sát thấy: 2s -> 11s
                    # mỗi 100 message trên 1 lần chạy thật ~3900 message).
                    attempted_ids.add(msg.id)
                    logger.error(
                        f"[SENTIMENT] Lỗi phân tích message id={msg.id}: {e}",
                        exc_info=True,
                    )
                    # KHÔNG set is_processed=True — để được thử lại ở lần
                    # chạy DAG kế tiếp (process mới, attempted_ids mới).

            try:
                await session.commit()
            except Exception:
                logger.error("[SENTIMENT] Commit batch thất bại.", exc_info=True)
                await session.rollback()
                raise

            logger.info(
                f"[SENTIMENT] Batch xong: {len(messages)} message trong batch "
                f"(lũy kế: {total_processed} thành công, {total_failed} lỗi)."
            )

            if len(messages) < batch_size:
                break

    logger.info(
        f"[SENTIMENT] Hoàn tất: {total_processed} xử lý thành công, "
        f"{total_failed} lỗi (sẽ được thử lại ở lần chạy DAG kế tiếp)."
    )
    return total_processed
