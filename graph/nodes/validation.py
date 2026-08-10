"""
ValidationNode — Deterministic Node kiểm tra code đã thực sự được sửa chưa.
Thực thi py_compile, pytest hoặc script runtime.
Xử lý fallback: DirectFix fail -> escalate sang Detailed Plan; Plan fail -> Replan hoặc Surrender (chịu thua).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_step, resolve_target_path
from graph.models import BugComplexity, BugFixState

if TYPE_CHECKING:
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode
    from graph.nodes.input_analyzer import InputAnalyzerNode


@dataclass
class ValidationNode(BaseNode[BugFixState]):
    """
    [Deterministic Node] Tái hiện và kiểm tra lỗi bằng runtime / pytest / py_compile.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[PlanningNode, ReportNode, InputAnalyzerNode]:
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode
        from graph.nodes.input_analyzer import InputAnalyzerNode

        print_step("🔍", "Validation Node", "1/2. Kiểm tra cú pháp mã nguồn (py_compile)...")

        if not ctx.state.final_fixes:
            print_step("⚠️", "Validation", "Không tìm thấy thông tin final_fixes để validate.")
            return self._handle_failure(ctx, "Không có file sửa đổi để kiểm thử.")

        for ffix in ctx.state.final_fixes:
            target_path = resolve_target_path(ffix.target_file, ctx.state.repo_path)
            if not os.path.exists(target_path):
                continue

            proc = subprocess.run([sys.executable, "-m", "py_compile", target_path], capture_output=True, text=True)
            if proc.returncode != 0:
                syntax_err = (proc.stderr or proc.stdout).strip()
                print_step("❌", "Validation", f"{RED}Lỗi cú pháp tại {ffix.target_file}:{RESET}\n  {syntax_err}")
                return self._handle_failure(ctx, f"Lỗi cú pháp ({ffix.target_file}): {syntax_err}")

        print_step("✅", "Validation", f"{GREEN}Cú pháp mã nguồn hợp lệ!{RESET}")

        # ── Lưu vào Causal Chain Context (IterationHistory)
        patch_summary = ""
        target_files = []
        for ffix in ctx.state.final_fixes:
            target_files.append(ffix.target_file)
            patch_summary += f"{ffix.target_file}: {ffix.changes_summary}\n"
        
        from graph.models import IterationContext
        ctx.state.iteration_history.append(
            IterationContext(
                initial_error=ctx.state.error_message or ctx.state.error_class or "Unknown Error",
                target_files=target_files,
                patch_summary=patch_summary.strip(),
                user_feedback="" # Sẽ được điền ở InputAnalyzerNode
            )
        )

        # Chuyển quyền quyết định thành công/thất bại cho lập trình viên
        return InputAnalyzerNode()

    def _handle_failure(
        self, ctx: GraphRunContext[BugFixState], error_msg: str
    ) -> Union[PlanningNode, ReportNode]:
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode

        ctx.state.validation_passed = False
        ctx.state.validation_errors.append(error_msg)
        ctx.state.action_history.append(f"Lần thử Replan {ctx.state.replan_count + 1}: Bản patch thất bại khi kiểm thử runtime. Chi tiết lỗi: {error_msg}")

        if ctx.state.replan_count < ctx.state.max_replan_limit:
            print(f"\n  {RED}❌ Lỗi gốc chưa được khắc phục triệt để.{RESET}")

            # Nếu đang là SIMPLE mà thất bại → Nâng cấp lên COMPLEX để Planner (Thinking) vào cuộc
            if ctx.state.complexity == BugComplexity.SIMPLE:
                ctx.state.complexity = BugComplexity.COMPLEX
                print(f"  {YELLOW}⬆ Nâng cấp: DirectFix thất bại → Chuyển sang Planner Agent (Thinking) để phân tích sâu...{RESET}")
            else:
                print(f"  {YELLOW}🔄 Quay về Planner để chẩn đoán và tạo Plan mới... (Lần replan {ctx.state.replan_count + 1}/{ctx.state.max_replan_limit}){RESET}")

            ctx.state.user_plan_feedback = f"Validation thất bại (Lỗi chưa hết). Chi tiết lỗi: {error_msg}"
            return PlanningNode()
        else:
            print_step("⛔", "Validation", f"{RED}Đã vượt quá {ctx.state.max_replan_limit} lần thử mà chưa sửa được lỗi gốc. Agent chịu thua.{RESET}")
            ctx.state.surrendered = True
            return ReportNode()

