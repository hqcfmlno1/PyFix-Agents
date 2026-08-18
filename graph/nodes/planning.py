"""
PlanningNode — Planner Agent phân tích symptom/runtime bug và sinh ra PlanWrapper.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import DEFAULT_PLANNER_USE_MCP, get_planner_agent
from graph.config import BOLD, CYAN, PLANNER_MODEL_NAME, RED, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugFixState, PlanWrapper
from graph.prompts import GENERIC_PLAN_TEMPLATE, PLAN_TEMPLATES

if TYPE_CHECKING:
    from graph.nodes.plan_interceptor import PlanInterceptorNode
    from graph.nodes.report import ReportNode
    from graph.nodes.validation import ValidationNode

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".pytest_cache", ".mypy_cache"}
_CODE_EXTS = {".py", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".json"}
_STOPWORDS = {
    "that", "this", "with", "from", "into", "instead", "clean", "returns", "return", "request",
    "should", "would", "there", "their", "about", "when", "what", "where", "which", "have",
}


def _extract_keywords(text: str) -> list[str]:
    lowered = (text or "").lower()
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", lowered)
    status_hits = re.findall(r"\b[1-5]\d\d\b", lowered)
    seen: list[str] = []
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if token not in seen:
            seen.append(token)
    for token in status_hits:
        if token not in seen:
            seen.append(token)
    return seen[:14]


def _iter_repo_files(repo_path: str):
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in _CODE_EXTS:
                yield os.path.join(root, name)


def _normalize_rel_path(path: str, repo_path: str) -> str:
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        try:
            return os.path.relpath(normalized, repo_path)
        except ValueError:
            return normalized
    return normalized


def _score_file(rel_path: str, content: str, keywords: list[str]) -> int:
    rel_lower = rel_path.lower()
    content_lower = content.lower()
    score = 0
    for kw in keywords:
        if kw in rel_lower:
            score += 5
        hits = content_lower.count(kw)
        score += min(hits, 10)
    return score


def _snippet_for_file(rel_path: str, content: str, keywords: list[str]) -> str:
    lines = content.splitlines()
    hit_lines: list[int] = []
    lowered = [line.lower() for line in lines]
    for idx, line in enumerate(lowered):
        if any(kw in line for kw in keywords):
            hit_lines.append(idx)
    blocks: list[str] = []
    if hit_lines:
        used = set()
        for idx in hit_lines[:3]:
            start = max(0, idx - 3)
            end = min(len(lines), idx + 4)
            if (start, end) in used:
                continue
            used.add((start, end))
            numbered = "\n".join(f"{start + i + 1:4d} | {line}" for i, line in enumerate(lines[start:end]))
            blocks.append(numbered)
    else:
        head = lines[: min(len(lines), 80)]
        blocks.append("\n".join(f"{i + 1:4d} | {line}" for i, line in enumerate(head)))
    return f"FILE: {rel_path}\n" + "\n\n".join(blocks)


def _collect_local_context(ctx: GraphRunContext[BugFixState], limit: int = 5) -> tuple[str, int]:
    repo_path = ctx.state.repo_path
    if not repo_path or not os.path.isdir(repo_path):
        return "(không thu thập được local context)", 0

    keywords = _extract_keywords(ctx.state.raw_user_input)
    candidates: list[tuple[int, str, str]] = []
    stack_files = {
        _normalize_rel_path(frame.file_path, repo_path)
        for frame in ctx.state.stack_trace
        if frame.file_path
    }
    target_file = _normalize_rel_path(ctx.state.target_file, repo_path) if ctx.state.target_file else None

    for abs_path in _iter_repo_files(repo_path):
        rel_path = os.path.relpath(abs_path, repo_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except Exception:
            continue
        score = _score_file(rel_path, content, keywords)
        if rel_path in stack_files:
            score += 100
        if target_file and rel_path == target_file:
            score += 120
        if score > 0:
            candidates.append((score, rel_path, content))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    if not candidates:
        return "(không tìm được file liên quan trực tiếp từ symptom)", 0

    blocks = []
    for score, rel_path, content in candidates[:limit]:
        blocks.append(f"[score={score}]\n{_snippet_for_file(rel_path, content, keywords)}")
    return "\n\n".join(blocks), len(candidates)


def _record_mcp_fallback(ctx: GraphRunContext[BugFixState], reason: str) -> None:
    if reason not in ctx.state.mcp_fallback_events:
        ctx.state.mcp_fallback_events.append(reason)
    print_step("🔁", "Planner MCP Fallback", f"{YELLOW}{reason}{RESET}")


@dataclass
class PlanningNode(BaseNode[BugFixState]):
    """[Agent] Planner đọc symptom + codebase, khoanh vùng nguyên nhân gốc rễ và sinh PlanWrapper."""

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union["PlanInterceptorNode", "ValidationNode", "ReportNode"]:
        from graph.nodes.plan_interceptor import PlanInterceptorNode
        from graph.nodes.report import ReportNode
        from graph.nodes.validation import ValidationNode

        if not ctx.state.scope_supported:
            reason = ctx.state.scope_rejection_reason or "Bug nằm ngoài scope runtime logic/data của PyFix."
            ctx.state.surrendered = True
            ctx.state.final_explanation = reason
            return ReportNode()

        local_context, candidate_count = _collect_local_context(ctx)
        use_mcp = ctx.state.planner_use_mcp if ctx.state.planner_use_mcp is not None else DEFAULT_PLANNER_USE_MCP
        if not use_mcp and candidate_count == 0:
            use_mcp = True
            ctx.state.planner_use_mcp = True
            _record_mcp_fallback(ctx, "Planner bật MCP vì local context không tìm được file liên quan nào.")

        print_step(
            "🧠",
            "Planner Agent",
            f"Đang đọc code và phân tích symptom với {PLANNER_MODEL_NAME} (Thinking via CoT, MCP={'on' if use_mcp else 'off'})...",
        )

        prompt = f"""CHẨN ĐOÁN VÀ LẬP KẾ HOẠCH SỬA BUG RUNTIME / HÀNH VI BẤT THƯỜNG:
