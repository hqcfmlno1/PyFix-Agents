"""
ExecutionNode — Coder Agent thực thi từng bước trong Plan với Per-Step Human Approval.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, List

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import DEFAULT_CODER_USE_MCP, get_coder_agent
from graph.config import BOLD, CODER_MODEL_NAME, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import (
    apply_delimiter_patch,
    compute_diff,
    load_file_content,
    print_diff,
    print_step,
    resolve_target_path,
)
from graph.models import BugFixState, CodeFix, PlanStep

if TYPE_CHECKING:
    from graph.nodes.validation import ValidationNode

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".pytest_cache", ".mypy_cache"}
_CODE_EXTS = {".py", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".json"}
_STOPWORDS = {"this", "that", "with", "from", "into", "should", "would", "about", "error", "runtime", "step", "file"}


def _extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", (text or "").lower())
    seen: list[str] = []
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if token not in seen:
            seen.append(token)
    return seen[:12]


def _iter_repo_files(repo_path: str):
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if os.path.splitext(name)[1].lower() in _CODE_EXTS:
                yield os.path.join(root, name)


def _normalize_rel_path(path: str, repo_path: str) -> str:
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        try:
            return os.path.relpath(normalized, repo_path)
        except ValueError:
            return normalized
    return normalized


def _collect_related_context(repo_path: str, seed_text: str, primary_file: str | None, limit: int = 3) -> str:
    if not repo_path or not os.path.isdir(repo_path):
        return ""
    keywords = _extract_keywords(seed_text)
    if not keywords:
        return ""

    primary_rel = _normalize_rel_path(primary_file, repo_path) if primary_file else None
    candidates: list[tuple[int, str, str]] = []
    for abs_path in _iter_repo_files(repo_path):
        rel_path = os.path.relpath(abs_path, repo_path)
        content = load_file_content(abs_path)
        if not content:
            continue
        score = 0
        rel_lower = rel_path.lower()
        content_lower = content.lower()
        for kw in keywords:
            if kw in rel_lower:
                score += 5
            score += min(content_lower.count(kw), 8)
        if primary_rel and rel_path == primary_rel:
            score += 100
        if score > 0:
            candidates.append((score, rel_path, content))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    blocks = []
    for score, rel_path, content in candidates[:limit]:
        lines = content.splitlines()
        hit_idx = None
        lowered = [line.lower() for line in lines]
        for idx, line in enumerate(lowered):
            if any(kw in line for kw in keywords):
                hit_idx = idx
                break
        if hit_idx is None:
            start, end = 0, min(len(lines), 80)
        else:
            start = max(0, hit_idx - 4)
            end = min(len(lines), hit_idx + 8)
        excerpt = "\n".join(f"{start + i + 1:4d} | {line}" for i, line in enumerate(lines[start:end]))
        blocks.append(f"[score={score}] FILE: {rel_path}\n{excerpt}")
    return "\n\n".join(blocks)


def _resolve_coder_use_mcp(state: BugFixState) -> bool:
    if state.coder_use_mcp is not None:
        return state.coder_use_mcp
    return DEFAULT_CODER_USE_MCP and not state.non_interactive


def _record_coder_mcp_fallback(ctx: GraphRunContext[BugFixState], reason: str) -> None:
    ctx.state.coder_use_mcp = True
    if reason not in ctx.state.mcp_fallback_events:
        ctx.state.mcp_fallback_events.append(reason)
    print_step("🔁", "Coder MCP Fallback", f"{YELLOW}{reason}{RESET}")


@dataclass
class ExecutionNode(BaseNode[BugFixState]):
    """[Agent] Thực thi từng bước trong Plan với human approval hoặc auto-approve."""

    async def run(self, ctx: GraphRunContext[BugFixState]) -> "ValidationNode":
        from graph.nodes.validation import ValidationNode

        print_step("🛠", "Coder Agent", f"Bắt đầu thực thi từng bước trong Plan với {CODER_MODEL_NAME}...")

        if ctx.state.validation_errors:
            for err in ctx.state.validation_errors:
                ctx.state.execution_logs.append(f"[Validation Failed] {err}")
        ctx.state.validation_errors = []

        plan_steps = ctx.state.current_plan
        simple_history = None

        if not plan_steps:
            from graph.models import BugExplanation

            use_mcp = _resolve_coder_use_mcp(ctx.state)
            coder_agent = get_coder_agent(use_mcp)
            target_path = resolve_target_path(ctx.state.target_file or "", ctx.state.repo_path) if ctx.state.target_file else None
            target_content = load_file_content(target_path) if target_path else ""
            related_context = _collect_related_context(
                ctx.state.repo_path,
                "\n".join([ctx.state.raw_user_input or "", ctx.state.error_class or "", ctx.state.error_message or ""]),
                ctx.state.target_file,
            )

            error_context = (
                f"- Exception: {ctx.state.error_class}: {ctx.state.error_message}\n"
                f"- File loi: {ctx.state.target_file}:{ctx.state.error_line}\n"
                f"- Runtime Data: {ctx.state.raw_user_input}\n\n"
                f"THONG TIN DU AN:\n"
                f"- Thu muc goc (repo_path): {ctx.state.repo_path}\n"
                f"- Cau truc thu muc:\n{ctx.state.project_tree[:1500]}\n\n"
                f"CALL STACK:\n"
            )
            if ctx.state.stack_trace:
                for frame in ctx.state.stack_trace:
                    error_context += f"  - {frame.file_path}:{frame.line_number} in {frame.function_name or 'main'}\n"
                    if frame.code_snippet:
                        error_context += f"    Code: {frame.code_snippet}\n"
            if target_content:
                error_context += f"\nNOI DUNG FILE NGHI VAN ({ctx.state.target_file}):\n{target_content[:8000]}\n"
            if related_context:
                error_context += f"\nCONTEXT LIEN QUAN KHAC:\n{related_context}\n"

            print_step("⚡", "Coder Agent", f"Chế độ {CYAN}Chẩn đoán lỗi (Phase 1){RESET}...")
            if use_mcp:
                diag_prompt = (
                    "LENH: CHAN DOAN LOI\n"
                    "Neu context hien tai chua du, ban CO THE dung tool read_file/list_dir/search_in_codebase, nhung uu tien toi da doan code da duoc cung cap.\n"
                    f"Thong tin loi:\n{error_context}"
                )
            else:
                diag_prompt = (
                    "LENH: CHAN DOAN LOI\n"
                    "Ban KHONG co tool trong lan chay nay. Hay suy luan truc tiep tu cac doan code da duoc cung cap.\n"
                    f"Thong tin loi:\n{error_context}"
                )

            try:
                diag_result = await coder_agent.run(diag_prompt)
                if isinstance(diag_result.output, BugExplanation):
                    print(f"\n{BOLD}Nguyen nhan & De xuat sua:{RESET}")
                    print(f"{YELLOW}{diag_result.output.explanation}{RESET}\n")
                else:
                    print(f"\n{BOLD}Agent da tra ve ket qua khac (khong phai BugExplanation).{RESET}\n")
                simple_history = diag_result.new_messages()
            except Exception as exc:
                print(f"Loi khi chan doan: {exc}")
                sys.exit(1)

            print(f"{BOLD}{YELLOW}  Can tao patch de sua loi nay khong?{RESET}")
            if ctx.state.non_interactive:
                choice = "y"
                print(f"  {GREEN}Che do non-interactive: tu dong tao patch.{RESET}")
            else:
                choice = input(
                    f"  [{GREEN}y{RESET}] Co, tien hanh tao patch  "
                    f"[{RED}n/q{RESET}] Khong, thoat\n"
                    f"  Lua chon [y/n/q]: "
                ).strip().lower()

            if choice != "y":
                print(f"  {RED}Ket thuc. Khong ghi de thay doi nao.{RESET}")
                sys.exit(0)

            plan_steps = [
                PlanStep(
                    step_id=1,
                    title=f"Sua loi: {ctx.state.error_class} tai {ctx.state.target_file}:{ctx.state.error_line}",
                    description="LENH: TAO PATCH. Dua vao ket qua chan doan o tren, hay tra ve truc tiep doan text chua dinh dang Delimiter Blocks.",
                    target_file=ctx.state.target_file or "main.py",
                )
            ]

        original_backups: dict[str, str] = {}
        for step in plan_steps:
            abs_path = resolve_target_path(step.target_file, ctx.state.repo_path)
            if abs_path not in original_backups:
                original_backups[abs_path] = load_file_content(abs_path)

        current_contents: dict[str, str] = dict(original_backups)
        committed_files: set[str] = set()
        total_steps = len(plan_steps)

        for idx, step in enumerate(plan_steps, start=1):
            print_step("📌", f"Buoc {idx}/{total_steps}", f"{step.title} ({step.target_file})")
            step_retry = 0
            step_accepted = False

            while not step_accepted:
                use_mcp = _resolve_coder_use_mcp(ctx.state)
                coder_agent = get_coder_agent(use_mcp)
                prev_step_errors = ""
                if ctx.state.execution_logs:
                    last_errors = "\n".join(f"  - {e}" for e in ctx.state.execution_logs[-3:])
                    prev_step_errors = f"\nLOI TU LAN THU TRUOC:\n{last_errors}\nHay dieu chinh de vuot qua loi nay."

                abs_path = resolve_target_path(step.target_file, ctx.state.repo_path)
                primary_content = current_contents.get(abs_path, load_file_content(abs_path))
                related_context = _collect_related_context(
                    ctx.state.repo_path,
                    "\n".join([ctx.state.raw_user_input or "", step.title, step.description]),
                    step.target_file,
                )

                tool_policy = (
                    "Ban CO THE dung read_file/list_dir/search_in_codebase neu context hien tai chua du, nhung uu tien toi da noi dung file va context da duoc cung cap."
                    if use_mcp
                    else "BAN KHONG CO TOOL TRONG LAN CHAY NAY. Hay sua truc tiep tu noi dung file va context da duoc cung cap."
                )
                step_prompt = f"""LENH: TAO PATCH (DELIMITER BLOCKS)

