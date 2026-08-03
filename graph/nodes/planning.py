"""
PlanningNode — Planner Agent nạp Plan Template và tạo danh sách PlanStep sửa lỗi dựa trên stack_trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import planner_agent
from graph.config import MODEL_NAME, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugFixState, BugType, PlanStep, RePlanHistory
from graph.prompts import PLAN_TEMPLATES

if TYPE_CHECKING:
    from graph.nodes.plan_interceptor import PlanInterceptorNode


@dataclass
class PlanningNode(BaseNode[BugFixState]):
    """
    [Agent] Dùng LLM tạo kế hoạch sửa lỗi dựa trên stack_trace có cấu trúc và Plan Template tương ứng.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> PlanInterceptorNode:
        from graph.nodes.plan_interceptor import PlanInterceptorNode

        if ctx.state.user_plan_feedback:
            print_step("🔄", "Planner Agent", f"Đang cập nhật plan lần {ctx.state.replan_count + 1}...")
        else:
            print_step("🧠", "Planner Agent", f"Đang tạo kế hoạch sửa lỗi với {MODEL_NAME}...")

        selected_templates: List[str] = []
        for bt in ctx.state.bug_types:
            key = ""
            if bt == BugType.DATA_DRIVEN_RUNTIME:
                key = "DATA_DRIVEN_RUNTIME"
            elif bt == BugType.LOGIC_DRIVEN_RUNTIME:
                key = "LOGIC_DRIVEN_RUNTIME"

            if key and key in PLAN_TEMPLATES:
                selected_templates.append(PLAN_TEMPLATES[key])

        templates_prompt_str = "\n\n".join(selected_templates) if selected_templates else PLAN_TEMPLATES["DATA_DRIVEN_RUNTIME"]

        stack_trace_formatted = ""
        if ctx.state.stack_trace:
            stack_lines = []
            for idx, frame in enumerate(ctx.state.stack_trace, start=1):
                role_str = "[CRASH POINT]" if frame.role == "crash_point" else "[CALLER]"
                code_str = f" | Code: `{frame.code_snippet}`" if frame.code_snippet else ""
                stack_lines.append(
                    f"  Frame {idx} {role_str}: File '{frame.file_path}', dòng {frame.line_number}, hàm `{frame.function_name or 'main'}`{code_str}"
                )
            stack_trace_formatted = "\n".join(stack_lines)
        else:
            stack_trace_formatted = f"  File crash chính: {ctx.state.target_file or 'N/A'}"

        replan_section = ""
        if ctx.state.user_plan_feedback and ctx.state.current_plan:
            old_plan_str = "\n".join(
                f"  Bước {s.step_id}: {s.title} ({s.target_file}:{s.target_lines}) — {s.description}" for s in ctx.state.current_plan
            )
            replan_section = f"""
⚠ YÊU CẦU ĐIỀU CHỈNH PLAN CŨ:
Feedback của User: {ctx.state.user_plan_feedback}

Plan cũ đã bị từ chối:
{old_plan_str}

Hãy tạo danh sách PlanStep mới KHÁC với plan cũ và tuân thủ đúng feedback.
"""

        types_str = ", ".join([bt.value.upper() for bt in ctx.state.bug_types]) if ctx.state.bug_types else "UNHANDLED_RUNTIME"

        prompt = f"""UNHANDLED RUNTIME EXCEPTION REPORT:
- Loại lỗi          : {types_str}
- Exception Class   : {ctx.state.error_class or 'N/A'}
- Exception Detail  : {ctx.state.error_message or 'N/A'}
- Runtime Input Data: {ctx.state.runtime_input_data or 'N/A'}

DANH SÁCH CALL STACK NỘI BỘ DỰ ÁN (từ caller đến crash_point):
{stack_trace_formatted}

CẤU TRÚC DỰ ÁN:
{ctx.state.project_tree}

{templates_prompt_str}

{replan_section}

HƯỚNG DẪN DÙNG TOOL & LẬP PLAN:
1. Dùng tool `read_file` đọc mã nguồn tại các file trong danh sách Call Stack (truyền start_line, end_line xung quanh vị trí dòng lỗi).
2. Xác định nguyên nhân gốc rễ (Root Cause) gây ra lỗi Unhandled Runtime Exception.
3. Lập danh sách các `PlanStep` sửa chi tiết: chỉ định rõ target_file, target_lines (các dòng liên quan), description và acceptance_criteria cho từng bước.
"""

        result = await planner_agent.run(prompt)
        steps: List[PlanStep] = result.output

        if ctx.state.user_plan_feedback and ctx.state.current_plan:
            ctx.state.plan_history.append(
                RePlanHistory(
                    revision=ctx.state.replan_count,
                    feedback=ctx.state.user_plan_feedback,
                    rejected_plan_summary="; ".join(s.title for s in ctx.state.current_plan),
                )
            )
            ctx.state.replan_count += 1

        ctx.state.current_plan = steps
        ctx.state.user_plan_feedback = None
        ctx.state.plan_approved = False

        print_step(
            "✅",
            "Plan tạo xong",
            f"Tổng {len(steps)} bước sửa đổi",
        )

        return PlanInterceptorNode()
