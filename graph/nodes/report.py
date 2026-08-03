"""
ReportNode — Tổng hợp & hiển thị báo cáo kết quả cuối cùng.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphRunContext

from graph.config import BOLD, CYAN, GREEN, RED, RESET, YELLOW
from graph.helpers import print_header
from graph.models import BugFixState


@dataclass
class ReportNode(BaseNode[BugFixState]):
    """
    [Deterministic] Tổng hợp & hiển thị báo cáo kết quả cuối cùng và diff tổng hợp.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> End[str]:
        print_header("Báo cáo kết quả — PyFix-Agents v2")

        passed = ctx.state.validation_passed
        surrendered = ctx.state.surrendered

        if passed:
            status_icon = "✅"
            status_text = "THÀNH CÔNG (Đã khắc phục lỗi gốc)"
            status_color = GREEN
        elif surrendered:
            status_icon = "🏳️"
            status_text = f"THẤT BẠI — AGENT CHỊU THUA (Vượt quá {ctx.state.max_replan_limit} lần Replan)"
            status_color = RED
        else:
            status_icon = "⚠"
            status_text = "KẾT THÚC (Chưa xử lý triệt để)"
            status_color = YELLOW

        types_str = ", ".join([bt.value.upper() for bt in ctx.state.bug_types]) if ctx.state.bug_types else "N/A"

        lines = [
            f"  {status_icon} Trạng thái : {status_color}{BOLD}{status_text}{RESET}",
            f"  📁 Dự án     : {ctx.state.repo_path}",
            f"  🐛 Loại lỗi  : {CYAN}{types_str}{RESET}",
            f"  🔄 Số lần Replan : {ctx.state.replan_count}/{ctx.state.max_replan_limit}",
        ]

        if ctx.state.code_fix and ctx.state.code_fix.files and ctx.state.want_apply:
            lines.append(f"\n  📄 Các file đã được chỉnh sửa ({len(ctx.state.code_fix.files)} file):")
            for ffix in ctx.state.code_fix.files:
                lines.append(f"     • {CYAN}{ffix.target_file}{RESET}")
            lines.append(f"  💡 Giải thích tổng thể: {ctx.state.code_fix.explanation}")
        elif not ctx.state.want_apply:
            lines.append(f"\n  💡 Chế độ: {YELLOW}Chỉ tư vấn / xem kế hoạch (không chỉnh sửa file thực tế){RESET}")

        if ctx.state.validation_errors:
            lines.append(f"\n  {YELLOW}⚠ Chi tiết lỗi / Ghi chú trong quá trình chạy:{RESET}")
            for err in ctx.state.validation_errors[-5:]:
                lines.append(f"     • {RED}{err}{RESET}")

        report = "\n".join(lines)
        ctx.state.final_report = report
        print(report)
        print()

        return End(report)