- Mô tả người dùng: {ctx.state.raw_user_input or 'N/A'}
- Repo path       : {ctx.state.repo_path}

THÔNG TIN DỰ ÁN:
{ctx.state.project_tree[:2500]}

BỐI CẢNH CODE ĐỊA PHƯƠNG (đã được hệ thống trích sẵn từ codebase, hãy dùng trước khi hỏi thêm):
{local_context}
"""

        if ctx.state.scope_confidence == "uncertain":
            prompt += """
CẢNH BÁO SOFT SCOPE GATE:
- Input này chưa được phân loại chắc chắn vào data-driven hay logic-driven runtime.
- Bạn VẪN phải cố gắng sửa trong phạm vi runtime bug nội bộ repo.
- Ưu tiên thay đổi nhỏ, đúng layer, không mở rộng sang môi trường/integration ngoài repo.
"""

        if ctx.state.error_class or ctx.state.error_message:
            prompt += f"""
THÔNG TIN LỖI ĐÃ BIẾT:
- Ngoại lệ      : {ctx.state.error_class or 'N/A'}
- Thông báo lỗi : {ctx.state.error_message or 'N/A'}
- File nghi vấn : {ctx.state.target_file or 'N/A'}
- Dòng nghi vấn : {ctx.state.error_line or 'N/A'}
"""

        if ctx.state.stack_trace:
            prompt += "\nCHI TIẾT CALL STACK (NẾU CÓ):\n"
            for frame in ctx.state.stack_trace:
                prompt += f"- File: {frame.file_path}, Line: {frame.line_number}, Function: {frame.function_name}\n"
                if frame.code_snippet:
                    prompt += f"  Code: {frame.code_snippet}\n"
        else:
            prompt += """
KHÔNG CÓ TRACEBACK CẤU TRÚC.
Bạn phải suy luận từ symptom, cấu trúc dự án, tên file, flow xử lý, và các snippet code đã được cung cấp.
KHÔNG được mở rộng ra ngoài scope runtime logic/data.
"""

        if getattr(ctx.state, "repro_confirmed", None) is False:
            prompt += """
