"""
DirectFixCreationNode — Direct Fix Agent sửa trực tiếp lỗi đơn giản (1 frame) dùng Chunk-Based Patching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import direct_fix_agent
from graph.config import BOLD, CYAN, GREEN, MODEL_NAME, RED, RESET, YELLOW
from graph.helpers import apply_hunks, load_file_content, print_step, resolve_target_path
from graph.models import BugFixState, CodeFix, DirectFix, SingleFileFix

if TYPE_CHECKING:
    from graph.nodes.validation import ValidationNode


@dataclass
class DirectFixCreationNode(BaseNode[BugFixState]):
    """
    [Agent] Dùng DirectFix Agent sửa trực tiếp lỗi đơn giản (1 frame) không qua Plan nhiều bước.
    Cơ chế: Chunk-Based Patching — Agent chỉ đọc khoảng ±30 dòng xung quanh dòng crash và trả về PatchHunk.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> ValidationNode:
        from graph.nodes.validation import ValidationNode

        print_step("🚀", "Direct Fix Agent", f"Đang tạo bản sửa lỗi nhanh (Chunk-Based) với {MODEL_NAME}...")

        target_file = ctx.state.target_file or ctx.state.error_file or "main.py"
        abs_target_path = resolve_target_path(target_file, ctx.state.repo_path)

        # Xác định khoảng dòng cần đọc xung quanh điểm crash
        error_line = ctx.state.error_line or 1
        read_start = max(1, error_line - 30)
        read_end = error_line + 30

        prompt = f"""TẠO DIRECT FIX CHO LỖI ĐƠN GIẢN (CHUNK-BASED PATCHING):
- File bị lỗi    : {target_file}
- Exception Class: {ctx.state.error_class or 'N/A'}
- Exception Detail: {ctx.state.error_message or 'N/A'}
- Dòng crash     : {error_line}

HƯỚNG DẪN:
1. Dùng tool `read_file(path='{target_file}', start_line={read_start}, end_line={read_end})` để đọc đoạn mã nguồn xung quanh dòng crash (tiết kiệm token).
2. Phát hiện nguyên nhân gây ra {ctx.state.error_class or 'lỗi'} tại dòng {error_line}.
3. Xác định chính xác range [start_line, end_line] trong file GỐC cần thay thế.
4. Sinh `new_lines` sửa đúng lỗi, giữ nguyên indentation Python.
5. Dùng `run_linter` kiểm tra cú pháp. Nếu lỗi syntax, điều chỉnh new_lines.
6. Trả về DirectFix với danh sách `hunks` (KHÔNG trả về toàn bộ nội dung file).
"""

        try:
            # Đọc file gốc để backup và chuẩn bị apply_hunks
            original_content = load_file_content(abs_target_path)

            result = await direct_fix_agent.run(prompt)
            direct_fix: DirectFix = result.output
            ctx.state.direct_fix = direct_fix

            if not direct_fix.hunks:
                print_step("⚠", "Direct Fix", f"{YELLOW}Không có hunk nào được trả về.{RESET}")
                ctx.state.validation_errors.append("DirectFix: Không có hunk được trả về.")
                return ValidationNode()

            # Áp dụng Chunk-Based Patching
            patched_content = apply_hunks(original_content, direct_fix.hunks)

            # Ghi file đã patch để chuẩn bị cho ValidationNode
            with open(abs_target_path, "w", encoding="utf-8") as fh:
                fh.write(patched_content)

            # Đóng gói vào CodeFix để ValidationNode sử dụng thống nhất
            ctx.state.code_fix = CodeFix(
                files=[
                    SingleFileFix(
                        target_file=target_file,
                        hunks=direct_fix.hunks,
                        changes_summary=direct_fix.diff_summary or f"DirectFix: áp {len(direct_fix.hunks)} hunk(s)",
                    )
                ],
                explanation=direct_fix.explanation or "Đã sửa lỗi trực tiếp.",
            )
            ctx.state.want_apply = True

            print_step(
                "✅", "Direct Fix thành công",
                f"Đã áp {len(direct_fix.hunks)} hunk(s) vào {CYAN}{target_file}{RESET}"
            )
            if direct_fix.explanation:
                print(f"   💡 {direct_fix.explanation}")

        except Exception as exc:
            print_step("❌", "Direct Fix Error", f"{RED}Lỗi khi tạo DirectFix: {exc}{RESET}")
            ctx.state.validation_errors.append(f"DirectFix Error: {exc}")

        return ValidationNode()
