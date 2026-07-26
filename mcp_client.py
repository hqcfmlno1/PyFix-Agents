from __future__ import annotations
import asyncio
import difflib
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Union

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext

load_dotenv()



# ── API Key & Provider Setup ──────────────────────────────────────────────────
API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
if not API_KEY:
    raise RuntimeError("Không tìm thấy GEMINI_API_KEY hoặc GOOGLE_API_KEY trong file .env!")

if API_KEY.startswith("AIzaSy"):
    provider = GoogleProvider(api_key=API_KEY)
else:
    from google.oauth2.credentials import Credentials
    from google import genai
    creds = Credentials(token=API_KEY)
    genai_client = genai.Client(credentials=creds)
    provider = GoogleProvider(client=genai_client)

#MODEL_DISPLAY_NAME = "gemma-4-31b-it"
MODEL_DISPLAY_NAME = "gemini-2.5-flash"

model = GoogleModel(MODEL_DISPLAY_NAME, provider=provider)

MODEL_NAME = MODEL_DISPLAY_NAME

# MCP server endpoint
MCP_SERVER_PORT = 8000
MCP_SERVER_URL  = f"http://localhost:{MCP_SERVER_PORT}/mcp"

MAX_SYNTAX_RETRIES = 3   # Số lần thử lại Execution khi lỗi syntax nhỏ
MAX_LOGIC_RETRIES = 2    # Số lần replan khi lỗi logic / retry quá nhiều

# ANSI colors
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"



# DATA MODELS
class BugType(str, Enum):
    CRASH = "crash"    # Exception / Traceback từ terminal
    LOGIC = "logic"    # Code chạy được nhưng ra kết quả sai


class PlanStep(BaseModel):
    """Một bước cụ thể trong kế hoạch sửa lỗi."""
    step_id: int
    title: str
    description: str
    target_file: str
    action: str          # "read" | "modify" | "analyze" | "test"
    reasoning: str = ""


class PlanOutput(BaseModel):
    """Output từ Planner Agent."""
    steps: List[PlanStep]
    summary: str
    risk_level: str = "medium"    # "low" | "medium" | "high"


class BugReport(BaseModel):
    """Output từ Input Analyzer Agent."""
    bug_type: BugType
    target_file: Optional[str] = None
    actual_output: Optional[str] = None
    expected_output: Optional[str] = None
    want_plan: bool = False


class SingleFileFix(BaseModel):
    """Chi tiết sửa đổi cho một file cụ thể."""
    target_file: str = Field(description="Đường dẫn tương đối của file cần sửa (vd: main.py, utils.py)")
    new_content: str = Field(description="TOÀN BỘ nội dung mới của file sau khi sửa")
    changes_summary: str = Field(description="Tóm tắt những thay đổi trong file này")


class CodeFix(BaseModel):
    """Output từ Coder Agent (Hỗ trợ sửa 1 hoặc nhiều file cùng lúc)."""
    files: List[SingleFileFix] = Field(default_factory=list, description="Danh sách tất cả các file cần sửa")
    explanation: str = Field(description="Giải thích nguyên nhân gốc rễ và giải pháp sửa tổng thể")


class RePlanHistory(BaseModel):
    """Lưu lịch sử các lần replan."""
    revision: int                # số thứ tự của lần replan
    feedback: str
    rejected_plan_summary: str


class BugFixState(BaseModel):
    """
    State trung tâm — lưu toàn bộ dữ liệu qua mọi node.
    Mutable: mỗi node đọc và cập nhật state này.
    """

    # ── Phase 1: Project ────────────────────────────────────────────────────
    repo_path: str = ""
    is_repo_valid: bool = False
    project_tree: str = ""

    # ── Phase 2: Input ──────────────────────────────────────────────────────
    raw_user_input: str = ""
    bug_type: Optional[BugType] = None
    target_file: Optional[str] = None
    actual_output: Optional[str] = None
    expected_output: Optional[str] = None
    want_plan: bool = False
    missing_fields: List[str] = Field(default_factory=list)      

    # ── Phase 3: Planning ───────────────────────────────────────────────────
    current_plan: List[PlanStep] = Field(default_factory=list)
    plan_approved: bool = False
    replan_count: int = 0
    user_plan_feedback: Optional[str] = None
    plan_history: List[RePlanHistory] = Field(default_factory=list)

    # ── Phase 4: Execution ──────────────────────────────────────────────────
    files_context: Dict[str, str] = Field(default_factory=dict)
    code_fix: Optional[CodeFix] = None
    execution_logs: List[str] = Field(default_factory=list)

    # ── Phase 5: Validation ─────────────────────────────────────────────────
    validation_passed: bool = False
    validation_errors: List[str] = Field(default_factory=list)
    retry_count: int = 0

    # ── Phase 6: Report ─────────────────────────────────────────────────────
    final_report: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}



# SYSTEM PROMPTS
_INPUT_ANALYZER_PROMPT = textwrap.dedent("""\
    Bạn là AI chuyên phân tích và phân loại lỗi phần mềm Python.
    Nhiệm vụ: Đọc mô tả lỗi từ người dùng (ngôn ngữ tự nhiên) và trích xuất thông tin có cấu trúc.

    PHÂN LOẠI LỖI (bug_type):
    - crash: User cung cấp stack trace, traceback, exception log từ terminal
    - logic: User mô tả hành vi sai của chương trình/hàm (ví dụ: "chạy ra True nhưng mong muốn False", "input 9 ra True nhưng 9 không phải số nguyên tố")

    PHÁT HIỆN want_plan (True nếu user đề cập):
    "xem plan", "show plan", "lên plan", "muốn duyệt", "kế hoạch", "confirm trước",
    "plan trước", "xem trước", "hiển thị plan", "tôi muốn plan"

    HƯỚNG DẪN TRÍCH XUẤT:
    1. target_file:
       - Tên file chứa lỗi.
       - Nếu user KHÔNG nêu tên file cụ thể trong mô tả, hãy chọn file .py phù hợp từ danh sách CẤU TRÚC DỰ ÁN được cung cấp bên dưới.

    2. actual_output (Hành vi / Kết quả thực tế):
       - Trích xuất từ mô tả thực tế của user (ví dụ: "output là true", "chạy ra True", "trả về 9").
       - Không được để None nếu user có đề cập đến kết quả hiện tại.

    3. expected_output (Hành vi / Kết quả mong muốn):
       - Trích xuất từ kỳ vọng của user (ví dụ: "9 không phải số nguyên tố" -> "False (vì 9 là hợp số)", "mong muốn False").

    Trả về JSON theo đúng schema. Không giải thích thêm.
""")

