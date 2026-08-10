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

        if ctx.state.final_fixes and ctx.state.want_apply:
            lines.append(f"\n  📄 Các file đã được chỉnh sửa ({len(ctx.state.final_fixes)} file):")
            for ffix in ctx.state.final_fixes:
                lines.append(f"     • {CYAN}{ffix.target_file}{RESET}")
            lines.append(f"  💡 Giải thích tổng thể: {ctx.state.final_explanation}")
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

        # Ghi log metrics
        import json
        import os
        from datetime import datetime

        metrics = {
            "timestamp": datetime.now().isoformat(),
            "repo_path": ctx.state.repo_path,
            "status": "success" if passed else ("surrendered" if surrendered else "incomplete"),
            "replan_count": ctx.state.replan_count,
            "analyzer": {
                "calls": ctx.state.metrics_analyzer_calls,
                "tokens": ctx.state.metrics_analyzer_tokens
            },
            "planner": {
                "calls": ctx.state.metrics_planner_calls,
                "tokens": ctx.state.metrics_planner_tokens
            },
            "coder": {
                "calls": ctx.state.metrics_coder_calls,
                "tokens": ctx.state.metrics_coder_tokens
            }
        }

        metrics_file = os.path.join(ctx.state.repo_path, "pyfix_metrics.json")
        try:
            if os.path.exists(metrics_file):
                with open(metrics_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = []
            data.append(metrics)
            with open(metrics_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"  {RED}⚠ Lỗi khi ghi file metrics: {e}{RESET}")

        return End(report)

