"""
PyFix MCP Server v2
Tools:
  • read_file            — đọc file Python (hỗ trợ đọc theo khoảng dòng) + liệt kê symbols
  • write_file           — đề xuất ghi code mới (ghi tạm, chờ hệ thống duyệt)
  • list_dir             — liệt kê cấu trúc thư mục
  • run_linter           — chạy py_compile kiểm tra cú pháp
  • search_in_codebase   — tìm kiếm text/pattern trong toàn bộ codebase
  • run_command          — thực thi lệnh shell để tái hiện hoặc kiểm tra lỗi
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ─────────────────────────────────────────────────────────────────────────────
server = FastMCP(
    "PyFix-MCP-Server",
    instructions=(
        "MCP Server của PyFix-Agents v2. "
        "Cung cấp tool đọc/ghi file, liệt kê thư mục và kiểm tra cú pháp Python."
    ),
)

BLOCKED_SEGMENTS = {"site-packages", ".venv", "venv", "env"}


def _is_blocked_path(path: str) -> bool:
    """Không cho phép đọc/ghi file trong venv hoặc site-packages."""
    parts = os.path.normpath(path).split(os.sep)
    return bool(BLOCKED_SEGMENTS.intersection(parts))


def _extract_symbols(source: str) -> list[dict]:
    """Trích xuất danh sách function và class từ source code."""
    symbols: list[dict] = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                symbols.append({
                    "type": "function",
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                })
            elif isinstance(node, ast.ClassDef):
                symbols.append({
                    "type": "class",
                    "name": node.name,
                    "line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                })
    except SyntaxError:
        pass  # File có lỗi cú pháp thì bỏ qua phần symbol
    return symbols


# ── TOOL 1: read_file ────────────────────────────────────────────────────────
@server.tool()
def read_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """
    Đọc nội dung file Python. Nếu chỉ cần đọc một đoạn cụ thể,
    truyền start_line và end_line để tiết kiệm token.
    Kết quả trả về bao gồm nội dung code và danh sách tất cả
    các symbol (function, class) trong file kèm vị trí dòng của chúng.

    Args:
        path: Đường dẫn đến file .py cần đọc.
              Không được trỏ vào file trong /venv/ hoặc /site-packages/.
        start_line: Dòng bắt đầu đọc (1-indexed, tùy chọn).
        end_line: Dòng kết thúc đọc (1-indexed, tùy chọn).
                  Nếu không truyền start_line và end_line thì đọc toàn bộ file.
    """
    abs_path = os.path.abspath(path)

    if _is_blocked_path(abs_path):
        return {"success": False, "error": f"Không được đọc file trong venv/site-packages: {abs_path}"}
    if not os.path.exists(abs_path):
        return {"success": False, "error": f"File không tồn tại: {abs_path}"}
    if not os.path.isfile(abs_path):
        return {"success": False, "error": f"Không phải file: {abs_path}"}

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            full_content = fh.read()

        all_lines = full_content.splitlines()
        total_lines = len(all_lines)

        # Luôn trích xuất symbols từ toàn bộ file
        symbols = _extract_symbols(full_content)

        # Xác định khoảng dòng cần trả về
        s = max(1, start_line) if start_line else 1
        e = min(total_lines, end_line) if end_line else total_lines
        selected = all_lines[s - 1 : e]

        numbered = "\n".join(f"{s + i:4d} | {line}" for i, line in enumerate(selected))

        return {
            "success": True,
            "file_path": abs_path,
            "content": "\n".join(selected),
            "content_with_line_numbers": numbered,
            "total_lines": total_lines,
            "showing_lines": f"{s}-{e}",
            "symbols": symbols,
            "size_bytes": os.path.getsize(abs_path),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── TOOL 2: write_file ──────────────────────────────────────────────────────
@server.tool()
def write_file(
    path: str,
    content: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> dict:
    """
    Ghi code mới vào file. Thay đổi sẽ được lưu tạm trên đĩa —
    hệ thống sẽ backup bản gốc, kiểm tra, và yêu cầu người dùng
    duyệt trước khi chấp nhận vĩnh viễn.
    Nếu chỉ sửa một đoạn cụ thể, truyền start_line và end_line
    để chỉ thay đúng đoạn đó, giữ nguyên phần còn lại.

    Args:
        path: Đường dẫn file cần sửa.
        content: Code mới được đề xuất.
        start_line: Dòng bắt đầu thay thế (1-indexed, tùy chọn).
        end_line: Dòng kết thúc thay thế (1-indexed, tùy chọn).
                  Nếu không truyền start_line và end_line thì ghi đè toàn bộ file.
                  Nên lấy start_line và end_line từ kết quả của read_file.
    """
    abs_path = os.path.abspath(path)

    if _is_blocked_path(abs_path):
        return {"success": False, "error": f"Không được ghi file trong venv/site-packages: {abs_path}"}

    try:
        # Đọc nội dung cũ (nếu file tồn tại)
        old_content = ""
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                old_content = fh.read()

        # Tính nội dung mới
        if start_line is not None and end_line is not None and old_content:
            old_lines = old_content.splitlines()
            s = max(1, start_line) - 1  # convert to 0-indexed
            e = min(len(old_lines), end_line)
            new_lines = old_lines[:s] + content.splitlines() + old_lines[e:]
            new_content = "\n".join(new_lines)
            if old_content.endswith("\n"):
                new_content += "\n"
        else:
            new_content = content

        # Tạo thư mục nếu chưa tồn tại
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # Ghi tạm xuống đĩa
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)

        return {
            "success": True,
            "file_path": abs_path,
            "action": "partial_replace" if (start_line and end_line) else "full_overwrite",
            "message": f"Đã ghi tạm file: {abs_path} (chờ hệ thống duyệt).",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── TOOL 3: list_dir ────────────────────────────────────────────────────────
@server.tool()
def list_dir(path: str) -> str:
    """
    Liệt kê toàn bộ file và thư mục trong một đường dẫn.
    Dùng để hiểu cấu trúc project hoặc xác định đường dẫn
    đúng của file khi import statement không rõ ràng.

    Args:
        path: Đường dẫn thư mục cần liệt kê.
    """
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        return f"Lỗi: Thư mục không tồn tại: {abs_path}"

    skip_dirs = {
        ".git", "__pycache__", ".venv", "venv", "env",
        "node_modules", ".pytest_cache", ".mypy_cache",
        "dist", "build", ".eggs", ".tox", ".idea", ".vscode",
    }

    lines = [f"📁 {os.path.basename(abs_path)}/"]

    def walk(dir_path: str, prefix: str = "", depth: int = 0) -> None:
        if depth >= 3:
            return
        try:
            entries = sorted(
                os.scandir(dir_path), key=lambda e: (not e.is_dir(), e.name.lower())
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
                size = entry.stat().st_size
                size_str = f" ({size}B)" if size < 10_000 else f" ({size // 1024}KB)"
                lines.append(f"{prefix}{connector}📄 {entry.name}{size_str}")

    walk(abs_path)
    return "\n".join(lines)


# ── TOOL 4: run_linter ──────────────────────────────────────────────────────
@server.tool()
def run_linter(path: str) -> dict:
    """
    Chạy linter trên file Python để kiểm tra code vừa được
    gen ra có lỗi syntax hoặc lỗi tiềm ẩn cơ bản không.
    Agent chủ động gọi tool này ngay sau khi gen code mới
    để phát hiện và sửa lỗi ngớ ngẩn sớm, tránh đợi đến
    bước validate cuối mới phát hiện.

    Args:
        path: Đường dẫn đến file Python cần kiểm tra.
    """
    abs_path = os.path.abspath(path)
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



# ── TOOL 5: search_in_codebase ──────────────────────────────────────────────
@server.tool()
def search_in_codebase(
    repo_path: str,
    query: str,
    file_pattern: str = "*.py",
    max_results: int = 30,
) -> list:
    """
    Tìm kiếm text/pattern trong toàn bộ codebase (case-insensitive).
    Hữu ích khi cần tìm nơi định nghĩa hàm, biến, hoặc truy vết
    luồng data đi qua nhiều file sau khi đã chỉnh sửa.

    Args:
        repo_path: Đường dẫn đến thư mục gốc của dự án.
        query: Chuỗi cần tìm kiếm.
        file_pattern: Pattern file (mặc định '*.py').
        max_results: Số kết quả tối đa trả về (mặc định 30).
    """
    import fnmatch

    repo_path = os.path.abspath(repo_path)
    results: list[dict] = []
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".pytest_cache"}
    query_lower = query.lower()

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if fnmatch.fnmatch(fname, file_pattern):
                fpath = os.path.join(root, fname)
                if _is_blocked_path(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        for line_no, line in enumerate(fh, start=1):
                            if query_lower in line.lower():
                                results.append({
                                    "file": os.path.relpath(fpath, repo_path),
                                    "line_number": line_no,
                                    "line_content": line.rstrip(),
                                })
                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue

    return results


# ── TOOL 6: run_command ─────────────────────────────────────────────────────
@server.tool()
def run_command(
    command: str,
    cwd: str = ".",
    timeout: int = 30,
) -> dict:
    """
    Thực thi lệnh shell để tái hiện lỗi gốc hoặc kiểm tra bản fix có hiệu quả không.
    Coder Agent dùng tool này để xác nhận hành vi thực tế của code trước khi trả về hunks.

    Args:
        command: Lệnh shell cần thực thi (VD: 'python main.py', 'pytest tests/test_x.py').
        cwd: Thư mục làm việc (mặc định thư mục hiện tại).
        timeout: Thời gian chờ tối đa (giây, mặc định 30).
    """
    abs_cwd = os.path.abspath(cwd)
    if not os.path.isdir(abs_cwd):
        return {"success": False, "error": f"Thư mục không tồn tại: {abs_cwd}"}

    # Danh sách lệnh nguy hiểm bị chặn
    blocked_prefixes = ("rm ", "del ", "rmdir ", "format ", "mkfs", "dd ", "shutdown", "reboot")
    cmd_lower = command.strip().lower()
    if any(cmd_lower.startswith(b) for b in blocked_prefixes):
        return {"success": False, "error": f"Lệnh bị chặn vì lý do bảo mật: {command}"}

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=abs_cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": True,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "passed": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Lệnh chạy quá {timeout}s và bị hủy."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server.run(transport="streamable-http")
