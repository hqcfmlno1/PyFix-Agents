"""
InputAnalyzerNode — phân tích traceback thành dữ liệu có cấu trúc, đồng thời giữ feedback loop sau validation.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import input_analyzer_agent
from graph.config import ANALYZER_MODEL_NAME, BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugComplexity, BugFixState, BugReport, UserSentiment

if TYPE_CHECKING:
    from graph.nodes.input_guardrail import InputGateGuardrailNode
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode
    from graph.nodes.reproduction_plan import ReproductionPlanNode


def _normalize_project_path(path: str, repo_path: str) -> str:
    if not path:
        return path
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        try:
            return os.path.relpath(normalized, repo_path)
        except ValueError:
            return normalized
    return normalized


def _normalize_bug_report(report: BugReport, repo_path: str) -> None:
    if report.target_file:
        report.target_file = _normalize_project_path(report.target_file, repo_path)
    for frame in report.stack_trace:
        if frame.file_path:
            frame.file_path = _normalize_project_path(frame.file_path, repo_path)


def _read_multiline_input(label: str) -> str:
    print(f"{BOLD}{label} (Nhấn Enter 2 lần liên tiếp để kết thúc nhập):{RESET}")
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
    print()
    return "\n".join(lines).strip()


def _apply_bug_report(ctx: GraphRunContext[BugFixState], bug_report: BugReport) -> None:
    _normalize_bug_report(bug_report, ctx.state.repo_path)
    ctx.state.bug_types = bug_report.bug_types
    ctx.state.error_class = bug_report.error_class
    ctx.state.error_message = bug_report.error_message
    ctx.state.stack_trace = bug_report.stack_trace
    ctx.state.target_file = bug_report.target_file
    ctx.state.error_file = bug_report.target_file
    ctx.state.error_line = bug_report.error_line
    ctx.state.runtime_input_data = bug_report.runtime_input_data
    ctx.state.want_plan = bug_report.want_plan

    if bug_report.bug_types:
        ctx.state.scope_supported = True
        ctx.state.scope_rejection_reason = ""
        ctx.state.scope_confidence = "supported"
    elif bug_report.stack_trace or bug_report.error_class:
        ctx.state.scope_supported = True
        ctx.state.scope_rejection_reason = ""
        ctx.state.scope_confidence = "uncertain"
    else:
        ctx.state.scope_supported = False
        ctx.state.scope_rejection_reason = "Analyzer không thể phân loại lỗi này vào runtime logic/data."
        ctx.state.scope_confidence = "unsupported"

    if not ctx.state.target_file and bug_report.stack_trace:
        for frame in reversed(bug_report.stack_trace):
            if frame.role == "crash_point" or not ctx.state.target_file:
                ctx.state.target_file = frame.file_path
                ctx.state.error_line = frame.line_number
                break


@dataclass
class InputAnalyzerNode(BaseNode[BugFixState]):
    """[Agent] Phân tích traceback có cấu trúc; dùng lại cho initial route và feedback loop."""

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union["InputGateGuardrailNode", "PlanningNode", "ReportNode", "ReproductionPlanNode"]:
        from graph.nodes.input_guardrail import InputGateGuardrailNode
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode
        from graph.nodes.reproduction_plan import ReproductionPlanNode

        if ctx.state.iteration_history:
            print(f"\n{'─' * 60}")
            if ctx.state.final_fixes:
                print(f"  {CYAN}Các file vừa được Agent chỉnh sửa:{RESET}")
                for ffix in ctx.state.final_fixes:
                    print(f"    - {ffix.target_file}")
                print()

            if getattr(ctx.state, "repro_confirmed", False):
                print_step("✨", "Automated Validation", f"{GREEN}Hệ thống đã chạy kịch bản tự động và xác nhận lỗi gốc không còn xuất hiện.{RESET}")
                print_step("🧪", "Human-in-the-Loop", "Vui lòng kiểm tra lại thủ công lần cuối (UI, data, side-effects) để đảm bảo chắc chắn.")
            else:
                print_step("🧪", "Human-in-the-Loop", "Vui lòng chạy thử ứng dụng (runtime) và kiểm tra xem lỗi đã được khắc phục chưa.")
            print(
                f"""
  {BOLD}Hướng dẫn phản hồi:{RESET}
  • {GREEN}Nhập 'ok', 'done', 'yes'{RESET} nếu ứng dụng đã chạy tốt.
  • {RED}Dán lỗi mới hoặc giải thích lỗi còn tồn đọng{RESET} nếu bản vá chưa triệt để.
"""
            )
            raw_input = _read_multiline_input("Phản hồi của bạn")

            happy_keywords = ["ok", "done", "yes", "y", "chạy ngon", "passed", "good", "tốt"]
            if raw_input.lower() in happy_keywords or raw_input.strip() == "":
                print_step("🎉", "Analyzer", f"{GREEN}Xác nhận bản vá chạy tốt! Kết thúc phiên.{RESET}")
                ctx.state.user_sentiment = UserSentiment.HAPPY
                ctx.state.validation_passed = True
                return ReportNode()

            ctx.state.user_sentiment = UserSentiment.UNHAPPY
            ctx.state.complexity = BugComplexity.COMPLEX
            if ctx.state.iteration_history:
                ctx.state.iteration_history[-1].user_feedback = raw_input

            print_step("🤖", "Input Analyzer Agent", f"Đang phân tích phản hồi/lỗi mới với {ANALYZER_MODEL_NAME}...")
            prompt = f"""Phân tích nội dung sau. Nếu đây là một Traceback log, hãy lọc nhiễu thư viện ngoài và trích xuất stack_trace.
