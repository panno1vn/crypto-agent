"""
scripts/compare_phobert_xlmr.py

So sánh PhoBERT (fine-tuned riêng) vs XLM-RoBERTa (tổng quát, đa ngôn
ngữ) trên 100 message tiếng Việt lấy từ test.json — CÓ ground truth,
so sánh công bằng, trả lời câu hỏi roadmap đặt ra Ngày 18: "PhoBERT có
vượt trội không? Ở đâu?"
"""

import json
import random
from pathlib import Path

import mlflow
from sklearn.metrics import accuracy_score, classification_report, f1_score

from nlp.phobert_analyzer import PhoBERTAnalyzer
from nlp.xlmr_analyzer import XLMRAnalyzer

MLFLOW_TRACKING_URI = "http://localhost:5000"
EXPERIMENT_NAME = "Crypto_Agent_PhoBERT_Sentiment_v3"
TEST_PATH = Path("data/phobert_dataset/test.json")
SAMPLE_SIZE = 100
SEED = 42


def main():
    with open(TEST_PATH, encoding="utf-8") as f:
        test_data = json.load(f)

    random.seed(SEED)
    sample = random.sample(test_data, min(SAMPLE_SIZE, len(test_data)))
    texts = [r["text"] for r in sample]
    true_labels = [r["label"] for r in sample]

    print(f"[SAMPLE] {len(sample)} message từ test.json (seed={SEED})")

    print("[PHOBERT] Đang predict...")
    phobert = PhoBERTAnalyzer()
    phobert_results = phobert.analyze_batch(texts)
    phobert_preds = [r.label for r in phobert_results]

    print("[XLMR] Đang predict...")
    xlmr = XLMRAnalyzer()
    xlmr_results = xlmr.analyze_batch(texts)
    xlmr_preds = [r.label for r in xlmr_results]

    phobert_acc = accuracy_score(true_labels, phobert_preds)
    phobert_f1 = f1_score(true_labels, phobert_preds, average="macro", zero_division=0)
    xlmr_acc = accuracy_score(true_labels, xlmr_preds)
    xlmr_f1 = f1_score(true_labels, xlmr_preds, average="macro", zero_division=0)

    print(f"\n[KẾT QUẢ] Trên {len(sample)} message tiếng Việt (có ground truth):")
    print(
        f"  PhoBERT (fine-tuned)  : accuracy={phobert_acc:.4f}  f1_macro={phobert_f1:.4f}"
    )
    print(f"  XLM-RoBERTa (tổng quát): accuracy={xlmr_acc:.4f}  f1_macro={xlmr_f1:.4f}")
    print(f"  Chênh lệch f1_macro   : {phobert_f1 - xlmr_f1:+.4f}")

    print("\n[PHOBERT - classification report]")
    print(classification_report(true_labels, phobert_preds, zero_division=0, digits=4))
    print("\n[XLM-R - classification report]")
    print(classification_report(true_labels, xlmr_preds, zero_division=0, digits=4))

    # Đối chiếu case cụ thể — nơi PhoBERT đúng nhưng XLM-R sai, và ngược lại
    phobert_wins, xlmr_wins = [], []
    for i, (true, p_pred, x_pred) in enumerate(
        zip(true_labels, phobert_preds, xlmr_preds)
    ):
        if p_pred == true and x_pred != true:
            phobert_wins.append({"text": texts[i], "true": true, "xlmr_pred": x_pred})
        elif x_pred == true and p_pred != true:
            xlmr_wins.append({"text": texts[i], "true": true, "phobert_pred": p_pred})

    print(f"\n[PHOBERT ĐÚNG, XLM-R SAI] {len(phobert_wins)} case")
    for ex in phobert_wins[:5]:
        print(
            f"  true={ex['true']:8s} xlmr_pred={ex['xlmr_pred']:8s} | {ex['text'][:70]}"
        )

    print(f"\n[XLM-R ĐÚNG, PHOBERT SAI] {len(xlmr_wins)} case")
    for ex in xlmr_wins[:5]:
        print(
            f"  true={ex['true']:8s} phobert_pred={ex['phobert_pred']:8s} | {ex['text'][:70]}"
        )

    # Log vào MLflow
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)
    with mlflow.start_run(run_name="compare-phobert-vs-xlmr"):
        mlflow.log_params(
            {
                "sample_size": len(sample),
                "seed": SEED,
                "xlmr_model": "cardiffnlp/twitter-xlm-roberta-base-sentiment",
            }
        )
        mlflow.log_metrics(
            {
                "phobert_accuracy": phobert_acc,
                "phobert_f1_macro": phobert_f1,
                "xlmr_accuracy": xlmr_acc,
                "xlmr_f1_macro": xlmr_f1,
                "phobert_advantage_f1": phobert_f1 - xlmr_f1,
                "phobert_wins_count": len(phobert_wins),
                "xlmr_wins_count": len(xlmr_wins),
            }
        )

        comparison_path = Path("data/raw/phobert_vs_xlmr_comparison.json")
        with open(comparison_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "phobert_wins": phobert_wins,
                    "xlmr_wins": xlmr_wins,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        mlflow.log_artifact(str(comparison_path))

        print(f"\n[DONE] Logged vào MLflow, run_id={mlflow.active_run().info.run_id}")

    print(
        f"\n[KẾT LUẬN] PhoBERT {'vượt trội' if phobert_f1 > xlmr_f1 else 'KHÔNG vượt trội'} "
        f"XLM-RoBERTa (chênh lệch f1_macro: {phobert_f1 - xlmr_f1:+.4f}). "
        f"Ghi kết quả này vào docs/WEEK3.md mục 'Buổi tối — Review'."
    )


if __name__ == "__main__":
    main()