_PLANNER_PROMPT = textwrap.dedent("""\
    Bạn là AI chuyên lập kế hoạch sửa lỗi Python an toàn và hiệu quả.
    Bạn có các MCP tools: get_file_context, read_file, search_in_codebase.

    QUY TRÌNH LẬP KẾ HOẠCH:
    1. KHÁM PHÁ: Dùng get_file_context(file_path, repo_path) để đọc nội dung file chính được báo lỗi.
    2. TRUY VẾT: Xem các import/dependencies trong file đó. Nếu lỗi có thể liên quan đến file khác,
       dùng read_file hoặc get_file_context để đọc thêm các file liên quan.
    3. TÌM KIẾM: Nếu cần tìm định nghĩa hàm/class cụ thể, dùng search_in_codebase(keyword, repo_path).
    4. PHÂN TÍCH: Sau khi đã đọc đủ context, phân tích nguyên nhân gốc rễ.
    5. LẬP PLAN: Tạo kế hoạch sửa lỗi chi tiết, mỗi bước nêu rõ file cần sửa và hành động cụ thể.

    NGUYÊN TẮC:
    1. AN TOÀN: Tối thiểu hóa thay đổi. Chỉ sửa đúng chỗ gây ra lỗi.
    2. CỤ THỂ: Mỗi bước nêu rõ file cần sửa (target_file) và hành động chính xác.
    3. THỰC TẾ: Không đề xuất refactor lớn khi không cần thiết.
    4. KIỂM TRA: Bước cuối luôn là kiểm tra kết quả sau sửa.
    5. BẮT BUỘC ĐỌC FILE: Bạn PHẢI dùng MCP tools để đọc mã nguồn thực tế TRƯỚC KHI lập plan.
       Không được lập plan dựa trên phỏng đoán.

    risk_level:
    - low:    Sửa 1 dòng hoặc logic đơn giản
    - medium: Sửa một hàm hoặc class
    - high:   Sửa nhiều file hoặc thay đổi kiến trúc

    Trả về JSON theo đúng schema.
""")

_CODER_PROMPT = textwrap.dedent("""\
    Bạn là AI chuyên thực thi sửa lỗi Python với độ chính xác tuyệt đối.
    Nhiệm vụ hàng đầu của bạn là THỰC THI NGHIÊM NGẶT THEO ĐÚNG TỪNG BƯỚC TRONG KẾ HOẠCH (PLAN).

    QUY TẮC TUÂN THỦ PLAN BẮT BUỘC:
    1. Kế hoạch (Plan) được duyệt là MỆNH LỆNH BẮT BUỘC, KHÔNG PHẢI GỢI Ý.
    2. Bạn BẮT BUỘC phải thực hiện đầy đủ và chính xác tất cả các bước được chỉ định trong Plan.
    3. Với mỗi bước chỉ định sửa file nào (target_file), bạn phải thực hiện đúng hành động sửa trên file đó.
    4. KHÔNG TỰ Ý bỏ qua bước nào, KHÔNG tự ý đổi sang cách sửa khác nằm ngoài Plan ngoại trừ việc sửa cú pháp nhỏ phát sinh.

    QUY TRÌNH THỰC THI:
    1. Đọc kỹ từng bước trong KẾ HOẠCH BẮT BUỘC.
    2. Dùng các MCP tools (get_file_context, read_file, search_in_codebase) để đọc kiểm tra lại nội dung đầy đủ các file cần sửa theo đúng kế hoạch.
    3. Thực hiện sửa đổi trên tất cả các file được yêu cầu trong Plan (trả về trong danh sách 'files').
    4. Với MỖI file trong danh sách 'files', trả về TOÀN BỘ (100%) nội dung file hoàn chỉnh sau khi sửa.

    QUY TẮC MÃ NGUỒN:
    ✗ Không dùng "..." hay "# rest of code unchanged"
    ✗ Không xóa code đang hoạt động tốt
    ✓ Với mỗi file sửa trong 'files', phải trả về 100% nội dung hoàn chỉnh của file đó sau khi sửa
    ✓ Trong trường 'explanation', giải thích rõ cách bạn đã tuân thủ thực hiện từng bước của Plan
""")



# AGENT DEFINITIONS  (gemma-4-31b-it via Google AI Studio)
input_analyzer_agent: Agent[None, BugReport] = Agent(
    model,
    output_type=BugReport,
    system_prompt=_INPUT_ANALYZER_PROMPT,
    retries=2,
)

mcp_toolset = MCPToolset(MCP_SERVER_URL)


planner_agent: Agent[None, PlanOutput] = Agent(
    model,
    output_type=PlanOutput,
    system_prompt=_PLANNER_PROMPT,
    toolsets=[mcp_toolset],
    retries=2,
)

coder_agent: Agent[None, CodeFix] = Agent(
    model,
    output_type=CodeFix,
    system_prompt=_CODER_PROMPT,
    toolsets=[mcp_toolset],
    retries=2,
)



# HELPER FUNCTIONS
def _print_header(title: str) -> None:
    bar = "═" * 58
    print(f"\n{CYAN}{BOLD}╔{bar}╗")
    print(f"║  {title:<56}║")
    print(f"╚{bar}╝{RESET}")


def _print_step(icon: str, label: str, msg: str = "") -> None:
    print(f"{BOLD}{icon} [{label}]{RESET} {msg}")


def _build_project_tree(repo_path: str, max_depth: int = 3) -> str:
    """Xây dựng cây thư mục dự án bằng Python thuần."""
    skip_dirs = {
        ".git", "__pycache__", ".venv", "venv", "env",
        "node_modules", ".pytest_cache", ".mypy_cache",
        "dist", "build", ".eggs", ".tox",
    }
    show_exts = {".py", ".txt", ".json", ".yaml", ".yml", ".toml", ".md", ".cfg", ".ini", ".env"}
    always_show = {".env", ".gitignore", "requirements.txt", "Makefile", "Dockerfile"}

    lines = [f"📁 {os.path.basename(repo_path)}/"]

    def walk(path: str, prefix: str = "", depth: int = 0) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        entries = [e for e in entries if not (e.is_dir() and e.name in skip_dirs)]
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            conn = "└── " if is_last else "├── "
            ext_p = "    " if is_last else "│   "
            if entry.is_dir():
                lines.append(f"{prefix}{conn}📁 {entry.name}/")
                walk(entry.path, prefix + ext_p, depth + 1)
            else:
                _, ext = os.path.splitext(entry.name)
                if ext in show_exts or entry.name in always_show:
                    size = entry.stat().st_size
                    sz = f" ({size}B)" if size < 10_000 else f" ({size // 1024}KB)"
                    lines.append(f"{prefix}{conn}📄 {entry.name}{sz}")

    walk(repo_path)
    return "\n".join(lines)


