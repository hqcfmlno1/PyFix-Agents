"""
Config — API key, model setup, constants, ANSI colors.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from httpx import AsyncClient

load_dotenv()

# ── API Keys ─────────────────────────────────────────────────────────────────
GOOGLE_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

if not GOOGLE_API_KEY:
    raise RuntimeError("Không tìm thấy GEMINI_API_KEY hoặc GOOGLE_API_KEY trong file .env!")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("Không tìm thấy DEEPSEEK_API_KEY trong file .env!")

# ── Google Provider (dùng cho Analyzer) ──────────────────────────────────────
if GOOGLE_API_KEY.startswith("AIzaSy"):
    google_provider = GoogleProvider(api_key=GOOGLE_API_KEY)
else:
    from google.oauth2.credentials import Credentials
    from google import genai

    creds = Credentials(token=GOOGLE_API_KEY)
    genai_client = genai.Client(credentials=creds)
    google_provider = GoogleProvider(client=genai_client)

# ── DeepSeek Provider ─────────────────────────────────────────────────────────
deepseek_provider = OpenAIProvider(base_url='https://api.deepinfra.com/v1/openai',
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    http_client=AsyncClient(timeout=120)
)

# ── Models ────────────────────────────────────────────────────────────────────
# Input Analyzer: Gemma — nhẹ, nhanh, chỉ cần trích xuất JSON có cấu trúc
ANALYZER_MODEL_NAME = "gemma-4-31b-it"
analyzer_model = GoogleModel(ANALYZER_MODEL_NAME, provider=google_provider)

# Planner: DeepSeek-R1 với Thinking Mode — phân tích sâu, tìm root cause
#PLANNER_MODEL_NAME = "deepseek-ai/DeepSeek-V4-Pro"
PLANNER_MODEL_NAME = "google/gemma-4-31B-it"
planner_model = OpenAIChatModel(
    PLANNER_MODEL_NAME,
    provider=deepseek_provider,
    settings=ModelSettings(thinking="high"),
)


#CODER_MODEL_NAME = "deepseek-ai/DeepSeek-V4-Pro"
CODER_MODEL_NAME = "google/gemma-4-31B-it"
coder_model = OpenAIChatModel(CODER_MODEL_NAME, provider=deepseek_provider)

# Backward compat — một số nơi vẫn import `model` và `MODEL_NAME` chung
model = analyzer_model
MODEL_NAME = ANALYZER_MODEL_NAME

# ── MCP Server ───────────────────────────────────────────────────────────────
MCP_SERVER_PORT = 8000
MCP_SERVER_URL = f"http://localhost:{MCP_SERVER_PORT}/mcp"

# ── Retry / Replan limits ────────────────────────────────────────────────────
MAX_RETRY = 5       # Số lần thử lại Execution khi validation thất bại
MAX_REPLAN = 2       # Số lần replan khi retry quá nhiều

# ── ANSI Colors ──────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
