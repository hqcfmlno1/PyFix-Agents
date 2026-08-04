"""
InputGateGuardrailNode — Kiểm tra và bổ sung thông tin đầu vào.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import CYAN, RED, RESET
from graph.helpers import print_step
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.need_more_info import NeedMoreInfoNode
    from graph.nodes.planning import PlanningNode


@dataclass
class InputGateGuardrailNode(BaseNode[BugFixState]):
    """
    [Deterministic] Kiểm tra thông tin đầu vào tối thiểu (stack_trace hoặc mô tả lỗi).
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[NeedMoreInfoNode, PlanningNode]:
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

        print_step("✅", "Guardrail", "Thông tin đầu vào hợp lệ. Chuyển sang Planner...")
        return PlanningNode()
