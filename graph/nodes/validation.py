"""
ValidationNode — Deterministic Node kiểm tra code đã thực sự được sửa chưa.
Thực thi py_compile, pytest hoặc script runtime.
Xử lý fallback: DirectFix fail -> escalate sang Detailed Plan; Plan fail -> Replan hoặc Surrender (chịu thua).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_step, resolve_target_path
from graph.models import BugComplexity, BugFixState


def _project_python(repo_path: str) -> str:
    candidate = os.path.join(repo_path, ".venv", "bin", "python")
    return candidate if os.path.exists(candidate) else sys.executable

if TYPE_CHECKING:
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode
    from graph.nodes.input_analyzer import InputAnalyzerNode
    from graph.nodes.reproduction_plan import ReproductionPlanNode


@dataclass
class ValidationNode(BaseNode[BugFixState]):
    """
    [Deterministic Node] Tái hiện và kiểm tra lỗi bằng runtime / pytest / py_compile.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[ReproductionPlanNode, PlanningNode, ReportNode, InputAnalyzerNode]:
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode
        from graph.nodes.input_analyzer import InputAnalyzerNode
        from graph.nodes.reproduction_plan import ReproductionPlanNode

        print_step("🔍", "Validation Node", "1/2. Kiểm tra cú pháp mã nguồn (py_compile)...")

        if not ctx.state.final_fixes:
            print_step("⚠️", "Validation", "Không tìm thấy thông tin final_fixes để validate.")
            return self._handle_failure(ctx, "Không có file sửa đổi để kiểm thử.")

        for ffix in ctx.state.final_fixes:
            target_path = resolve_target_path(ffix.target_file, ctx.state.repo_path)
            if not os.path.exists(target_path):
                continue

            proc = subprocess.run([_project_python(ctx.state.repo_path), "-m", "py_compile", target_path], capture_output=True, text=True)
            if proc.returncode != 0:
                syntax_err = (proc.stderr or proc.stdout).strip()
                print_step("❌", "Validation", f"{RED}Lỗi cú pháp tại {ffix.target_file}:{RESET}\n  {syntax_err}")
                return self._handle_failure(ctx, f"Lỗi cú pháp ({ffix.target_file}): {syntax_err}")

        print_step("✅", "Validation", f"{GREEN}Cú pháp mã nguồn hợp lệ!{RESET}")

        baseline_test = os.path.join(ctx.state.repo_path, "scenarios", "test_baseline.py")
        if os.path.exists(baseline_test):
            print_step("🔍", "Validation Node", "Kiểm tra regression bằng baseline pytest...")
            env = os.environ.copy()
            env["PYTHONPATH"] = ctx.state.repo_path
            proc = subprocess.run(
                [_project_python(ctx.state.repo_path), "-m", "pytest", baseline_test, "-q"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=ctx.state.repo_path,
                env=env,
            )
            if proc.returncode != 0:
                baseline_err = (proc.stderr or proc.stdout).strip()
                print_step("❌", "Validation", f"{RED}Baseline regression test thất bại:{RESET}\n  {baseline_err}")
                return self._handle_failure(ctx, f"Baseline pytest thất bại: {baseline_err}")
            print_step("✅", "Validation", f"{GREEN}Baseline pytest đã pass.{RESET}")

        print_step("🔍", "Validation Node", "2/2. Kiểm tra bằng kịch bản tái hiện (nếu có)...")
        if getattr(ctx.state, 'repro_confirmed', False) and ctx.state.repro_script_path and os.path.exists(ctx.state.repro_script_path):
            env = os.environ.copy()
            env["PYTHONPATH"] = ctx.state.repo_path
            try:
                proc = subprocess.run(
                    [_project_python(ctx.state.repo_path), ctx.state.repro_script_path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=ctx.state.repo_path,
                    env=env
                )
                if proc.returncode != 0:
                    runtime_err = (proc.stderr or proc.stdout).strip()
                    print_step("❌", "Validation", f"{RED}Lỗi khi chạy kịch bản tái hiện:{RESET}\n  {runtime_err}")
                    return self._handle_failure(ctx, f"Kịch bản tái hiện vẫn báo lỗi: {runtime_err}")
                else:
                    print_step("✅", "Validation", f"{GREEN}Kịch bản tái hiện chạy thành công (không còn lỗi)!{RESET}")
            except Exception as e:
                print_step("⚠️", "Validation", f"{YELLOW}Không thể chạy kịch bản tái hiện: {e}{RESET}")
        else:
            print_step("⏭️", "Validation", f"{YELLOW}Bỏ qua do không có kịch bản tái hiện tự động.{RESET}")

        # ── Lưu vào Causal Chain Context (IterationHistory)
        patch_summary = ""
        target_files = []
        for ffix in ctx.state.final_fixes:
            target_files.append(ffix.target_file)
            patch_summary += f"{ffix.target_file}: {ffix.explanation}\n"
        
        from graph.models import IterationContext
        ctx.state.iteration_history.append(
            IterationContext(
                initial_error=ctx.state.error_message or ctx.state.error_class or "Unknown Error",
                target_files=target_files,
                patch_summary=patch_summary.strip(),
                user_feedback="" # Sẽ được điền ở InputAnalyzerNode
            )
        )

        ctx.state.validation_passed = True

        if ctx.state.non_interactive:
            return ReportNode()

        # Chuyển quyền quyết định thành công/thất bại cho lập trình viên
        return InputAnalyzerNode()

    def _handle_failure(
        self, ctx: GraphRunContext[BugFixState], error_msg: str
    ) -> Union[ReproductionPlanNode, PlanningNode, ReportNode]:
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode
        from graph.nodes.reproduction_plan import ReproductionPlanNode

        ctx.state.validation_passed = False
        ctx.state.validation_errors.append(error_msg)
        ctx.state.action_history.append(f"Lần thử Replan {ctx.state.replan_count + 1}: Bản patch thất bại khi kiểm thử runtime. Chi tiết lỗi: {error_msg}")

        if ctx.state.non_interactive and (not ctx.state.final_fixes or "Không có file sửa đổi" in error_msg):
            print(f"\n  {RED}☠️ Dừng sớm do không tạo được bản vá hợp lệ trong non-interactive mode.{RESET}")
            ctx.state.surrendered = True
            ctx.state.final_explanation = error_msg
            return ReportNode()

        if ctx.state.replan_count < ctx.state.max_replan_limit:
            print(f"\n  {RED}❌ Lỗi gốc chưa được khắc phục triệt để.{RESET}")

            # Nếu đang là SIMPLE mà thất bại → Nâng cấp lên COMPLEX để Planner (Thinking) vào cuộc
            if ctx.state.complexity == BugComplexity.SIMPLE:
                ctx.state.complexity = BugComplexity.COMPLEX
                print(f"  {YELLOW}⬆ Nâng cấp: Lỗi phức tạp hơn dự kiến → Chuyển sang Planner Agent (Thinking) để phân tích sâu...{RESET}")
            else:
                print(f"  {YELLOW}🔄 Quay về bước Planning để lên phương án sửa lỗi mới... (Lần replan {ctx.state.replan_count + 1}/{ctx.state.max_replan_limit}){RESET}")

            ctx.state.user_plan_feedback = f"Validation thất bại (Lỗi chưa hết hoặc mã nguồn sai cú pháp). Chi tiết lỗi: {error_msg}"
            
            # CHÚ Ý: KHÔNG reset repro_script ở đây.
            # Chúng ta giữ nguyên file _pyfix_repro.py cũ để sau khi Planning/Execution sửa xong,
            # ValidationNode có thể chạy lại chính xác file test này (TDD style).
            
            return PlanningNode()
        else:
            # Quá giới hạn replan -> chịu thua
            print(f"\n  {RED}☠️ Đã thử sửa quá {ctx.state.max_replan_limit} lần nhưng không thành công.{RESET}")
            ctx.state.surrendered = True
            return ReportNode()
