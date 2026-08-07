"""
scripts/log_phobert_v2_to_mlflow.py

Log params + metrics + artifacts của PhoBERT v2 vào MLflow — việc bị
bỏ sót ở buổi sáng Ngày 18 (đã evaluate, download model, nhưng chưa
log MLflow như checklist roadmap yêu cầu).

Yêu cầu: MLflow server đang chạy (docker compose, từ Tuần 2).
"""

import json
from pathlib import Path

import mlflow

MLFLOW_TRACKING_URI = "http://localhost:5000"
EXPERIMENT_NAME = "Crypto_Agent_PhoBERT_Sentiment_v3"  # đổi tên — experiment cũ bị soft-delete, tên cũ vẫn bị giữ chỗ trong thùng rác MLflow
MODEL_DIR = Path("models/phobert-crypto")


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    with open(MODEL_DIR / "val_metrics.json", encoding="utf-8") as f:
        val_metrics = json.load(f)
    with open(MODEL_DIR / "test_metrics.json", encoding="utf-8") as f:
        test_metrics = json.load(f)

    with mlflow.start_run(run_name="phobert-v2-sqrt-balanced"):
        # Params — ghi lại đúng cấu hình đã dùng, để so sánh run sau này
        mlflow.log_params(
            {
                "model_name": "vinai/phobert-base-v2",
                "dataset_version": "v2",
                "train_size": 1384,
                "val_size": 297,
                "test_size": 297,
                "class_weight_mode": "sqrt_balanced",
                "num_train_epochs": 4,
                "learning_rate": 2e-5,
                "max_length": 256,
                "imbalance_ratio": 6.7,
            }
        )

        # Metrics — cả val (lúc train chọn best checkpoint) lẫn test (chính thức)
        for k, v in val_metrics.items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(f"val_{k}", v)
        for k, v in test_metrics.items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(f"test_{k}", v)

        # So sánh với v1 — log luôn để tiện nhìn trên MLflow UI
        mlflow.log_metrics(
            {
                "v1_test_f1_macro": 0.576,
                "v1_test_f1_negative": 0.486,
                "v1_test_f1_neutral": 0.802,
                "v1_test_f1_positive": 0.441,
                "improvement_f1_macro": test_metrics["f1_macro"] - 0.576,
            }
        )

        # Artifacts
        for fname in [
            "confusion_matrix.png",
            "training_curves.png",
            "classification_report.txt",
        ]:
            path = MODEL_DIR / fname
            if path.exists():
                mlflow.log_artifact(str(path))
            else:
                print(f"[WARNING] Không thấy {fname}, bỏ qua.")

        run_id = mlflow.active_run().info.run_id
        print(f"[DONE] Logged run_id={run_id}")
        print(f"Xem tại: {MLFLOW_TRACKING_URI}/#/experiments")


if __name__ == "__main__":
    main()
