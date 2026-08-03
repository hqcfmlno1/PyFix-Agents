"""
ExecutionNode — Coder Agent thực thi từng bước trong Plan (Chunk-Based Patching).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, List

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import coder_agent
from graph.config import BOLD, CYAN, GREEN, MODEL_NAME, RED, RESET, YELLOW
from graph.helpers import (
    apply_hunks,
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
    [Agent] Thực thi từng bước trong Plan dùng Chunk-Based Patching.
    Coder Agent chỉ đọc khoảng dòng liên quan và trả về List[PatchHunk].
    apply_hunks() tự áp hunk vào file gốc không cần LLM đọc lại toàn bộ file.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> ValidationNode:
        from graph.nodes.validation import ValidationNode

        print_step("🛠", "Coder Agent", f"Đang thực thi từng bước trong Plan (Chunk-Based) với {MODEL_NAME}...")

        if ctx.state.validation_errors:
            for err in ctx.state.validation_errors:
                ctx.state.execution_logs.append(f"[Validation Failed] {err}")
        ctx.state.validation_errors = []

        prev_errors_str = ""
        if ctx.state.execution_logs:
            last_errors = "\n".join(f"  • {e}" for e in ctx.state.execution_logs[-3:])
            prev_errors_str = f"\nLỖI THỬ LẠI TỪ LẦN TRƯỚC:\n{last_errors}\nHãy điều chỉnh code để vượt qua lỗi này."

        # Wrap DirectFix into a 1-step Plan
        plan_steps = ctx.state.current_plan
        if not plan_steps and ctx.state.direct_fix:
            plan_steps = [
                PlanStep(
                    step_id=1,
                    title=f"DirectFix: {ctx.state.direct_fix.bug_summary}",
                    description=ctx.state.direct_fix.fix_description,
                    target_file=ctx.state.direct_fix.file_path,
                    target_lines=[ctx.state.direct_fix.error_line],
                    acceptance_criteria="Áp dụng thành công bản vá DirectFix theo hướng dẫn.",
                )
            ]
        elif not plan_steps:
            plan_steps = [
                PlanStep(
                    step_id=1,
                    title="Sửa lỗi dự án",
                    description="Sửa code theo mô tả lỗi",
                    target_file=ctx.state.target_file or "main.py",
                )
            ]

        # Backup nội dung gốc của tất cả file trước khi sửa
        original_backups: dict[str, str] = {}
        for step in plan_steps:
            abs_path = resolve_target_path(step.target_file, ctx.state.repo_path)
            if abs_path not in original_backups:
                original_backups[abs_path] = load_file_content(abs_path)

        # current_contents theo dõi nội dung hiện tại (sau từng hunk áp dụng)
        current_contents: dict[str, str] = dict(original_backups)
        touched_files: set[str] = set()
        aggregated_explanations: List[str] = []

        for idx, step in enumerate(plan_steps, start=1):
            target_lines_str = f" (dòng {step.target_lines})" if step.target_lines else ""
            print_step("📌", f"Bước {idx}/{len(plan_steps)}", f"{step.title} ({step.target_file}{target_lines_str})")

            acc_criteria_str = f"\n- Tiêu chí nghiệm thu: {step.acceptance_criteria}" if step.acceptance_criteria else ""

            if step.target_lines:
                min_line = max(1, min(step.target_lines) - 30)
                max_line = max(step.target_lines) + 30
                line_read_instruction = (
                    f"1. Dùng tool `read_file(path='{step.target_file}', start_line={min_line}, end_line={max_line})` "
                    f"để chỉ đọc ~60 dòng xung quanh khu vực dòng liên quan {step.target_lines}."
                )
            else:
                line_read_instruction = f"1. Dùng tool `read_file(path='{step.target_file}')` đọc nội dung file."

            step_prompt = f"""THỰC THI BƯỚC {idx}/{len(plan_steps)} CỦA PLAN (CHUNK-BASED PATCHING):
- Tiêu đề       : {step.title}
- File cần sửa   : {step.target_file}
- Các dòng bị lỗi: {step.target_lines if step.target_lines else 'Không chỉ định'}
- Hướng dẫn sửa  : {step.description}{acc_criteria_str}
{prev_errors_str}

