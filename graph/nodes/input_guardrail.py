"""
InputGateGuardrailNode — Kiểm tra thông tin đầu vào và phân loại độ phức tạp lỗi.
- Nếu SIMPLE (1 file): Chuyển thẳng sang ExecutionNode (Coder Agent không thinking).
- Nếu COMPLEX (nhiều file / user yêu cầu xem plan): Chuyển sang PlanningNode (Thinking).
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
    from graph.nodes.planning import PlanningNode


@dataclass
class InputGateGuardrailNode(BaseNode[BugFixState]):
    """
    [Deterministic] Kiểm tra thông tin đầu vào tối thiểu, phân loại độ phức tạp,
    và định tuyến đến đúng node xử lý tiếp theo.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[NeedMoreInfoNode, ExecutionNode, PlanningNode]:
        from graph.nodes.execution import ExecutionNode
        from graph.nodes.need_more_info import NeedMoreInfoNode
        from graph.nodes.planning import PlanningNode

        missing: List[str] = []

        if not ctx.state.target_file and ctx.state.repo_path:
            py_files = []
            for root, dirs, files in os.walk(ctx.state.repo_path):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
                for f in files:
                    if f.endswith(".py"):
                        full_f = os.path.join(root, f)
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

        # ── Phân loại độ phức tạp (Deterministic Heuristic) ──────────────────
        files_in_stack = {frame.file_path for frame in ctx.state.stack_trace if frame.file_path}

        if ctx.state.want_plan:
            ctx.state.complexity = BugComplexity.COMPLEX
        elif len(files_in_stack) > 1:
            ctx.state.complexity = BugComplexity.COMPLEX
        else:
            ctx.state.complexity = BugComplexity.SIMPLE

        # ── Định tuyến dựa trên độ phức tạp ─────────────────────────────────
        if ctx.state.complexity == BugComplexity.SIMPLE:
            print_step("⚡", "Guardrail", f"{GREEN}Lỗi ĐƠN GIẢN (1 file){RESET} — Chuyển thẳng sang {BOLD}Coder Agent{RESET} (không thinking)...")
            return ExecutionNode()
        else:
            print_step("🧠", "Guardrail", f"{YELLOW}Lỗi PHỨC TẠP (nhiều file / yêu cầu plan){RESET} — Chuyển sang {BOLD}Planner Agent{RESET} (thinking)...")
            return PlanningNode()
