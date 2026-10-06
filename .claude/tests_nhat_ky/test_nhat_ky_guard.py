"""Test hook nhat_ky_guard.py bằng git repo thật trong thư mục tạm (không mock).

Gọi script như Claude Code gọi hook: subprocess, JSON qua stdin, đọc JSON ở stdout.
Chạy: .venv/bin/python -m pytest .claude/tests_nhat_ky -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "hooks" / "nhat_ky_guard.py"


def _env():
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "CLAUDE_PROJECT_DIR"):
        env.pop(key, None)
    return env


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        env=_env(),
    )


def hook(mode, repo, **payload):
    data = {"cwd": str(repo), "session_id": "test", "hook_event_name": mode}
    data.update(payload)
    res = subprocess.run(
        [sys.executable, str(GUARD), mode],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout) if res.stdout.strip() else {}


def cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(),
    )


def pending_text(out):
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def write_journal(repo, name, text):
    path = repo / "docs" / "nhat-ky" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / ".gitignore").write_text("*.session\n")
    (r / "rag").mkdir()
    (r / "rag" / "config.py").write_text("A = 1\n")
    (r / "docs" / "context").mkdir(parents=True)
    (r / "docs" / "context" / "notes.md").write_text("x\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def tracked(repo):
    """Repo đã có mốc theo dõi."""
    out = hook("stop", repo)
    assert "khởi tạo mốc" in out["systemMessage"]
    return repo


def test_lan_dau_khoi_tao_moc_va_cho_dung(repo):
    out = hook("stop", repo)
    assert "khởi tạo mốc" in out["systemMessage"]
    assert "hookSpecificOutput" not in out
    assert (repo / ".git" / "nhat-ky-guard.json").is_file()


def test_thay_doi_co_tu_truoc_moc_khong_bi_tinh(repo):
    (repo / "rag" / "config.py").write_text("A = 2\n")
    hook("stop", repo)  # khởi tạo mốc sau khi file đã đổi
    assert hook("stop", repo) == {}


def test_khong_doi_gi_thi_im_lang(tracked):
    assert hook("stop", tracked) == {}


def test_sua_file_ma_khong_ghi_nhat_ky_thi_bi_nhac(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    out = hook("stop", tracked)
    assert "rag/config.py" in pending_text(out)


def test_nhat_ky_neu_dung_duong_dan_thi_cho_dung_va_xac_nhan(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    write_journal(tracked, "2026-10-06_thay-doi_x.md", "Sửa `rag/config.py` vì ...")
    assert hook("stop", tracked) == {}
    assert hook("stop", tracked) == {}  # đã xác nhận, không nhắc lại


def test_nhat_ky_thieu_mot_file_thi_chi_nhac_file_do(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    (tracked / "docs" / "context" / "notes.md").write_text("y\n")
    write_journal(tracked, "2026-10-06_thay-doi_x.md", "Sửa rag/config.py")
    text = pending_text(hook("stop", tracked))
    assert "docs/context/notes.md" in text
    assert "rag/config.py" not in text


def test_thu_muc_cha_sau_2_cap_duoc_tinh(tracked):
    (tracked / "docs" / "context" / "notes.md").write_text("y\n")
    write_journal(
        tracked, "2026-10-06_thay-doi_x.md", "Cập nhật docs/context/ cho khớp"
    )
    assert hook("stop", tracked) == {}


def test_thu_muc_cha_1_cap_khong_duoc_tinh(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    write_journal(tracked, "2026-10-06_thay-doi_x.md", "Có sửa trong rag/ và docs/")
    assert "rag/config.py" in pending_text(hook("stop", tracked))


def test_file_moi_untracked_bi_phat_hien(tracked):
    (tracked / "rag" / "moi.py").write_text("B = 1\n")
    assert "rag/moi.py" in pending_text(hook("stop", tracked))


def test_file_bi_xoa_bi_phat_hien(tracked):
    (tracked / "rag" / "config.py").unlink()
    assert "rag/config.py" in pending_text(hook("stop", tracked))


def test_file_ignored_khong_tinh(tracked):
    (tracked / "crypto.session").write_text("auth")
    assert hook("stop", tracked) == {}


def test_settings_local_json_khong_tinh(tracked):
    (tracked / ".claude").mkdir()
    (tracked / ".claude" / "settings.local.json").write_text("{}")
    assert hook("stop", tracked) == {}


def test_commit_khong_tinh_la_thay_doi(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    write_journal(tracked, "2026-10-06_thay-doi_x.md", "rag/config.py")
    assert hook("stop", tracked) == {}
    git(tracked, "add", "-A")
    git(tracked, "commit", "-qm", "commit")
    assert hook("stop", tracked) == {}


def test_nhat_ky_cu_da_xac_nhan_khong_che_thay_doi_moi(tracked):
    # Lượt 1: nhật ký nhắc trước rag/config.py nhưng chưa sửa file -> xác nhận.
    write_journal(tracked, "2026-10-06_thay-doi_x.md", "Sẽ sửa rag/config.py")
    assert hook("stop", tracked) == {}
    # Lượt 2: sửa file mà không cập nhật nhật ký -> phải bị nhắc.
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    assert "rag/config.py" in pending_text(hook("stop", tracked))


def test_stop_hook_active_cho_dung_nhung_khong_xac_nhan(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    out = hook("stop", tracked, stop_hook_active=True)
    assert "hookSpecificOutput" not in out
    assert "chưa có trong nhật ký" in out["systemMessage"]
    # Không xác nhận mốc: lượt sau vẫn nhắc.
    assert "rag/config.py" in pending_text(hook("stop", tracked))


def test_session_start_bao_thay_doi_ton_dong(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    out = hook("session-start", tracked, source="startup")
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "rag/config.py" in out["hookSpecificOutput"]["additionalContext"]
    assert "chưa có" in out["systemMessage"]


def test_status_exit_code(tracked):
    assert cli(tracked, "status").returncode == 0
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    res = cli(tracked, "status")
    assert res.returncode == 1
    assert "rag/config.py" in res.stdout


def test_ack_bat_buoc_ly_do_va_ghi_log(tracked):
    (tracked / "rag" / "config.py").write_text("A = 2\n")
    assert cli(tracked, "ack").returncode == 2
    res = cli(tracked, "ack", "git", "pull", "từ", "remote")
    assert res.returncode == 0, res.stderr
    log = (tracked / ".git" / "nhat-ky-guard.log").read_text(encoding="utf-8")
    entry = json.loads(log.strip().splitlines()[-1])
    assert entry["reason"] == "git pull từ remote"
    assert entry["accepted_without_journal"] == ["rag/config.py"]
    assert hook("stop", tracked) == {}


def test_khong_phai_git_repo_thi_canh_bao_khong_crash(tmp_path):
    out = hook("stop", tmp_path)
    assert "nhat_ky_guard lỗi" in out["systemMessage"]
    assert "KHÔNG được kiểm tra" in out["systemMessage"]


def test_file_trang_thai_hong_thi_canh_bao_va_khong_tu_xoa(tracked):
    state = tracked / ".git" / "nhat-ky-guard.json"
    state.write_text("{hỏng", encoding="utf-8")
    out = hook("stop", tracked)
    assert "hỏng" in out["systemMessage"]
    assert state.read_text(encoding="utf-8") == "{hỏng"


def test_danh_sach_dai_bi_cat_gon(tracked):
    for i in range(45):
        (tracked / "rag" / f"f{i:02d}.py").write_text("x\n")
    text = pending_text(hook("stop", tracked))
    assert "và 5 file khác" in text
    assert len(text) < 10_000
