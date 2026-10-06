#!/usr/bin/env python3
"""nhat_ky_guard.py: hook bắt buộc ghi nhật ký theo task (skill nhat-ky).

Chế độ (đối số đầu tiên):
  session-start  Hook SessionStart. Lần đầu chạy trong repo: chụp mốc (baseline).
                 Những lần sau: nếu còn thay đổi chưa có trong nhật ký thì báo cho
                 Claude (additionalContext) và cho Pan (systemMessage).
  stop           Hook Stop. Nếu có file đổi nội dung kể từ lần xác nhận trước mà chưa
                 được nêu trong một file nhật ký vừa tạo/sửa ở docs/nhat-ky/, yêu cầu
                 Claude ghi nhật ký trước khi kết thúc lượt.
  status         Chạy tay: in các thay đổi chưa có nhật ký. Exit 1 nếu còn tồn đọng.
  ack "lý do"    CHỈ Pan chạy tay (vd sau git pull / checkout nhánh khác): chấp nhận
                 trạng thái hiện tại không cần nhật ký. Ghi log vào <git-dir>/nhat-ky-guard.log.

Cơ chế:
  - Ảnh chụp = {đường dẫn: git blob hash} của mọi file tracked và untracked không bị
    ignore (git ls-files + git hash-object). Dựa trên NỘI DUNG nên commit không bị tính
    là thay đổi, còn sửa bằng bất kỳ cách nào (Edit, sed, script, IDE) đều bị phát hiện.
  - docs/nhat-ky/ được chụp riêng. Một file thay đổi được coi là "đã ghi" khi đường dẫn
    của nó, hoặc một thư mục cha sâu từ 2 cấp có dấu "/" cuối (vd docs/context/), xuất
    hiện trong nội dung một file nhật ký đã tạo/sửa kể từ lần xác nhận trước.
  - Trạng thái lưu ở <git-dir>/nhat-ky-guard.json: theo từng working tree, không bị
    commit, sống qua reboot. Thay đổi chưa ghi KHÔNG được tha khi sang phiên mới.

Fail loudly: lỗi nội bộ không được làm treo phiên làm việc, nhưng luôn hiện cảnh báo cho
Pan (systemMessage) rằng nhật ký KHÔNG được kiểm tra. Không có nhánh nào im lặng bỏ qua.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

JOURNAL_DIR = "docs/nhat-ky/"
STATE_FILE = "nhat-ky-guard.json"
LOG_FILE = "nhat-ky-guard.log"
STATE_VERSION = 1
# File do chính Claude Code ghi (khi Pan chọn "Yes, don't ask again"), không phải thay đổi của task.
EXCLUDED_PATHS = frozenset({".claude/settings.local.json"})
# Thư mục cha phải sâu từ 2 cấp mới được tính là "đã nêu" (docs/context/ được, docs/ thì không),
# để một câu nhắc chung chung như "docs/" không che mất thay đổi của task khác.
MIN_DIR_DEPTH = 2
# additionalContext/systemMessage bị cắt ở 10.000 ký tự; liệt kê tối đa ngần này file.
MAX_LISTED = 40
DELETED = "<deleted>"


class GuardError(Exception):
    """Lỗi có thông điệp rõ ràng để hiện cho Pan."""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    try:
        res = subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GuardError(f"không chạy được `git {' '.join(args)}`: {exc}") from exc
    if res.returncode != 0:
        raise GuardError(
            f"`git {' '.join(args)}` lỗi (exit {res.returncode}): {res.stderr.strip()}"
        )
    return res.stdout


def find_repo(start: str) -> tuple[Path, Path]:
    """Trả (thư mục gốc working tree, git-dir của working tree đó)."""
    start_path = Path(start)
    if not start_path.is_dir():
        raise GuardError(f"thư mục làm việc không tồn tại: {start}")
    top = git(start_path, "rev-parse", "--show-toplevel").strip()
    gitdir = git(start_path, "rev-parse", "--absolute-git-dir").strip()
    return Path(top), Path(gitdir)


def snapshot(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Ảnh chụp nội dung: (file thường, file nhật ký). Giá trị là git blob hash hoặc DELETED."""
    listing = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = sorted({p for p in listing.split("\0") if p})
    result: dict[str, str] = {}
    to_hash: list[str] = []
    for p in paths:
        if p in EXCLUDED_PATHS:
            continue
        full = root / p
        if full.is_file():
            if "\n" in p:
                # hash-object --stdin-paths đọc theo dòng; tên file chứa newline không băm được.
                result[p] = "<ten-file-co-newline>"
            else:
                to_hash.append(p)
        elif full.is_dir():
            result[p] = "<dir>"  # submodule: không theo dõi nội dung bên trong
        else:
            result[p] = DELETED  # tracked nhưng đã bị xóa khỏi đĩa
    if to_hash:
        hashes = git(
            root, "hash-object", "--stdin-paths", stdin="\n".join(to_hash) + "\n"
        ).split()
        if len(hashes) != len(to_hash):
            raise GuardError(
                f"git hash-object trả {len(hashes)} hash cho {len(to_hash)} file"
            )
        result.update(zip(to_hash, hashes))
    main = {p: h for p, h in result.items() if not p.startswith(JOURNAL_DIR)}
    journal = {p: h for p, h in result.items() if p.startswith(JOURNAL_DIR)}
    return main, journal


