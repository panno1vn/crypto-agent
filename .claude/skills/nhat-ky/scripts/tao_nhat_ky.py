#!/usr/bin/env python3
"""Tạo file nhật ký cho một task: docs/nhat-ky/YYYY-MM-DD_<loai>_<slug>.md, từ template.

Dùng (chạy ở gốc repo):
  python3 .claude/skills/nhat-ky/scripts/tao_nhat_ky.py --loai thay-doi --tieu-de "Tiêu đề task"
  python3 .claude/skills/nhat-ky/scripts/tao_nhat_ky.py --loai bug --tieu-de "Mô tả bug" --slug ten-ngan

Không bao giờ ghi đè: file đã tồn tại thì báo lỗi (exit 1), để cập nhật file của task đó
thay vì tạo bản trùng. In ra đường dẫn file vừa tạo (tương đối với gốc repo).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

LOAI = ("thay-doi", "bug")
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
JOURNAL_DIR = Path("docs") / "nhat-ky"


def slugify(text: str, max_len: int = 60) -> str:
    """Tiêu đề tiếng Việt -> slug ASCII không dấu, nối bằng '-'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", text)
    no_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    ascii_text = no_marks.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    if not slug:
        raise ValueError(f"không tạo được slug từ {text!r}; truyền --slug")
    return slug


def git_out(repo: Path, *args: str) -> str:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        raise SystemExit(
            f"Lỗi: `git {' '.join(args)}` thất bại: {stderr.strip() or exc}"
        )
    return res.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tạo file nhật ký theo task từ template.")
    ap.add_argument("--loai", required=True, choices=LOAI)
    ap.add_argument(
        "--tieu-de", required=True, help="tiêu đề task, tiếng Việt có dấu được"
    )
    ap.add_argument("--slug", help="tùy chọn: slug tự đặt (mặc định suy từ tiêu đề)")
    ap.add_argument(
        "--repo", default=".", help="thư mục bất kỳ trong repo (mặc định: hiện tại)"
    )
    args = ap.parse_args(argv)

    repo = Path(git_out(Path(args.repo), "rev-parse", "--show-toplevel"))
    now = datetime.now().astimezone()
    ngay = now.strftime("%Y-%m-%d")
    try:
        slug = slugify(args.slug or args.tieu_de)
    except ValueError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 2

    out_dir = repo / JOURNAL_DIR
    path = out_dir / f"{ngay}_{args.loai}_{slug}.md"
    if path.exists():
        print(
            f"Lỗi: {path.relative_to(repo)} đã tồn tại. Nếu là task đang tiếp diễn thì cập nhật "
            "file đó; nếu là task khác thì truyền --slug khác.",
            file=sys.stderr,
        )
        return 1

    template_file = TEMPLATE_DIR / f"{args.loai}.md"
    if not template_file.is_file():
        print(f"Lỗi: không thấy template {template_file}", file=sys.stderr)
        return 2
    values = {
        "ngay": ngay,
        "gio": now.strftime("%H:%M %z"),
        "tieu_de": args.tieu_de.strip().replace('"', "'"),
        "nhanh": git_out(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": git_out(repo, "rev-parse", "--short", "HEAD"),
    }
    content = template_file.read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", value)
    leftover = sorted(set(re.findall(r"\{\{[a-z_]+\}\}", content)))
    if leftover:
        print(f"Lỗi: template còn placeholder chưa thay: {leftover}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(path.relative_to(repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
