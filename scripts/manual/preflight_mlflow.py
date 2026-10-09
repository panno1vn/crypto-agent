"""
scripts/manual/preflight_mlflow.py

Kiểm MLflow ghi xuống đĩa thật, trước khi chạy thí nghiệm cần log.

Bug thật (phát hiện 2026-10-09): mlflow bind `./mlflow_data:/mlflow`. Docker
Desktop bật container lúc khởi động, trước khi WSL sẵn sàng, và thay bind
thư mục bằng tmpfs mà không báo gì. Server vẫn "healthy", log run bình
thường, rồi mất sạch ở lần restart sau. Từ 2026-08-07 tới 10-09 không run
nào xuống đĩa. Đã chuyển sang named volume (tests/unit/test_compose_volumes.py).

Hai check:
  1. /mlflow trong container không phải tmpfs (đọc /proc/mounts).
  2. Experiment lịch sử HISTORY_MARKER còn — DB rỗng nghĩa là đang chạy trên
     store mới tạo, không phải store thật.

Chạy:
    MLFLOW_TRACKING_URI=http://localhost:5000 python scripts/manual/preflight_mlflow.py
"""

import os
import subprocess
import sys

from mlflow import MlflowClient

CONTAINER = "crypto_mlflow"
MOUNT_POINT = "/mlflow"
# Run 2026-07-11, có trong mlflow_data/mlflow.db đã chép vào volume.
HISTORY_MARKER = "Crypto_Agent_Backtesting"


def check_mount() -> str:
    out = subprocess.run(
        ["docker", "exec", CONTAINER, "cat", "/proc/mounts"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fstypes = [
        line.split()[2] for line in out.splitlines() if line.split()[1] == MOUNT_POINT
    ]
    if len(fstypes) != 1:
        raise RuntimeError(f"{MOUNT_POINT} không phải mount point trong {CONTAINER}")
    if fstypes[0] == "tmpfs":
        raise RuntimeError(
            f"{MOUNT_POINT} là tmpfs — run sẽ mất khi restart. "
            "Kiểm docker-compose.yml dùng named volume, rồi "
            "`docker compose up -d mlflow`."
        )
    return fstypes[0]


def check_history(tracking_uri: str) -> None:
    if MlflowClient(tracking_uri).get_experiment_by_name(HISTORY_MARKER) is None:
        raise RuntimeError(
            f"MLflow {tracking_uri} không có experiment {HISTORY_MARKER} — "
            "đang chạy trên store rỗng (tmpfs hoặc volume mới)."
        )


def main() -> None:
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        raise RuntimeError("Thiếu env MLFLOW_TRACKING_URI")
    fstype = check_mount()
    check_history(uri)
    print(f"OK: {MOUNT_POINT} là {fstype}, có {HISTORY_MARKER}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
