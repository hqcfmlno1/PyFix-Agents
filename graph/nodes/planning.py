"""
PlanningNode — Planner Agent phân tích Traceback và quyết định sinh ra DirectFix hoặc Plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import planner_agent
from graph.config import BOLD, CYAN, MODEL_NAME, RED, RESET
from graph.helpers import print_step
from graph.models import BugComplexity, BugFixState, DirectFix, PlanWrapper

if TYPE_CHECKING:
    from graph.nodes.execution import ExecutionNode
    from graph.nodes.plan_interceptor import PlanInterceptorNode
    from graph.nodes.validation import ValidationNode


@dataclass
class PlanningNode(BaseNode[BugFixState]):
    """
    [Agent] Dùng Planner Agent phân tích mã nguồn và quyết định:
    - Nếu đơn giản: Trả về DirectFix (chỉ thị 1 bước). Chuyển thẳng sang ExecutionNode (Coder thực thi).
    - Nếu phức tạp: Trả về PlanWrapper (nhiều bước). Chuyển sang PlanInterceptorNode để hỏi ý kiến Dev.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[PlanInterceptorNode, ExecutionNode, ValidationNode]:
        from graph.nodes.execution import ExecutionNode
        from graph.nodes.plan_interceptor import PlanInterceptorNode
        from graph.nodes.validation import ValidationNode

        # 1. Chẩn đoán độ phức tạp bằng Code thuần (Deterministic Heuristic)
        files_in_stack = {frame.file_path for frame in ctx.state.stack_trace if frame.file_path}
        
        if ctx.state.want_plan:
            ctx.state.complexity = BugComplexity.COMPLEX
        elif len(files_in_stack) > 1:
            ctx.state.complexity = BugComplexity.COMPLEX
        else:
            ctx.state.complexity = BugComplexity.SIMPLE

        complexity_str = "PHỨC TẠP (Plan)" if ctx.state.complexity == BugComplexity.COMPLEX else "ĐƠN GIẢN (DirectFix)"
        print_step("🧠", "Planner Agent", f"Đã phân loại bằng code: {BOLD}{complexity_str}{RESET}. Đang đọc file và chẩn đoán với {MODEL_NAME}...")

        prompt = f"""CHẨN ĐOÁN VÀ QUYẾT ĐỊNH (UNHANDLED RUNTIME EXCEPTION):
- Ngoại lệ        : {ctx.state.error_class}
- Thông báo lỗi   : {ctx.state.error_message}
- Mô tả người dùng: {ctx.state.raw_user_input}

CHI TIẾT CALL STACK (BẮT BUỘC ĐỌC):
"""

        for frame in ctx.state.stack_trace:
            prompt += f"- File: {frame.file_path}, Line: {frame.line_number}, Function: {frame.function_name}\n"
            if frame.code_snippet:
                prompt += f"  Code: {frame.code_snippet}\n"

        if ctx.state.execution_logs:
            prompt += "\nLỊCH SỬ THỬ NGHIỆM THẤT BẠI TRƯỚC ĐÓ (Action History):\n"
            for log in ctx.state.execution_logs:
                prompt += f"- {log}\n"
            prompt += "->HÃY ĐẢM BẢO KHÔNG LẶP LẠI CÁC CÁCH SỬA ĐÃ THẤT BẠI.\n"

        prompt += "\nLỆNH: Dùng tool `read_file` đọc code tại các điểm crash. Xác định nguyên nhân gốc rễ (root_cause) rõ ràng, sau đó trả về:"
        if ctx.state.complexity == BugComplexity.SIMPLE:
            prompt += "\n- Lỗi này ĐÃ ĐƯỢC XÁC ĐỊNH LÀ ĐƠN GIẢN (1 chỗ, 1 file). Bạn BẮT BUỘC trả về mô hình `DirectFix` kèm theo `root_cause`."
        else:
            prompt += "\n- Lỗi này ĐÃ ĐƯỢC XÁC ĐỊNH LÀ PHỨC TẠP (hoặc user yêu cầu xem plan). Bạn BẮT BUỘC trả về mô hình `PlanWrapper` kèm theo `root_cause`."

        try:
            result = await planner_agent.run(prompt)
            output = result.output

            if isinstance(output, DirectFix):
                ctx.state.direct_fix = output
                ctx.state.current_plan = []  # Đảm bảo plan trống
                ctx.state.root_cause_explanation = output.root_cause

                print(f"\n{'─'*60}")
                print_step("⚡", "Planner", f"Quyết định: {CYAN}DirectFix{RESET} (Lỗi đơn giản, 1 chỗ)")
                print(f"  {BOLD}Tóm tắt lỗi  :{RESET} {output.bug_summary}")
                print(f"  {BOLD}Nguyên nhân  :{RESET} {RED}{output.root_cause}{RESET}")
                print(f"  {BOLD}Cách sửa     :{RESET} {output.fix_description}")
                print(f"  {BOLD}File mục tiêu:{RESET} {CYAN}{output.file_path}:{output.error_line}{RESET}")
                print(f"{'─'*60}")
                print(f"  → Tự động tiến hành sửa code...\n")
                # DirectFix không cần hỏi duyệt, đi thẳng sang Execution (Coder)
                return ExecutionNode()
            else:
                # isinstance(output, PlanWrapper)
                ctx.state.current_plan = output.steps
                ctx.state.direct_fix = None
                ctx.state.root_cause_explanation = output.root_cause

                print(f"\n{'─'*60}")
                print_step("📜", "Planner", f"Quyết định: {CYAN}Plan{RESET} ({len(output.steps)} bước — Lỗi phức tạp)")
                print(f"  {BOLD}Nguyên nhân  :{RESET} {RED}{output.root_cause}{RESET}")
                print(f"{'─'*60}")
                # Plan cần Human-in-the-loop duyệt
                return PlanInterceptorNode()

        except Exception as exc:
            print_step("❌", "Planner Error", f"{RED}Lỗi khi tạo Plan/DirectFix: {exc}{RESET}")
            ctx.state.validation_errors.append(f"Planner Error: {exc}")
            ctx.state.retry_count += 1
            return ValidationNode()
