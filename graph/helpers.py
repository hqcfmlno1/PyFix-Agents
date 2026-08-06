"""
Helper functions — UI printing, diff, project tree, file operations.
"""

from __future__ import annotations

import difflib
import os
from typing import List

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.models import PatchHunk, PlanStep
from typing import Optional

def _count_lines(filepath: str) -> Optional[int]:
    """Đếm số dòng của file. Trả về None nếu là file binary hoặc lỗi."""
    try:
        with open(filepath, 'rb') as f:
            chunk = f.read(1024)
            if b'\x00' in chunk:
                return None
            f.seek(0)
            return sum(1 for _ in f)
    except Exception:
        return None


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
                    size_str = f"{size}B" if size < 10_000 else f"{size // 1024}KB"
                    
                    line_count = _count_lines(entry.path)
                    lines_str = f", {line_count} lines" if line_count is not None else ""
                    
                    lines.append(f"{prefix}{conn}📄 {entry.name} ({size_str}{lines_str})")

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


def apply_hunk(file_content: str, hunk: PatchHunk) -> tuple[bool, str]:
    """
    Áp dụng một PatchHunk vào nội dung file bằng cơ chế Search-and-Replace.

    Returns:
        (True, new_content)  nếu thành công.
        (False, error_msg)   nếu thất bại:
            - NOT_FOUND   : old_lines không tồn tại trong file.
            - AMBIGUOUS   : old_lines xuất hiện nhiều lần (thiếu context).
    """
    occurrences = file_content.count(hunk.old_lines)

    if occurrences == 0:
        return False, (
            f"NOT_FOUND: old_lines không khớp với nội dung file hiện tại.\n"
            f"  old_lines gửi lên:\n{hunk.old_lines!r}"
        )

    if occurrences > 1:
        return False, (
            f"AMBIGUOUS: old_lines xuất hiện {occurrences} lần trong file. "
            f"Cần thêm context để xác định duy nhất vị trí cần sửa."
        )

    new_content = file_content.replace(hunk.old_lines, hunk.new_lines, 1)
    return True, new_content


def apply_all_hunks(original_content: str, hunks: List[PatchHunk]) -> tuple[bool, str, List[str]]:
    """
    Áp dụng tuần tự danh sách PatchHunk vào nội dung file.
    Mỗi hunk được áp dụng lên kết quả của hunk trước (nội dung đã được cập nhật).

    Returns:
        (success, final_content_or_partial, errors)
        - Nếu thành công: (True, nội_dung_sau_patch, [])
        - Nếu có lỗi   : (False, nội_dung_tại_thời_điểm_lỗi, [danh_sách_lỗi])
    """
    if not hunks:
        return True, original_content, []

    current = original_content
    errors: List[str] = []

    for i, hunk in enumerate(hunks, start=1):
        success, result = apply_hunk(current, hunk)
        if success:
            current = result
        else:
            errors.append(f"Hunk #{i} thất bại: {result}")
            # Dừng sớm khi gặp lỗi để không áp dụng các hunk phụ thuộc sai lên trên
            return False, current, errors

    return True, current, errors