NHIEM VU: {step.title}
FILE CAN SUA UU TIEN: {step.target_file}
HUONG DAN SUA: {step.description}

THONG TIN DU AN:
- Thu muc goc (repo_path): {ctx.state.repo_path}
- Cau truc thu muc:
{ctx.state.project_tree[:1500]}
{prev_step_errors}

{tool_policy}
Neu context cho thay mot file khac cung lien quan, chi duoc sua file uu tien o tren khi SEARCH/REPLACE khop ro rang.

NOI DUNG HIEN TAI CUA FILE {step.target_file}:
{primary_content[:12000]}

CONTEXT FILE LIEN QUAN:
{related_context}

QUY TAC QUAN TRONG:
- KHONG su dung markdown code block.
- KHONG giai thich.
- SEARCH block PHAI khop chinh xac voi noi dung file hien tai da cho o tren.
- BAT BUOC tra ve it nhat 1 block.
"""

                try:
                    if simple_history and idx == 1:
                        result = await coder_agent.run(step_prompt, message_history=simple_history)
                    else:
                        result = await coder_agent.run(step_prompt)

                    from graph.helpers import count_tool_calls

                    ctx.state.metrics_coder_tool_calls += count_tool_calls(result.new_messages())
                    try:
                        usage = result.usage()
                        ctx.state.metrics_coder_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
                    except Exception:
                        pass

                    step_fix = result.output
                    if isinstance(step_fix, str):
                        if not step_fix.strip():
                            raise ValueError("Model tra ve chuoi rong. Khong sinh duoc patch. Retry...")
                        step_fix = CodeFix(
                            target_file=step.target_file,
                            patch_blocks=step_fix,
                            explanation="Ap dung patch tu raw text",
                        )
                    elif not isinstance(step_fix, CodeFix):
                        raise ValueError(f"Agent khong tra ve str ma tra ve {type(step_fix).__name__}.")

                    print_step("🔍", "DEBUG", f"Patch (text tho): {len(step_fix.patch_blocks)} ky tu.")
                    blocks_preview = step_fix.patch_blocks[:200].replace("\n", " | ")
                    print(f"  patch_blocks preview: {blocks_preview}...")

                    step_patched_files: dict[str, str] = {}
                    patch_errors: list[str] = []

                    ffix = step_fix
                    abs_p = resolve_target_path(ffix.target_file, ctx.state.repo_path)
                    if abs_p not in original_backups:
                        original_backups[abs_p] = load_file_content(abs_p)
                        current_contents[abs_p] = original_backups[abs_p]

                    if ffix.patch_blocks.strip():
                        success, patched, errors = apply_delimiter_patch(current_contents[abs_p], ffix.patch_blocks)
                        if success:
                            step_patched_files[abs_p] = patched
                        else:
                            patch_errors.extend(errors)
                    else:
                        print_step("⚠", f"Buoc {idx}", f"Khong co patch_blocks cho {ffix.target_file}")

                    if patch_errors:
                        error_summary = "\n".join(patch_errors)
                        print_step("❌", f"Buoc {idx}", f"{RED}Patch that bai:\n{error_summary}{RESET}")
                        step_retry += 1
                        ctx.state.execution_logs.append(f"Buoc {idx}: Patch loi (lan {step_retry}): {error_summary}")
                        if not use_mcp:
                            _record_coder_mcp_fallback(ctx, f"Coder bật MCP ở bước {idx} vì patch không áp dụng được trên file đích.")
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Buoc {idx}", f"{RED}Vuot qua {ctx.state.step_max_retries} lan retry. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = f"Buoc {idx} ({step.title}) that bai sau {step_retry} lan: {error_summary}"
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        continue

                    if not step_patched_files:
                        print_step("⚠", f"Buoc {idx}", "Coder khong tra ve thay doi nao.")
                        step_retry += 1
                        ctx.state.execution_logs.append(f"Buoc {idx}: Coder khong tao duoc patch_blocks hop le.")
                        if not use_mcp:
                            _record_coder_mcp_fallback(ctx, f"Coder bật MCP ở bước {idx} vì không tạo được patch hợp lệ.")
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Buoc {idx}", f"{RED}Vuot qua {ctx.state.step_max_retries} lan retry. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = f"Buoc {idx} ({step.title}) that bai sau {step_retry} lan thu: Coder khong tao duoc patch hop le."
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        continue

                    syntax_errors: list[str] = []
                    for abs_p, patched_content in step_patched_files.items():
                        if abs_p.endswith(".py"):
                            try:
                                ast.parse(patched_content, filename=os.path.basename(abs_p))
                            except SyntaxError as exc:
                                err_text = exc.text.strip() if exc.text else ""
                                syntax_errors.append(
                                    f"Loi cu phap tai {os.path.basename(abs_p)} dong {exc.lineno}: {exc.msg}\nCode bi loi: {err_text}"
                                )

                    if syntax_errors:
                        error_summary = "\n".join(syntax_errors)
                        print_step("❌", f"Buoc {idx}", f"{RED}Loi cu phap (SyntaxError):\n{error_summary}{RESET}")
                        step_retry += 1
                        ctx.state.execution_logs.append(f"Buoc {idx}: Loi cu phap sau patch (lan {step_retry}): {error_summary}")
                        if not use_mcp:
                            _record_coder_mcp_fallback(ctx, f"Coder bật MCP ở bước {idx} vì patch local liên tục sinh lỗi cú pháp.")
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Buoc {idx}", f"{RED}Vuot qua {ctx.state.step_max_retries} lan retry do loi cu phap. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = f"Buoc {idx} ({step.title}) that bai sau {step_retry} lan: Lien tuc tao ra loi cu phap."
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        continue

                except Exception as exc:
                    print_step("❌", "Coder Agent", f"{RED}Loi khi thuc thi Buoc {idx}: {exc}{RESET}")
                    step_retry += 1
                    ctx.state.execution_logs.append(f"Buoc {idx} loi API/timeout: {exc}")
                    if not use_mcp:
                        _record_coder_mcp_fallback(ctx, f"Coder bật MCP ở bước {idx} sau lỗi thực thi/timeout: {exc}")
                    if step_retry > ctx.state.step_max_retries:
                        self._rollback(original_backups, committed_files)
                        ctx.state.user_plan_feedback = f"Buoc {idx} that bai sau {step_retry} lan thu: {exc}"
                        ctx.state.replan_count += 1
                        return ValidationNode()
                    continue

                print(f"\n{'─' * 60}")
                print(f"  {BOLD}Diff Buoc {idx}/{total_steps}: {CYAN}{step.title}{RESET}")
                if step_fix.explanation:
                    print(f"  Giai thich: {step_fix.explanation}")
                for abs_p, patched_content in step_patched_files.items():
                    rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
                    base_content = current_contents.get(abs_p, "")
                    print(f"\n{'─' * 40} DIFF: {CYAN}{rel_p}{RESET} {'─' * 40}\n")
                    diff_lines = compute_diff(base_content, patched_content, os.path.basename(abs_p))
                    print_diff(diff_lines)

                print(f"\n{BOLD}{YELLOW}  HUMAN APPROVAL - Buoc {idx}/{total_steps}{RESET}")
                if ctx.state.non_interactive:
                    for abs_p, patched_content in step_patched_files.items():
                        os.makedirs(os.path.dirname(abs_p) or ".", exist_ok=True)
                        with open(abs_p, "w", encoding="utf-8") as fh:
                            fh.write(patched_content)
                        current_contents[abs_p] = patched_content
                        committed_files.add(abs_p)
                        rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
                        print(f"  {GREEN}Auto-approve va ghi thanh cong: {rel_p}{RESET}")
                    ctx.state.execution_logs.append(f"Buoc {idx}: Auto-approved trong non-interactive mode.")
                    step_accepted = True
                    continue

                while True:
                    choice = input(
                        f"  [{GREEN}y{RESET}] Chap nhan & Ghi file  "
                        f"[{RED}n{RESET}] Tu choi (kem ly do)  "
                        f"[{YELLOW}q{RESET}] Thoat\n"
                        f"  Lua chon [y/n/q]: "
                    ).strip().lower()

                    if choice == "y":
                        for abs_p, patched_content in step_patched_files.items():
                            os.makedirs(os.path.dirname(abs_p) or ".", exist_ok=True)
                            with open(abs_p, "w", encoding="utf-8") as fh:
                                fh.write(patched_content)
                            current_contents[abs_p] = patched_content
                            committed_files.add(abs_p)
                            rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
                            print(f"  {GREEN}Ghi thanh cong: {rel_p}{RESET}")
                        ctx.state.execution_logs.append(f"Buoc {idx}: Dev chap nhan.")
                        step_accepted = True
                        break

                    if choice == "n":
                        reason = input("  Ly do tu choi (de trong = khong ro): ").strip() or "Dev tu choi khong kem ly do."
                        print(f"  {YELLOW}Retry Buoc {idx} voi feedback: {reason}{RESET}")
                        ctx.state.execution_logs.append(f"Buoc {idx} bi reject lan {step_retry + 1}: {reason}")
                        step_retry += 1
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Buoc {idx}", f"{RED}Vuot qua {ctx.state.step_max_retries} lan reject. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = f"Dev tu choi Buoc {idx} ({step.title}) nhieu lan. Ly do cuoi: {reason}"
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        break

                    if choice == "q":
                        print(f"  {RED}Nguoi dung thoat khoi qua trinh sua loi.{RESET}")
                        self._rollback(original_backups, committed_files)
                        sys.exit(0)

        final_files: List[CodeFix] = []
        for abs_p in committed_files:
            rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
            final_files.append(
                CodeFix(
                    target_file=rel_p,
                    patch_blocks="",
                    explanation="Da ap dung va duoc Dev chap nhan.",
                )
            )

        ctx.state.final_fixes = final_files
        ctx.state.final_explanation = f"Hoan thanh {len(plan_steps)} buoc. Dev da review va chap nhan tung buoc."
        print_step("✅", "Coder Agent", f"{GREEN}Hoan tat tat ca {len(plan_steps)} buoc. Chuyen sang Validation...{RESET}")
        return ValidationNode()

    @staticmethod
    def _rollback(backups: dict[str, str], committed: set[str]) -> None:
        for abs_p in committed:
            if abs_p in backups:
                try:
                    with open(abs_p, "w", encoding="utf-8") as fh:
                        fh.write(backups[abs_p])
                    print(f"  Rollback: {abs_p}")
                except Exception:
                    pass