def changed_paths(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """File đổi nội dung, mới xuất hiện, hoặc bị xóa. Vắng mặt và DELETED coi như nhau."""
    keys = set(old) | set(new)
    return sorted(k for k in keys if old.get(k, DELETED) != new.get(k, DELETED))


def is_mentioned(path: str, text: str) -> bool:
    if path in text:
        return True
    parts = path.split("/")
    for depth in range(len(parts) - 1, MIN_DIR_DEPTH - 1, -1):
        if "/".join(parts[:depth]) + "/" in text:
            return True
    return False


def read_journals(root: Path, paths: list[str]) -> str:
    chunks = []
    for p in paths:
        f = root / p
        if f.is_file():
            try:
                chunks.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError as exc:
                raise GuardError(f"không đọc được nhật ký {p}: {exc}") from exc
    return "\n".join(chunks)


def load_state(gitdir: Path) -> dict | None:
    f = gitdir / STATE_FILE
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GuardError(
            f"file trạng thái {f} hỏng ({exc}). Không tự khởi tạo lại vì như vậy sẽ âm thầm "
            "bỏ qua thay đổi chưa ghi. Kiểm tra rồi xóa file này nếu chắc chắn muốn đặt lại mốc."
        ) from exc
    if (
        not isinstance(data, dict)
        or data.get("version") != STATE_VERSION
        or not isinstance(data.get("main"), dict)
        or not isinstance(data.get("journal"), dict)
    ):
        raise GuardError(
            f"file trạng thái {f} sai định dạng (cần version={STATE_VERSION}). "
            "Kiểm tra rồi xóa file này nếu chắc chắn muốn đặt lại mốc."
        )
    return data


def save_state(
    gitdir: Path, main: dict[str, str], journal: dict[str, str], note: str
) -> None:
    data = {
        "version": STATE_VERSION,
        "updated_at": now_iso(),
        "note": note,
        "main": main,
        "journal": journal,
    }
    target = gitdir / STATE_FILE
    tmp = gitdir / (STATE_FILE + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    os.replace(
        tmp, target
    )  # ghi nguyên tử: không bao giờ để lại file trạng thái dở dang


def evaluate(root: Path, gitdir: Path):
    """Trả (state hoặc None, ảnh chụp file thường, ảnh chụp nhật ký, danh sách tồn đọng)."""
    cur_main, cur_journal = snapshot(root)
    state = load_state(gitdir)
    if state is None:
        return None, cur_main, cur_journal, []
    changed = changed_paths(state["main"], cur_main)
    if not changed:
        return state, cur_main, cur_journal, []
    touched = changed_paths(state["journal"], cur_journal)
    text = read_journals(root, touched)
    pending = [p for p in changed if not is_mentioned(p, text)]
    return state, cur_main, cur_journal, pending


def format_paths(paths: list[str]) -> str:
    shown = [f"- {p}" for p in paths[:MAX_LISTED]]
    if len(paths) > MAX_LISTED:
        shown.append(
            f"- ... và {len(paths) - MAX_LISTED} file khác (xem bằng lệnh status)"
        )
    return "\n".join(shown)


def pending_message(pending: list[str]) -> str:
    return (
        f"nhat_ky_guard: {len(pending)} file đã thay đổi kể từ lần xác nhận nhật ký gần nhất "
        "nhưng chưa được nêu trong file nhật ký nào vừa tạo hoặc sửa ở docs/nhat-ky/:\n"
        f"{format_paths(pending)}\n"
        "Quy ước của repo (CLAUDE.md, skill nhat-ky): mỗi thay đổi và mỗi bug được ghi vào nhật ký "
        "theo task, nêu đúng đường dẫn file (hoặc thư mục cha sâu từ 2 cấp, có dấu / cuối). "
        "Cần tạo hoặc cập nhật nhật ký của task tương ứng bằng skill nhat-ky trước khi kết thúc lượt. "
        "Nếu trong danh sách có file Pan tự sửa ngoài Claude, ghi rõ điều đó hoặc hỏi Pan."
    )


def repo_from(payload: dict) -> tuple[Path, Path]:
    start = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return find_repo(start)


def handle_stop(payload: dict) -> dict:
    root, gitdir = repo_from(payload)
    state, cur_main, cur_journal, pending = evaluate(root, gitdir)
    if state is None:
        save_state(gitdir, cur_main, cur_journal, "khởi tạo mốc (Stop)")
        return {
            "systemMessage": "nhat_ky_guard: đã khởi tạo mốc theo dõi nhật ký cho repo này "
            "(thay đổi có từ trước mốc không bị tính)."
        }
    if not pending:
        save_state(
            gitdir, cur_main, cur_journal, "xác nhận: mọi thay đổi đã có nhật ký"
        )
        return {}
    if payload.get("stop_hook_active"):
        # Đã nhắc trong lượt này mà vẫn thiếu: cho dừng để tránh vòng lặp, KHÔNG xác nhận mốc,
        # nên lượt sau và phiên sau sẽ nhắc lại.
        return {
            "systemMessage": f"⚠ nhat_ky_guard: còn {len(pending)} file thay đổi chưa có trong "
            f"nhật ký (docs/nhat-ky/), ví dụ {pending[0]}. Claude đã được nhắc trong lượt này; "
            "lượt sau sẽ nhắc lại. Xem: python3 .claude/hooks/nhat_ky_guard.py status"
        }
    return {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": pending_message(pending),
        }
    }


def handle_session_start(payload: dict) -> dict:
    root, gitdir = repo_from(payload)
    state, cur_main, cur_journal, pending = evaluate(root, gitdir)
    if state is None:
        save_state(gitdir, cur_main, cur_journal, "khởi tạo mốc (SessionStart)")
        return {
            "systemMessage": "nhat_ky_guard: đã khởi tạo mốc theo dõi nhật ký cho repo này "
            "(thay đổi có từ trước mốc không bị tính)."
        }
    if not pending:
        save_state(gitdir, cur_main, cur_journal, "xác nhận khi mở phiên")
        return {}
    return {
        "systemMessage": f"⚠ nhat_ky_guard: còn {len(pending)} file thay đổi từ trước chưa có "
        "trong nhật ký. Xem: python3 .claude/hooks/nhat_ky_guard.py status",
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": pending_message(pending),
        },
    }


