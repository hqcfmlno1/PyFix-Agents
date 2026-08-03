"""
BugExplainerNode — Giải thích nguyên nhân lỗi sơ bộ & xử lý lỗi RT3 Environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_header
from graph.models import BugFixState, BugType

from graph.nodes.planning import PlanningNode


@dataclass
class BugExplainerNode(BaseNode[BugFixState]):
    """
    [Deterministic] Giải thích sơ bộ Unhandled Runtime Exception cho người dùng.
    Nếu phát hiện lỗi Môi trường (RUNTIME_ENVIRONMENT), hiển thị hướng dẫn xử lý thủ công và kết thúc.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> PlanningNode:
        from graph.nodes.planning import PlanningNode

        print_header("Chẩn đoán & Phân tích Call Stack")

        types_display = []
        for bt in ctx.state.bug_types:
            if bt == BugType.DATA_DRIVEN_RUNTIME:
                types_display.append(f"{YELLOW}DATA-DRIVEN RUNTIME (Dữ liệu sai schema / dev hiểu sai data){RESET}")
            elif bt == BugType.LOGIC_DRIVEN_RUNTIME:
                types_display.append(f"{YELLOW}LOGIC-DRIVEN RUNTIME (Lỗi thuật toán / edge-case khi lặp/tính toán){RESET}")

        print(f"  {BOLD}Phân loại Unhandled Exception:{RESET} {', '.join(types_display)}")
        if ctx.state.error_class:
            msg_str = f": {ctx.state.error_message}" if ctx.state.error_message else ""
            print(f"  {BOLD}Exception Name:{RESET} {RED}{ctx.state.error_class}{msg_str}{RESET}")

        if ctx.state.stack_trace:
            print(f"\n  {BOLD}📍 Call Stack nội bộ ({len(ctx.state.stack_trace)} frames):{RESET}")
            for frame in ctx.state.stack_trace:
                badge = f"{RED}[Crash Point]{RESET}" if frame.role == "crash_point" else f"{CYAN}[Caller]{RESET}"
                print(f"     • {badge} {frame.file_path}:{frame.line_number} in `{frame.function_name or 'main'}`")
                if frame.code_snippet:
                    print(f"       Code: {YELLOW}{frame.code_snippet}{RESET}")

        if ctx.state.bug_explanation:
            print(f"\n  {BOLD}💡 Tóm tắt chẩn đoán sơ bộ:{RESET}\n  {ctx.state.bug_explanation}")

        return PlanningNode()
