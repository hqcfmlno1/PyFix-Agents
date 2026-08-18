"""
InputGateGuardrailNode — Kiểm tra thông tin đầu vào và phân loại độ phức tạp lỗi.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugComplexity, BugFixState

if TYPE_CHECKING:
    from graph.nodes.execution import ExecutionNode
    from graph.nodes.need_more_info import NeedMoreInfoNode
    from graph.nodes.report import ReportNode
    from graph.nodes.reproduction_plan import ReproductionPlanNode


def _rel_in_repo(path: str, repo_path: str) -> str:
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        try:
            return os.path.relpath(normalized, repo_path)
        except ValueError:
            return normalized
    return normalized


@dataclass
class InputGateGuardrailNode(BaseNode[BugFixState]):
    """[Deterministic] Kiểm tra đầu vào tối thiểu, scope và độ phức tạp."""

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union["NeedMoreInfoNode", "ExecutionNode", "ReproductionPlanNode", "ReportNode"]:
        from graph.nodes.execution import ExecutionNode
        from graph.nodes.need_more_info import NeedMoreInfoNode
        from graph.nodes.report import ReportNode
        from graph.nodes.reproduction_plan import ReproductionPlanNode

        if not ctx.state.scope_supported:
            reason = ctx.state.scope_rejection_reason or "Bug nằm ngoài scope runtime logic/data của PyFix."
            ctx.state.want_apply = False
            ctx.state.surrendered = True
            ctx.state.final_explanation = reason
            print_step("⛔", "Guardrail", f"{YELLOW}{reason}{RESET}")
            return ReportNode()

        missing: List[str] = []

        if not ctx.state.target_file and ctx.state.repo_path:
            py_files = []
            for root, dirs, files in os.walk(ctx.state.repo_path):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
                for filename in files:
                    if filename.endswith(".py"):
                        full_f = os.path.join(root, filename)
                        rel_f = os.path.relpath(full_f, ctx.state.repo_path)
                        py_files.append(rel_f)
            if len(py_files) == 1:
                ctx.state.target_file = py_files[0]
                print(f"  {CYAN}💡 Tự động nhận diện file dự án: {py_files[0]}{RESET}")

        if not ctx.state.bug_types and not ctx.state.stack_trace and not ctx.state.raw_user_input:
            missing.append("Log Traceback hoặc mô tả lỗi Unhandled Runtime Exception")

        ctx.state.missing_fields = missing

        if missing:
            print_step("⚠", "Guardrail", f"{RED}Thiếu thông tin:{RESET}")
            for field in missing:
                print(f"   • {field}")
            return NeedMoreInfoNode()

        if ctx.state.scope_confidence == "uncertain":
            print_step("⚠", "Soft Scope Gate", "Input nằm trong vùng chưa chắc chắn; vẫn tiếp tục với hướng sửa thận trọng.")

        files_in_stack = {
            _rel_in_repo(frame.file_path, ctx.state.repo_path)
            for frame in ctx.state.stack_trace
            if frame.file_path
        }

        if ctx.state.want_plan:
            ctx.state.complexity = BugComplexity.COMPLEX
        elif len(files_in_stack) > 1:
            ctx.state.complexity = BugComplexity.COMPLEX
        else:
            ctx.state.complexity = BugComplexity.SIMPLE

        if ctx.state.complexity == BugComplexity.SIMPLE:
            print_step("⚡", "Guardrail", f"{GREEN}Lỗi ĐƠN GIẢN (1 file){RESET} — Chuyển thẳng sang {BOLD}Coder Agent{RESET} (không thinking)...")
            return ExecutionNode()

        print_step("🧪", "Guardrail", f"{YELLOW}Lỗi PHỨC TẠP (nhiều file / yêu cầu plan){RESET} — Chuyển sang {BOLD}Reproduction Plan{RESET} để viết kịch bản tái hiện...")
        return ReproductionPlanNode()
