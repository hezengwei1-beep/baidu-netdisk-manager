#!/usr/bin/env python3
"""自动化字幕提取脚本 - 配合 Puppeteer MCP 使用

从 Puppeteer evaluate 返回的结果文件中解析 SRT 数据并保存到本地。
同时提供辅助函数供批量提取使用。
"""
import json
import os
import subprocess
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SUBTITLES_DIR = DATA_DIR / "subtitles"
PROGRESS_FILE = DATA_DIR / "video_extract_progress.json"


def srt_to_text(srt: str) -> str:
    """SRT 字幕转纯文本"""
    return '\n'.join(
        l.strip() for l in srt.strip().split('\n')
        if l.strip() and not l.strip().isdigit()
        and '-->' not in l and '此字幕由AI自动生成' not in l
    )


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed": {}, "failed": {}, "courses_done": []}


def save_progress(progress: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2))


def save_srt_results(items: list[dict]) -> int:
    """保存 SRT 结果列表，返回保存数量"""
    progress = load_progress()
    saved = 0
    for item in items:
        path = item['path']
        srt = item.get('srt', '')
        status = item.get('status', 'ok')

        if status == 'ok' and srt:
            rel_dir = path.rsplit('/', 1)[0].lstrip('/')
            stem = os.path.splitext(path.rsplit('/', 1)[-1])[0]
            save_dir = SUBTITLES_DIR / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)

            (save_dir / f"{stem}.srt").write_text(srt, encoding='utf-8')
            text = srt_to_text(srt)
            (save_dir / f"{stem}.txt").write_text(text, encoding='utf-8')

            progress["completed"][path] = {
                "srt_length": len(srt),
                "text_length": len(text),
                "extracted_at": int(time.time()),
            }
            saved += 1
        else:
            progress["failed"][path] = {
                "status": status,
                "failed_at": int(time.time()),
            }

    save_progress(progress)
    return saved


def parse_puppeteer_result(filepath: str) -> list[dict]:
    """解析 Puppeteer evaluate 结果文件"""
    result = subprocess.run(
        ['jq', '-r', '.[0].text', filepath],
        capture_output=True, text=True
    )
    raw = result.stdout
    if raw.startswith('Execution result:\n'):
        raw = raw[len('Execution result:\n'):]
    idx = raw.find('\n\nConsole output:')
    if idx > 0:
        raw = raw[:idx]
    decoded = json.loads(raw)
    return json.loads(decoded) if isinstance(decoded, str) else decoded


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python auto_extract.py <puppeteer_result_file>")
        sys.exit(1)

    filepath = sys.argv[1]
    items = parse_puppeteer_result(filepath)
    ok_items = [i for i in items if i.get('status') == 'ok']
    saved = save_srt_results(items)
    print(f"解析 {len(items)} 条结果，保存 {saved} 个字幕文件")
