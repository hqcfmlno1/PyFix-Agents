"""
PyFix-Agents v2 — Main CLI Entry Point
Chạy hệ thống multi-agent sửa lỗi Python tự động kết nối với FastMCP Server.
"""

from __future__ import annotations

import asyncio
import sys

from graph import BugFixState, ProjectInitializerNode, bug_fix_graph
from graph.config import CYAN, MCP_SERVER_URL, RESET, BOLD, YELLOW, RED


async def main() -> None:
    """Khởi chạy PyFix-Agents CLI."""
    print(f"{CYAN}🔗 Kết nối với PyFix MCP Server tại: {MCP_SERVER_URL}{RESET}")

    state = BugFixState()
    try:
        result: str = await bug_fix_graph.run(
            state=state,
            inputs=ProjectInitializerNode(),
        )
        print(f"\n{BOLD}🏁 Kết quả hoàn thành.{RESET}")
    except KeyboardInterrupt:
        print(f"\n{YELLOW}👋 Đã hủy bởi người dùng.{RESET}")
    except Exception as exc:
        print(f"\n{RED}❌ Lỗi không mong đợi: {exc}{RESET}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
