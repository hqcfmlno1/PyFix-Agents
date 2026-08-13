"""
ReproductionPlanNode — Dùng Planner Agent để viết kịch bản tái hiện lỗi.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_graph import BaseNode, GraphRunContext

from graph.agents import repro_agent
from graph.config import BOLD, CYAN, REPRO_MODEL_NAME, RED, RESET, YELLOW
from graph.helpers import print_step, print_agent_thinking
from graph.models import BugFixState

if TYPE_CHECKING:
    from graph.nodes.reproduction_run import ReproductionRunNode
    from graph.nodes.planning import PlanningNode


@dataclass
class ReproductionPlanNode(BaseNode[BugFixState]):
    """
    [Agent Node]
    Yêu cầu Planner viết một script Python ngắn để tái hiện lỗi,
    nhằm xác minh LLM đã hiểu nguyên nhân gốc rễ và để làm test tự động.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> ReproductionRunNode | PlanningNode:
        from graph.nodes.reproduction_run import ReproductionRunNode
        from graph.nodes.planning import PlanningNode

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
1. Bạn CẦN ĐỌC CODE THỰC TẾ (dùng tool đọc file) tại các điểm crash để hiểu hàm cần gọi.
2. CHIẾN THUẬT 1 (Ưu tiên): Nếu trong stack trace có file chạy (entry-point script) KHÔNG yêu cầu nhập liệu I/O (không có `input()`, không đòi `sys.argv`) và có sẵn kịch bản (scenario), HÃY chạy thẳng file đó bằng `subprocess.run` (dùng `python -m` nếu cần) hoặc import trực tiếp.
3. CHIẾN THUẬT 2 (Bắt buộc dùng Mock Data): Nếu file chạy có chứa I/O hoặc phụ thuộc dữ liệu ngoài phức tạp, BỎ QUA file chạy đó. Hãy tiến thẳng vào lớp bên trong, import trực tiếp hàm/class bị lỗi và truyền dữ liệu giả (mock data) để kích hoạt lỗi.
4. TUYỆT ĐỐI KHÔNG dùng `input()` thực tế trong script của bạn vì sẽ làm treo terminal.
5. Script không được quá dài, phải cô đọng, tự chứa và chạy được ngay ở thư mục gốc của dự án.
"""

        if ctx.state.repro_retry_count > 0:
            prompt += f"""
CẢNH BÁO MẠNH: Ở lần thử thứ {ctx.state.repro_retry_count}, kịch bản của bạn KHÔNG tái hiện được lỗi ban đầu.
Output nhận được từ script của bạn là:
---
{ctx.state.repro_output}
---
Hãy kiểm tra lại cách bạn import, class dependencies, hoặc mock data. 
Bạn PHẢI làm cho nó văng ra chính xác lỗi {ctx.state.error_class}. NẾU CHƯA ĐỌC FILE NGUỒN, HÃY DÙNG TOOL ĐỂ ĐỌC TRƯỚC KHI VIẾT SCRIPT!
"""

        prompt += """
OUTPUT FORMAT: 
CHỈ TRẢ VỀ NỘI DUNG FILE PYTHON (DẠNG CHUỖI THUẦN). KHÔNG GIẢI THÍCH, KHÔNG BỌC TRONG QUOTE MARKDOWN (```python). Bắt đầu dòng 1 bằng `import` hoặc code luôn.
"""

        try:
            result = await repro_agent.run(prompt)
            
            # Cập nhật Metrics
            from graph.helpers import count_tool_calls
            ctx.state.metrics_repro_tool_calls += count_tool_calls(result.new_messages())
            try:
                usage = result.usage()
                ctx.state.metrics_repro_tokens += (usage.request_tokens or 0) + (usage.response_tokens or 0)
            except Exception:
                pass

            script_content = result.output.strip()
            
            # Xóa markdown backticks nếu LLM vẫn ngoan cố trả về
            if script_content.startswith("```python"):
                script_content = script_content[9:]
            elif script_content.startswith("```"):
                script_content = script_content[3:]
            if script_content.endswith("```"):
                script_content = script_content[:-3]
            
            script_content = script_content.strip()

            repro_path = os.path.join(ctx.state.repo_path, "_pyfix_repro.py")
            with open(repro_path, "w", encoding="utf-8") as f:
                f.write(script_content)
                
            ctx.state.repro_script_path = repro_path
            
            print(f"  {CYAN}💾 Đã tạo file kịch bản tái hiện tại: _pyfix_repro.py{RESET}")
            return ReproductionRunNode()

        except Exception as exc:
            print_step("❌", "Repro Plan Error", f"{RED}Lỗi khi tạo script tái hiện: {exc}{RESET}")
            # Nếu có lỗi khi tạo repro script (ví dụ LLM sập), ta đành bỏ qua tái hiện và nhảy thẳng sang Planner
            ctx.state.repro_confirmed = False
            return PlanningNode()
