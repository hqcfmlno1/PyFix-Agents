"""
Helper functions — UI printing, diff, project tree, file operations.
"""

from __future__ import annotations

import difflib
import os
from typing import List

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.models import PlanStep
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


def parse_delimiter_blocks(patch_text: str) -> list[tuple[str, str]]:
    """
    Dùng regex bóc tách tất cả cặp (search_block, replace_block)
    từ chuỗi định dạng Delimiter Blocks.
    Returns: list[tuple[search, replace]]
    """
    import re
    pattern = r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE"
    matches = re.findall(pattern, patch_text, re.DOTALL)
    return matches  # [(search1, replace1), (search2, replace2), ...]


def apply_delimiter_patch(file_content: str, patch_text: str) -> tuple[bool, str, List[str]]:
    """
    Áp dụng tuần tự tất cả delimiter blocks vào nội dung file.
    Dùng str.replace() thuần túy — không cần exact JSON escaping.

    Returns:
        (True, final_content, [])         nếu thành công
        (False, partial_content, errors)  nếu thất bại
    """
    blocks = parse_delimiter_blocks(patch_text)
    if not blocks:
        return False, file_content, ["Không tìm thấy block SEARCH/REPLACE nào trong patch_blocks"]

    current = file_content
    errors: List[str] = []

    for i, (search, replace) in enumerate(blocks, start=1):
        occurrences = current.count(search)
        if occurrences == 0:
            errors.append(
                f"Block #{i}: NOT_FOUND — search_block không khớp với nội dung file.\n"
                f"  search_block gửi lên:\n{search!r}"
            )
            return False, current, errors
        if occurrences > 1:
            errors.append(
                f"Block #{i}: AMBIGUOUS — search_block xuất hiện {occurrences} lần. "
                f"Cần thêm context để xác định duy nhất vị trí cần sửa."
            )
            return False, current, errors
        current = current.replace(search, replace, 1)

    return True, current, []

def count_tool_calls(messages: list) -> int:
    """Đếm số lượng ToolCallPart trong danh sách các messages từ LLM."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    count = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in getattr(msg, 'parts', []):
                if isinstance(part, ToolCallPart):
                    count += 1
    return count


def print_agent_thinking(result, agent_name: str = "AGENT") -> None:
    """
    Duyệt result.all_messages() và in ThinkingPart / TextPart xuất hiện
    trong cùng turn với ToolCallPart (tức là suy nghĩ trước khi gọi tool).
    Gọi hàm này ngay SAU agent.run() để hiển thị Chain-of-Thought.
    """
    try:
        from pydantic_ai.messages import ModelResponse, ToolCallPart, TextPart
        try:
            from pydantic_ai.messages import ThinkingPart
        except ImportError:
            ThinkingPart = None

        from graph.config import BOLD, MAGENTA, RESET

        for msg in result.all_messages():
            if not isinstance(msg, ModelResponse):
                continue
            parts = getattr(msg, 'parts', [])
            has_tool_call = any(isinstance(p, ToolCallPart) for p in parts)
            if not has_tool_call:
                continue

            for part in parts:
                if ThinkingPart and isinstance(part, ThinkingPart) and part.content.strip():
                    print(f"\n  {BOLD}{MAGENTA}🧠 [{agent_name}] SUY NGHĨ (Native Thinking):{RESET}")
                    # Giới hạn 1000 ký tự để không làm rối terminal
                    content = part.content.strip()
                    if len(content) > 1000:
                        content = content[:1000] + "\n  ...(truncated)"
                    print(f"  {MAGENTA}{content}{RESET}")
                elif isinstance(part, TextPart) and part.content.strip():
                    print(f"\n  {BOLD}{MAGENTA}💬 [{agent_name}] LÝ DO:{RESET}")
                    content = part.content.strip()
                    if len(content) > 500:
                        content = content[:500] + "\n  ...(truncated)"
                    print(f"  {MAGENTA}{content}{RESET}")
    except Exception:
        pass  # Không để lỗi UX phá vỡ luồng chính

