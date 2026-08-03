"""
PlanningStrategyNode — Đánh giá lỗi là Đơn giản (DirectFix) hay Phức tạp (Planning).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugComplexity, BugFixState

if TYPE_CHECKING:
    from graph.nodes.direct_fix import DirectFixCreationNode
    from graph.nodes.planning import PlanningNode


@dataclass
class PlanningStrategyNode(BaseNode[BugFixState]):
    """
    [Deterministic / Decision] Phân loại độ phức tạp của Unhandled Runtime Exception:
    - Bug Đơn giản: Call Stack chỉ có đúng 1 frame trong dự án, không qua chuỗi truyền dữ liệu nhiều hàm & chưa fail DirectFix -> DirectFixCreationNode
    - Bug Phức tạp: Call Stack nhiều hơn 1 frame (có cả caller & crash_point) hoặc user yêu cầu plan hoặc đã fail DirectFix -> PlanningNode
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[DirectFixCreationNode, PlanningNode]:
        from graph.nodes.direct_fix import DirectFixCreationNode
        from graph.nodes.planning import PlanningNode

        print_step("⚖️", "Planning Strategy", "Đánh giá độ phức tạp của Unhandled Runtime Exception...")

        # 1. Nếu user chủ động muốn xem plan -> COMPLEX
        if ctx.state.want_plan:
            print(f"  {CYAN}👉 Người dùng yêu cầu xem Plan chi tiết.{RESET}")
            ctx.state.complexity = BugComplexity.COMPLEX
            return PlanningNode()

        # 2. Nếu đã từng fail DirectFix quá số lần quy định -> COMPLEX
        if ctx.state.direct_fix_fail_count >= ctx.state.max_direct_fix_retries:
            print(f"  {YELLOW}⚠️ DirectFix đã thất bại {ctx.state.direct_fix_fail_count} lần. Tự động nâng độ phức tạp thành Plan chi tiết.{RESET}")
            ctx.state.complexity = BugComplexity.COMPLEX
            return PlanningNode()

        # 3. Phân loại theo số lượng Frame trong Call Stack dự án:
        # Nếu stack_trace chỉ có đúng 1 frame (lỗi nổ cục bộ 1 vị trí, không qua chuỗi hàm gọi khác) -> SIMPLE
        is_single_frame = len(ctx.state.stack_trace) <= 1

        if is_single_frame:
            ctx.state.complexity = BugComplexity.SIMPLE
            print_step("⚡", "Simple Runtime Bug", f"Lỗi Runtime cục bộ 1 vị trí ({CYAN}DirectFix{RESET}) — Không cần lên plan chi tiết từng bước.")
            return DirectFixCreationNode()
        else:
            ctx.state.complexity = BugComplexity.COMPLEX
            print_step("🧠", "Complex Runtime Bug", f"Lỗi Runtime liên-hàm ({len(ctx.state.stack_trace)} frames) — Cần lên plan chi tiết từng bước ({CYAN}Planner Agent{RESET}).")
            return PlanningNode()
