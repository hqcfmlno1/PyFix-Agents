"""
SymptomInputNode — chuẩn hóa symptom tự do và áp dụng soft scope gate cho runtime logic/data.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugComplexity, BugFixState, BugType

if TYPE_CHECKING:
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode

_DATA_HINTS = {
    "missing",
    "omit",
    "schema",
    "payload",
    "keyerror",
    "typeerror",
    "valueerror",
    "attributeerror",
    "validation",
    "field",
    "fields",
    "null",
    "none",
    "deserialize",
    "422",
    "invalid literal",
}
_LOGIC_HINTS = {
    "timeout",
    "hang",
    "race",
    "index",
    "loop",
    "deadlock",
    "logic",
    "concurrent",
    "threshold",
    "guard",
    "branch",
    "semaphore",
    "retry",
    "ordering",
    "state",
    "empty list",
    "list index",
    "misroute",
    "vanish",
    "wrongly rejected",
    "accepted and processed",
    "shared list",
    "messages silently vanish",
}
_UNSUPPORTED_HINTS = {
    "api key",
    "credential",
    "network",
    "dns",
    "connection refused",
    "service unavailable",
    "docker",
    "kubernetes",
    "terraform",
    "helm",
    "permission denied",
    "certificate",
    "proxy",
    "dependency",
    "pip install",
    "version mismatch",
    "oauth",
    "auth token",
    "postgres is down",
}


def _infer_scope(text: str) -> tuple[list[BugType], bool, str, str]:
    lowered = (text or "").lower()
    bug_types: list[BugType] = []
    has_unsupported = any(token in lowered for token in _UNSUPPORTED_HINTS)

    if has_unsupported:
        return [], False, "Bug có vẻ phụ thuộc môi trường/integration nằm ngoài scope hiện tại của PyFix (chỉ hỗ trợ runtime logic/data).", "unsupported"

    if any(token in lowered for token in _DATA_HINTS):
        bug_types.append(BugType.DATA_DRIVEN_RUNTIME)
    if any(token in lowered for token in _LOGIC_HINTS):
        bug_types.append(BugType.LOGIC_DRIVEN_RUNTIME)

    if bug_types:
        return bug_types, True, "", "supported"
    return [], True, "Chưa đủ tín hiệu để phân loại chắc vào runtime data-driven hay runtime logic-driven; vẫn tiếp tục theo soft scope gate.", "uncertain"


def _prompt_for_symptom() -> str:
    print(f"{BOLD}Symptom / Mô tả lỗi (Nhấn Enter 2 lần liên tiếp để kết thúc nhập):{RESET}")
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
class SymptomInputNode(BaseNode[BugFixState]):
    """[Deterministic] Nhận symptom/log/mô tả lỗi dạng tự do và áp dụng soft scope gate."""

    async def run(self, ctx: GraphRunContext[BugFixState]) -> "PlanningNode | ReportNode":
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode

        print(f"\n{'─' * 60}")
        print_step("📝", "Symptom Intake", "Chuẩn hóa symptom tự do cho planner và kiểm tra scope runtime logic/data.")

        raw_input = (ctx.state.raw_user_input or "").strip()
        if not raw_input and ctx.state.preset_user_input is not None:
            raw_input = ctx.state.preset_user_input.strip()
            print(f"{BOLD}Symptom / Mô tả lỗi:{RESET}")
            print(raw_input)
            print()
        elif not raw_input:
            raw_input = _prompt_for_symptom()

        if not raw_input:
            if ctx.state.non_interactive:
                raise ValueError("Không có symptom đầu vào cho Planner.")
            print_step("⚠", "Symptom Intake", "Bạn chưa nhập symptom. Vui lòng mô tả bug cụ thể hơn.")
            return SymptomInputNode()

        ctx.state.raw_user_input = raw_input
        bug_types, supported, reason, confidence = _infer_scope(raw_input)
        ctx.state.scope_supported = supported
        ctx.state.scope_rejection_reason = reason if not supported else ""
        ctx.state.scope_confidence = confidence
        ctx.state.want_plan = not ctx.state.non_interactive
        ctx.state.complexity = BugComplexity.COMPLEX
        ctx.state.bug_types = bug_types
        ctx.state.stack_trace = []
        ctx.state.target_file = None
        ctx.state.error_file = None
        ctx.state.error_line = None
        ctx.state.error_class = None
        ctx.state.error_message = None
        ctx.state.missing_fields = []
        ctx.state.root_cause_explanation = ""

        if not supported:
            ctx.state.want_apply = False
            ctx.state.surrendered = True
            ctx.state.final_explanation = reason
            print_step("⛔", "Scope Gate", f"{YELLOW}{reason}{RESET}")
            return ReportNode()

        if bug_types:
            types_str = ", ".join(bt.value for bt in bug_types)
            print_step("✅", "Scope Gate", f"Trong scope hỗ trợ: {CYAN}{types_str}{RESET}")
        else:
            print_step("⚠", "Soft Scope Gate", f"{YELLOW}{reason}{RESET}")
            print_step("🧭", "Soft Scope Gate", "Tiếp tục sang Planner với confidence thấp và template tổng quát.")
        return PlanningNode()
