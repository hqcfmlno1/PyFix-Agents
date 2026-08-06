"""
InputAnalyzerNode — Dùng LLM phân loại lỗi Unhandled Runtime và trích xuất stack_trace có cấu trúc.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import input_analyzer_agent
from graph.config import BOLD, CYAN, ANALYZER_MODEL_NAME, RED, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugFixState, BugReport

if TYPE_CHECKING:
    from graph.nodes.input_guardrail import InputGateGuardrailNode


@dataclass
class InputAnalyzerNode(BaseNode[BugFixState]):
    """
    [Agent] Nhận log traceback từ user, lọc nhiễu và băm thành stack_trace có cấu trúc.
    Chỉ trích xuất dữ liệu kỹ thuật — KHÔNG giải thích nguyên nhân.
    Việc chẩn đoán root cause được để PlanningNode thực hiện sau khi đọc code thực tế.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> InputGateGuardrailNode:
        from graph.nodes.input_guardrail import InputGateGuardrailNode

        print(f"\n{'─'*60}")
        print_step("📝", "Input Analyzer", "Nhập mô tả lỗi hoặc dán log traceback từ terminal.")

        print(f"""
  {BOLD}Hướng dẫn nhập:{RESET}
  • {CYAN}Dán traceback log{RESET} từ terminal (KeyError, IndexError, TypeError, AttributeError...)
  
  {YELLOW}Tip:{RESET} Thêm "tôi muốn xem plan" nếu bạn muốn duyệt kế hoạch trước khi fix.
  {YELLOW}Tip:{RESET} Nhập 'quit' để thoát.
""")

        print(f"{BOLD}Mô tả lỗi / Traceback log (Nhấn Enter 2 lần liên tiếp để kết thúc nhập):{RESET}")
        
        lines = []
        empty_count = 0
        while True:
            try:
                line = input()
                if line.strip().lower() == "quit" and not lines:
                    sys.exit(0)
                
                if not line.strip():
                    empty_count += 1
                    if empty_count >= 2:
                        break
                else:
                    empty_count = 0
                
                lines.append(line)
            except (EOFError, KeyboardInterrupt):
                if not lines:
                    sys.exit(0)
                break
        
        print("\n")  # Xuống dòng sau khi paste xong để tách biệt UI
        
        raw_input = "\n".join(lines).strip()

        if ctx.state.missing_fields and ctx.state.raw_user_input:
            combined = ctx.state.raw_user_input + "\n\nThông tin bổ sung:\n" + raw_input
            ctx.state.raw_user_input = combined
        else:
            ctx.state.raw_user_input = raw_input

        print_step("🤖", "Input Analyzer Agent", f"Đang phân tích traceback & trích xuất Call Stack với {ANALYZER_MODEL_NAME}...")

        prompt = f"""Phân tích Traceback log sau, lọc nhiễu thư viện ngoài và trích xuất stack_trace:

---
{ctx.state.raw_user_input}
---

Cấu trúc dự án để đối chiếu đường dẫn file tương đối:
{ctx.state.project_tree[:1500]}
"""

        result = await input_analyzer_agent.run(prompt)
        bug_report: BugReport = result.output

        # Cập nhật State
        ctx.state.bug_types = bug_report.bug_types
        ctx.state.error_class = bug_report.error_class
        ctx.state.error_message = bug_report.error_message
        ctx.state.stack_trace = bug_report.stack_trace
        ctx.state.target_file = bug_report.target_file
        ctx.state.error_file = bug_report.target_file
        ctx.state.error_line = bug_report.error_line
        ctx.state.runtime_input_data = bug_report.runtime_input_data
        ctx.state.want_plan = bug_report.want_plan
        # root_cause_explanation không đặt ở đây — sẽ được PlanningNode điền sau khi đọc code thực tế

        # Nếu target_file chưa được xác định nhưng stack_trace có phần tử crash_point
        if not ctx.state.target_file and bug_report.stack_trace:
            for frame in reversed(bug_report.stack_trace):
                if frame.role == "crash_point" or not ctx.state.target_file:
                    ctx.state.target_file = frame.file_path
                    ctx.state.error_line = frame.line_number
                    break

        types_str = ", ".join([bt.value.upper() for bt in bug_report.bug_types]) if bug_report.bug_types else "UNKNOWN"
        print_step("✅", "Phân tích xong", f"Loại lỗi: {CYAN}{types_str}{RESET}")
        if bug_report.error_class:
            msg_str = f": {bug_report.error_message}" if bug_report.error_message else ""
            print(f"     Exception    : {RED}{bug_report.error_class}{msg_str}{RESET}")
        if bug_report.stack_trace:
            print(f"     Call Stack   : {len(bug_report.stack_trace)} frames trong project:")
            for frame in bug_report.stack_trace:
                role_icon = "🔴 [Crash Point]" if frame.role == "crash_point" else "🔹 [Caller]"
                print(f"       • {role_icon} {frame.file_path}:{frame.line_number} (in {frame.function_name or 'main'})")

        return InputGateGuardrailNode()
