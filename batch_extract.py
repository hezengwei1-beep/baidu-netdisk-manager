#!/usr/bin/env python3
"""批量字幕提取脚本 - 使用签名 URL 并行下载

工作流程:
1. 从 JSON 文件读取 SRT URL 列表 (由浏览器端生成)
2. 使用 curl 并行下载 SRT 内容
3. 保存 SRT + TXT 文件并更新进度

用法:
    python batch_extract.py <urls_json_file> [--workers N]
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def download_srt(item: dict) -> dict:
    """下载单个 SRT 文件"""
    path = item['path']
    srt_url = item['srt_url']
    try:
        result = subprocess.run(
            ['curl', '-sL', '--max-time', '30', srt_url],
            capture_output=True, text=True, timeout=35
        )
        srt = result.stdout
        if '-->' in srt and len(srt) > 50:
            return {'path': path, 'status': 'ok', 'srt': srt}
        else:
            return {'path': path, 'status': 'invalid_srt', 'preview': srt[:100]}
    except Exception as e:
        return {'path': path, 'status': 'error', 'msg': str(e)}


def save_single_srt(path: str, srt: str) -> dict:
    """保存单个 SRT 文件到磁盘"""
    rel_dir = path.rsplit('/', 1)[0].lstrip('/')
    stem = os.path.splitext(path.rsplit('/', 1)[-1])[0]
    save_dir = SUBTITLES_DIR / rel_dir
    save_dir.mkdir(parents=True, exist_ok=True)

    (save_dir / f"{stem}.srt").write_text(srt, encoding='utf-8')
    text = srt_to_text(srt)
    (save_dir / f"{stem}.txt").write_text(text, encoding='utf-8')
    return {"srt_length": len(srt), "text_length": len(text)}


def batch_download(urls_file: str, workers: int = 5):
    """并行下载并保存 SRT 文件"""
    items = json.loads(Path(urls_file).read_text())
    progress = load_progress()

    # 跳过已完成的
    todo = [it for it in items if it['path'] not in progress.get('completed', {})]
    print(f"总计 {len(items)} 个, 跳过已完成 {len(items) - len(todo)} 个, 待处理 {len(todo)} 个")

    if not todo:
        print("全部已完成!")
        return

    ok = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_srt, it): it for it in todo}
        for future in as_completed(futures):
            result = future.result()
            path = result['path']
            if result['status'] == 'ok':
                info = save_single_srt(path, result['srt'])
                progress["completed"][path] = {
                    **info,
                    "extracted_at": int(time.time()),
                }
                ok += 1
                print(f"  ✓ [{ok+fail}/{len(todo)}] {path.rsplit('/', 1)[-1]} ({info['srt_length']} chars)")
            else:
                progress["failed"][path] = {
                    "status": result['status'],
                    "failed_at": int(time.time()),
                }
                fail += 1
                print(f"  ✗ [{ok+fail}/{len(todo)}] {path.rsplit('/', 1)[-1]} ({result['status']})")

    save_progress(progress)
    print(f"\n完成: {ok} 成功, {fail} 失败")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python batch_extract.py <urls_json_file> [--workers N]")
        sys.exit(1)

    urls_file = sys.argv[1]
    workers = 5
    if '--workers' in sys.argv:
        idx = sys.argv.index('--workers')
        workers = int(sys.argv[idx + 1])

    batch_download(urls_file, workers)
