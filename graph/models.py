"""
Data Models — BugType, StackFrame, BugReport, PlanStep, CodeFix, BugFixState, ...
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from graph.config import MAX_REPLAN, MAX_RETRY


# ── Bug Type & Complexity ───────────────────────────────────────────────────
class BugType(str, Enum):
    """Phân loại lỗi chính cho Unhandled Runtime Exception."""
    DATA_DRIVEN_RUNTIME = "data_driven_runtime"   # Lỗi do data sai schema / dev hiểu sai schema (KeyError, TypeError, ValueError, Pydantic, ...)
    LOGIC_DRIVEN_RUNTIME = "logic_driven_runtime" # Lỗi do logic thuật toán sai ở case đặc biệt (IndexError, ZeroDivisionError, UnboundLocalError, ...)


class BugComplexity(str, Enum):
    """Đánh giá độ phức tạp của lỗi."""
    SIMPLE = "simple"     # Lỗi đơn giản 1 frame, chỉ cần DirectFix
    COMPLEX = "complex"   # Lỗi phức tạp nhiều frame / multi-file, cần Plan nhiều bước


class UserSentiment(str, Enum):
    """Phản hồi của người dùng về bản vá."""
    INITIAL = "initial"
    HAPPY = "happy"
    UNHAPPY = "unhappy"


# ── Stack Frame (Call Stack nội bộ dự án) ──────────────────────────────────
class StackFrame(BaseModel):
    """Một frame trong Call Stack của dự án (đã lọc bỏ thư viện ngoài / venv / stdlib)."""
    file_path: str = Field(description="Đường dẫn tương đối của file trong dự án")
    line_number: int = Field(description="Số dòng code xuất hiện trong traceback log")
    function_name: str = Field(default="", description="Tên hàm hoặc phương thức được gọi tại frame này")
    code_snippet: str = Field(default="", description="Đoạn mã nguồn hiển thị trong traceback log")
    role: str = Field(default="caller", description="Vai trò: 'crash_point' (file ném lỗi crash) hoặc 'caller' (hàm gọi truyền dữ liệu)")


# ── Bug Report (output từ InputAnalyzerNode) ─────────────────────────────────
class BugReport(BaseModel):
    """
    Output có cấu trúc từ Input Analyzer Agent.
    Chỉ trích xuất dữ liệu kỹ thuật từ traceback — KHÔNG giải thích nguyên nhân
    (việc đó để Planner Agent làm sau khi đọc code thực tế).
    """
    bug_types: List[BugType] = Field(description="Danh sách loại lỗi (data_driven_runtime hoặc logic_driven_runtime)")
    error_class: str = Field(default="", description="Tên Exception class cụ thể (VD: KeyError, IndexError, TypeError)")
    error_message: str = Field(default="", description="Thông điệp lỗi chi tiết kèm theo Exception (VD: 'user_id', 'list index out of range')")
    stack_trace: List[StackFrame] = Field(default_factory=list, description="Danh sách các frame trong Call Stack dự án theo thứ tự gọi (từ caller đến crash_point)")
    target_file: Optional[str] = Field(default=None, description="File crash chính trong dự án")
    error_line: Optional[int] = Field(default=None, description="Dòng code crash chính trong dự án")
    runtime_input_data: Optional[str] = Field(default=None, description="Dữ liệu đầu vào runtime gây crash (nếu có)")
    want_plan: bool = Field(default=False, description="User có muốn xem/duyệt plan trước khi sửa hay không")


# ── Plan Step ────────────────────────────────────────────────────────────────
class PlanStep(BaseModel):
    """Một bước cụ thể trong kế hoạch sửa lỗi."""
    step_id: int = Field(description="Số thứ tự bước (1, 2, 3...)")
    title: str = Field(description="Tiêu đề tóm tắt ngắn gọn bước sửa")
    target_file: str = Field(description="Đường dẫn tương đối của file cần chỉnh sửa")
    description: str = Field(description="Hướng dẫn kỹ thuật chi tiết những đoạn code/hàm cần sửa đổi")


# ── Plan (Lỗi phức tạp) ───────────────────────────────────────
class PlanWrapper(BaseModel):
    """Bao bọc danh sách PlanStep cho Pydantic AI Output."""
    root_cause: str = Field(description="Nguyên nhân gốc rễ của lỗi sau khi đọc code thực tế")
    steps: List[PlanStep] = Field(description="Danh sách các bước thực thi")


# ── Bug Explanation (Cho lỗi Simple) ─────────────────────────────────────────
class BugExplanation(BaseModel):
    """Output chẩn đoán lỗi từ Coder Agent cho Phase 1 của lỗi Simple."""
    explanation: str = Field(description="Giải thích nguyên nhân gốc rễ gây ra lỗi và cách bạn dự định sửa nó (chỉ text, không code)")


# ── Replan History ───────────────────────────────────────────────────────────
class RePlanHistory(BaseModel):
    """Lưu lịch sử các lần replan."""
    revision: int
    feedback: str
    rejected_plan_summary: str


class IterationContext(BaseModel):
    """Ngữ cảnh của một vòng lặp sửa lỗi (Causal Chain)."""
    initial_error: str = Field(description="Lỗi ban đầu ở vòng này")
    target_files: List[str] = Field(default_factory=list, description="Các file đã sửa")
    patch_summary: str = Field(description="Tóm tắt nội dung patch")
    user_feedback: str = Field(default="", description="Phản hồi/lỗi mới từ người dùng")


# ── CodeFix (Internal State) ────────────────────────────────────────────────
class CodeFix(BaseModel):
    """
    State nội bộ: Lưu trữ patch đã được Coder Agent (trả về dưới dạng str thô)
    áp dụng thành công vào file. Không dùng làm Output Schema cho Agent nữa.
    """
    target_file: str
    patch_blocks: str
    explanation: str = ""


# ── State ────────────────────────────────────────────────────────────────────
class BugFixState(BaseModel):
    """
    State trung tâm — lưu toàn bộ dữ liệu qua mọi node.
    Mutable: mỗi node đọc và cập nhật state này.
    """

    # ── Phase 1: Project ────────────────────────────────────────────────────
    repo_path: str = ""
    is_repo_valid: bool = False
    project_tree: str = ""

    # ── Phase 2: Input & Assessment ─────────────────────────────────────────
    raw_user_input: str = ""
    runtime_input_data: Optional[str] = None
    bug_types: List[BugType] = Field(default_factory=list)
    target_file: Optional[str] = None
    error_file: Optional[str] = None
    error_line: Optional[int] = None
    error_class: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: List[StackFrame] = Field(default_factory=list)
    want_plan: bool = False
    want_apply: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    root_cause_explanation: str = ""  # Được điền bởi PlanningNode sau khi đọc code thực tế
    complexity: BugComplexity = BugComplexity.COMPLEX
    user_sentiment: UserSentiment = UserSentiment.INITIAL
    user_suggested_fix: Optional[str] = None

    # ── Phase 3: Planning / Reproduction ─────────────────────────────────────

    repro_script_path: Optional[str] = None
    repro_confirmed: Optional[bool] = None
    repro_output: str = ""
    repro_retry_count: int = 0

    current_plan: List[PlanStep] = Field(default_factory=list)
    current_step_index: int = 0
    plan_approved: bool = False
    replan_count: int = 0
    max_replan_limit: int = MAX_REPLAN
    user_plan_feedback: Optional[str] = None
    plan_history: List[RePlanHistory] = Field(default_factory=list)
    iteration_history: List[IterationContext] = Field(default_factory=list)

    # ── Phase 4: Execution ──────────────────────────────────────────────────
    step_max_retries: int = MAX_RETRY
    files_context: Dict[str, str] = Field(default_factory=dict)
    final_fixes: List[CodeFix] = Field(default_factory=list)
    final_explanation: str = ""
    execution_logs: List[str] = Field(default_factory=list)
    applied_diffs_history: List[Dict] = Field(default_factory=list)
    action_history: List[str] = Field(default_factory=list)

    # ── Phase 5: Validation ─────────────────────────────────────────────────
    validation_passed: bool = False
    validation_errors: List[str] = Field(default_factory=list)
    retry_count: int = 0
    surrendered: bool = False

    # ── Phase 6: Report ─────────────────────────────────────────────────────
    final_report: Optional[str] = None

    # ── Phase 7: Metrics ─────────────────────────────────────────────────────
    metrics_analyzer_tokens: int = 0
    metrics_analyzer_tool_calls: int = 0
    metrics_repro_tokens: int = 0
    metrics_repro_tool_calls: int = 0
    metrics_planner_tokens: int = 0
    metrics_planner_tool_calls: int = 0
    metrics_coder_tokens: int = 0
    metrics_coder_tool_calls: int = 0

    model_config = {"arbitrary_types_allowed": True}