HƯỚNG DẪN THỰC THI:
{line_read_instruction}
2. Thực hiện chính xác các chỉnh sửa theo hướng dẫn trong 'Hướng dẫn sửa' ở trên.
3. Xác định chính xác start_line và end_line của từng đoạn cần thay đổi trong file GỐC.
4. Dùng `run_linter` để kiểm tra cú pháp đoạn code mới. Nếu có lỗi syntax, điều chỉnh new_lines cho hợp lệ.
5. Trả về 'files' chứa 1 SingleFileFix với danh sách 'hunks' (KHÔNG trả về toàn bộ nội dung file).
"""

            try:
                result = await coder_agent.run(step_prompt)
                step_fix: CodeFix = result.output

                if step_fix.explanation:
                    aggregated_explanations.append(f"Bước {idx}: {step_fix.explanation}")

                for ffix in step_fix.files:
                    abs_p = resolve_target_path(ffix.target_file, ctx.state.repo_path)

                    # Đảm bảo có backup
                    if abs_p not in original_backups:
                        original_backups[abs_p] = load_file_content(abs_p)
                        current_contents[abs_p] = original_backups[abs_p]

                    if ffix.hunks:
                        # Áp dụng Chunk-Based Patching
                        patched = apply_hunks(current_contents[abs_p], ffix.hunks)
                        current_contents[abs_p] = patched

                        # Ghi file tạm để chuẩn bị cho Validation
                        os.makedirs(os.path.dirname(abs_p) or ".", exist_ok=True)
                        with open(abs_p, "w", encoding="utf-8") as fh:
                            fh.write(patched)
                        touched_files.add(abs_p)
                        print_step("💾", f"Bước {idx}", f"Đã áp {len(ffix.hunks)} hunk(s) vào {ffix.target_file}")
                    else:
                        print_step("⚠", f"Bước {idx}", f"Không có hunk nào được trả về cho {ffix.target_file}")

            except Exception as exc:
                print_step("❌", "Coder Agent", f"{RED}Lỗi khi thực thi Bước {idx}: {exc}{RESET}")
                self._rollback(original_backups, touched_files)
                ctx.state.validation_errors.append(f"Lỗi thực thi bước {idx}: {exc}")
                ctx.state.retry_count += 1
                return ValidationNode()

        # Tổng hợp CodeFix từ current_contents
        final_files: List[SingleFileFix] = []
        for abs_p in touched_files:
            rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
            final_files.append(
                SingleFileFix(
                    target_file=rel_p,
                    hunks=[],  # Hunks đã được áp dụng, lưu kết quả cuối
                    changes_summary="Đã áp dụng chunk-based patch theo kế hoạch",
                )
            )

        code_fix = CodeFix(
            files=final_files,
            explanation="\n".join(aggregated_explanations) if aggregated_explanations else "Hoàn thành các bước trong plan.",
        )
        ctx.state.code_fix = code_fix

        if not final_files:
            print_step("⚠", "Coder Agent", "Không có file nào được sửa.")
            return ValidationNode()

        # Rollback file tạm (khôi phục file gốc) trước khi hỏi Human Approval
        self._rollback(original_backups, touched_files)

        # Hiển thị diff tổng hợp từ original → patched
        print(f"\n  {BOLD}💡 Tổng hợp kết quả sửa đổi ({len(touched_files)} file):{RESET}")
        for abs_p in touched_files:
            rel_p = os.path.relpath(abs_p, ctx.state.repo_path)
            orig = original_backups.get(abs_p, "")
            patched = current_contents.get(abs_p, "")
            print(f"\n{'─'*40} DIFF: {CYAN}{rel_p}{RESET} {'─'*40}\n")
            diff_lines = compute_diff(orig, patched, os.path.basename(abs_p))
            print_diff(diff_lines)

        print(f"\n{BOLD}{YELLOW}⚠ HUMAN APPROVAL — Duyệt để ghi thật vào file{RESET}")
        while True:
            choice = input(
                f"  [{GREEN}y{RESET}] Đồng ý & Ghi file  "
                f"[{RED}n{RESET}] Bỏ qua & Thử lại  "
                f"[{YELLOW}q{RESET}] Thoát\n"
                f"  Lựa chọn [y/n/q]: "
            ).strip().lower()

            if choice == "y":
                for abs_p in touched_files:
                    patched = current_contents.get(abs_p, "")
                    with open(abs_p, "w", encoding="utf-8") as fh:
                        fh.write(patched)
                    print(f"  {GREEN}✅ Đã ghi file thành công: {abs_p}{RESET}")
                break

            elif choice == "n":
                print(f"  {YELLOW}🔄 Từ chối thay đổi — Thử lại...{RESET}")
                ctx.state.execution_logs.append("User từ chối chấp nhận diff.")
                ctx.state.retry_count += 1
                break

            elif choice == "q":
                sys.exit(0)

        return ValidationNode()

    @staticmethod
    def _rollback(backups: dict[str, str], touched: set[str]) -> None:
        for abs_p in touched:
            if abs_p in backups:
                try:
                    with open(abs_p, "w", encoding="utf-8") as fh:
                        fh.write(backups[abs_p])
                except Exception:
                    pass
