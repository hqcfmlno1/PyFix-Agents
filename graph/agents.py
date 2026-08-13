"""
Agent Definitions — input_analyzer, planner, coder agents.
"""

from __future__ import annotations


from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import Tool

from graph.config import MCP_SERVER_URL, model, BOLD, CYAN, RESET
from graph.config import analyzer_model, planner_model, coder_model, repro_model
from graph.config import ANALYZER_MODEL_NAME, PLANNER_MODEL_NAME, CODER_MODEL_NAME, REPRO_MODEL_NAME
from graph.models import BugExplanation, BugReport, CodeFix, PlanWrapper
from graph.prompts import CODER_PROMPT, INPUT_ANALYZER_PROMPT, PLANNER_PROMPT

import contextvars
current_agent_name: contextvars.ContextVar[str] = contextvars.ContextVar("current_agent_name", default="AGENT")

# ── Monkey-patch Agent.run để theo dõi Agent đang chạy ───────────────────────
original_agent_run = Agent.run

async def patched_agent_run(self, *args, **kwargs):
    agent_name = getattr(self, 'name', None) or "AGENT"
    token = current_agent_name.set(agent_name.upper())
    try:
        return await original_agent_run(self, *args, **kwargs)
    finally:
        current_agent_name.reset(token)

Agent.run = patched_agent_run

# ── Monkey-patch Model.request để in nội dung suy nghĩ Real-time ────────────
import types
from pydantic_ai.messages import ModelResponse, TextPart
try:
    from pydantic_ai.messages import ThinkingPart
except ImportError:
    ThinkingPart = None

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.google import GoogleModel

def inject_realtime_logging(original_request_method):
    async def patched_request(self, *args, **kwargs):
        result = await original_request_method(self, *args, **kwargs)
        
        # Pydantic AI versions differ: some return (ModelResponse, Usage), some return ModelResponse directly.
        response = None
        if isinstance(result, tuple) and len(result) >= 1:
            response = result[0]
        elif isinstance(result, ModelResponse):
            response = result
            
        if response and getattr(response, 'parts', None):
            agent_name = current_agent_name.get()
            for part in response.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    from graph.config import BOLD, MAGENTA, RESET
                    print(f"\n  {BOLD}{MAGENTA}💬 [{agent_name}] LÝ DO:{RESET}")
                    print(f"  {MAGENTA}{part.content.strip()}{RESET}")
                elif ThinkingPart and isinstance(part, ThinkingPart) and part.content.strip():
                    from graph.config import BOLD, MAGENTA, RESET
                    print(f"\n  {BOLD}{MAGENTA}🧠 [{agent_name}] SUY NGHĨ:{RESET}")
                    print(f"  {MAGENTA}{part.content.strip()}{RESET}")
        return result
    return patched_request

OpenAIChatModel.request = inject_realtime_logging(OpenAIChatModel.request)
GoogleModel.request = inject_realtime_logging(GoogleModel.request)


# ── Monkey-patch MCPToolset để in log khi gọi tool ───────────────────────────
original_call_tool = MCPToolset.call_tool

async def patched_call_tool(self, name: str, arguments: dict, *args, **kwargs):
    agent_name = current_agent_name.get()
    print(f"\n  {BOLD}{CYAN}🛠  [{agent_name}] ĐANG GỌI TOOL: {name}{RESET}")
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
    coder_model,  # Gemma — nhẹ, nhanh
    name="Input Analyzer",
    output_type=BugReport,
    system_prompt=INPUT_ANALYZER_PROMPT,
    retries=2,
)

# ── Lập Kế Hoạch & Suy Luận ──────────────────────────────────────────
planner_agent: Agent[None, PlanWrapper] = Agent(
    planner_model,  
    name="Planner",
    output_type=PlanWrapper,
    system_prompt=PLANNER_PROMPT,
    toolsets=[mcp_toolset_planner],
    tools=[ask_human_tool],
    retries=2,
)

# ── Viết Kịch Bản Tái Hiện (Reproduction) ───────────────────────────
repro_agent: Agent[None, str] = Agent(
    repro_model,  # DeepSeek-R1-0528 — reasoning mạnh, viết repro chính xác
    name="Reproduction Agent",
    output_type=str,
    system_prompt="Bạn là một Software Engineer / QA chuyên nghiệp. Nhiệm vụ của bạn là viết kịch bản Python độc lập (Reproduction Script) để tái hiện chính xác lỗi dựa trên Traceback được cung cấp.",
    toolsets=[mcp_toolset_planner],
    retries=2,
)

from typing import Union

# Coder — DeepSeek-V3 (không thinking): viết code nhanh, xuất JSON chẳt chẽ
coder_agent: Agent[None, Union[BugExplanation, CodeFix]] = Agent(
    coder_model,
    name="Coder Agent",
    output_type=Union[BugExplanation, CodeFix],
    system_prompt=CODER_PROMPT,
    toolsets=[mcp_toolset_coder],
    retries=2,
)
