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
        lines = ["\n--- KẾT QUẢ SỬA LỖI ---"]

        if ctx.state.final_fixes and ctx.state.want_apply:
            lines.append("Các file đã được chỉnh sửa:")
            for ffix in ctx.state.final_fixes:
                lines.append(f"  • {ffix.target_file}")
                if hasattr(ffix, 'hunks') and ffix.hunks:
                    for hunk in ffix.hunks:
                        if hunk.old_lines:
                            for line in hunk.old_lines.splitlines():
                                lines.append(f"    - {line}")
                        if hunk.new_lines:
                            for line in hunk.new_lines.splitlines():
                                lines.append(f"    + {line}")
        elif not ctx.state.want_apply:
            lines.append("Chế độ: Chỉ tư vấn (không sửa file).")
        else:
            lines.append("Không có file nào được chỉnh sửa.")

        if ctx.state.surrendered:
            lines.append("\n[THẤT BẠI] Hệ thống không thể tự sửa lỗi triệt để.")

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
            "status": "success" if ctx.state.validation_passed else ("surrendered" if ctx.state.surrendered else "incomplete"),
            "replan_count": ctx.state.replan_count,
            "analyzer": {
                "tool_calls": ctx.state.metrics_analyzer_tool_calls,
                "tokens": ctx.state.metrics_analyzer_tokens
            },
            "repro": {
                "tool_calls": ctx.state.metrics_repro_tool_calls,
                "tokens": ctx.state.metrics_repro_tokens
            },
            "planner": {
                "tool_calls": ctx.state.metrics_planner_tool_calls,
                "tokens": ctx.state.metrics_planner_tokens
            },
            "coder": {
                "tool_calls": ctx.state.metrics_coder_tool_calls,
                "tokens": ctx.state.metrics_coder_tokens
            }
        }

        # Lưu file metrics vào thư mục gốc của PyFix-Agents thay vì repo bị lỗi
        pyfix_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        metrics_file = os.path.join(pyfix_root, "pyfix_metrics.json")
        
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

        # Dọn dẹp: Xóa file repro (nếu có) khi phiên làm việc đã hoàn thành
        if getattr(ctx.state, 'repro_script_path', None) and os.path.exists(ctx.state.repro_script_path):
            try:
                os.remove(ctx.state.repro_script_path)
            except Exception:
                pass

        return End(report)

