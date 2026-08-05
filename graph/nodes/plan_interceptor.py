"""
PlanInterceptorNode — Gộp toàn bộ chức năng duyệt Plan:
- proceed: Chấp nhận plan & tiến hành sửa code luôn (ExecutionNode)
- replan <lý do>: Yêu cầu lập plan mới kèm lý do bắt buộc (PlanningNode)
- quit: Chỉ xem plan, không sửa code thực tế (ReportNode)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import format_plan, print_header
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.execution import ExecutionNode
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode


@dataclass
class PlanInterceptorNode(BaseNode[BugFixState]):
    """
    [Deterministic / Interceptor] Hiển thị plan chi tiết cho Dev xem và xử lý 3 lựa chọn:
    1. proceed (hoặc /ok): Duyệt plan & sửa code thực tế (ExecutionNode)
    2. replan <lý do>: Làm lại plan mới kèm lý do bắt buộc (PlanningNode)
    3. quit: Chỉ xem plan, dừng lại không sửa file (ReportNode)
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[ExecutionNode, PlanningNode, ReportNode]:
        from graph.nodes.execution import ExecutionNode
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode

        print_header("Kế hoạch sửa lỗi — Chờ người dùng duyệt (Plan Interceptor)")
        print(format_plan(ctx.state.current_plan))
        print(f"\n{'─'*60}")
        print(f"  {BOLD}Lựa chọn của bạn:{RESET}")
        print(f"  {GREEN}1. proceed{RESET} (hoặc {GREEN}/ok{RESET})      → Tiến hành sửa code theo plan")
        print(f"  {YELLOW}2. replan <lý do>{RESET}           → Yêu cầu làm lại plan mới (kèm lý do)")
        print(f"  {CYAN}3. quit{RESET} (hoặc {CYAN}exit{RESET})           → Chỉ xem plan, không chỉnh sửa file")
        print(f"{'─'*60}")

        while True:
            cmd = input(f"\n{BOLD}Nhập lựa chọn (proceed / replan <lý do> / quit):{RESET} ").strip()
            cmd_lower = cmd.lower()

            if cmd_lower in ["proceed", "/ok", "ok", "y", "yes", "1"]:
                print(f"  {GREEN}✓ Plan đã được chấp nhận! Tiến hành sửa code...{RESET}\n")
                ctx.state.plan_approved = True
                ctx.state.want_apply = True
                return ExecutionNode()

            elif cmd_lower.startswith("replan") or cmd_lower.startswith("/replan") or cmd_lower == "2":
                parts = cmd.split(maxsplit=1)
                feedback = parts[1].strip() if len(parts) > 1 else ""
                if not feedback:
                    feedback = input(f"  {YELLOW}⚠ Nhập lý do bạn muốn replan:{RESET} ").strip()

                if not feedback:
                    print(f"  {RED}✗ Replan bắt buộc phải kèm theo lý do.{RESET}")
                    continue

                print(f"  {YELLOW}🔄 Thực hiện Replan với lý do: '{feedback}'{RESET}\n")
                ctx.state.user_plan_feedback = feedback
                ctx.state.plan_approved = False
                return PlanningNode()

            elif cmd_lower in ["quit", "exit", "q", "3"]:
                print(f"  {CYAN}Dừng lại theo yêu cầu (chỉ xem plan, không chỉnh sửa file).{RESET}\n")
                sys.exit(0)

            else:
                print(f"  {RED}✗ Lựa chọn không hợp lệ. Vui lòng nhập 'proceed', 'replan <lý do>', hoặc 'quit'.{RESET}")
