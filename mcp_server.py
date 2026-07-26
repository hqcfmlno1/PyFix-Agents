"""
Các tool:
  • read_file               — đọc nội dung file có đánh số dòng
  • list_directory          — hiển thị cây thư mục
  • get_file_context        — đọc file chính + các import local
  • run_python_syntax_check — kiểm tra cú pháp py_compile
  • search_in_codebase      — tìm kiếm text trong codebase

Human approval (write / run command) được xử lý phía client.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import subprocess
import sys
from typing import Optional
from mcp.server.fastmcp import FastMCP

# ─────────────────────────────────────────────────────────────────────────────
server = FastMCP(
    "PyFix-MCP-Server",
    instructions=(
        "Đây là MCP Server của PyFix-Agents. "
        "Cung cấp các tool đọc file và kiểm tra cú pháp Python. "
        "Dùng các tool này để đọc và phân tích codebase trước khi đề xuất bản fix."
    ),
)


# TOOL 1 — read_file
@server.tool()
def read_file(file_path: str) -> dict:
    """
    Đọc nội dung của một file (Python hoặc text).
    Trả về nội dung gốc và phiên bản có đánh số dòng.

    Args:
        file_path: Đường dẫn đến file cần đọc (tuyệt đối hoặc tương đối).
    """
    try:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File không tồn tại: {abs_path}"}
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"Không phải file: {abs_path}"}

        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()

        lines = content.splitlines()
        numbered = "\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))

        return {
            "success": True,
            "file_path": abs_path,
            "content": content,
            "content_with_line_numbers": numbered,
            "line_count": len(lines),
            "size_bytes": os.path.getsize(abs_path),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}



# TOOL 2 — list_directory
@server.tool()
def list_directory(
    dir_path: str,
    max_depth: int = 3,
    include_extensions: str = ".py,.txt,.json,.yaml,.yml,.toml,.md,.cfg,.ini,.env",
) -> str:
    """
    Liệt kê cấu trúc thư mục dưới dạng cây ASCII.

    Args:
        dir_path: Đường dẫn đến thư mục cần liệt kê.
        max_depth: Độ sâu tối đa của cây (mặc định 3).
        include_extensions: Danh sách extension hiển thị, phân cách bằng dấu phẩy.
    """
    dir_path = os.path.abspath(dir_path)
    if not os.path.isdir(dir_path):
        return f"Lỗi: Thư mục không tồn tại: {dir_path}"

    exts = {e.strip() for e in include_extensions.split(",")}
    skip_dirs = {
        ".git", "__pycache__", ".venv", "venv", "env",
        "node_modules", ".pytest_cache", ".mypy_cache",
        "dist", "build", ".eggs", ".tox", ".idea", ".vscode",
    }
    always_show = {".env", ".gitignore", "requirements.txt", "Makefile", "Dockerfile"}

    lines = [f"📁 {os.path.basename(dir_path)}/"]

    def walk(path: str, prefix: str = "", depth: int = 0) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(
                os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())
            )
        except PermissionError:
            return

        entries = [e for e in entries if not (e.is_dir() and e.name in skip_dirs)]

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            ext_prefix = "    " if is_last else "│   "

            if entry.is_dir():
                lines.append(f"{prefix}{connector}📁 {entry.name}/")
                walk(entry.path, prefix + ext_prefix, depth + 1)
            else:
                _, ext = os.path.splitext(entry.name)
                if ext in exts or entry.name in always_show:
                    size = entry.stat().st_size
                    size_str = f" ({size}B)" if size < 10_000 else f" ({size // 1024}KB)"
                    lines.append(f"{prefix}{connector}📄 {entry.name}{size_str}")

    walk(dir_path)
    return "\n".join(lines)



# TOOL 3 — get_file_context
@server.tool()
def get_file_context(file_path: str, repo_path: str, max_extra_files: int = 4) -> dict:
    """
    Đọc file chính và tự động phát hiện + đọc các module Python local được import.
    Kỹ thuật Selective File Loading — tối đa 5 file (1 chính + max_extra_files phụ).

    Args:
        file_path: File cần phân tích.
        repo_path: Đường dẫn gốc của dự án (để tìm module local).
        max_extra_files: Số file import phụ tối đa (mặc định 4).
    """
    file_path = os.path.abspath(file_path)
    repo_path = os.path.abspath(repo_path)
    collected: dict[str, str] = {}
    errors: list[str] = []

    def safe_read(fp: str) -> Optional[str]:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except Exception as exc:
            errors.append(f"Không đọc được {fp}: {exc}")
            return None

    # ── Đọc file chính ──────────────────────────────────────────────────────
    main_content = safe_read(file_path)
    if main_content is None:
        return {
            "success": False,
            "error": f"Không đọc được file chính: {file_path}",
            "errors": errors,
        }
    collected[file_path] = main_content

    # ── Parse imports để tìm module local ────────────────────────────────────
    local_imports: list[str] = []
    try:
        tree = ast.parse(main_content)
        search_bases = [repo_path, os.path.dirname(file_path)]

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod_name = node.module.split(".")[0]
                for base in search_bases:
                    candidate = os.path.join(base, f"{mod_name}.py")
                    if os.path.isfile(candidate) and candidate != file_path:
                        local_imports.append(candidate)
                        break
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod_name = alias.name.split(".")[0]
                    for base in search_bases:
                        candidate = os.path.join(base, f"{mod_name}.py")
                        if os.path.isfile(candidate) and candidate != file_path:
                            local_imports.append(candidate)
                            break
    except SyntaxError:
        errors.append(
            "File có lỗi cú pháp, không parse được imports (vẫn đọc được file chính)."
        )

    # ── Đọc các file phụ (dedup, giới hạn) ──────────────────────────────────
    seen_imports: list[str] = []
    for imp_path in dict.fromkeys(local_imports):  # preserve order, dedup
        if imp_path not in collected and len(collected) - 1 < max_extra_files:
            content = safe_read(imp_path)
            if content is not None:
                collected[imp_path] = content
                seen_imports.append(imp_path)

    return {
        "success": True,
        "main_file": file_path,
        "files": collected,
        "imported_local_files": seen_imports,
        "total_files_read": len(collected),
        "errors": errors,
    }



# TOOL 4 — run_python_syntax_check
@server.tool()
def run_python_syntax_check(file_path: str) -> dict:
    """
    Kiểm tra cú pháp Python bằng py_compile.
    Chỉ đọc/kiểm tra — KHÔNG ghi, KHÔNG sửa file.

    Args:
        file_path: Đường dẫn đến file Python cần kiểm tra.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        return {"passed": False, "error": f"File không tồn tại: {abs_path}"}

    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", abs_path],
        capture_output=True,
        text=True,
    )

    if proc.returncode == 0:
        return {
            "passed": True,
            "file_path": abs_path,
            "message": "✅ Cú pháp hợp lệ, không có lỗi.",
        }

    error_msg = (proc.stderr or proc.stdout).strip()
    return {
        "passed": False,
        "file_path": abs_path,
        "error": error_msg,
    }



