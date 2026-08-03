"""
ProjectInitializerNode — Nhận repo path, validate và build project tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, GREEN, RED, RESET
from graph.helpers import build_project_tree, print_header, print_step
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.input_analyzer import InputAnalyzerNode


@dataclass
class ProjectInitializerNode(BaseNode[BugFixState]):
    """
    [Deterministic] Nhận đường dẫn repo từ user, validate và build project tree.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> InputAnalyzerNode:
        from graph.nodes.input_analyzer import InputAnalyzerNode

        print_header("PyFix-Agents v2.0  —  AI-Powered Python Bug Fixer")
        print(f"\n{BOLD}Chào mừng!{RESET} Hệ thống phân tích & tự động sửa lỗi Python với Bug Classifier & Plan Templates.\n")

        while True:
            raw = input(f"{BOLD}📁 Nhập đường dẫn đến thư mục dự án:{RESET} ").strip()
            if not raw:
                print(f"  {RED}✗ Vui lòng nhập đường dẫn.{RESET}")
                continue

            abs_path = os.path.abspath(raw)
            if not os.path.isdir(abs_path):
                print(f"  {RED}✗ Thư mục không tồn tại: {abs_path}{RESET}")
                continue

            ctx.state.repo_path = abs_path
            ctx.state.is_repo_valid = True
            print(f"  {GREEN}✓ Đã nhận dự án: {abs_path}{RESET}")
            break

        print_step("📂", "Project Tree", "")
        tree = build_project_tree(ctx.state.repo_path)
        ctx.state.project_tree = tree
        print(tree)

        return InputAnalyzerNode()
