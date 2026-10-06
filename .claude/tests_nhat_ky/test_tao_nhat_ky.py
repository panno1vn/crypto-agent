"""Test script tạo file nhật ký (tao_nhat_ky.py) bằng git repo thật trong thư mục tạm.

Chạy: .venv/bin/python -m pytest .claude/tests_nhat_ky -q
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "nhat-ky"
    / "scripts"
    / "tao_nhat_ky.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("tao_nhat_ky", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env():
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return env


def run(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(
            ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
            env=_env(),
        )
    return r


@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "Thiết lập permission rules (auto mode)!",
            "thiet-lap-permission-rules-auto-mode",
        ),
        ("Đường dẫn ĐẶC BIỆT", "duong-dan-dac-biet"),
        ("Bug: watermark xuyên kênh 5/13", "bug-watermark-xuyen-kenh-5-13"),
    ],
)
def test_slugify_tieng_viet(title, expected):
    assert _load_module().slugify(title) == expected


def test_slugify_cat_theo_ranh_gioi_tu():
    slug = _load_module().slugify(
        "một hai ba bốn năm sáu bảy tám chín mười " * 5, max_len=30
    )
    assert len(slug) <= 30
    assert not slug.endswith("-")


def test_slugify_rong_thi_bao_loi():
    with pytest.raises(ValueError):
        _load_module().slugify("!!! ???")


def test_tao_file_tu_template_thay_doi(repo):
    res = run(repo, "--loai", "thay-doi", "--tieu-de", "Thiết lập hook nhật ký")
    assert res.returncode == 0, res.stderr
    rel = res.stdout.strip()
    assert re.fullmatch(
        r"docs/nhat-ky/\d{4}-\d{2}-\d{2}_thay-doi_thiet-lap-hook-nhat-ky\.md", rel
    )
    content = (repo / rel).read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert 'tieu_de: "Thiết lập hook nhật ký"' in content
    assert "{{" not in content
    assert "## 5. Phương án thay thế" in content


def test_tao_file_bug_co_muc_chong_tai_pham(repo):
    res = run(repo, "--loai", "bug", "--tieu-de", "Watermark xuyên kênh")
    assert res.returncode == 0, res.stderr
    content = (repo / res.stdout.strip()).read_text(encoding="utf-8")
    assert "loai: bug" in content
    assert "## 12. Chống tái phạm" in content


def test_khong_ghi_de_file_da_co(repo):
    first = run(repo, "--loai", "bug", "--tieu-de", "Trùng tên")
    assert first.returncode == 0, first.stderr
    path = repo / first.stdout.strip()
    path.write_text("nội dung đã viết", encoding="utf-8")
    second = run(repo, "--loai", "bug", "--tieu-de", "Trùng tên")
    assert second.returncode == 1
    assert "đã tồn tại" in second.stderr
    assert path.read_text(encoding="utf-8") == "nội dung đã viết"


def test_loai_sai_bi_tu_choi(repo):
    res = run(repo, "--loai", "khac", "--tieu-de", "x")
    assert res.returncode == 2


def test_tieu_de_co_ngoac_kep_khong_lam_hong_frontmatter(repo):
    res = run(repo, "--loai", "thay-doi", "--tieu-de", 'Đổi "add" thành "upsert"')
    assert res.returncode == 0, res.stderr
    content = (repo / res.stdout.strip()).read_text(encoding="utf-8")
    assert "tieu_de: \"Đổi 'add' thành 'upsert'\"" in content
