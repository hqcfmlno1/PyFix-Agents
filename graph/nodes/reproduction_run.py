"""
ReproductionRunNode — Thực thi file _pyfix_repro.py và kiểm tra kết quả.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_step
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.reproduction_plan import ReproductionPlanNode
    from graph.nodes.planning import PlanningNode


@dataclass
class ReproductionRunNode(BaseNode[BugFixState]):
    """
    [Deterministic Node]
    Thực thi file _pyfix_repro.py do Planner vừa sinh ra.
    Xác minh xem script có văng ra đúng lỗi mà người dùng miêu tả không.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[ReproductionPlanNode, PlanningNode]:
        from graph.nodes.reproduction_plan import ReproductionPlanNode
        from graph.nodes.planning import PlanningNode

        print_step("🏃", "Reproduction Run", "Đang chạy thử kịch bản tái hiện lỗi...")

        if not ctx.state.repro_script_path or not os.path.exists(ctx.state.repro_script_path):
            print_step("❌", "Repro Error", f"{RED}Không tìm thấy file repro script.{RESET}")
            ctx.state.repro_confirmed = False
            return PlanningNode()

        try:
            # Set PYTHONPATH để script có thể import các module trong dự án
            env = os.environ.copy()
            env["PYTHONPATH"] = ctx.state.repo_path

            proc = subprocess.run(
                [sys.executable, ctx.state.repro_script_path],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=ctx.state.repo_path,
                env=env
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            
            output = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            ctx.state.repro_output = output

            # Lớp 1: Bắt buộc phải có lỗi (returncode != 0)
            # Lớp 2: Exception class phải khớp
            # Lớp 3: Traceback nên chứa file đích
            # Lớp 4: Error message phải khớp (tránh lỗi cùng file, cùng loại nhưng khác bản chất)
            
            error_class_str = ctx.state.error_class or ""
            target_file_str = os.path.basename(ctx.state.target_file) if ctx.state.target_file else ""
            error_message_str = ctx.state.error_message or ""

            is_crash = proc.returncode != 0
            has_correct_exception = error_class_str in stderr if error_class_str else True
            has_target_file = target_file_str in stderr if target_file_str else True
            has_correct_message = error_message_str in stderr if error_message_str else True

            if is_crash and has_correct_exception and has_target_file and has_correct_message:
                print(f"  {GREEN}✅ Tái hiện THÀNH CÔNG! Đã kích hoạt đúng lỗi {error_class_str}: {error_message_str}{RESET}")
                ctx.state.repro_confirmed = True
                # KHÔNG xóa file ở đây, giữ lại cho ValidationNode chạy
                return PlanningNode()
            else:
                ctx.state.repro_retry_count += 1
                if ctx.state.repro_retry_count < 3:
                    if not is_crash:
                        reason = "Script chạy thành công (return 0), không văng ra lỗi nào."
                    elif not has_correct_exception:
                        reason = f"Script văng ra lỗi nhưng không chứa `{error_class_str}`."
                    elif not has_target_file:
                        reason = f"Script văng lỗi nhưng traceback không chứa file gốc `{target_file_str}` (có thể lỗi ở ngay file test)."
                    elif not has_correct_message:
                        reason = f"Script văng đúng lỗi {error_class_str} tại {target_file_str} nhưng sai thông điệp. Cần thông điệp chứa `{error_message_str}`."
                    
                    print(f"  {YELLOW}⚠️ Tái hiện thất bại lần {ctx.state.repro_retry_count}: {reason}{RESET}")
                    print(f"  {CYAN}🔄 Đang yêu cầu Planner viết lại script...{RESET}")
                    return ReproductionPlanNode()
                else:
                    print(f"  {RED}❌ Đã thử 3 lần nhưng không thể tái hiện lỗi tự động.{RESET}")
                    ctx.state.repro_confirmed = False
                    return PlanningNode()

        except subprocess.TimeoutExpired:
            ctx.state.repro_output = "TimeoutExpired: Script chạy quá 10s (có thể bị treo do I/O, while True, hoặc deadlock)."
            ctx.state.repro_retry_count += 1
            if ctx.state.repro_retry_count < 3:
                print(f"  {YELLOW}⚠️ Tái hiện thất bại lần {ctx.state.repro_retry_count}: Script bị treo (Timeout).{RESET}")
                return ReproductionPlanNode()
            else:
                print(f"  {RED}❌ Đã thử 3 lần nhưng script tái hiện toàn bị treo.{RESET}")
                ctx.state.repro_confirmed = False
                return PlanningNode()
        except Exception as exc:
            ctx.state.repro_output = f"Lỗi hệ thống khi chạy subprocess: {exc}"
            ctx.state.repro_confirmed = False
            return PlanningNode()
            
    def _cleanup(self, ctx: GraphRunContext[BugFixState]):
        """Xóa file tạm _pyfix_repro.py"""
        if ctx.state.repro_script_path and os.path.exists(ctx.state.repro_script_path):
            try:
                os.remove(ctx.state.repro_script_path)
            except Exception:
                pass