def _format_plan(plan: List[PlanStep]) -> str:
    """Hiển thị plan dưới dạng bảng."""
    if not plan:
        return "  (Chưa có plan)"
    lines = []
    for step in plan:
        lines.append(f"  {BOLD}Bước {step.step_id}: {step.title}{RESET}")
        lines.append(f"    Mô tả   : {step.description}")
        lines.append(f"    File    : {step.target_file}")
        lines.append(f"    Hành động: {step.action}")
        if step.reasoning:
            lines.append(f"    Lý do   : {step.reasoning}")
    return "\n".join(lines)


def _compute_diff(original: str, new_content: str, filename: str) -> List[str]:
    """Tạo unified diff giữa 2 nội dung file."""
    return list(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    ))


def _print_diff(diff_lines: List[str]) -> None:
    """In diff có màu sắc."""
    if not diff_lines:
        print(f"  {YELLOW}⚠ Không có thay đổi trong diff.{RESET}")
        return
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            print(f"{BOLD}{line}{RESET}")
        elif line.startswith("+"):
            print(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            print(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            print(f"{CYAN}{line}{RESET}")
        else:
            print(line)


def _resolve_target_path(target_file: str, repo_path: str) -> str:
    """Chuyển đổi path tương đối thành tuyệt đối dựa trên repo."""
    if os.path.isabs(target_file):
        return target_file
    return os.path.join(repo_path, target_file)


def _load_file_content(path: str) -> str:
    """Đọc nội dung file, trả về chuỗi rỗng nếu không tồn tại."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""



# GRAPH NODES
# ─── 1. ProjectInitializerNode ───────────────────────────────────────────────

@dataclass
class ProjectInitializerNode(BaseNode[BugFixState]):
    """
    [Deterministic] Nhận đường dẫn repo từ user, validate và build project tree.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> InputAnalyzerNode:
        _print_header("PyFix-Agents v1.0  —  AI-Powered Python Bug Fixer")
        print(f"\n{BOLD}Chào mừng!{RESET} Hệ thống sẽ giúp bạn phân tích và sửa lỗi Python tự động.\n")

        # ── Nhận repo path ────────────────────────────────────────────────────
        while True:
            raw = input(f"{BOLD}📁 Nhập đường dẫn đến thư mục dự án:{RESET} ").strip()
            if not raw:
                print(f"  {RED}✗ Vui lòng nhập đường dẫn.{RESET}")
                continue

            abs_path = os.path.abspath(raw)
            if not os.path.isdir(abs_path):
                print(f"  {RED}✗ Thư mục không tồn tại: {abs_path}{RESET}")
                continue

            ctx.state.repo_path = abs_path
            ctx.state.is_repo_valid = True
            print(f"  {GREEN}✓ Đã nhận dự án: {abs_path}{RESET}")
            break

        # ── Build project tree ────────────────────────────────────────────────
        _print_step("📂", "Project Tree", "")
        tree = _build_project_tree(ctx.state.repo_path)
        ctx.state.project_tree = tree
        print(tree)

        return InputAnalyzerNode()


# ─── 2. InputAnalyzerNode ─────────────────────────────────────────────────────

@dataclass
class InputAnalyzerNode(BaseNode[BugFixState]):
    """
    [Agent] Nhận mô tả lỗi từ user, dùng LLM parse thành BugReport JSON.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> InputGateGuardrailNode:
        print(f"\n{'─'*60}")
        _print_step("📝", "Input Analyzer", "Nhập mô tả lỗi bạn gặp phải.")

        # Hướng dẫn format input
        print(f"""
  {BOLD}Định dạng nhập:{RESET}
  • {CYAN}Lỗi CRASH{RESET}: Copy & Paste toàn bộ log / traceback từ terminal
  • {CYAN}Lỗi LOGIC{RESET}: "Lỗi ở file X, hiện tại ra Y nhưng mong muốn là Z"
  
  {YELLOW}Tip:{RESET} Thêm "tôi muốn xem plan" nếu bạn muốn duyệt kế hoạch trước khi fix.
  {YELLOW}Tip:{RESET} Nhập 'quit' để thoát.
""")

        # Hỏi user nhập input
        print(f"{BOLD}Mô tả lỗi:{RESET} ", end="", flush=True)
        try:
            first_line = input()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

        if first_line.strip().lower() == "quit":
            sys.exit(0)

        lines = [first_line]

        # Nếu user copy & paste nhiều dòng (như log traceback), tự động đọc các dòng còn lại trong buffer
        try:
            import msvcrt
            while msvcrt.kbhit():
                lines.append(input())
        except Exception:
            pass

        raw_input = "\n".join(lines).strip()

        # Thêm feedback nếu đang trong vòng lặp NeedMoreInfo
        if ctx.state.missing_fields and ctx.state.raw_user_input:
            # Ghép input mới vào sau input cũ
            combined = ctx.state.raw_user_input + "\n\nThông tin bổ sung:\n" + raw_input
            ctx.state.raw_user_input = combined
        else:
            ctx.state.raw_user_input = raw_input

        # ── Gọi LLM parse ─────────────────────────────────────────────────────
        _print_step("🤖", "Input Analyzer Agent", f"Đang phân tích với {MODEL_NAME}...")

        prompt = f"""Phân tích mô tả lỗi sau và trả về thông tin có cấu trúc:

---
{ctx.state.raw_user_input}
---

Cấu trúc dự án để tham chiếu:
{ctx.state.project_tree[:1500]}          
"""
        # có thể limit lại project tree để tránh tràn context
        result = await input_analyzer_agent.run(prompt)
        bug_report: BugReport = result.output

        # ── Cập nhật state ────────────────────────────────────────────────────
        ctx.state.bug_type = bug_report.bug_type
        ctx.state.target_file = bug_report.target_file
        ctx.state.actual_output = bug_report.actual_output
        ctx.state.expected_output = bug_report.expected_output
        ctx.state.want_plan = bug_report.want_plan

        _print_step("✅", "Phân tích xong", f"Loại lỗi: {CYAN}{bug_report.bug_type.value.upper()}{RESET}")
        if bug_report.target_file:
            print(f"     File nghi ngờ : {bug_report.target_file}")
        if bug_report.want_plan:
            print(f"     {YELLOW} User muốn xem plan trước khi fix{RESET}")

        return InputGateGuardrailNode()


# ─── 3. InputGateGuardrailNode ────────────────────────────────────────────────

@dataclass
class InputGateGuardrailNode(BaseNode[BugFixState]):
    """
    [Deterministic] Kiểm tra và tự động bổ sung các thông tin còn thiếu nếu có thể:
    - Nếu target_file chưa có nhưng dự án chỉ có 1 file .py, tự động nhận diện.
    - Tránh chặn user vô lý khi mô tả tự nhiên đã đủ ngữ cảnh.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> Union[NeedMoreInfoNode, PlanPromptNode]:
        missing: List[str] = []

        # Tự động nhận diện target_file nếu chưa có
        if not ctx.state.target_file and ctx.state.repo_path:
            py_files = []
            for root, dirs, files in os.walk(ctx.state.repo_path):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
                for f in files:
                    if f.endswith(".py"):
                        full_f = os.path.join(root, f)
                        rel_f = os.path.relpath(full_f, ctx.state.repo_path)
                        py_files.append(rel_f)
            if len(py_files) == 1:
                ctx.state.target_file = py_files[0]
                print(f"  {CYAN}💡 Tự động nhận diện file dự án: {py_files[0]}{RESET}")

        if ctx.state.bug_type == BugType.CRASH:
            if not ctx.state.actual_output and not ctx.state.raw_user_input:
                missing.append("actual_output — Log / traceback từ terminal")

        elif ctx.state.bug_type == BugType.LOGIC:
            if not ctx.state.target_file:
                missing.append("target_file — File chứa lỗi (chưa rõ file cần sửa)")
            if not ctx.state.actual_output:
                if ctx.state.raw_user_input:
                    # Auto-fallback: dùng raw input của user làm mô tả thực tế
                    ctx.state.actual_output = ctx.state.raw_user_input
                else:
                    missing.append("actual_output — Hành vi / giá trị thực tế")
            if not ctx.state.expected_output:
                if ctx.state.raw_user_input:
                    # Auto-fallback: dùng raw input của user làm mô tả kỳ vọng
                    ctx.state.expected_output = "Chạy đúng theo mô tả yêu cầu"
                else:
                    missing.append("expected_output — Hành vi / giá trị mong muốn")

        ctx.state.missing_fields = missing

        if missing:
            _print_step("⚠", "Guardrail", f"{RED}Thiếu thông tin:{RESET}")
            for field in missing:
                print(f"   • {field}")
            return NeedMoreInfoNode()

        _print_step("✅", "Guardrail", "Đủ thông tin cần thiết.")
        return PlanPromptNode()


# ─── 4. NeedMoreInfoNode ─────────────────────────────────────────────────────

@dataclass
class NeedMoreInfoNode(BaseNode[BugFixState]):
    """
    [Deterministic] Thông báo cho user biết field còn thiếu, quay lại InputAnalyzer.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> InputAnalyzerNode:
        print(f"\n  {YELLOW}📋 Vui lòng cung cấp thêm thông tin:{RESET}")
        for field in ctx.state.missing_fields:
            print(f"     → {field}")
        print()
        # Reset missing_fields để InputAnalyzer biết đây là lần bổ sung
        return InputAnalyzerNode()


# ─── 4.5. PlanPromptNode ──────────────────────────────────────────────────────

@dataclass
class PlanPromptNode(BaseNode[BugFixState]):
    """
    [Deterministic] Hỏi trực tiếp người dùng có cần hiển thị & duyệt kế hoạch (plan) chi tiết trước khi sửa hay không.
    - Chọn 'y' -> want_plan = True  -> PlanningNode -> PlanInterceptorNode (dừng chờ /ok)
    - Chọn 'n' -> want_plan = False -> PlanningNode (tự động sửa thẳng)
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> PlanningNode:
        if ctx.state.want_plan == False:
            print(f"\n{BOLD}❓ Bạn có cần lên kế hoạch (plan) chi tiết trước khi sửa không?{RESET}")
            while True:
                ans = input(
                    f"  [{GREEN}y{RESET}] Có, hiển thị plan để tôi duyệt\n"
                    f"  [{RED}n{RESET}] Không, sửa thẳng luôn (tự động)\n"
                    f"  Lựa chọn [y/n]: "
                ).strip().lower()

                if ans in ("y", "yes"):
                    ctx.state.want_plan = True
                    print(f"  {GREEN} Đã chọn: Lên plan chi tiết và chờ duyệt.{RESET}\n")
                    break
                elif ans in ("n", "no"):
                    ctx.state.want_plan = False
                    print(f"  {YELLOW} Đã chọn: Tự động sửa thẳng (không dừng chờ duyệt plan).{RESET}\n")
                    break
                else:
                    print(f"  {RED}✗ Vui lòng nhập 'y' (có) hoặc 'n' (không).{RESET}")

        return PlanningNode()


# ─── 5. PlanningNode ─────────────────────────────────────────────────────────

@dataclass
class PlanningNode(BaseNode[BugFixState]):
    """
    [Agent] Dùng LLM tạo / cập nhật kế hoạch sửa lỗi dựa trên bug report + context.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> PlanInterceptorNode:
        if ctx.state.user_plan_feedback:
            _print_step("🔄", "Planner Agent", f"Đang cập nhật plan lần {ctx.state.replan_count + 1}...")
        else:
            _print_step("🧠", "Planner Agent", f"Đang tạo kế hoạch sửa lỗi với {MODEL_NAME}... (Planner sẽ tự đọc file qua MCP Tools)")

        # ── Xây dựng prompt — Planner sẽ tự dùng MCP tools để đọc file ────────
        replan_section = ""
        if ctx.state.user_plan_feedback and ctx.state.current_plan:
            old_plan_str = "\n".join(
                f"  Bước {s.step_id}: {s.title} — {s.description}"
                for s in ctx.state.current_plan
            )
            replan_section = f"""
⚠ YÊU CẦU ĐIỀU CHỈNH PLAN:
User feedback: {ctx.state.user_plan_feedback}

Plan cũ đã bị từ chối:
{old_plan_str}

Hãy tạo plan mới KHÁC với plan cũ, tính đến feedback của user.
"""

        prompt = f"""BUG REPORT:
- Loại lỗi    : {ctx.state.bug_type.value if ctx.state.bug_type else 'chưa xác định'}
- File chính  : {ctx.state.target_file or 'chưa xác định'}
- Repo path   : {ctx.state.repo_path}
- Lỗi thực tế : {ctx.state.actual_output or 'N/A'}
- Kỳ vọng     : {ctx.state.expected_output or 'N/A (không crash là được)'}

CẤU TRÚC DỰ ÁN:
{ctx.state.project_tree}

HƯỚNG DẪN BẮT BUỘC:
1. Dùng get_file_context("{ctx.state.target_file or ''}", "{ctx.state.repo_path}") để đọc file chính bị lỗi.
2. Xem các import trong file đó. Nếu lỗi có thể liên quan file khác, dùng read_file hoặc get_file_context để đọc thêm.
3. Sau khi đã đọc đủ mã nguồn, phân tích nguyên nhân gốc rễ và tạo kế hoạch sửa lỗi chi tiết.
{replan_section}
Hãy BẮT ĐẦU bằng việc đọc file qua MCP tools, rồi tạo kế hoạch sửa lỗi (nêu rõ bước nào sửa file nào).
"""

        result = await planner_agent.run(prompt)
        plan_output: PlanOutput = result.output

        # ── Lưu lịch sử replan nếu có ─────────────────────────────────────────
        if ctx.state.user_plan_feedback and ctx.state.current_plan:
            ctx.state.plan_history.append(RePlanHistory(
                revision=ctx.state.replan_count,
                feedback=ctx.state.user_plan_feedback,
                rejected_plan_summary="; ".join(s.title for s in ctx.state.current_plan),
            ))
            ctx.state.replan_count += 1

        ctx.state.current_plan = plan_output.steps
        ctx.state.user_plan_feedback = None
        ctx.state.plan_approved = False

        _print_step("✅", "Plan tạo xong",
                    f"Tổng {len(plan_output.steps)} bước | Rủi ro: {YELLOW}{plan_output.risk_level}{RESET}")
        print(f"   Tóm tắt: {plan_output.summary}")

        return PlanInterceptorNode()


# ─── 6. PlanInterceptorNode ───────────────────────────────────────────────────

@dataclass
class PlanInterceptorNode(BaseNode[BugFixState]):
    """
    [Deterministic] Kiểm tra want_plan:
    - False → Tự động duyệt, đi thẳng đến Execution
    - True  → Hiển thị plan, chờ /ok hoặc /replan <feedback>
    """
    # bắt buộc phải cung cáp feed back nếu muốn replan
    async def run(self, ctx: GraphRunContext[BugFixState]) -> Union[ExecutionNode, PlanningNode]:
        # ── Không cần xem plan → auto approve ────────────────────────────────
        if not ctx.state.want_plan:
            _print_step("⚡", "Plan Interceptor", "User không yêu cầu xem plan → Tự động duyệt.")
            ctx.state.plan_approved = True
            return ExecutionNode()

        # ── Hiển thị plan và chờ duyệt ───────────────────────────────────────
        _print_header("Kế hoạch sửa lỗi — Chờ duyệt")
        print(_format_plan(ctx.state.current_plan))
        print(f"\n{'─'*60}")
        print(f"  {BOLD}Lệnh:{RESET}")
        print(f"  {GREEN}/ok{RESET}               → Duyệt plan và bắt đầu sửa")
        print(f"  {YELLOW}/replan <feedback>{RESET} → Yêu cầu làm lại plan (VD: /replan dùng approach khác)")
        print(f"{'─'*60}")

        while True:
            cmd = input(f"\n{BOLD}Nhập lệnh:{RESET} ").strip()

            if cmd.lower() == "/ok":
                print(f"  {GREEN}✓ Plan đã được duyệt!{RESET}")
                ctx.state.plan_approved = True
                return ExecutionNode()

            elif cmd.lower().startswith("/replan"):
                feedback = cmd[7:].strip()
                if not feedback:
                    print(f"  {YELLOW}⚠ Vui lòng cung cấp feedback: /replan <feedback>{RESET}")
                    continue
                print(f"  {YELLOW}🔄 Làm lại plan theo feedback: '{feedback}'{RESET}")
                ctx.state.user_plan_feedback = feedback
                return PlanningNode()

            else:
                print(f"  {RED}✗ Lệnh không hợp lệ. Dùng /ok hoặc /replan <feedback>{RESET}")


# ─── 7. ExecutionNode ────────────────────────────────────────────────────────

@dataclass
class ExecutionNode(BaseNode[BugFixState]):
    """
    [Agent] Thực thi tuần tự từng bước trong Kế hoạch (Plan-step by Plan-step execution).
    Ép Coder Agent tuân thủ 100% từng bước của Plan bằng vòng lặp điều khiển cấu trúc code Python.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> ValidationNode:
        _print_step("🛠", "Coder Agent", f"Đang thực thi tuần tự từng bước Plan với {MODEL_NAME}...")

        # ── Chuyển validation errors cũ vào logs ─────────────────────────────
        if ctx.state.validation_errors:
            for err in ctx.state.validation_errors:
                ctx.state.execution_logs.append(f"[Lỗi validation lần trước] {err}")
        ctx.state.validation_errors = []

        prev_errors_str = ""
        if ctx.state.execution_logs:
            last_errors = "\n".join(f"  • {e}" for e in ctx.state.execution_logs[-3:])
            prev_errors_str = f"\nLỖI TỪ LẦN THỬ TRƯỚC:\n{last_errors}\nHãy sửa khác đi, tránh lặp lại lỗi trên."

        # ── Chuẩn bị plan steps ────────────────────────────────────────────────
        plan_steps = ctx.state.current_plan
        if not plan_steps:
            plan_steps = [PlanStep(step_id=1, title="Sửa lỗi dự án",
                                  description="Sửa code theo mô tả lỗi",
                                  target_file=ctx.state.target_file or "main.py")]

        # ── PHASE 1: Backup bản gốc tất cả file liên quan ────────────────────
        # Lưu nội dung gốc trước khi bất kỳ bước nào ghi đè,
        # để hiển thị diff chính xác (gốc vs cuối) và rollback nếu user reject.
        original_backups: dict[str, str] = {}  # {absolute_path: original_content}
        touched_files: set[str] = set()
        for step in plan_steps:
            abs_path = _resolve_target_path(step.target_file, ctx.state.repo_path)
            if abs_path not in original_backups:
                original_backups[abs_path] = _load_file_content(abs_path)

        aggregated_explanations: List[str] = []

        # ── PHASE 2: Vòng lặp thực thi tuần tự — GHI TẠM sau mỗi bước ───────
        # Sau khi Coder Agent trả về fix cho Bước N, hệ thống ghi ngay xuống đĩa.
        # Khi Bước N+1 chạy, Coder Agent dùng MCP tools đọc file → thấy phiên bản
        # đã được cập nhật bởi Bước N → đảm bảo tính tuần tự thực sự.
        for idx, step in enumerate(plan_steps, start=1):
            _print_step("📌", f"Bước {idx}/{len(plan_steps)}",
                        f"{step.title} (file: {CYAN}{step.target_file}{RESET})")

            step_prompt = f"""BẠN ĐANG THỰC THI BƯỚC {idx}/{len(plan_steps)} CỦA KẾ HOẠCH.

NHIỆM VỤ BẮT BUỘC CỦA BƯỚC NÀY:
- Tiêu đề  : {step.title}
- Chi tiết : {step.description}
- File sửa : {step.target_file}

THÔNG TIN DỰ ÁN:
- Repo path  : {ctx.state.repo_path}
- Loại lỗi  : {ctx.state.bug_type.value if ctx.state.bug_type else 'unknown'}
- Lỗi thực tế: {ctx.state.actual_output or 'N/A'}
- Kỳ vọng    : {ctx.state.expected_output or 'N/A'}
{prev_errors_str}

HƯỚNG DẪN:
1. Dùng get_file_context("{step.target_file}", "{ctx.state.repo_path}") hoặc read_file để đọc mã nguồn HIỆN TẠI của file.
2. CHỈ thực hiện sửa đổi theo đúng yêu cầu của Bước {idx} trên file {step.target_file}.
3. Trả về kết quả trong danh sách 'files' (chứa đúng 1 SingleFileFix cho file {step.target_file}).
4. Trả về TOÀN BỘ (100%) nội dung hoàn chỉnh của file sau khi sửa.
"""
            try:
                result = await coder_agent.run(step_prompt)
                step_fix: CodeFix = result.output

                if step_fix.explanation:
                    aggregated_explanations.append(f"Bước {idx} ({step.title}): {step_fix.explanation}")

                # Ghi tạm kết quả bước này xuống đĩa ngay lập tức
                if step_fix.files:
                    for ffix in step_fix.files:
                        abs_path = _resolve_target_path(ffix.target_file, ctx.state.repo_path)
                        # Backup gốc nếu file này chưa được backup (file ngoài plan)
                        if abs_path not in original_backups:
                            original_backups[abs_path] = _load_file_content(abs_path)
                        # Ghi tạm xuống đĩa để bước tiếp theo đọc được qua MCP
                        try:
                            if os.path.dirname(abs_path):
                                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                            with open(abs_path, "w", encoding="utf-8") as fh:
                                fh.write(ffix.new_content)
                            touched_files.add(abs_path)
                            _print_step("💾", f"Bước {idx}",
                                        f"Đã ghi tạm {ffix.target_file} (bước sau sẽ thấy thay đổi này)")
                        except Exception as write_exc:
                            _print_step("❌", f"Bước {idx}",
                                        f"{RED}Lỗi ghi tạm {ffix.target_file}: {write_exc}{RESET}")

            except Exception as exc:
                _print_step("❌", "Coder Agent",
                            f"{RED}Lỗi khi thực thi Bước {idx}: {exc}{RESET}")
                # Rollback tất cả file đã ghi tạm về bản gốc
                self._rollback(original_backups, touched_files)
                ctx.state.validation_errors.append(f"Lỗi thực thi bước {idx}: {exc}")
                ctx.state.retry_count += 1
                return ValidationNode()

        # ── PHASE 3: Đọc lại nội dung cuối cùng từ đĩa để đóng gói CodeFix ───
        final_files: List[SingleFileFix] = []
        for abs_path in touched_files:
            final_content = _load_file_content(abs_path)
            rel_path = os.path.relpath(abs_path, ctx.state.repo_path)
            final_files.append(SingleFileFix(
                target_file=rel_path,
                new_content=final_content,
                changes_summary=f"Đã sửa theo plan",
            ))

        code_fix = CodeFix(
            files=final_files,
            explanation="\n".join(aggregated_explanations) if aggregated_explanations else "Đã thực thi thành công tất cả các bước trong plan."
        )
        ctx.state.code_fix = code_fix

        if not final_files:
            _print_step("⚠", "Coder Agent", "Không có file nào được sửa → Xuất báo cáo.")
            return ValidationNode()

        # ── PHASE 4: Rollback tạm về bản gốc trước khi hiển thị diff ──────────
        # Khôi phục bản gốc trên đĩa để diff so sánh chính xác "gốc vs mới"
        # và để user quyết định có áp dụng hay không.
        self._rollback(original_backups, touched_files)

        # ── Hiển thị tổng hợp diff (bản gốc vs bản sửa cuối cùng) ────────────
        print(f"\n  {BOLD}💡 Tổng hợp kết quả:{RESET}")
        print(f"  {BOLD}📝 Các file đã sửa ({len(final_files)} file):{RESET}")
        for ffix in final_files:
            print(f"     • {CYAN}{ffix.target_file}{RESET}: {ffix.changes_summary}")
        print(f"\n  {BOLD}Giải thích:{RESET} {code_fix.explanation}")

        for ffix in final_files:
            abs_path = _resolve_target_path(ffix.target_file, ctx.state.repo_path)
            orig = original_backups.get(abs_path, "")
            print(f"\n{'─'*50} DIFF: {CYAN}{ffix.target_file}{RESET} {'─'*50}\n")
            diff_lines = _compute_diff(orig, ffix.new_content, os.path.basename(abs_path))
            _print_diff(diff_lines)

        print(f"\n{'─'*120}")

        # ── Human approval ────────────────────────────────────────────────────
        print(f"\n{BOLD}{YELLOW}⚠  HUMAN APPROVAL — Xem xét thay đổi trước khi áp dụng{RESET}")
        print(f"   Số lượng file sẽ ghi: {CYAN}{len(final_files)} file{RESET}\n")

        while True:
            choice = input(
                f"  [{GREEN}y{RESET}] Duyệt & ghi tất cả  "
                f"[{RED}n{RESET}] Bỏ qua / thử lại  "
                f"[{YELLOW}q{RESET}] Thoát chương trình\n"
                f"  Lựa chọn: "
            ).strip().lower()

            if choice == "y":
                for ffix in final_files:
                    abs_path = _resolve_target_path(ffix.target_file, ctx.state.repo_path)
                    try:
                        with open(abs_path, "w", encoding="utf-8") as fh:
                            fh.write(ffix.new_content)
                        print(f"  {GREEN}✅ Đã ghi file: {abs_path}{RESET}")
                        ctx.state.execution_logs.append(
                            f"Applied fix to {abs_path}: {ffix.changes_summary}"
                        )
                    except Exception as write_exc:
                        print(f"  {RED}❌ Lỗi khi ghi file {abs_path}: {write_exc}{RESET}")
                        ctx.state.validation_errors.append(
                            f"Write error ({ffix.target_file}): {write_exc}")
                break

            elif choice == "n":
                print(f"  {YELLOW}🔄 Bỏ qua — Thử lại với approach khác...{RESET}")
                ctx.state.execution_logs.append("User từ chối thay đổi, thử lại execution.")
                ctx.state.retry_count += 1
                break

            elif choice == "q":
                print(f"\n{YELLOW}👋 Đã thoát PyFix-Agents.{RESET}")
                sys.exit(0)

            else:
                print(f"  {RED}✗ Vui lòng nhập y, n, hoặc q{RESET}")

        return ValidationNode()

    @staticmethod
    def _rollback(backups: dict[str, str], touched: set[str]) -> None:
        """Khôi phục nội dung gốc cho tất cả file đã bị ghi tạm."""
        for abs_path in touched:
            if abs_path in backups:
                try:
                    with open(abs_path, "w", encoding="utf-8") as fh:
                        fh.write(backups[abs_path])
                except Exception:
                    pass  # Best-effort rollback


# ─── 8. ValidationNode ───────────────────────────────────────────────────────

@dataclass
class ValidationNode(BaseNode[BugFixState]):
    """
    [Deterministic] Kiểm tra cú pháp và tự động chạy test/thực thi script để kiểm tra logic:
    1. Kiểm tra cú pháp bằng py_compile.
    2. Tự động chạy pytest (nếu có file test) hoặc thực thi script Python để kiểm tra runtime & output.
    3. Nếu test/logic bị hỏng -> tự động phản hồi về ExecutionNode / PlanningNode để sửa lại.
    """

    async def run(
        self, ctx: GraphRunContext[BugFixState]
    ) -> Union[ExecutionNode, PlanningNode, ReportNode]:
        _print_step("🔍", "Validation", "1/2. Kiểm tra cú pháp file đã sửa...")

        if not ctx.state.code_fix or not ctx.state.code_fix.files:
            _print_step("⚠", "Validation", "Không có file fix để kiểm tra → Xuất báo cáo.")
            return ReportNode()

        # ── 1. Kiểm tra cú pháp (py_compile) trên tất cả các file đã sửa ────────
        for ffix in ctx.state.code_fix.files:
            target_path = _resolve_target_path(ffix.target_file, ctx.state.repo_path)
            if not os.path.exists(target_path):
                _print_step("⚠", "Validation", f"File không tồn tại: {target_path} → Xuất báo cáo.")
                return ReportNode()

            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", target_path],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                syntax_err = (proc.stderr or proc.stdout).strip()
                _print_step("❌", "Validation", f"{RED}Lỗi cú pháp tại {ffix.target_file}:{RESET}\n  {syntax_err}")
                return self._handle_failure(ctx, f"Lỗi cú pháp ({ffix.target_file}): {syntax_err}")

        _print_step("✅", "Validation", f"{GREEN}Cú pháp hợp lệ trên tất cả {len(ctx.state.code_fix.files)} file!{RESET}")

        # ── 2. Tự động chạy test suite hoặc thực thi script để kiểm tra logic ────
        _print_step("🧪", "Validation", "2/2. Tự động chạy test & kiểm tra logic...")

        # Tìm các file test trong repo (pytest)
        test_files = []
        for root, dirs, files in os.walk(ctx.state.repo_path):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv", "node_modules"}]
            for f in files:
                if (f.startswith("test_") or f.endswith("_test.py")) and f.endswith(".py"):
                    test_files.append(os.path.join(root, f))

        test_passed = True
        test_error_msg = ""

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
                    test_error_msg = f"Pytest thất bại:\n{(test_proc.stdout or test_proc.stderr).strip()}"
                    _print_step("❌", "Validation", f"{RED}Pytest không đạt!{RESET}")
                else:
                    print(f"  {GREEN}✅ Tất cả pytest unit tests đều ĐẠT!{RESET}")
            except Exception as test_exc:
                test_passed = False
                test_error_msg = f"Lỗi khi chạy pytest: {test_exc}"

        elif target_path.endswith(".py"):
            # Chạy trực tiếp script để xem có bị crash runtime hay kiểm tra logic output không
            print(f"  {CYAN}🏃 Chạy thực thi {os.path.basename(target_path)} để kiểm tra runtime & output...{RESET}")
            try:
                run_proc = subprocess.run(
                    [sys.executable, target_path],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if run_proc.returncode != 0:
                    test_passed = False
                    test_error_msg = f"Lỗi runtime crash khi thực thi:\n{(run_proc.stderr or run_proc.stdout).strip()}"
                    _print_step("❌", "Validation", f"{RED}Script bị crash khi thực thi!{RESET}")
                else:
                    out = (run_proc.stdout or "").strip()
                    print(f"  {GREEN}✅ Script thực thi không có lỗi crash.{RESET}")
                    if out:
                        print(f"     Output thực tế thu được: {CYAN}{out}{RESET}")

                    # ── Kiểm tra Logic Output (đối chiếu output thực tế với expected_output) ──
                    logic_ok, logic_msg = self._verify_logic_output(
                        ctx.state.actual_output, ctx.state.expected_output, out
                    )
                    if not logic_ok:
                        test_passed = False
                        test_error_msg = logic_msg
                        _print_step("❌", "Validation", f"{RED}{logic_msg}{RESET}")
                    elif logic_msg:
                        print(f"  {GREEN}✅ Kiểm tra Logic: {logic_msg}{RESET}")

            except Exception as run_exc:
                test_passed = False
                test_error_msg = f"Lỗi khi chạy script: {run_exc}"

        if not test_passed:
            return self._handle_failure(ctx, test_error_msg)

        _print_step("✅", "Validation", f"{GREEN}Tất cả kiểm tra & test đều THÀNH CÔNG!{RESET}")
        ctx.state.validation_passed = True
        ctx.state.validation_errors = []
        ctx.state.retry_count = 0
        return ReportNode()

    def _verify_logic_output(
        self, actual_str: Optional[str], expected_str: Optional[str], stdout: str
    ) -> tuple[bool, str]:
        """
        Đối chiếu kết quả stdout thu được với actual_output (lỗi cũ) và expected_output (kỳ vọng).
        """
        if not stdout and not expected_str:
            return True, ""

        stdout_clean = stdout.strip().lower()
        actual_clean = (actual_str or "").strip().lower()
        expected_clean = (expected_str or "").strip().lower()

        def extract_keywords(text: str) -> set[str]:
            words = set()
            for w in ["true", "false", "none", "0", "1"]:
                if w in text:
                    words.add(w)
            return words

        actual_kw = extract_keywords(actual_clean)
        expected_kw = extract_keywords(expected_clean)
        stdout_kw = extract_keywords(stdout_clean)

        # 1. Nếu stdout vẫn còn chứa từ khóa của lỗi cũ (ví dụ vẫn ra 'true' khi lỗi cũ là 'true' và kỳ vọng 'false')
        if actual_kw and expected_kw and actual_kw != expected_kw:
            if expected_kw.issubset(stdout_kw):
                return True, f"Output '{stdout.strip()}' đã khớp với kỳ vọng '{expected_str}'."
            if actual_kw.issubset(stdout_kw) and not expected_kw.issubset(stdout_kw):
                return False, f"Lỗi LOGIC chưa được khắc phục! Output thực tế vẫn trả về '{stdout.strip()}', chưa khớp với kỳ vọng '{expected_str}'."

        # 2. Kiểm tra trực tiếp từ khóa kỳ vọng trong stdout
        if expected_clean:
            for kw in expected_kw:
                if kw in stdout_kw:
                    return True, f"Output '{stdout.strip()}' chứa kết quả kỳ vọng '{kw}'."
            if expected_clean in stdout_clean:
                return True, f"Output '{stdout.strip()}' chứa kết quả kỳ vọng '{expected_str}'."

        return True, ""

    def _handle_failure(
        self, ctx: GraphRunContext[BugFixState], error_msg: str
    ) -> Union[ExecutionNode, PlanningNode, ReportNode]:
        ctx.state.validation_passed = False
        ctx.state.validation_errors = [error_msg]
        ctx.state.retry_count += 1

        if ctx.state.retry_count <= MAX_SYNTAX_RETRIES:
            print(f"  {YELLOW}🔄 Tự động sửa lại theo phản hồi lỗi (Thử lại {ctx.state.retry_count}/{MAX_SYNTAX_RETRIES})...{RESET}")
            return ExecutionNode()

        if ctx.state.replan_count < MAX_LOGIC_RETRIES:
            print(f"  {RED}❌ Quá {MAX_SYNTAX_RETRIES} lần thử lại. Quay về lập kế hoạch sửa mới...{RESET}")
            ctx.state.retry_count = 0
            ctx.state.user_plan_feedback = (
                f"Coder đã sửa nhưng test/thực thi vẫn thất bại sau {MAX_SYNTAX_RETRIES} lần thử.\n"
                f"Chi tiết lỗi: {error_msg}\nHãy đưa ra kế hoạch sửa khác."
            )
            return PlanningNode()

        _print_step("⛔", "Validation", "Đã hết lượt replan. Xuất báo cáo kết quả cuối cùng.")
        return ReportNode()


# ─── 9. ReportNode ───────────────────────────────────────────────────────────

@dataclass
class ReportNode(BaseNode[BugFixState]):
    """
    [Deterministic] Tổng hợp và hiển thị báo cáo kết quả sửa lỗi.
    """

    async def run(self, ctx: GraphRunContext[BugFixState]) -> End[str]:
        _print_header("Báo cáo kết quả — PyFix-Agents")

        status_icon = "✅" if ctx.state.validation_passed else "⚠"
        status_text = "THÀNH CÔNG" if ctx.state.validation_passed else "HOÀN THÀNH (có vấn đề)"
        status_color = GREEN if ctx.state.validation_passed else YELLOW

        lines = [
            f"  {status_icon} {status_color}{BOLD}Trạng thái : {status_text}{RESET}",
            f"  📁 Repo     : {ctx.state.repo_path}",
            f"  🐛 Loại lỗi : {ctx.state.bug_type.value.upper() if ctx.state.bug_type else 'N/A'}",
        ]

        if ctx.state.code_fix and ctx.state.code_fix.files:
            lines.append(f"  📄 Danh sách file đã sửa ({len(ctx.state.code_fix.files)} file):")
            for ffix in ctx.state.code_fix.files:
                lines.append(f"     • {CYAN}{ffix.target_file}{RESET}: {ffix.changes_summary}")
            lines.append(f"  💡 Nguyên nhân/Giải pháp: {ctx.state.code_fix.explanation}")

        if ctx.state.replan_count > 0:
            lines.append(f"  🔄 Số lần replan        : {ctx.state.replan_count}")
        if ctx.state.retry_count > 0:
            lines.append(f"  🔁 Số lần retry execution: {ctx.state.retry_count}")

        if ctx.state.execution_logs:
            lines.append(f"\n  {BOLD}📜 Nhật ký (5 dòng cuối):{RESET}")
            for log in ctx.state.execution_logs[-5:]:
                lines.append(f"     • {log}")

        if ctx.state.validation_errors:
            lines.append(f"\n  {YELLOW}⚠ Cảnh báo / Lỗi còn lại:{RESET}")
            for err in ctx.state.validation_errors:
                lines.append(f"     • {RED}{err}{RESET}")

        if ctx.state.plan_history:
            lines.append(f"\n  {BOLD}📋 Lịch sử replan:{RESET}")
            for h in ctx.state.plan_history:
                lines.append(f"     Lần {h.revision}: {h.feedback}")

        report = "\n".join(lines)
        ctx.state.final_report = report
        print(report)
        print()

        return End(report)



# GRAPH DEFINITION  (GraphBuilder API — pydantic-graph mới)
_builder = GraphBuilder(
    state_type=BugFixState,
    input_type=ProjectInitializerNode,  
    output_type=str,                    
    auto_instrument=False,             
)

_builder.add_edge(_builder.start_node, ProjectInitializerNode)

_builder.add(
    _builder.node(ProjectInitializerNode),
    _builder.node(InputAnalyzerNode),
    _builder.node(InputGateGuardrailNode),
    _builder.node(NeedMoreInfoNode),
    _builder.node(PlanPromptNode),
    _builder.node(PlanningNode),
    _builder.node(PlanInterceptorNode),
    _builder.node(ExecutionNode),
    _builder.node(ValidationNode),
    _builder.node(ReportNode),
)

bug_fix_graph = _builder.build()



# ENTRY POINT
async def main() -> None:
    """Khởi chạy PyFix-Agents CLI (chạy độc lập, kết nối tới MCP Server qua HTTP)."""
    print(f"{CYAN}🔗 MCP Client kết nối tới MCP Server tại {MCP_SERVER_URL}{RESET}")

    state = BugFixState()
    try:
        result: str = await bug_fix_graph.run(
            state=state,
            inputs=ProjectInitializerNode(),
        )
        print(f"\n{BOLD}🏁 Kết quả cuối:{RESET} {result[:120]}...")
    except KeyboardInterrupt:
        print(f"\n{YELLOW}👋 Đã hủy bởi người dùng.{RESET}")
    except Exception as exc:
        print(f"\n{RED}❌ Lỗi không mong đợi: {exc}{RESET}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
