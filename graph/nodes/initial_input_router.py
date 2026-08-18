"""
InitialInputRouterNode — nhận input ban đầu một lần rồi route sang nhánh traceback hoặc symptom.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.input_analyzer import InputAnalyzerNode
    from graph.nodes.symptom_input import SymptomInputNode

_FRAME_RE = re.compile(r'^\s*File ".+?", line \d+', re.MULTILINE)
_EXC_RE = re.compile(r'^[A-Za-z_][\w.]*?(Error|Exception|Exit|Interrupt)\s*:', re.MULTILINE)


def _looks_like_traceback(text: str) -> tuple[bool, str]:
    lowered = (text or "").lower()
    frame_count = len(_FRAME_RE.findall(text or ""))
    has_traceback_banner = "traceback (most recent call last):" in lowered
    has_exception_line = bool(_EXC_RE.search(text or ""))

    if has_traceback_banner and frame_count >= 1:
        return True, f"Detected traceback banner with {frame_count} Python frame(s)."
    if frame_count >= 2 and has_exception_line:
        return True, f"Detected {frame_count} frame lines plus an exception summary."
    if frame_count >= 1 and any(sig in lowered for sig in ["keyerror:", "typeerror:", "valueerror:", "attributeerror:", "indexerror:"]):
        return True, f"Detected Python frame lines plus a runtime exception signature ({frame_count} frame(s))."
    return False, "No structured traceback pattern detected; treating input as free-form symptom."


def _read_multiline_input() -> str:
    print(f"{BOLD}Mô tả lỗi / Traceback log (Nhấn Enter 2 lần liên tiếp để kết thúc nhập):{RESET}")
    lines = []
    empty_count = 0
    while True:
        try:
            line = input()
            if line.strip().lower() == "quit" and not lines:
                sys.exit(0)
            if not line.strip():
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0
            lines.append(line)
        except (EOFError, KeyboardInterrupt):
            if not lines:
                sys.exit(0)
            break
    print()
    return "\n".join(lines).strip()


@dataclass
class InitialInputRouterNode(BaseNode[BugFixState]):
    """[Deterministic] Nhận input ban đầu một lần rồi route sang analyzer hoặc symptom flow."""

    async def run(self, ctx: GraphRunContext[BugFixState]) -> "InputAnalyzerNode | SymptomInputNode":
        from graph.nodes.input_analyzer import InputAnalyzerNode
        from graph.nodes.symptom_input import SymptomInputNode

        print(f"\n{'─' * 60}")
        print_step("🧭", "Input Router", "Nhập traceback đầy đủ hoặc mô tả symptom tự do. Hệ thống sẽ tự chọn luồng phù hợp.")
        print(
            f"""
  {BOLD}Hướng dẫn nhập:{RESET}
  • {GREEN}Traceback/runtime log rõ ràng{RESET} → đi nhánh phân tích có cấu trúc.
  • {CYAN}Mô tả symptom/hành vi lỗi tự do{RESET} → đi nhánh symptom-first.
  • {YELLOW}Tip:{RESET} Nhập 'quit' để thoát.
"""
        )

        raw_input = (ctx.state.raw_user_input or "").strip()
        if not raw_input and ctx.state.preset_user_input is not None:
            raw_input = ctx.state.preset_user_input.strip()
            print(f"{BOLD}Mô tả lỗi / Traceback log:{RESET}")
            print(raw_input)
            print()
        elif not raw_input:
            raw_input = _read_multiline_input()

        if not raw_input:
            if ctx.state.non_interactive:
                raise ValueError("Không có input đầu vào cho hybrid router.")
            print_step("⚠", "Input Router", "Bạn chưa nhập dữ liệu. Vui lòng mô tả symptom hoặc dán traceback.")
            return InitialInputRouterNode()

        ctx.state.raw_user_input = raw_input
        is_traceback, reason = _looks_like_traceback(raw_input)
        ctx.state.input_route_reason = reason

        if is_traceback:
            ctx.state.initial_input_kind = "traceback"
            print_step("🛣", "Input Router", f"{GREEN}Route -> InputAnalyzerNode{RESET} ({reason})")
            return InputAnalyzerNode()

        ctx.state.initial_input_kind = "symptom"
        print_step("🛣", "Input Router", f"{CYAN}Route -> SymptomInputNode{RESET} ({reason})")
        return SymptomInputNode()
