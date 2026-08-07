"""
ExecutionNode — Coder Agent thực thi từng bước trong Plan với Per-Step Human Approval.
Sau mỗi bước: hiện diff → hỏi Dev duyệt → Accept thì ghi file, Reject thì retry step đó.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, List

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import coder_agent
from graph.config import BOLD, CODER_MODEL_NAME, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import (
    apply_all_hunks,
    compute_diff,
    load_file_content,
    print_diff,
    print_step,
    resolve_target_path,
)
from graph.models import BugFixState, CodeFix, PlanStep, SingleFileFix

if TYPE_CHECKING:
    from graph.nodes.validation import ValidationNode


@dataclass
class ExecutionNode(BaseNode[BugFixState]):
    """
    [Agent] Thực thi từng bước trong Plan theo cơ chế Per-Step Human Approval.

    Luồng tại mỗi step:
    1. Coder Agent đọc file và tạo hunks.
    2. Áp dụng hunks tạm vào bộ nhớ (current_contents).
    3. Hiển thị diff của step đó cho Dev.
    4. Hỏi Dev: [y] Accept → ghi file thật / [n] Reject (kèm lý do) → Coder retry step / [q] Thoát.
    5. Nếu step bị reject > step_max_retries lần → trigger replan.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> "ValidationNode":
        from graph.nodes.validation import ValidationNode

        print_step("🛠", "Coder Agent", f"Bắt đầu thực thi từng bước trong Plan với {CODER_MODEL_NAME}...")

        if ctx.state.validation_errors:
            for err in ctx.state.validation_errors:
                ctx.state.execution_logs.append(f"[Validation Failed] {err}")
        ctx.state.validation_errors = []

        # ── Trường hợp SIMPLE: Coder nhận thẳng traceback, không qua Planner ──
        plan_steps = ctx.state.current_plan
        simple_history = None

        if not plan_steps:
            from graph.models import BugExplanation
            error_context = (
                f"- Exception: {ctx.state.error_class}: {ctx.state.error_message}\n"
                f"- File lỗi: {ctx.state.target_file}:{ctx.state.error_line}\n"
                f"- Runtime Data: {ctx.state.raw_user_input}\n\n"
                f"THÔNG TIN DỰ ÁN:\n"
                f"- Thư mục gốc (repo_path): {ctx.state.repo_path}\n"
                f"- Cấu trúc thư mục:\n{ctx.state.project_tree[:1500]}\n\n"
                f"CALL STACK:\n"
            )
            if ctx.state.stack_trace:
                for frame in ctx.state.stack_trace:
                    error_context += f"  - {frame.file_path}:{frame.line_number} in {frame.function_name or 'main'}\n"
                    if frame.code_snippet:
                        error_context += f"    Code: {frame.code_snippet}\n"

            print_step("⚡", "Coder Agent", f"Chế độ {CYAN}Chẩn đoán lỗi (Phase 1){RESET}...")
            diag_prompt = f"LỆNH: CHẨN ĐOÁN LỖI\nThông tin lỗi:\n{error_context}"
            
            try:
                diag_result = await coder_agent.run(diag_prompt)
                if isinstance(diag_result.output, BugExplanation):
                    print(f"\n{BOLD}Nguyên nhân & Đề xuất sửa:{RESET}")
                    print(f"{YELLOW}{diag_result.output.explanation}{RESET}\n")
                else:
                    print(f"\n{BOLD}Agent đã trả về kết quả khác (không phải BugExplanation).{RESET}\n")
                
                simple_history = diag_result.new_messages()
            except Exception as e:
                print(f"Lỗi khi chẩn đoán: {e}")
                sys.exit(1)

            print(f"{BOLD}{YELLOW}  ⚠ BẠN CÓ MUỐN TẠO PATCH ĐỂ SỬA LỖI NÀY KHÔNG?{RESET}")
            choice = input(
                f"  [{GREEN}y{RESET}] Có, tiến hành tạo patch  "
                f"[{RED}n/q{RESET}] Không, thoát\n"
                f"  Lựa chọn [y/n/q]: "
            ).strip().lower()

            if choice != 'y':
                print(f"  {RED}Kết thúc. Không ghi đè thay đổi nào.{RESET}")
                sys.exit(0)

            plan_steps = [
                PlanStep(
                    step_id=1,
                    title=f"Sửa lỗi: {ctx.state.error_class} tại {ctx.state.target_file}:{ctx.state.error_line}",
                    description="LỆNH: TẠO PATCH. Dựa vào kết quả chẩn đoán ở trên, hãy trả về CodeFix chứa các hunks để sửa lỗi này.",
                    target_file=ctx.state.target_file or "main.py",
                )
            ]



        # Backup nội dung gốc của tất cả file trước khi sửa
        original_backups: dict[str, str] = {}
        for step in plan_steps:
            abs_path = resolve_target_path(step.target_file, ctx.state.repo_path)
            if abs_path not in original_backups:
                original_backups[abs_path] = load_file_content(abs_path)

        # current_contents theo dõi nội dung hiện tại (tích lũy qua từng step)
        current_contents: dict[str, str] = dict(original_backups)
        # Theo dõi các file đã được Dev chấp thuận ghi thật
        committed_files: set[str] = set()

        total_steps = len(plan_steps)

        for idx, step in enumerate(plan_steps, start=1):
            print_step("📌", f"Bước {idx}/{total_steps}", f"{step.title} ({step.target_file})")

            step_retry = 0
            step_accepted = False

            while not step_accepted:
                # Xây dựng prompt cho Coder Agent
                prev_step_errors = ""
                if ctx.state.execution_logs:
                    last_errors = "\n".join(f"  • {e}" for e in ctx.state.execution_logs[-3:])
                    prev_step_errors = f"\nLỖI TỪ LẦN THỬ TRƯỚC:\n{last_errors}\nHãy điều chỉnh để vượt qua lỗi này."

                step_prompt = f"""LỆNH: TẠO PATCH (SEARCH-AND-REPLACE)

NHIỆM VỤ: {step.title}
FILE CẦN SỬA: {step.target_file}
HƯỚNG DẪN SỬA: {step.description}

THÔNG TIN DỰ ÁN:
- Thư mục gốc (repo_path): {ctx.state.repo_path}
- Cấu trúc thư mục:
{ctx.state.project_tree[:1500]}
{prev_step_errors}

HƯỚNG DẪN THỰC THI:
1. QUYẾT ĐỊNH ĐỌC FILE:
   - Nhìn vào Cấu trúc thư mục (thấy số lines), nếu file < 200 lines, hãy dùng tool `read_file(path='{step.target_file}')` đọc toàn bộ.
   - Nếu file > 200 lines và chưa rõ dòng cần sửa, BẮT BUỘC dùng `search_in_codebase(query="...", files=["{step.target_file}"])` để tìm số dòng. Sau đó mới dùng `read_file(start_line=..., end_line=...)` đọc vùng code đó.
2. Xác định chính xác đoạn code cần sửa, COPY NGUYÊN VĂN vào `old_lines` (bao gồm 2-3 dòng context xung quanh để đảm bảo TÍNH DUY NHẤT).
3. Viết `new_lines` thay thế — giữ nguyên indentation y hệt file gốc.
4. BẮT BUỘC trả về CodeFix với `file` chứa ÍT NHẤT 1 hunk. TUYỆT ĐỐI KHÔNG trả về hunks=[] rỗng.

QUY TẮC QUAN TRỌNG:
- old_lines PHẢI khớp chính xác với nội dung file (kể cả khoảng trắng và indentation).
- Nếu muốn xóa code, để new_lines là chuỗi rỗng.
- Nếu muốn thêm code mới (không xóa gì), dùng old_lines là đoạn đứng ngay trước vị trí chèn và new_lines = old_lines + code_mới.
"""

                try:
                    if simple_history and idx == 1:
                        result = await coder_agent.run(step_prompt, message_history=simple_history)
                    else:
                        result = await coder_agent.run(step_prompt)

                    step_fix = result.output

                    # ── Debug: Hiển thị output thực tế từ Coder ──────────
                    if not isinstance(step_fix, CodeFix):
                        print_step("🔍", "DEBUG", f"Coder trả về kiểu: {type(step_fix).__name__}")
                        if hasattr(step_fix, 'explanation'):
                            print(f"  Explanation: {step_fix.explanation[:200]}")
                        raise ValueError(f"Agent không trả về CodeFix mà trả về {type(step_fix).__name__}.")

                    # ── Validate: Reject empty files/hunks ──────────────
                    if not step_fix.file.hunks:
                        raise ValueError(
                            f"CodeFix có hunks=[] rỗng (explanation='{step_fix.explanation[:100]}'). "
                            f"Model không sinh được hunks. Retry..."
                        )

                    # Debug: kiểm tra nội dung CodeFix
                    print_step("🔍", "DEBUG", f"CodeFix: file {step_fix.file.target_file}, explanation='{step_fix.explanation[:100]}...'")
                    print(f"  File: {step_fix.file.target_file}, {len(step_fix.file.hunks)} hunk(s)")
                    for hi, h in enumerate(step_fix.file.hunks):
                        print(f"    Hunk[{hi}]: old_lines={repr(h.old_lines[:80])}..., new_lines={repr(h.new_lines[:80])}...")

                    # Áp dụng hunks vào current_contents (chưa ghi file thật)
                    step_patched_files: dict[str, str] = {}
                    patch_errors: list[str] = []

                    ffix = step_fix.file
                    abs_p = resolve_target_path(ffix.target_file, ctx.state.repo_path)
                    if abs_p not in original_backups:
                        original_backups[abs_p] = load_file_content(abs_p)
                        current_contents[abs_p] = original_backups[abs_p]

                    if ffix.hunks:
                        success, patched, errors = apply_all_hunks(current_contents[abs_p], ffix.hunks)
                        if success:
                            step_patched_files[abs_p] = patched
                        else:
                            patch_errors.extend(errors)
                    else:
                        print_step("⚠", f"Bước {idx}", f"Không có hunk nào cho {ffix.target_file}")

                    if patch_errors:
                        error_summary = "\n".join(patch_errors)
                        print_step("❌", f"Bước {idx}", f"{RED}Patch thất bại:\n{error_summary}{RESET}")
                        step_retry += 1
                        ctx.state.execution_logs.append(
                            f"Bước {idx}: Patch lỗi (lần {step_retry}): {error_summary}"
                        )
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Bước {idx}", f"{RED}Vượt quá {ctx.state.step_max_retries} lần retry. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = (
                                f"Bước {idx} ({step.title}) thất bại sau {step_retry} lần: {error_summary}"
                            )
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        continue  # Retry step

                    if not step_patched_files:
                        print_step("⚠", f"Bước {idx}", "Coder không trả về thay đổi nào.")
                        step_retry += 1
                        ctx.state.execution_logs.append(f"Bước {idx}: Coder không tạo được hunks hợp lệ.")
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Bước {idx}", f"{RED}Vượt quá {ctx.state.step_max_retries} lần retry. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = (
                                f"Bước {idx} ({step.title}) thất bại sau {step_retry} lần thử: Coder không tạo được hunks hợp lệ."
                            )
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        continue  # Retry step

                    # ── Tự động kiểm tra cú pháp (Linter) ───────────────────────
                    import ast
                    syntax_errors: list[str] = []
                    for abs_p, patched_content in step_patched_files.items():
                        if abs_p.endswith(".py"):
                            try:
                                ast.parse(patched_content, filename=os.path.basename(abs_p))
                            except SyntaxError as e:
                                err_text = e.text.strip() if e.text else ""
                                error_msg = f"Lỗi cú pháp tại {os.path.basename(abs_p)} dòng {e.lineno}: {e.msg}\nCode bị lỗi: {err_text}"
                                syntax_errors.append(error_msg)

                    if syntax_errors:
                        error_summary = "\n".join(syntax_errors)
                        print_step("❌", f"Bước {idx}", f"{RED}Lỗi cú pháp (SyntaxError):\n{error_summary}{RESET}")
                        step_retry += 1
                        ctx.state.execution_logs.append(
                            f"Bước {idx}: Lỗi cú pháp sau patch (lần {step_retry}): {error_summary}"
                        )
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Bước {idx}", f"{RED}Vượt quá {ctx.state.step_max_retries} lần retry do lỗi cú pháp. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = (
                                f"Bước {idx} ({step.title}) thất bại sau {step_retry} lần: Liên tục tạo ra lỗi cú pháp."
                            )
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        continue  # Retry step

                except Exception as exc:
                    print_step("❌", "Coder Agent", f"{RED}Lỗi khi thực thi Bước {idx}: {exc}{RESET}")
                    step_retry += 1
                    ctx.state.execution_logs.append(f"Bước {idx} lỗi API/timeout: {exc}")
                    if step_retry > ctx.state.step_max_retries:
                        self._rollback(original_backups, committed_files)
                        ctx.state.user_plan_feedback = f"Bước {idx} thất bại sau {step_retry} lần thử: {exc}"
                        ctx.state.replan_count += 1
                        return ValidationNode()
                    continue  # Retry step

                # ── Hiển thị diff của riêng bước này ────────────────────────
                print(f"\n{'─'*60}")
                print(f"  {BOLD}Diff Bước {idx}/{total_steps}: {CYAN}{step.title}{RESET}")
                if step_fix.explanation:
                    print(f"  Giải thích: {step_fix.explanation}")
                for abs_p, patched_content in step_patched_files.items():
                    rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
                    base_content = current_contents.get(abs_p, "")
                    print(f"\n{'─'*40} DIFF: {CYAN}{rel_p}{RESET} {'─'*40}\n")
                    diff_lines = compute_diff(base_content, patched_content, os.path.basename(abs_p))
                    print_diff(diff_lines)

                # ── Per-Step Human Approval ──────────────────────────────────
                print(f"\n{BOLD}{YELLOW}  ⚠ HUMAN APPROVAL — Bước {idx}/{total_steps}{RESET}")
                while True:
                    choice = input(
                        f"  [{GREEN}y{RESET}] Chấp nhận & Ghi file  "
                        f"[{RED}n{RESET}] Từ chối (kèm lý do)  "
                        f"[{YELLOW}q{RESET}] Thoát\n"
                        f"  Lựa chọn [y/n/q]: "
                    ).strip().lower()

                    if choice == "y":
                        # Ghi file thật vào đĩa
                        for abs_p, patched_content in step_patched_files.items():
                            os.makedirs(os.path.dirname(abs_p) or ".", exist_ok=True)
                            with open(abs_p, "w", encoding="utf-8") as fh:
                                fh.write(patched_content)
                            current_contents[abs_p] = patched_content
                            committed_files.add(abs_p)
                            rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
                            print(f"  {GREEN}✅ Ghi thành công: {rel_p}{RESET}")
                        ctx.state.execution_logs.append(f"Bước {idx}: Dev chấp nhận.")
                        step_accepted = True
                        break

                    elif choice == "n":
                        reason = input(f"  Lý do từ chối (để trống = không rõ): ").strip()
                        if not reason:
                            reason = "Dev từ chối không kèm lý do."
                        print(f"  {YELLOW}🔄 Retry Bước {idx} với feedback: {reason}{RESET}")
                        ctx.state.execution_logs.append(f"Bước {idx} bị reject lần {step_retry + 1}: {reason}")
                        step_retry += 1
                        if step_retry > ctx.state.step_max_retries:
                            print_step("❌", f"Bước {idx}", f"{RED}Vượt quá {ctx.state.step_max_retries} lần reject. Trigger replan.{RESET}")
                            self._rollback(original_backups, committed_files)
                            ctx.state.user_plan_feedback = (
                                f"Dev từ chối Bước {idx} ({step.title}) nhiều lần. Lý do cuối: {reason}"
                            )
                            ctx.state.replan_count += 1
                            return ValidationNode()
                        break  # Thoát vòng while approval → retry step

                    elif choice == "q":
                        print(f"  {RED}Người dùng thoát khỏi quá trình sửa lỗi.{RESET}")
                        self._rollback(original_backups, committed_files)
                        sys.exit(0)

        # ── Tất cả bước đã được chấp nhận ───────────────────────────────────
        final_files: List[SingleFileFix] = []
        for abs_p in committed_files:
            rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
            final_files.append(
                SingleFileFix(
                    target_file=rel_p,
                    hunks=[],
                    changes_summary="Đã áp dụng và được Dev chấp nhận.",
                )
            )

        ctx.state.final_fixes = final_files
        ctx.state.final_explanation = f"Hoàn thành {len(plan_steps)} bước. Dev đã review và chấp nhận từng bước."
        print_step("✅", "Coder Agent", f"{GREEN}Hoàn tất tất cả {len(plan_steps)} bước. Chuyển sang Validation...{RESET}")
        return ValidationNode()

    @staticmethod
    def _rollback(backups: dict[str, str], committed: set[str]) -> None:
        """Rollback chỉ các file đã được commit (ghi thật) về bản gốc."""
        for abs_p in committed:
            if abs_p in backups:
                try:
                    with open(abs_p, "w", encoding="utf-8") as fh:
                        fh.write(backups[abs_p])
                    print(f"  ↩ Rollback: {abs_p}")
                except Exception:
                    pass
