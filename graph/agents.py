"""
Agent Definitions — input_analyzer, planner, repro, coder agents.
"""

from __future__ import annotations

import contextvars
import os
import time
from datetime import datetime
from functools import lru_cache
from typing import Union

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.tools import Tool

from graph.config import BOLD, CYAN, MCP_SERVER_URL, RESET
from graph.config import analyzer_model, planner_model, coder_model, repro_model
from graph.models import BugExplanation, BugReport, PlanWrapper
from graph.prompts import CODER_PROMPT, INPUT_ANALYZER_PROMPT, PLANNER_PROMPT

current_agent_name: contextvars.ContextVar[str] = contextvars.ContextVar("current_agent_name", default="AGENT")

DEFAULT_PLANNER_USE_MCP = os.getenv("PYFIX_PLANNER_USE_MCP", "0") == "1"
DEFAULT_CODER_USE_MCP = os.getenv("PYFIX_CODER_USE_MCP", "0") == "1"
DEFAULT_NON_INTERACTIVE = os.getenv("PYFIX_NON_INTERACTIVE", "0") == "1"

original_agent_run = Agent.run


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def patched_agent_run(self, *args, **kwargs):
    agent_name = getattr(self, "name", None) or "AGENT"
    token = current_agent_name.set(agent_name.upper())
    started = time.perf_counter()
    print(f"\n  {BOLD}{CYAN}⏱ [{agent_name.upper()}] Agent.run start {_ts()}{RESET}")
    try:
        result = await original_agent_run(self, *args, **kwargs)
        elapsed = time.perf_counter() - started
        print(f"  {BOLD}{CYAN}⏱ [{agent_name.upper()}] Agent.run done in {elapsed:.2f}s at {_ts()}{RESET}")
        return result
    except Exception:
        elapsed = time.perf_counter() - started
        print(f"  {BOLD}{CYAN}⏱ [{agent_name.upper()}] Agent.run failed after {elapsed:.2f}s at {_ts()}{RESET}")
        raise
    finally:
        current_agent_name.reset(token)


Agent.run = patched_agent_run

try:
    from pydantic_ai.messages import ThinkingPart
except ImportError:
    ThinkingPart = None


def inject_realtime_logging(original_request_method):
    async def patched_request(self, *args, **kwargs):
        agent_name = current_agent_name.get()
        started = time.perf_counter()
        model_name = getattr(self, "model_name", None) or getattr(self, "model", None) or self.__class__.__name__
        print(f"\n  {BOLD}{CYAN}⏱ [{agent_name}] model request start ({model_name}) {_ts()}{RESET}")
        try:
            result = await original_request_method(self, *args, **kwargs)
            elapsed = time.perf_counter() - started
            print(f"  {BOLD}{CYAN}⏱ [{agent_name}] model request done in {elapsed:.2f}s ({model_name}) {_ts()}{RESET}")
        except Exception:
            elapsed = time.perf_counter() - started
            print(f"  {BOLD}{CYAN}⏱ [{agent_name}] model request failed after {elapsed:.2f}s ({model_name}) {_ts()}{RESET}")
            raise

        response = None
        if isinstance(result, tuple) and len(result) >= 1:
            response = result[0]
        elif isinstance(result, ModelResponse):
            response = result

        if response and getattr(response, "parts", None):
            for part in response.parts:
                if ThinkingPart and isinstance(part, ThinkingPart) and part.content.strip():
                    from graph.config import MAGENTA

                    print(f"\n  {BOLD}{MAGENTA}🧠 [{agent_name}] SUY NGHĨ:{RESET}")
                    print(f"  {MAGENTA}{part.content.strip()}{RESET}")
        return result

    return patched_request


OpenAIChatModel.request = inject_realtime_logging(OpenAIChatModel.request)
GoogleModel.request = inject_realtime_logging(GoogleModel.request)

original_call_tool = MCPToolset.call_tool


async def patched_call_tool(self, name: str, arguments: dict, *args, **kwargs):
    agent_name = current_agent_name.get()
    started = time.perf_counter()
    print(f"\n  {BOLD}{CYAN}🛠  [{agent_name}] ĐANG GỌI TOOL: {name} at {_ts()}{RESET}")
    if arguments:
        print(f"  {CYAN}Tham số: {arguments}{RESET}")
    try:
        result = await original_call_tool(self, name, arguments, *args, **kwargs)
        elapsed = time.perf_counter() - started
        print(f"  {BOLD}{CYAN}⏱ [{agent_name}] tool {name} done in {elapsed:.2f}s at {_ts()}{RESET}")
        return result
    except Exception:
        elapsed = time.perf_counter() - started
        print(f"  {BOLD}{CYAN}⏱ [{agent_name}] tool {name} failed after {elapsed:.2f}s at {_ts()}{RESET}")
        raise


MCPToolset.call_tool = patched_call_tool

