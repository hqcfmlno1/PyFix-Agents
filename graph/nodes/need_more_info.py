"""
NeedMoreInfoNode — Yêu cầu nhập bổ sung thông tin khi thiếu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import RESET, YELLOW
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.input_analyzer import InputAnalyzerNode


@dataclass
class NeedMoreInfoNode(BaseNode[BugFixState]):
    """
    [Deterministic] Yêu cầu user nhập bổ sung thông tin.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> InputAnalyzerNode:
        from graph.nodes.input_analyzer import InputAnalyzerNode

        print(f"\n  {YELLOW}📋 Vui lòng cung cấp thêm thông tin:{RESET}")
        for field in ctx.state.missing_fields:
            print(f"     → {field}")
        print()
        return InputAnalyzerNode()
