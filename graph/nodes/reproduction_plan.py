"""
ReproductionPlanNode — Dùng Reproduction Agent để viết kịch bản tái hiện lỗi.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import DEFAULT_PLANNER_USE_MCP, get_repro_agent
from graph.config import CYAN, RED, RESET, REPRO_MODEL_NAME
from graph.helpers import print_step
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.planning import PlanningNode
    from graph.nodes.reproduction_run import ReproductionRunNode


@dataclass
class ReproductionPlanNode(BaseNode[BugFixState]):
    """[Agent Node] Viết script Python ngắn để tái hiện lỗi."""

    async def run(self, ctx: GraphRunContext[BugFixState]) -> "ReproductionRunNode | PlanningNode":
        from graph.nodes.planning import PlanningNode
        from graph.nodes.reproduction_run import ReproductionRunNode

        print_step("🧪", "Reproduction Plan", f"Đang yêu cầu {REPRO_MODEL_NAME} viết kịch bản tái hiện lỗi...")

        prompt = f"""NHIỆM VỤ: Viết một Python script ngắn gọn để tái hiện lỗi sau:
- Exception: {ctx.state.error_class}: {ctx.state.error_message}
- File crash chính: {ctx.state.target_file}, Dòng: {ctx.state.error_line}
"""
        if ctx.state.runtime_input_data:
            prompt += f"- Dữ liệu đầu vào (Runtime Input) từ người dùng: {ctx.state.runtime_input_data}\n"

        prompt += """
CHI TIẾT CALL STACK (BẮT BUỘC ĐỌC ĐỂ HIỂU CONTEXT):
"""
        for frame in ctx.state.stack_trace:
            prompt += f"- File: {frame.file_path}, Line: {frame.line_number}, Function: {frame.function_name}\n"
            if frame.code_snippet:
                prompt += f"  Code: {frame.code_snippet}\n"

        prompt += """
YÊU CẦU CỦA SCRIPT TÁI HIỆN:
1. Nếu có tool, ưu tiên đọc code thực tế tại các điểm crash để hiểu hàm cần gọi.
2. Nếu file chạy có I/O hoặc phụ thuộc dữ liệu ngoài phức tạp, bỏ qua entrypoint và import trực tiếp hàm/class bên trong để mock data.
3. TUYỆT ĐỐI KHÔNG dùng `input()` thực tế trong script của bạn vì sẽ làm treo terminal.
4. Script phải cô đọng, tự chứa và chạy được ngay ở thư mục gốc của dự án.
"""

        if ctx.state.repro_retry_count > 0:
            prompt += f"""
CẢNH BÁO MẠNH: Ở lần thử thứ {ctx.state.repro_retry_count}, kịch bản của bạn KHÔNG tái hiện được lỗi ban đầu.
Output nhận được từ script của bạn là:
---
{ctx.state.repro_output}
---
Hãy kiểm tra lại cách bạn import, class dependencies, hoặc mock data.
Bạn PHẢI làm cho nó văng ra chính xác lỗi {ctx.state.error_class}.
"""

        prompt += """
OUTPUT FORMAT:
CHỈ TRẢ VỀ NỘI DUNG FILE PYTHON (DẠNG CHUỖI THUẦN). KHÔNG GIẢI THÍCH, KHÔNG BỌC TRONG QUOTE MARKDOWN (```python).
"""

        try:
            use_mcp = ctx.state.planner_use_mcp if ctx.state.planner_use_mcp is not None else DEFAULT_PLANNER_USE_MCP
            result = await get_repro_agent(use_mcp).run(prompt)

            from graph.helpers import count_tool_calls

            ctx.state.metrics_repro_tool_calls += count_tool_calls(result.new_messages())
            try:
                usage = result.usage()
                ctx.state.metrics_repro_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
            except Exception:
                pass

            script_content = result.output.strip()
            if script_content.startswith("```python"):
                script_content = script_content[9:]
            elif script_content.startswith("```"):
                script_content = script_content[3:]
            if script_content.endswith("```"):
                script_content = script_content[:-3]
            script_content = script_content.strip()

            repro_path = os.path.join(ctx.state.repo_path, "_pyfix_repro.py")
            with open(repro_path, "w", encoding="utf-8") as fh:
                fh.write(script_content)

            ctx.state.repro_script_path = repro_path
            print(f"  {CYAN}💾 Đã tạo file kịch bản tái hiện tại: _pyfix_repro.py{RESET}")
            return ReproductionRunNode()
        except Exception as exc:
            print_step("❌", "Repro Plan Error", f"{RED}Lỗi khi tạo script tái hiện: {exc}{RESET}")
            ctx.state.repro_confirmed = False
            return PlanningNode()
