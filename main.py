"""
PyFix-Agents v2 — Main CLI Entry Point
Chạy hệ thống multi-agent sửa lỗi Python tự động kết nối với FastMCP Server.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO

from graph import BugFixState, ProjectInitializerNode, bug_fix_graph
from graph.config import CYAN, MCP_SERVER_URL, RESET, BOLD, YELLOW, RED


class _TeeStream:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, 'isatty', lambda: False)() for stream in self._streams)


@contextmanager
def _session_log_context(args: argparse.Namespace) -> Iterator[Path]:
    pyfix_root = Path(__file__).resolve().parent
    logs_dir = pyfix_root / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    bug_id = os.getenv('ARENA_BUG_ID', 'manual').replace('/', '_')
    mode = 'noninteractive' if args.non_interactive else 'interactive'
    log_path = logs_dir / f'{timestamp}_{mode}_{bug_id}_pid{os.getpid()}.log'

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with log_path.open('w', encoding='utf-8', buffering=1) as log_file:
        sys.stdout = _TeeStream(original_stdout, log_file)
        sys.stderr = _TeeStream(original_stderr, log_file)
        try:
            print(f'[PyFix Session] started_at={datetime.now().isoformat()}')
            print(f'[PyFix Session] log_path={log_path}')
            print(f'[PyFix Session] bug_id={os.getenv("ARENA_BUG_ID", "") or "manual"}')
            print(f'[PyFix Session] mode={mode}')
            print(f'[PyFix Session] repo={args.repo or ""}')
            print(f'[PyFix Session] report={args.report or ""}')
            yield log_path
        finally:
            print(f'[PyFix Session] finished_at={datetime.now().isoformat()}')
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout = original_stdout
            sys.stderr = original_stderr


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='PyFix-Agents CLI')
    parser.add_argument('--repo', help='Đường dẫn repo cần sửa.')
    parser.add_argument('--symptom-file', help='File chứa mô tả lỗi/traceback.')
    parser.add_argument('--symptom-text', help='Mô tả lỗi/traceback truyền trực tiếp.')
    parser.add_argument('--report', help='Đường dẫn ghi report JSON cho harness.')
    parser.add_argument(
        '--non-interactive',
        action='store_true',
        help='Chạy không tương tác: nhận repo/symptom từ tham số và tự động approve plan/patch.',
    )
    return parser.parse_args()


async def main() -> None:
    """Khởi chạy PyFix-Agents CLI."""
    args = parse_args()
    with _session_log_context(args) as session_log_path:
        print(f"{CYAN}🔗 Kết nối với PyFix MCP Server tại: {MCP_SERVER_URL}{RESET}")
        print(f"{CYAN}📝 Session log: {session_log_path}{RESET}")

        symptom_text = args.symptom_text
        if args.symptom_file:
            symptom_text = Path(args.symptom_file).read_text(encoding='utf-8')

        state = BugFixState(
            non_interactive=args.non_interactive,
            preset_repo_path=args.repo,
            preset_user_input=symptom_text,
            report_json_path=args.report,
            step_max_retries=2 if args.non_interactive else 5,
            max_replan_limit=1 if args.non_interactive else 2,
        )
        try:
            result: str = await bug_fix_graph.run(
                state=state,
                inputs=ProjectInitializerNode(),
            )
            print(f"\n{BOLD}🏁 Kết quả hoàn thành.{RESET}")
        except KeyboardInterrupt:
            print(f"\n{YELLOW}👋 Đã hủy bởi người dùng.{RESET}")
        except Exception as exc:
            print(f"\n{RED}❌ Lỗi không mong đợi: {exc}{RESET}")
            raise


if __name__ == '__main__':
    asyncio.run(main())