def run_hook(mode: str) -> int:
    handlers = {"stop": handle_stop, "session-start": handle_session_start}
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise GuardError("input của hook không phải JSON object")
        out = handlers[mode](payload)
    except Exception as exc:  # noqa: BLE001
        # Cố ý bắt rộng: lỗi của hook không được làm treo phiên làm việc. Nhưng không nuốt:
        # traceback vào stderr (debug log) và cảnh báo hiện cho Pan.
        traceback.print_exc(file=sys.stderr)
        out = {
            "systemMessage": f"⚠ nhat_ky_guard lỗi ({type(exc).__name__}): {exc}. "
            "Nhật ký KHÔNG được kiểm tra ở bước này."
        }
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


def run_status() -> int:
    root, gitdir = find_repo(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    state, _main, _journal, pending = evaluate(root, gitdir)
    if state is None:
        print("Chưa có mốc theo dõi (hook chưa chạy lần nào trong repo này).")
        return 0
    print(f"Mốc cập nhật lúc {state.get('updated_at')} ({state.get('note')}).")
    if not pending:
        print("Không có thay đổi nào thiếu nhật ký.")
        return 0
    print(f"{len(pending)} file thay đổi chưa có trong nhật ký:")
    for p in pending:
        print(f"- {p}")
    return 1


def run_ack(reason: str) -> int:
    if not reason.strip():
        print('Thiếu lý do. Dùng: nhat_ky_guard.py ack "lý do"', file=sys.stderr)
        return 2
    root, gitdir = find_repo(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    state, cur_main, cur_journal, pending = evaluate(root, gitdir)
    entry = {
        "at": now_iso(),
        "reason": reason.strip(),
        "accepted_without_journal": pending,
    }
    with open(gitdir / LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    save_state(gitdir, cur_main, cur_journal, f"ack: {reason.strip()}")
    print(
        f"Đã chấp nhận {len(pending)} file không cần nhật ký. Log: {gitdir / LOG_FILE}"
    )
    return 0


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else ""
    if mode in ("stop", "session-start"):
        return run_hook(mode)
    try:
        if mode == "status":
            return run_status()
        if mode == "ack":
            return run_ack(" ".join(argv[2:]))
    except GuardError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
