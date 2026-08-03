"""
Agent Definitions — input_analyzer, planner, coder agents.
"""

from __future__ import annotations

from typing import List

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from graph.config import MCP_SERVER_URL, model
from graph.models import BugReport, CodeFix, DirectFix, PlanStep
from graph.prompts import CODER_PROMPT, DIRECT_FIX_PROMPT, INPUT_ANALYZER_PROMPT, PLANNER_PROMPT

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

planner_agent: Agent[None, List[PlanStep]] = Agent(
    model,
    output_type=List[PlanStep],
    system_prompt=PLANNER_PROMPT,
    toolsets=[mcp_toolset_planner],
    retries=2,
)

direct_fix_agent: Agent[None, DirectFix] = Agent(
    model,
    output_type=DirectFix,
    system_prompt=DIRECT_FIX_PROMPT,
    toolsets=[mcp_toolset_coder],
    retries=2,
)

coder_agent: Agent[None, CodeFix] = Agent(
    model,
    output_type=CodeFix,
    system_prompt=CODER_PROMPT,
    toolsets=[mcp_toolset_coder],
    retries=2,
)