KẾT QUẢ TÁI HIỆN LỖI (CHƯA XÁC NHẬN / THẤT BẠI):
⚠️ Cảnh báo: Lỗi này phụ thuộc vào runtime data phức tạp hoặc môi trường cụ thể.
Hãy thận trọng hơn khi đọc code và KHÔNG đưa ra giả định vô căn cứ.
"""

        if ctx.state.iteration_history or ctx.state.action_history or ctx.state.user_plan_feedback:
            prompt += "\nLỊCH SỬ CÁC VÒNG LẶP (CAUSAL CHAIN CONTEXT - RẤT QUAN TRỌNG):\n"
            if ctx.state.iteration_history:
                for i, iter_ctx in enumerate(ctx.state.iteration_history, 1):
                    prompt += f"--- Vòng {i} ---\n"
                    prompt += f"  - Lỗi ban đầu: {iter_ctx.initial_error}\n"
                    prompt += f"  - Các file đã sửa: {', '.join(iter_ctx.target_files)}\n"
                    prompt += f"  - Tóm tắt patch:\n{iter_ctx.patch_summary}\n"
                    prompt += f"  - Phản hồi/Lỗi mới từ User: {iter_ctx.user_feedback}\n"
            if ctx.state.action_history:
                prompt += "\nLịch sử các lần crash/lỗi hệ thống:\n"
                for log in ctx.state.action_history:
                    prompt += f"  - {log}\n"
            if ctx.state.user_plan_feedback:
                prompt += f"\nPhản hồi yêu cầu Replan lần này:\n  -> {ctx.state.user_plan_feedback}\n"
            prompt += "-> HÃY PHÂN TÍCH LÝ DO TẠI SAO CÁCH SỬA TRƯỚC ĐÓ LẠI THẤT BẠI VÀ TÌM HƯỚNG TIẾP CẬN MỚI. KHÔNG LẶP LẠI PLAN CŨ.\n"

        selected_template = None
        for bug_type in ctx.state.bug_types:
            template = PLAN_TEMPLATES.get(bug_type.value.upper())
            if template:
                selected_template = template
                break
        if not selected_template:
            selected_template = GENERIC_PLAN_TEMPLATE
        prompt += f"\n{selected_template}\n"

        if ctx.state.user_suggested_fix:
            prompt += (
                "\nGỢI Ý CÁCH SỬA TỪ NGƯỜI DÙNG (ƯU TIÊN CAO — BẮT BUỘC TUÂN THEO):\n"
                f"  → {ctx.state.user_suggested_fix}\n"
                "Lưu ý: Người dùng đã xem bản vá trước và chủ động đề xuất hướng sửa này. "
                "Plan mới PHẢI bám sát gợi ý của họ, KHÔNG được phớt lờ hoặc đề xuất hướng khác trừ khi gợi ý đó không khả thi về mặt kỹ thuật sau khi đọc code.\n"
            )

        prompt += """
LỆNH:
1. Chỉ giải quyết bug nằm trong scope runtime bug nội bộ repo; không mở rộng sang lỗi môi trường/integration ngoài repo.
2. Dùng local context đã được cung cấp trước; chỉ dùng tool nếu phiên hiện tại có MCP và bạn thật sự cần thêm xác nhận.
3. Trả về PlanWrapper với các bước sửa mã nguồn cụ thể, tối giản, đúng layer.
4. Nếu symptom có vẻ là lỗi một file / một layer, ưu tiên plan ngắn 1-2 bước thay vì plan dàn trải.
"""

        attempt_use_mcp = use_mcp
        for _attempt in range(2):
            try:
                result = await get_planner_agent(attempt_use_mcp, ctx.state.non_interactive).run(prompt)
                output: PlanWrapper = result.output

                from graph.helpers import count_tool_calls

                ctx.state.metrics_planner_tool_calls += count_tool_calls(result.new_messages())
                try:
                    usage = result.usage()
                    ctx.state.metrics_planner_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
                except Exception:
                    pass

                if not output.steps:
                    raise ValueError("Planner không trả về bước sửa nào.")

                ctx.state.planner_use_mcp = attempt_use_mcp
                ctx.state.current_plan = output.steps
                ctx.state.root_cause_explanation = output.root_cause

                print(f"\n{'─' * 60}")
                print_step("📜", "Planner", f"Quyết định: {CYAN}Plan{RESET} ({len(output.steps)} bước)")
                print(f"  {BOLD}Nguyên nhân  :{RESET} {RED}{output.root_cause}{RESET}")
                print(f"{'─' * 60}")
                return PlanInterceptorNode()
            except Exception as exc:
                error_text = str(exc)
                if not attempt_use_mcp:
                    attempt_use_mcp = True
                    ctx.state.planner_use_mcp = True
                    _record_mcp_fallback(ctx, f"Planner bật MCP sau lỗi lập plan: {error_text}")
                    continue
                print_step("❌", "Planner Error", f"{RED}Lỗi khi tạo Plan: {error_text}{RESET}")
                ctx.state.validation_errors.append(f"Planner Error: {error_text}")
                ctx.state.retry_count += 1
                if ctx.state.non_interactive:
                    ctx.state.surrendered = True
                    ctx.state.final_explanation = f"Planner failed before code changes: {error_text}"
                    return ReportNode()
                return ValidationNode()

        ctx.state.surrendered = True
        ctx.state.final_explanation = "Planner exhausted fallback attempts without producing a valid plan."
        return ReportNode()
