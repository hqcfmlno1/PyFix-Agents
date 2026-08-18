"""
ReportNode — Tổng hợp & hiển thị báo cáo kết quả cuối cùng.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_graph import BaseNode, End, GraphRunContext

from graph.config import RED, RESET
from graph.models import BugFixState


@dataclass
class ReportNode(BaseNode[BugFixState]):
    """[Deterministic] Tổng hợp báo cáo cuối và ghi metrics/report JSON."""

    async def run(self, ctx: GraphRunContext[BugFixState]) -> End[str]:
        lines = ["\n--- KẾT QUẢ SỬA LỖI ---"]
        if ctx.state.initial_input_kind and ctx.state.initial_input_kind != "unknown":
            lines.append(f"Route ban đầu: {ctx.state.initial_input_kind}")
        if ctx.state.input_route_reason:
            lines.append(f"Lý do route: {ctx.state.input_route_reason}")
        if ctx.state.bug_types:
            lines.append("Bug class: " + ", ".join(bt.value for bt in ctx.state.bug_types))

        if ctx.state.final_fixes and ctx.state.want_apply:
            lines.append("Các file đã được chỉnh sửa:")
            for ffix in ctx.state.final_fixes:
                lines.append(f"  • {ffix.target_file}")
                if ffix.explanation:
                    lines.append(f"    → {ffix.explanation[:120]}")
        elif not ctx.state.want_apply:
            lines.append("Chế độ: Chỉ tư vấn (không sửa file).")
        else:
            lines.append("Không có file nào được chỉnh sửa.")

        if ctx.state.mcp_fallback_events:
            lines.append("MCP fallback:")
            for event in ctx.state.mcp_fallback_events:
                lines.append(f"  • {event}")

        if ctx.state.scope_rejection_reason:
            lines.append(f"Scope gate: {ctx.state.scope_rejection_reason}")
        if ctx.state.final_explanation:
            lines.append(f"Tóm tắt: {ctx.state.final_explanation}")
        if ctx.state.surrendered:
            lines.append("\n[THẤT BẠI] Hệ thống không thể tự sửa lỗi triệt để.")

        report = "\n".join(lines)
        ctx.state.final_report = report
        print(report)
        print()

        import json
        import os
        from datetime import datetime

        metrics = {
            "timestamp": datetime.now().isoformat(),
            "repo_path": ctx.state.repo_path,
            "status": "success" if ctx.state.validation_passed else ("surrendered" if ctx.state.surrendered else "incomplete"),
            "initial_input_kind": ctx.state.initial_input_kind,
            "bug_types": [bt.value for bt in ctx.state.bug_types],
            "mcp_fallback_events": ctx.state.mcp_fallback_events,
            "replan_count": ctx.state.replan_count,
            "analyzer": {"tool_calls": ctx.state.metrics_analyzer_tool_calls, "tokens": ctx.state.metrics_analyzer_tokens},
            "repro": {"tool_calls": ctx.state.metrics_repro_tool_calls, "tokens": ctx.state.metrics_repro_tokens},
            "planner": {"tool_calls": ctx.state.metrics_planner_tool_calls, "tokens": ctx.state.metrics_planner_tokens},
            "coder": {"tool_calls": ctx.state.metrics_coder_tool_calls, "tokens": ctx.state.metrics_coder_tokens},
        }

        pyfix_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        metrics_file = os.path.join(pyfix_root, "pyfix_metrics.json")

        try:
            if os.path.exists(metrics_file):
                with open(metrics_file, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            else:
                data = []
            data.append(metrics)
            with open(metrics_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"  {RED}⚠ Lỗi khi ghi file metrics: {exc}{RESET}")

        if ctx.state.report_json_path:
            bug_report = {
                "reproduced": bool(ctx.state.repro_confirmed or ctx.state.raw_user_input),
                "root_cause": (ctx.state.root_cause_explanation or ctx.state.final_explanation or "").strip(),
                "fix_summary": (ctx.state.final_explanation or report).strip(),
                "initial_input_kind": ctx.state.initial_input_kind,
                "bug_types": [bt.value for bt in ctx.state.bug_types],
                "mcp_fallback_events": ctx.state.mcp_fallback_events,
            }
            try:
                os.makedirs(os.path.dirname(ctx.state.report_json_path) or ".", exist_ok=True)
                with open(ctx.state.report_json_path, "w", encoding="utf-8") as fh:
                    json.dump(bug_report, fh, indent=2, ensure_ascii=False)
            except Exception as exc:
                print(f"  {RED}⚠ Lỗi khi ghi file report JSON: {exc}{RESET}")

        if getattr(ctx.state, "repro_script_path", None) and os.path.exists(ctx.state.repro_script_path):
            try:
                os.remove(ctx.state.repro_script_path)
            except Exception:
                pass

        return End(report)