# TOOL 5 — search_in_codebase
@server.tool()
def search_in_codebase(
    repo_path: str,
    query: str,
    file_pattern: str = "*.py",
    max_results: int = 30,
) -> list:
    """
    Tìm kiếm text/pattern trong toàn bộ codebase (case-insensitive).
    Hữu ích khi cần tìm nơi định nghĩa hàm hoặc biến liên quan đến lỗi.

    Args:
        repo_path: Đường dẫn đến thư mục dự án.
        query: Chuỗi cần tìm kiếm.
        file_pattern: Pattern file (mặc định '*.py').
        max_results: Số kết quả tối đa trả về (mặc định 30).
    """
    repo_path = os.path.abspath(repo_path)
    results: list[dict] = []
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules"}
    query_lower = query.lower()

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if fnmatch.fnmatch(fname, file_pattern):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        for line_no, line in enumerate(fh, start=1):
                            if query_lower in line.lower():
                                results.append(
                                    {
                                        "file": fpath,
                                        "line_number": line_no,
                                        "line_content": line.rstrip(),
                                    }
                                )
                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue



# TOOL 6 — run_project_tests
@server.tool()
def run_project_tests(repo_path: str, target_file: str = "") -> dict:
    """
    Tự động chạy test suite (pytest/unittest) hoặc thực thi file Python để kiểm tra kết quả.

    Args:
        repo_path: Đường dẫn gốc dự án.
        target_file: File Python cần chạy kiểm tra (nếu có).
    """
    abs_repo = os.path.abspath(repo_path)
    abs_target = os.path.abspath(os.path.join(abs_repo, target_file)) if target_file else ""

    # 1. Tìm file test trong repo
    test_files = []
    for root, dirs, files in os.walk(abs_repo):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
        for f in files:
            if (f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py"):
                test_files.append(os.path.join(root, f))

    # Nếu có file test -> chạy pytest
    if test_files:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", abs_repo, "-v"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            passed = proc.returncode == 0
            return {
                "test_type": "pytest",
                "passed": passed,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "message": "✅ Pytest đã vượt qua thành công." if passed else "❌ Pytest thất bại.",
            }
        except Exception as exc:
            return {"test_type": "pytest", "passed": False, "error": str(exc)}

    # 2. Nếu không có file test riêng, chạy trực tiếp file target_file bằng Python
    if abs_target and os.path.isfile(abs_target):
        try:
            proc = subprocess.run(
                [sys.executable, abs_target],
                capture_output=True,
                text=True,
                timeout=15,
            )
            passed = proc.returncode == 0
            return {
                "test_type": "script_execution",
                "passed": passed,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "message": "✅ Script thực thi thành công." if passed else "❌ Script bị lỗi runtime.",
            }
        except Exception as exc:
            return {"test_type": "script_execution", "passed": False, "error": str(exc)}

    return {
        "test_type": "none",
        "passed": True,
        "message": "Không tìm thấy file test hoặc script để thực thi.",
    }


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server.run(transport='streamable-http')

