"""
Config — API key, model setup, constants, ANSI colors.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

load_dotenv()

# ── API Key & Provider ───────────────────────────────────────────────────────
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
MODEL_DISPLAY_NAME = "gemini-3.5-flash"
model = GoogleModel(MODEL_DISPLAY_NAME, provider=provider)
MODEL_NAME = MODEL_DISPLAY_NAME

# ── MCP Server ───────────────────────────────────────────────────────────────
MCP_SERVER_PORT = 8000
MCP_SERVER_URL = f"http://localhost:{MCP_SERVER_PORT}/mcp"

# ── Retry / Replan limits ────────────────────────────────────────────────────
MAX_RETRY = 3       # Số lần thử lại Execution khi validation thất bại
MAX_REPLAN = 2       # Số lần replan khi retry quá nhiều

# ── ANSI Colors ──────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
