"""
InputAnalyzerNode — Dùng LLM phân loại lỗi Unhandled Runtime và trích xuất stack_trace có cấu trúc.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import input_analyzer_agent
from graph.config import BOLD, CYAN, ANALYZER_MODEL_NAME, RED, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugFixState, BugReport, BugComplexity, UserSentiment

if TYPE_CHECKING:
    from graph.nodes.input_guardrail import InputGateGuardrailNode
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode
    from graph.nodes.reproduction_plan import ReproductionPlanNode


@dataclass
class InputAnalyzerNode(BaseNode[BugFixState]):
    """
    [Agent] Nhận log traceback từ user, lọc nhiễu và băm thành stack_trace có cấu trúc.
    Chỉ trích xuất dữ liệu kỹ thuật — KHÔNG giải thích nguyên nhân.
    Việc chẩn đoán root cause được để PlanningNode thực hiện sau khi đọc code thực tế.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[InputGateGuardrailNode, PlanningNode, ReportNode, ReproductionPlanNode]:
        from graph.nodes.input_guardrail import InputGateGuardrailNode
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode
        from graph.nodes.reproduction_plan import ReproductionPlanNode

        # ── Dual-Purpose Hub: Kiểm tra xem đây là lần chạy đầu hay vòng lặp phản hồi
        if ctx.state.iteration_history:
            print(f"\n{'─'*60}")
            print_step("🧪", "Human-in-the-Loop", "Vui lòng chạy thử ứng dụng (runtime) và kiểm tra xem lỗi đã được khắc phục chưa.")
            print(f"""
  {BOLD}Hướng dẫn phản hồi:{RESET}
  • {GREEN}Nhập 'ok', 'done', 'yes'{RESET} nếu ứng dụng đã chạy tốt.
  • {RED}Dán lỗi mới hoặc giải thích lỗi còn tồn đọng{RESET} nếu bản vá chưa triệt để.
""")
            print(f"{BOLD}Phản hồi của bạn (Nhấn Enter 2 lần liên tiếp để kết thúc nhập):{RESET}")
            
            lines = []
            empty_count = 0
            while True:
                try:
                    line = input()
                    if not line.strip():
                        empty_count += 1
                        if empty_count >= 2:
                            break
                    else:
                        empty_count = 0
                    lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    break
            
            raw_input = "\n".join(lines).strip()
            
            # Simple heuristic để xác định HAPPY hay UNHAPPY
            happy_keywords = ["ok", "done", "yes", "y", "chạy ngon", "passed", "good", "tốt"]
            if raw_input.lower() in happy_keywords or raw_input.strip() == "":
                print_step("🎉", "Analyzer", f"{GREEN}Xác nhận bản vá chạy tốt! Kết thúc phiên.{RESET}")
                ctx.state.user_sentiment = UserSentiment.HAPPY
                ctx.state.validation_passed = True
                return ReportNode()
            else:
                ctx.state.user_sentiment = UserSentiment.UNHAPPY
                ctx.state.user_suggested_fix = raw_input
                ctx.state.complexity = BugComplexity.COMPLEX
                if ctx.state.iteration_history:
                    ctx.state.iteration_history[-1].user_feedback = raw_input
                
                # Reset repro state
                ctx.state.repro_confirmed = None
                ctx.state.repro_script_path = None
                ctx.state.repro_output = ""
                ctx.state.repro_retry_count = 0
                
                print_step("🔄", "Analyzer", f"{YELLOW}Lỗi chưa triệt để. Chuyển cấp (Escalation) sang bước Tái hiện lỗi...{RESET}")
                return ReproductionPlanNode()

        # ── INITIAL: Phân tích Traceback lần đầu tiên
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

        # Cập nhật Metrics
        ctx.state.metrics_analyzer_calls += 1
        try:
            usage = result.usage()
            ctx.state.metrics_analyzer_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
        except Exception:
            pass

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
