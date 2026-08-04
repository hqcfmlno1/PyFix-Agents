"""
Agent Definitions — input_analyzer, planner, coder agents.
"""

from __future__ import annotations

from typing import Union

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import Tool

from graph.config import MCP_SERVER_URL, model
from graph.models import BugReport, CodeFix, DirectFix, PlanWrapper
from graph.prompts import CODER_PROMPT, INPUT_ANALYZER_PROMPT, PLANNER_PROMPT

# ── MCP Toolsets ─────────────────────────────────────────────────────────────
mcp_toolset = MCPToolset(MCP_SERVER_URL)

# Planner: đọc file, tìm kiếm codebase, liệt kê thư mục
mcp_toolset_planner = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in [
        "read_file",
        "list_dir",
        "search_in_codebase",
    ]
)

# Coder: đọc file, tìm kiếm, linter, chạy lệnh để tái hiện/kiểm tra lỗi
mcp_toolset_coder = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in [
        "read_file",
        "list_dir",
        "search_in_codebase",
        "run_linter",
        "run_command",
    ]
)


# ── ask_human Tool (Native Tool cho Planner) ─────────────────────────────────
def _ask_human_sync(question: str) -> str:
    """
    Hỏi lập trình viên khi Planner cần thêm thông tin về runtime data
    hoặc context mà không thể suy luận từ code (VD: schema thực tế của data
    payload, giá trị biến tại thời điểm lỗi, v.v.).

    Args:
        question: Câu hỏi rõ ràng, cụ thể gửi đến lập trình viên.
    """
    from graph.config import BOLD, CYAN, RESET, YELLOW
    print(f"\n{'─' * 60}")
    print(f"  {BOLD}{YELLOW}❓ PLANNER HỎI BẠN:{RESET}")
    print(f"  {CYAN}{question}{RESET}")
    print(f"{'─' * 60}")
    answer = input("  Trả lời của bạn: ").strip()
    print(f"{'─' * 60}\n")
    return answer if answer else "(Không có câu trả lời)"


ask_human_tool = Tool(_ask_human_sync, name="ask_human")


# ── Agent Definitions ────────────────────────────────────────────────────────
input_analyzer_agent: Agent[None, BugReport] = Agent(
    model,
    output_type=BugReport,
    system_prompt=INPUT_ANALYZER_PROMPT,
    retries=2,
)

# ── Lập Kế Hoạch & Suy Luận ──────────────────────────────────────────
planner_agent: Agent[None, Union[DirectFix, PlanWrapper]] = Agent(
    model,
    output_type=Union[DirectFix, PlanWrapper],
    system_prompt=PLANNER_PROMPT,
    toolsets=[mcp_toolset_planner],
    tools=[ask_human_tool],
    retries=2,
)

coder_agent: Agent[None, CodeFix] = Agent(
    model,
    output_type=CodeFix,
    system_prompt=CODER_PROMPT,
    toolsets=[mcp_toolset_coder],
    retries=2,
)
