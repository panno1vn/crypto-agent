"""
Ngày 21 — Benchmark tốc độ inference PhoBERT trên CPU (WSL2).
Yêu cầu: mean < 200ms/message trên dữ liệu thật (không phải câu mẫu lặp lại).

Cách chạy:
    python3 scripts/manual/benchmark_phobert_inference.py
"""
import json
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_PATH = "models/phobert-crypto/best_model"
SAMPLE_FILE = "/tmp/sample_real_messages.jsonl"
THRESHOLD_MS = 200


def load_real_messages(path: str) -> list[str]:
    texts = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            texts.append(json.loads(line)["message_text"])
    return texts


def main():
    tok = AutoTokenizer.from_pretrained(MODEL_PATH, use_fast=False)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH)
    model.eval()

    texts = load_real_messages(SAMPLE_FILE)
    print(f"Loaded {len(texts)} real messages")
    lens = [len(t) for t in texts]
    print(
        f"Char length — min:{min(lens)} max:{max(lens)} mean:{sum(lens)/len(lens):.0f}"
    )

    # Warm-up — loại trừ chi phí init kernel lần đầu, không tính vào kết quả
    with torch.no_grad():
        warm = tok(texts[0], return_tensors="pt", truncation=True, max_length=256)
        _ = model(**warm)

    times = []
    with torch.no_grad():
        for text in texts:
            t0 = time.time()
            inputs = tok(text, return_tensors="pt", truncation=True, max_length=256)
            _ = model(**inputs)
            times.append((time.time() - t0) * 1000)

    ts = sorted(times)
    mean = sum(ts) / len(ts)
    p50 = ts[len(ts) // 2]
    p95 = ts[int(len(ts) * 0.95)]
    print(f"N={len(ts)}")
    print(f"Mean: {mean:.1f}ms")
    print(f"P50:  {p50:.1f}ms")
    print(f"P95:  {p95:.1f}ms")
    print(f"Max:  {max(ts):.1f}ms")
    status = "PASS" if mean < THRESHOLD_MS else "FAIL"
    print(f"Threshold: {THRESHOLD_MS}ms — {status}")


if __name__ == "__main__":
    main()
