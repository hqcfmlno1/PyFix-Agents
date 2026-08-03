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

if TYPE_CHECKING:
    from graph.nodes.direct_fix import DirectFixCreationNode
    from graph.nodes.planning import PlanningNode
    from graph.nodes.report import ReportNode


@dataclass
class ValidationNode(BaseNode[BugFixState]):
    """
    [Deterministic Node] Tái hiện và kiểm tra lỗi bằng runtime / pytest / py_compile.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[DirectFixCreationNode, PlanningNode, ReportNode]:
        from graph.nodes.direct_fix import DirectFixCreationNode
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode

        print_step("🔍", "Validation Node", "1/2. Kiểm tra cú pháp mã nguồn (py_compile)...")

        if not ctx.state.code_fix or not ctx.state.code_fix.files:
            print_step("⚠️", "Validation", "Không tìm thấy thông tin code_fix để validate.")
            return self._handle_failure(ctx, "Không có file sửa đổi để kiểm thử.")

        for ffix in ctx.state.code_fix.files:
            target_path = resolve_target_path(ffix.target_file, ctx.state.repo_path)
            if not os.path.exists(target_path):
                continue

            proc = subprocess.run([sys.executable, "-m", "py_compile", target_path], capture_output=True, text=True)
            if proc.returncode != 0:
                syntax_err = (proc.stderr or proc.stdout).strip()
                print_step("❌", "Validation", f"{RED}Lỗi cú pháp tại {ffix.target_file}:{RESET}\n  {syntax_err}")
                return self._handle_failure(ctx, f"Lỗi cú pháp ({ffix.target_file}): {syntax_err}")

        print_step("✅", "Validation", f"{GREEN}Cú pháp mã nguồn hợp lệ!{RESET}")

        print_step("🧪", "Validation Node", "2/2. Chạy runtime / test suite kiểm tra xem lỗi đã hết hẳn chưa...")

        test_files = []
        for root, dirs, files in os.walk(ctx.state.repo_path):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
            for f in files:
                if (f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py"):
                    test_files.append(os.path.join(root, f))

        test_passed = True
        test_err_msg = ""

        if test_files:
            print(f"  {CYAN}🏃 Phát hiện {len(test_files)} file test. Đang chạy pytest...{RESET}")
            try:
                test_proc = subprocess.run(
                    [sys.executable, "-m", "pytest", ctx.state.repo_path, "-v"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if test_proc.returncode != 0:
                    test_passed = False
                    test_err_msg = f"Pytest thất bại:\n{(test_proc.stdout or test_proc.stderr).strip()}"
                    print_step("❌", "Validation", f"{RED}Unit test không đạt!{RESET}")
                else:
                    print(f"  {GREEN}✅ Tất cả unit tests ĐẠT!{RESET}")
            except Exception as exc:
                test_passed = False
                test_err_msg = f"Lỗi khi chạy pytest: {exc}"

        elif ctx.state.target_file and ctx.state.target_file.endswith(".py"):
            target_path = resolve_target_path(ctx.state.target_file, ctx.state.repo_path)
            print(f"  {CYAN}🏃 Thực thi script {os.path.basename(target_path)} kiểm tra crash...{RESET}")
            try:
                run_proc = subprocess.run([sys.executable, target_path], capture_output=True, text=True, timeout=15)
                if run_proc.returncode != 0:
                    test_passed = False
                    test_err_msg = f"Crash khi thực thi:\n{(run_proc.stderr or run_proc.stdout).strip()}"
                    print_step("❌", "Validation", f"{RED}Script vẫn bị crash!{RESET}")
                else:
                    print(f"  {GREEN}✅ Script thực thi thành công, lỗi ban đầu đã hết!{RESET}")
            except Exception as exc:
                test_passed = False
                test_err_msg = f"Lỗi khi chạy script: {exc}"

        if not test_passed:
            return self._handle_failure(ctx, test_err_msg)

        print_step("🎉", "Validation", f"{GREEN}XÁC NHẬN: Lỗi gốc đã được khắc phục hoàn toàn!{RESET}")
        ctx.state.validation_passed = True
        return ReportNode()

    def _handle_failure(
        self, ctx: GraphRunContext[BugFixState], error_msg: str
    ) -> Union[DirectFixCreationNode, PlanningNode, ReportNode]:
        from graph.nodes.direct_fix import DirectFixCreationNode
        from graph.nodes.planning import PlanningNode
        from graph.nodes.report import ReportNode

        ctx.state.validation_passed = False
        ctx.state.validation_errors.append(error_msg)
        ctx.state.action_history.append(f"Lần thử Replan {ctx.state.replan_count + 1}: Bản patch thất bại khi kiểm thử runtime. Chi tiết lỗi: {error_msg}")

        # Xử lý cho luồng DirectFix (Bug đơn giản)

        if ctx.state.complexity == BugComplexity.SIMPLE:
            ctx.state.direct_fix_fail_count += 1
            if ctx.state.direct_fix_fail_count < ctx.state.max_direct_fix_retries:
                print(f"  {YELLOW}🔄 DirectFix chưa khắc phục được lỗi. Thử lại DirectFix ({ctx.state.direct_fix_fail_count}/{ctx.state.max_direct_fix_retries})...{RESET}")
                return DirectFixCreationNode()
            else:
                print(f"\n  {RED}⚠️ DirectFix thất bại sau {ctx.state.direct_fix_fail_count} lần thử.{RESET}")
                print(f"  {YELLOW}👉 Đánh giá lại: Bug phức tạp hơn dự kiến -> Thăng cấp sang luồng Planning chi tiết!{RESET}")
                ctx.state.complexity = BugComplexity.COMPLEX
                ctx.state.user_plan_feedback = f"DirectFix thất bại sau {ctx.state.max_direct_fix_retries} lần thử. Lỗi runtime: {error_msg}"
                return PlanningNode()

        # Xử lý cho luồng Detailed Plan (Bug phức tạp)
        if ctx.state.replan_count < ctx.state.max_replan_limit:
            print(f"\n  {RED}❌ Lỗi gốc chưa được khắc phục triệt để.{RESET}")
            print(f"  {YELLOW}🔄 Quay về Planner để REPLAN lại... (Lần replan {ctx.state.replan_count + 1}/{ctx.state.max_replan_limit}){RESET}")
            ctx.state.user_plan_feedback = f"Validation thất bại (Lỗi chưa hết). Chi tiết lỗi: {error_msg}"
            return PlanningNode()
        else:
            print_step("⛔", "Validation", f"{RED}Đã vượt quá {ctx.state.max_replan_limit} lần Replan mà chưa sửa được lỗi gốc. Agent chịu thua.{RESET}")
            ctx.state.surrendered = True
            return ReportNode()