mcp_toolset = MCPToolset(MCP_SERVER_URL)
mcp_toolset_planner = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in ["read_file", "list_dir", "search_in_codebase"]
)
mcp_toolset_coder = mcp_toolset.filtered(
    lambda ctx, tool_def: tool_def.name in ["read_file", "list_dir", "search_in_codebase"]
)


def _ask_human_sync(question: str) -> str:
    from graph.config import BOLD, CYAN, RESET, YELLOW

    print(f"\n{'─' * 60}")
    print(f"  {BOLD}{YELLOW}❓ PLANNER HỎI BẠN:{RESET}")
    print(f"  {CYAN}{question}{RESET}")
    print(f"{'─' * 60}")
    if os.getenv("PYFIX_NON_INTERACTIVE") == "1":
        answer = "Không có thêm thông tin runtime ngoài symptom/traceback đã cung cấp. Hãy suy luận từ code hiện có và chọn giả định an toàn nhất."
        print(f"  {YELLOW}[auto-answer]{RESET} {answer}")
        print(f"{'─' * 60}\n")
        return answer
    answer = input("  Trả lời của bạn: ").strip()
    print(f"{'─' * 60}\n")
    return answer if answer else "(Không có câu trả lời)"


ask_human_tool = Tool(_ask_human_sync, name="ask_human")

input_analyzer_agent: Agent[None, BugReport] = Agent(
    analyzer_model,
    name="Input Analyzer",
    output_type=BugReport,
    system_prompt=INPUT_ANALYZER_PROMPT,
    retries=2,
)


def _planner_system_prompt(use_mcp: bool) -> str:
    if use_mcp:
        return PLANNER_PROMPT
    return (
        PLANNER_PROMPT
        + "\n\nQUAN TRỌNG CHO PHIÊN NÀY: Bạn KHÔNG có bất kỳ tool nào. "
        + "Tuyệt đối không gọi, không nhắc tới, không giả định tồn tại read_file, list_dir, search_in_codebase hay bất kỳ tool nào khác. "
        + "Chỉ được suy luận từ symptom, project tree và local context đã được cung cấp ngay trong prompt. "
        + "Nếu thông tin chưa hoàn hảo, hãy chọn giả định an toàn nhất và vẫn phải trả về PlanWrapper."
    )


def _coder_system_prompt(use_mcp: bool) -> str:
    if use_mcp:
        return CODER_PROMPT
    return (
        CODER_PROMPT
        + "\n\nQUAN TRỌNG CHO PHIÊN NÀY: Bạn KHÔNG có tool trong agent session này. "
        + "Nếu prompt đã cung cấp nội dung file và context liên quan thì chỉ được suy luận từ đó, không được giả định có read_file hay search_in_codebase."
    )


def _repro_system_prompt(use_mcp: bool) -> str:
    base = "Bạn là một Software Engineer / QA chuyên nghiệp. Nhiệm vụ của bạn là viết kịch bản Python độc lập (Reproduction Script) để tái hiện chính xác lỗi dựa trên Traceback được cung cấp."
    if use_mcp:
        return base
    return base + "\n\nQUAN TRỌNG CHO PHIÊN NÀY: Bạn không có tool. Nếu prompt không đủ dữ liệu để tái hiện chính xác thì hãy viết script ngắn nhất dựa trên thông tin đã có và tránh giả định quá mức."


@lru_cache(maxsize=4)
def get_planner_agent(use_mcp: bool, non_interactive: bool) -> Agent[None, PlanWrapper]:
    return Agent(
        planner_model,
        name="Planner",
        output_type=PlanWrapper,
        system_prompt=_planner_system_prompt(use_mcp),
        toolsets=[mcp_toolset_planner] if use_mcp else [],
        tools=[] if non_interactive else [ask_human_tool],
        retries=2,
    )


@lru_cache(maxsize=2)
def get_repro_agent(use_mcp: bool) -> Agent[None, str]:
    return Agent(
        repro_model,
        name="Reproduction Agent",
        output_type=str,
        system_prompt=_repro_system_prompt(use_mcp),
        toolsets=[mcp_toolset_planner] if use_mcp else [],
        retries=4,
    )


@lru_cache(maxsize=2)
def get_coder_agent(use_mcp: bool) -> Agent[None, Union[BugExplanation, str]]:
    return Agent(
        coder_model,
        name="Coder Agent",
        output_type=Union[BugExplanation, str],
        system_prompt=_coder_system_prompt(use_mcp),
        toolsets=[mcp_toolset_coder] if use_mcp else [],
        retries=2,
    )


planner_agent = get_planner_agent(DEFAULT_PLANNER_USE_MCP, DEFAULT_NON_INTERACTIVE)
repro_agent = get_repro_agent(DEFAULT_PLANNER_USE_MCP)
coder_agent = get_coder_agent(DEFAULT_CODER_USE_MCP and not DEFAULT_NON_INTERACTIVE)
