"""
Helper functions — UI printing, diff, project tree, file operations.
"""

from __future__ import annotations

import difflib
import os
from typing import List

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.models import PatchHunk, PlanStep


def print_header(title: str) -> None:
    """In tiêu đề đóng khung."""
    bar = "═" * 58
    print(f"\n{CYAN}{BOLD}╔{bar}╗")
    print(f"║  {title:<56}║")
    print(f"╚{bar}╝{RESET}")


def print_step(icon: str, label: str, msg: str = "") -> None:
    """In một bước xử lý."""
    print(f"{BOLD}{icon} [{label}]{RESET} {msg}")


def build_project_tree(repo_path: str, max_depth: int = 3) -> str:
    """Xây dựng cây thư mục dự án."""
    skip_dirs = {
        ".git", "__pycache__", ".venv", "venv", "env",
        "node_modules", ".pytest_cache", ".mypy_cache",
        "dist", "build", ".eggs", ".tox",
    }
    show_exts = {".py", ".txt", ".json", ".yaml", ".yml", ".toml", ".md", ".cfg", ".ini", ".env"}
    always_show = {".env", ".gitignore", "requirements.txt", "Makefile", "Dockerfile"}

    lines = [f"📁 {os.path.basename(repo_path)}/"]

    def walk(path: str, prefix: str = "", depth: int = 0) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries if not (e.is_dir() and e.name in skip_dirs)]
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            conn = "└── " if is_last else "├── "
            ext_p = "    " if is_last else "│   "
            if entry.is_dir():
                lines.append(f"{prefix}{conn}📁 {entry.name}/")
                walk(entry.path, prefix + ext_p, depth + 1)
            else:
                _, ext = os.path.splitext(entry.name)
                if ext in show_exts or entry.name in always_show:
                    size = entry.stat().st_size
                    sz = f" ({size}B)" if size < 10_000 else f" ({size // 1024}KB)"
                    lines.append(f"{prefix}{conn}📄 {entry.name}{sz}")

    walk(repo_path)
    return "\n".join(lines)


def format_plan(plan: List[PlanStep]) -> str:
    """Hiển thị plan dưới dạng bảng."""
    if not plan:
        return "  (Chưa có plan)"
    lines = []
    for step in plan:
        lines.append(f"  {BOLD}Bước {step.step_id}: {step.title}{RESET}")
        lines.append(f"    Mô tả   : {step.description}")
        lines.append(f"    File    : {step.target_file}")
        if step.target_lines:
            lines.append(f"    Dòng    : {step.target_lines}")
        if step.acceptance_criteria:
            lines.append(f"    Nghiệm thu: {step.acceptance_criteria}")
    return "\n".join(lines)


def compute_diff(original: str, new_content: str, filename: str) -> List[str]:
    """Tạo unified diff giữa 2 nội dung file."""
    return list(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    ))


def print_diff(diff_lines: List[str]) -> None:
    """In diff có màu sắc."""
    if not diff_lines:
        print(f"  {YELLOW}⚠ Không có thay đổi trong diff.{RESET}")
        return
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            print(f"{BOLD}{line}{RESET}")
        elif line.startswith("+"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{CYAN}{line}{RESET}")
        else:
            print(line)


def resolve_target_path(target_file: str, repo_path: str) -> str:
    """Chuyển đổi path tương đối thành tuyệt đối dựa trên repo."""
    if os.path.isabs(target_file):
        return target_file
    return os.path.join(repo_path, target_file)


def load_file_content(path: str) -> str:
    """Đọc nội dung file, trả về chuỗi rỗng nếu không tồn tại."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


def apply_hunks(original_content: str, hunks: List[PatchHunk]) -> str:
    """
    Áp dụng danh sách PatchHunk vào nội dung file gốc (Chunk-Based Patching).

    Thuật toán:
    1. Tách file gốc thành danh sách dòng.
    2. Sắp xếp hunks theo start_line GIẢM DẦN — áp từ dưới lên trên để tránh lệch offset.
    3. Thay thế lines[start_line-1 : end_line] bằng new_lines.splitlines().
    4. Join lại thành chuỗi đầy đủ và trả về.

    Args:
        original_content: Nội dung gốc của file.
        hunks: Danh sách PatchHunk cần áp dụng.

    Returns:
        Nội dung mới sau khi áp dụng tất cả hunks.
    """
    if not hunks:
        return original_content

    lines = original_content.splitlines(keepends=True)

    # Thêm newline cho dòng cuối nếu file không kết thúc bằng newline
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    # Sắp xếp GIẢM DẦN theo start_line: áp hunk phía dưới trước, tránh lệch offset
    sorted_hunks = sorted(hunks, key=lambda h: h.start_line, reverse=True)

    for hunk in sorted_hunks:
        # Clamp về phạm vi hợp lệ
        start = max(1, hunk.start_line)
        end = min(len(lines), hunk.end_line)

        # Chuẩn hóa new_lines: đảm bảo mỗi dòng có newline kết thúc
        new_line_list = hunk.new_lines.splitlines()
        normalized: List[str] = [ln + "\n" for ln in new_line_list]

        # Thay thế slice
        lines[start - 1 : end] = normalized

    return "".join(lines)
