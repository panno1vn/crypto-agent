"""
tests/unit/test_compose_volumes.py

Dữ liệu bền của stack phải nằm trên named volume, không bind thư mục WSL.

Bug thật (phát hiện 2026-10-09): mlflow mount `./mlflow_data:/mlflow`.
Docker Desktop tự bật container `restart: always` lúc khởi động, trước khi
distro WSL sẵn sàng; bind THƯ MỤC khi đó bị thay im lặng bằng tmpfs (bind
FILE thì container exit 127 — cùng cơ chế, nhưng ồn ào). MLflow chạy bình
thường trên RAM, mỗi lần restart Docker Desktop mất sạch run: từ 2026-08-07
tới 10-09 không run nào xuống đĩa (mất Retrieval_Eval_Day26, Ablation_Day32).

Check runtime đi kèm: scripts/manual/preflight_mlflow.py.
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

# service -> thư mục dữ liệu bền trong container
STATEFUL_PATHS = {
    "mlflow": "/mlflow",
    "postgres": "/var/lib/postgresql/data",
    "chroma": "/data",
}


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())


@pytest.mark.parametrize("service,target", sorted(STATEFUL_PATHS.items()))
def test_du_lieu_ben_dung_named_volume(compose, service, target):
    mounts = compose["services"][service].get("volumes", [])
    sources = [m.split(":")[0] for m in mounts if m.split(":")[1] == target]
    assert len(sources) == 1, f"{service}: không thấy mount nào vào {target}"
    source = sources[0]
    assert not source.startswith((".", "/", "~")), (
        f"{service}: {source}:{target} là bind mount — Docker Desktop có thể "
        f"thay bằng tmpfs khi WSL chưa sẵn sàng. Dùng named volume."
    )
    assert source in (
        compose.get("volumes") or {}
    ), f"{service}: volume '{source}' chưa khai báo ở khối volumes: top-level"
