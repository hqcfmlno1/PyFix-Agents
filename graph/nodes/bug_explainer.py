"""
BugExplainerNode — Giải thích nguyên nhân lỗi sơ bộ & xử lý lỗi RT3 Environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from pydantic_graph import BaseNode, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_header
from graph.models import BugFixState, BugType

if TYPE_CHECKING:
    from graph.nodes.planning_strategy import PlanningStrategyNode
    from graph.nodes.report import ReportNode


@dataclass
class BugExplainerNode(BaseNode[BugFixState]):
    """
    [Deterministic] Giải thích sơ bộ Unhandled Runtime Exception cho người dùng.
    Nếu phát hiện lỗi Môi trường (RUNTIME_ENVIRONMENT), hiển thị hướng dẫn xử lý thủ công và kết thúc.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[PlanningStrategyNode, ReportNode]:
        from graph.nodes.planning_strategy import PlanningStrategyNode
        from graph.nodes.report import ReportNode

        print_header("Chẩn đoán & Phân tích Call Stack")

        types_display = []
        for bt in ctx.state.bug_types:
            if bt == BugType.DATA_DRIVEN_RUNTIME:
                types_display.append(f"{YELLOW}DATA-DRIVEN RUNTIME (Dữ liệu sai schema / dev hiểu sai data){RESET}")
            elif bt == BugType.LOGIC_DRIVEN_RUNTIME:
                types_display.append(f"{YELLOW}LOGIC-DRIVEN RUNTIME (Lỗi thuật toán / edge-case khi lặp/tính toán){RESET}")
            elif bt == BugType.SYNTAX:
                types_display.append(f"{RED}SYNTAX ERROR (Lỗi cú pháp){RESET}")
            elif bt == BugType.RUNTIME_ENVIRONMENT:
                types_display.append(f"{RED}RUNTIME ENVIRONMENT (Lỗi hạ tầng / môi trường){RESET}")

        print(f"  {BOLD}Phân loại Unhandled Exception:{RESET} {', '.join(types_display)}")
        if ctx.state.error_class:
            msg_str = f": {ctx.state.error_message}" if ctx.state.error_message else ""
            print(f"  {BOLD}Exception Name:{RESET} {RED}{ctx.state.error_class}{msg_str}{RESET}")

        if ctx.state.stack_trace:
            print(f"\n  {BOLD}📍 Call Stack nội bộ ({len(ctx.state.stack_trace)} frames):{RESET}")
            for frame in ctx.state.stack_trace:
                badge = f"{RED}[Crash Point]{RESET}" if frame.role == "crash_point" else f"{CYAN}[Caller]{RESET}"
                print(f"     • {badge} {frame.file_path}:{frame.line_number} in `{frame.function_name or 'main'}`")
                if frame.code_snippet:
                    print(f"       Code: {YELLOW}{frame.code_snippet}{RESET}")

        if ctx.state.bug_explanation:
            print(f"\n  {BOLD}💡 Tóm tắt chẩn đoán sơ bộ:{RESET}\n  {ctx.state.bug_explanation}")

        if BugType.RUNTIME_ENVIRONMENT in ctx.state.bug_types:
            print(f"\n  {YELLOW}⚠ ĐÂY LÀ LỖI MÔ TRƯỜNG / HẠ TẦNG:{RESET}")
            print("  Lỗi này không phát sinh do logic mã nguồn mà do môi trường hoặc tài nguyên bên ngoài.")
            print("  Hệ thống đề xuất hướng xử lý thủ công cho bạn như sau:\n")

            err_cls = (ctx.state.error_class or "").lower()
            raw_inp = ctx.state.raw_user_input.lower()

            if "module" in err_cls or "modulenotfound" in raw_inp or "no module named" in raw_inp:
                print(f"    • {GREEN}ModuleNotFound{RESET}: Thư viện chưa được cài đặt trong venv.")
                print(f"      → Hãy chạy lệnh cài đặt: {CYAN}pip install <tên_module>{RESET}")
            elif "memory" in err_cls or "memoryerror" in raw_inp:
                print(f"    • {GREEN}MemoryError{RESET}: Hết bộ nhớ RAM.")
                print(f"      → Giảm batch size hoặc tối ưu xử lý dữ liệu lớn.")
            elif "timeout" in err_cls or "connection" in err_cls or "timeout" in raw_inp:
                print(f"    • {GREEN}Network/TimeoutError{RESET}: Lỗi kết nối mạng hoặc server không phản hồi.")
                print(f"      → Kiểm tra đường truyền mạng hoặc tăng timeout config.")
            elif "permission" in err_cls or "ioerror" in err_cls:
                print(f"    • {GREEN}IOError/PermissionError{RESET}: Quyền truy cập file hoặc ổ đĩa bị từ chối.")
                print(f"      → Kiểm tra dung lượng ổ đĩa và phân quyền chmod/sudo.")
            else:
                print("    • Kiểm tra cấu hình môi trường, venv, hoặc tài nguyên hệ thống.")

            ctx.state.validation_passed = False
            ctx.state.validation_errors.append("Lỗi môi trường/hạ tầng — Cần xử lý thủ công ngoài môi trường.")
            return ReportNode()

        return PlanningStrategyNode()