Nếu đây chỉ là câu nói bình thường, hãy trả về stack_trace rỗng.

---
{raw_input}
---

Cấu trúc dự án để đối chiếu đường dẫn file tương đối:
{ctx.state.project_tree[:1500]}
"""
            try:
                result = await input_analyzer_agent.run(prompt)
                bug_report: BugReport = result.output

                from graph.helpers import count_tool_calls

                ctx.state.metrics_analyzer_tool_calls += count_tool_calls(result.new_messages())
                try:
                    usage = result.usage()
                    ctx.state.metrics_analyzer_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
                except Exception:
                    pass

                if bug_report.stack_trace:
                    _apply_bug_report(ctx, bug_report)
                    print_step("✅", "Phân tích xong", "Phát hiện lỗi mới (new exception) từ phản hồi của bạn.")
                    ctx.state.repro_confirmed = None
                    ctx.state.repro_script_path = None
                    ctx.state.repro_output = ""
                    ctx.state.repro_retry_count = 0
                    ctx.state.user_suggested_fix = None
                    print_step("🔄", "Analyzer", f"{YELLOW}Lỗi mới phát sinh. Chuyển sang bước tái hiện lỗi mới...{RESET}")
                    return ReproductionPlanNode()

                print_step("✅", "Phân tích xong", "Ghi nhận gợi ý/phản hồi (không chứa traceback mới).")
                ctx.state.user_suggested_fix = raw_input
                print_step("🔄", "Analyzer", f"{YELLOW}Đã nhận gợi ý. Bỏ qua Repro, quay thẳng lại Planner để lên kế hoạch mới...{RESET}")
                return PlanningNode()
            except Exception as exc:
                print_step("⚠", "Input Analyzer Agent", f"Không thể phân tích phản hồi bằng LLM: {exc}")
                ctx.state.user_suggested_fix = raw_input
                print_step("🔄", "Analyzer", f"{YELLOW}Đã nhận gợi ý. Quay thẳng lại Planner để lên kế hoạch mới...{RESET}")
                return PlanningNode()

        print(f"\n{'─' * 60}")
        print_step("📝", "Input Analyzer", "Phân tích traceback/runtime log theo luồng traceback-first.")
        print(
            f"""
  {BOLD}Hướng dẫn nhập:{RESET}
  • {CYAN}Dán traceback log{RESET} từ terminal (KeyError, IndexError, TypeError, AttributeError...)
  • {YELLOW}Tip:{RESET} Thêm 'tôi muốn xem plan' nếu bạn muốn duyệt kế hoạch trước khi fix.
  • {YELLOW}Tip:{RESET} Nhập 'quit' để thoát.
"""
        )

        if ctx.state.raw_user_input.strip():
            raw_input = ctx.state.raw_user_input.strip()
            print(f"{BOLD}Mô tả lỗi / Traceback log:{RESET}")
            print(raw_input)
            print()
        elif ctx.state.preset_user_input is not None:
            raw_input = ctx.state.preset_user_input.strip()
            print(f"{BOLD}Mô tả lỗi / Traceback log:{RESET}")
            print(raw_input)
            print()
        else:
            raw_input = _read_multiline_input("Mô tả lỗi / Traceback log")

        if ctx.state.missing_fields and ctx.state.raw_user_input and raw_input and raw_input != ctx.state.raw_user_input:
            ctx.state.raw_user_input = ctx.state.raw_user_input + "\n\nThông tin bổ sung:\n" + raw_input
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

        from graph.helpers import count_tool_calls

        ctx.state.metrics_analyzer_tool_calls += count_tool_calls(result.new_messages())
        try:
            usage = result.usage()
            ctx.state.metrics_analyzer_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
        except Exception:
            pass

        _apply_bug_report(ctx, bug_report)
        ctx.state.initial_input_kind = ctx.state.initial_input_kind or "traceback"

        if not ctx.state.scope_supported:
            ctx.state.want_apply = False
            ctx.state.surrendered = True
            ctx.state.final_explanation = ctx.state.scope_rejection_reason
            print_step("⛔", "Scope Gate", f"{YELLOW}{ctx.state.scope_rejection_reason}{RESET}")
            return ReportNode()

        if ctx.state.scope_confidence == "uncertain":
            print_step("⚠", "Soft Scope Gate", f"{YELLOW}Analyzer chưa phân loại chắc bug type; vẫn tiếp tục theo soft scope gate.{RESET}")

        types_str = ", ".join(bt.value.upper() for bt in bug_report.bug_types) if bug_report.bug_types else "UNCERTAIN"
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
