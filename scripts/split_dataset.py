"""
scripts/split_dataset.py

Split labeled dataset (Label Studio export, project crypto sentiment)
thành train/val/test, stratified theo nhãn.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from sklearn.model_selection import train_test_split


def load_label_studio_export(path: str) -> tuple[list[dict], list[int]]:
    """
    Parse export JSON thật của project (cấu trúc xác nhận ngày 24/07/2026):
      item["data"]["text"]          -> nội dung message
      item["data"]["message_id"]    -> id gốc trong PostgreSQL
      item["annotations"][0]["result"][0]["value"]["choices"][0] -> nhãn

    Returns:
        (records hợp lệ, list id bị rỗng/không parse được)
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    records = []
    skipped_ids = []

    for item in raw:
        task_id = item.get("id")
        annotations = item.get("annotations", [])

        if not annotations or not annotations[0].get("result"):
            skipped_ids.append(task_id)
            continue

        try:
            text = item["data"]["text"]
            message_id = item["data"].get("message_id")
            label = annotations[0]["result"][0]["value"]["choices"][0]
            records.append(
                {
                    "task_id": task_id,
                    "message_id": message_id,
                    "text": text,
                    "label": label,
                }
            )
        except (KeyError, IndexError) as e:
            print(f"[SKIP] task_id={task_id} lỗi parse: {e}")
            skipped_ids.append(task_id)

    return records, skipped_ids


def check_balance(records: list[dict], name: str, warn_ratio: float = 3.0) -> None:
    counts = Counter(r["label"] for r in records)
    total = sum(counts.values())
    print(f"\n[{name}] Tổng: {total}")
    for label, c in counts.most_common():
        print(f"  {label:10s}: {c:5d} ({c/total*100:.1f}%)")

    if len(counts) >= 2:
        max_c, min_c = max(counts.values()), min(counts.values())
        ratio = max_c / min_c
        if ratio > warn_ratio:
            print(f"  ⚠️  Imbalanced! max/min ratio = {ratio:.2f} > {warn_ratio}")


def split_dataset(
    input_path: str,
    output_dir: str,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
) -> None:
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6

    records, skipped_ids = load_label_studio_export(input_path)

    print(f"[LOAD] Tổng task trong file: {len(records) + len(skipped_ids)}")
    print(f"[LOAD] Hợp lệ (có label thật): {len(records)}")
    if skipped_ids:
        ids_preview = skipped_ids[:20]
        more = "..." if len(skipped_ids) > 20 else ""
        print(
            f"[LOAD] BI SKIP (rong/loi): {len(skipped_ids)} - task_ids: {ids_preview}{more}"
        )
        print(
            "[LOAD] -> Kiem tra lai cac task nay trong Label Studio truoc khi train, "
            "neu so luong lon thi KHONG nen bo qua."
        )

    if not records:
        raise ValueError("Không có record hợp lệ nào — dừng lại, kiểm tra export trước")

    labels = [r["label"] for r in records]

    train, temp = train_test_split(
        records,
        train_size=train_ratio,
        stratify=labels,
        random_state=seed,
    )
    temp_labels = [r["label"] for r in temp]
    val_size_relative = val_ratio / (val_ratio + test_ratio)
    val, test = train_test_split(
        temp,
        train_size=val_size_relative,
        stratify=temp_labels,
        random_state=seed,
    )

    check_balance(records, "TOÀN BỘ (hợp lệ)")
    check_balance(train, "TRAIN")
    check_balance(val, "VAL")
    check_balance(test, "TEST")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for name, data in [("train", train), ("val", val), ("test", test)]:
        path = out / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n[SAVED] {path} ({len(data)} records)")

    if skipped_ids:
        skipped_path = out / "skipped_task_ids.json"
        with open(skipped_path, "w") as f:
            json.dump(skipped_ids, f, indent=2)
        print(
            f"[SAVED] {skipped_path} — danh sách task_id cần xem lại trong Label Studio"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", default="data/phobert_dataset")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    split_dataset(args.input, args.output_dir, seed=args.seed)
