"""
Agent Definitions — input_analyzer, planner, coder agents.
"""

from __future__ import annotations

from typing import List, Union

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from graph.config import MCP_SERVER_URL, model
from graph.models import BugReport, CodeFix, DirectFix, PlanWrapper
from graph.prompts import CODER_PROMPT, INPUT_ANALYZER_PROMPT, PLANNER_PROMPT

# ── MCP Toolsets ─────────────────────────────────────────────────────────────
mcp_toolset = MCPToolset(MCP_SERVER_URL)

# Planner chỉ cần đọc file, liệt kê thư mục
mcp_toolset_planner = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in ["read_file", "list_dir"]
)

# Coder cần đọc file, liệt kê thư mục, và chạy linter để tự kiểm tra
mcp_toolset_coder = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in ["read_file", "list_dir", "run_linter"]
)


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
    retries=2,
)

coder_agent: Agent[None, CodeFix] = Agent(
    model,
    output_type=CodeFix,
    system_prompt=CODER_PROMPT,
    toolsets=[mcp_toolset_coder],
    retries=2,
)
