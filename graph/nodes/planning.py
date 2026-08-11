"""
PlanningNode — Planner Agent (Thinking) phân tích Traceback và sinh ra PlanWrapper cho lỗi phức tạp.
Chỉ được gọi khi lỗi đã được phân loại là COMPLEX bởi InputGateGuardrailNode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import planner_agent
from graph.config import BOLD, CYAN, PLANNER_MODEL_NAME, RED, RESET
from graph.helpers import print_step
from graph.models import BugFixState, PlanWrapper, BugType
from graph.prompts import PLAN_TEMPLATES

if TYPE_CHECKING:
    from graph.nodes.plan_interceptor import PlanInterceptorNode
    from graph.nodes.validation import ValidationNode


@dataclass
class PlanningNode(BaseNode[BugFixState]):
    """
    [Agent — Thinking Mode via Chain-of-Thought Prompt]
    Planner đọc file, viết khối <thinking> phân tích sâu,
    sau đó xuất ra PlanWrapper (nhiều bước) cho Coder Agent thi công.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[PlanInterceptorNode, ValidationNode]:
        from graph.nodes.plan_interceptor import PlanInterceptorNode
        from graph.nodes.validation import ValidationNode

        print_step("🧠", "Planner Agent", f"Đang đọc file và phân tích sâu với {PLANNER_MODEL_NAME} (Thinking via CoT)...")

        prompt = f"""CHẨN ĐOÁN LỖI PHỨC TẠP (UNHANDLED RUNTIME EXCEPTION):
- Ngoại lệ        : {ctx.state.error_class}
- Thông báo lỗi   : {ctx.state.error_message}
- Mô tả người dùng: {ctx.state.raw_user_input}

CHI TIẾT CALL STACK (BẮT BUỘC ĐỌC):
"""

        for frame in ctx.state.stack_trace:
            prompt += f"- File: {frame.file_path}, Line: {frame.line_number}, Function: {frame.function_name}\n"
            if frame.code_snippet:
                prompt += f"  Code: {frame.code_snippet}\n"

        if getattr(ctx.state, 'repro_confirmed', None) is False:
            prompt += f"""
KẾT QUẢ TÁI HIỆN LỖI (CHƯA XÁC NHẬN / THẤT BẠI):
⚠️ Cảnh báo: Lỗi này phụ thuộc vào runtime data phức tạp hoặc môi trường cụ thể.
Hãy thận trọng hơn khi đọc code và KHÔNG đưa ra giả định.
"""

        if ctx.state.iteration_history or ctx.state.action_history or ctx.state.user_plan_feedback:
            prompt += "\nLỊCH SỬ CÁC VÒNG LẶP (CAUSAL CHAIN CONTEXT - RẤT QUAN TRỌNG):\n"
            
            if ctx.state.iteration_history:
                for i, iter_ctx in enumerate(ctx.state.iteration_history, 1):
                    prompt += f"--- Vòng {i} ---\n"
                    prompt += f"  - Lỗi ban đầu: {iter_ctx.initial_error}\n"
                    prompt += f"  - Các file đã sửa: {', '.join(iter_ctx.target_files)}\n"
                    prompt += f"  - Tóm tắt patch:\n{iter_ctx.patch_summary}\n"
                    prompt += f"  - Phản hồi/Lỗi mới từ User: {iter_ctx.user_feedback}\n"
            
            if ctx.state.action_history:
                prompt += "\nLịch sử các lần crash/lỗi hệ thống:\n"
                for log in ctx.state.action_history:
                    prompt += f"  - {log}\n"
                    
            if ctx.state.user_plan_feedback:
                prompt += f"\nPhản hồi yêu cầu Replan lần này:\n  -> {ctx.state.user_plan_feedback}\n"
                
            prompt += "-> HÃY PHÂN TÍCH LÝ DO TẠI SAO CÁCH SỬA TRƯỚC ĐÓ LẠI THẤT BẠI VÀ TÌM HƯỚNG TIẾP CẬN MỚI. KHÔNG LẶP LẠI PLAN CŨ.\n"

        # Nhúng Plan Template tương ứng với loại lỗi
        if ctx.state.bug_types:
            for btype in ctx.state.bug_types:
                template = PLAN_TEMPLATES.get(btype.value.upper())
                if template:
                    prompt += f"\n{template}\n"
                    break  # Ưu tiên lấy template của loại lỗi đầu tiên tìm thấy

        prompt += "\nLỆNH: Dùng tool `read_file` đọc code tại các điểm crash. Viết khối <thinking> phân tích nguyên nhân gốc rễ, sau đó trả về PlanWrapper chi tiết tuân theo KHUNG KẾ HOẠCH ở trên."

        try:
            result = await planner_agent.run(prompt)
            output: PlanWrapper = result.output
            
            # Cập nhật Metrics
            from graph.helpers import count_tool_calls
            ctx.state.metrics_planner_tool_calls += count_tool_calls(result.new_messages())
            try:
                usage = result.usage()
                ctx.state.metrics_planner_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
            except Exception:
                pass

            ctx.state.current_plan = output.steps
            ctx.state.root_cause_explanation = output.root_cause

            print(f"\n{'─'*60}")
            print_step("📜", "Planner", f"Quyết định: {CYAN}Plan{RESET} ({len(output.steps)} bước — Lỗi phức tạp)")
            print(f"  {BOLD}Nguyên nhân  :{RESET} {RED}{output.root_cause}{RESET}")
            print(f"{'─'*60}")
            # Plan cần Human-in-the-loop duyệt
            return PlanInterceptorNode()

        except Exception as exc:
            print_step("❌", "Planner Error", f"{RED}Lỗi khi tạo Plan: {exc}{RESET}")
            ctx.state.validation_errors.append(f"Planner Error: {exc}")
            ctx.state.retry_count += 1
            return ValidationNode()
