"""
Agent Definitions — input_analyzer, planner, coder agents.
"""

from __future__ import annotations


from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import Tool

from graph.config import MCP_SERVER_URL, model, BOLD, CYAN, RESET
from graph.config import analyzer_model, planner_model, coder_model, ANALYZER_MODEL_NAME, PLANNER_MODEL_NAME, CODER_MODEL_NAME
from graph.models import BugExplanation, BugReport, CodeFix, PlanWrapper
from graph.prompts import CODER_PROMPT, INPUT_ANALYZER_PROMPT, PLANNER_PROMPT

# ── Monkey-patch MCPToolset để in log khi gọi tool ───────────────────────────
original_call_tool = MCPToolset.call_tool

async def patched_call_tool(self, name: str, arguments: dict, *args, **kwargs):
    print(f"\n  {BOLD}{CYAN}🛠  AGENT ĐANG GỌI TOOL: {name}{RESET}")
    if arguments:
        print(f"  {CYAN}Tham số: {arguments}{RESET}")
    return await original_call_tool(self, name, arguments, *args, **kwargs)

MCPToolset.call_tool = patched_call_tool


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

# Coder: đọc file, tìm kiếm, lấy cấu trúc thư mục
mcp_toolset_coder = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in [
        "read_file",
        "list_dir",
        "search_in_codebase",
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
    planner_model,  # Gemma — nhẹ, nhanh
    output_type=BugReport,
    system_prompt=INPUT_ANALYZER_PROMPT,
    retries=2,
)

# ── Lập Kế Hoạch & Suy Luận ──────────────────────────────────────────
planner_agent: Agent[None, PlanWrapper] = Agent(
    planner_model,  # DeepSeek-R1 thinking=high
    output_type=PlanWrapper,
    system_prompt=PLANNER_PROMPT,
    toolsets=[mcp_toolset_planner],
    tools=[ask_human_tool],
    retries=2,
)

# ── Viết Kịch Bản Tái Hiện (Reproduction) ───────────────────────────
repro_agent: Agent[None, str] = Agent(
    planner_model,  # Dùng gemma free
    output_type=str,
    system_prompt="Bạn là một Software Engineer / QA chuyên nghiệp. Nhiệm vụ của bạn là viết kịch bản Python độc lập (Reproduction Script) để tái hiện chính xác lỗi dựa trên Traceback được cung cấp.",
    toolsets=[mcp_toolset_planner],
    retries=2,
)

from typing import Union

# Coder — DeepSeek-V3 (không thinking): viết code nhanh, xuất JSON chẳt chẽ
coder_agent: Agent[None, Union[BugExplanation, CodeFix]] = Agent(
    coder_model,
    output_type=Union[BugExplanation, CodeFix],
    system_prompt=CODER_PROMPT,
    toolsets=[mcp_toolset_coder],
    retries=2,
)
