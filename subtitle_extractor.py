#!/usr/bin/env python3
"""百度网盘视频字幕批量提取工具

利用百度网盘 SVIP 的 AI 智能字幕功能，批量提取视频文件的逐字稿（SRT 格式）。

API 原理（通过浏览器逆向发现）：
1. GET /api/streaming?type=M3U8_SUBTITLE_SRT&path=<video_path> → M3U8 播放列表
2. 解析 M3U8 提取 netdisk-subtitle CDN URL
3. GET <subtitle_url> → SRT 字幕内容

重要限制：
- 认证需要浏览器完整 Cookie（含 HTTP-only 的 BDUSS），无法仅靠 OAuth access_token
- 视频必须先被播放/转码过（至少打开过一次），否则返回 errno=31066
- errno=31066 表示视频未转码，需要先在网盘客户端中打开播放

推荐工作流：
1. 在 Puppeteer 浏览器中登录 pan.baidu.com
2. 运行 generate-js 命令生成浏览器端批量提取脚本
3. 在 Puppeteer evaluate 中执行该脚本
4. 结果保存到本地 data/subtitles/ 目录
"""

import json
import time
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from rich.table import Table

from db import get_connection
from state_store import load_json_state, save_json_state

console = Console()

# 视频/音频扩展名
VIDEO_EXTS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.rmvb', '.rm', '.3gp', '.ts', '.m4v', '.webm'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.ape', '.m4a'}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

# 状态文件
DATA_DIR = Path(__file__).parent / "data"
PROGRESS_FILE = DATA_DIR / "subtitle_progress.json"


def srt_to_text(srt_content: str) -> str:
    """将 SRT 字幕转换为纯文本（去除时间戳和序号）"""
    lines = []
    for line in srt_content.strip().split("\n"):
        line = line.strip()
        if not line or line.isdigit() or "-->" in line or "此字幕由AI自动生成" in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def load_progress() -> dict:
    """加载提取进度"""
    return load_json_state(
        PROGRESS_FILE,
        {"completed": {}, "failed": {}, "not_transcoded": [], "no_subtitle": []},
    )


def save_progress(progress: dict):
    """保存提取进度"""
    save_json_state(PROGRESS_FILE, progress)


def get_media_files(path_filter: str = None, ext_filter: set = None) -> list[dict]:
    """从本地索引获取媒体文件列表"""
    conn = get_connection()
    exts = ext_filter or MEDIA_EXTS
    placeholders = ",".join("?" for _ in exts)
    sql = f"SELECT * FROM files WHERE isdir=0 AND extension IN ({placeholders})"
    params = list(exts)
    if path_filter:
        sql += " AND path LIKE ?"
        params.append(path_filter.rstrip("/") + "/%")
    sql += " ORDER BY path"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── CLI ──

@click.group()
def cli():
    """百度网盘视频字幕批量提取工具"""
    pass


@cli.command()
@click.option("--path", default=None, help="限制目录路径（如 /17、湛卢阅读）")
@click.option("--video-only", is_flag=True, help="仅视频（不含音频）")
@click.option("--audio-only", is_flag=True, help="仅音频")
def stats(path, video_only, audio_only):
    """统计媒体文件和字幕提取进度"""
    ext_filter = VIDEO_EXTS if video_only else (AUDIO_EXTS if audio_only else None)
    files = get_media_files(path_filter=path, ext_filter=ext_filter)
    progress = load_progress()

    total = len(files)
    completed = sum(1 for f in files if f["path"] in progress.get("completed", {}))
    failed = sum(1 for f in files if f["path"] in progress.get("failed", {}))
    not_transcoded = sum(1 for f in files if f["path"] in progress.get("not_transcoded", []))
    no_sub = sum(1 for f in files if f["path"] in progress.get("no_subtitle", []))
    pending = total - completed - failed - not_transcoded - no_sub

    total_size = sum(f["size"] for f in files)

    table = Table(title=f"字幕提取统计 {path or '(全部)'}")
    table.add_column("状态", style="bold")
    table.add_column("文件数", justify="right")
    table.add_column("占比", justify="right")
    table.add_row("已完成", str(completed), f"{completed/total*100:.1f}%" if total else "-", style="green")
    table.add_row("未转码(31066)", str(not_transcoded), f"{not_transcoded/total*100:.1f}%" if total else "-", style="yellow")
    table.add_row("无字幕", str(no_sub), f"{no_sub/total*100:.1f}%" if total else "-", style="dim")
    table.add_row("失败", str(failed), f"{failed/total*100:.1f}%" if total else "-", style="red")
    table.add_row("待处理", str(pending), f"{pending/total*100:.1f}%" if total else "-", style="cyan")
    table.add_row("总计", str(total), f"{total_size/1024**3:.1f} GB", style="bold")
    console.print(table)


@cli.command(name="generate-js")
@click.option("--path", default=None, help="限制目录路径")
@click.option("--limit", default=50, help="单批最大文件数")
@click.option("--delay", default=1.0, help="请求间隔（秒）")
@click.option("--video-only", is_flag=True, help="仅视频")
@click.option("--bdstoken", required=True, help="bdstoken（从浏览器提取）")
@click.option("--jstoken", required=True, help="jsToken（从浏览器提取）")
def generate_js(path, limit, delay, video_only, bdstoken, jstoken):
    """生成浏览器端批量提取 JavaScript 代码

    在 Puppeteer 或浏览器控制台中执行生成的代码，
    利用浏览器的完整 Cookie 上下文调用字幕 API。
    """
    ext_filter = VIDEO_EXTS if video_only else None
    files = get_media_files(path_filter=path, ext_filter=ext_filter)
    progress = load_progress()

    # 过滤已处理的
    done = (
        set(progress.get("completed", {}).keys())
        | set(progress.get("no_subtitle", []))
        | set(progress.get("not_transcoded", []))
    )
    files = [f for f in files if f["path"] not in done]

    if limit > 0:
        files = files[:limit]

    if not files:
        console.print("[yellow]没有待处理的文件[/yellow]")
        return

    paths_json = json.dumps([f["path"] for f in files], ensure_ascii=False)

    js_code = f"""
// === 百度网盘字幕批量提取 ===
// 文件数: {len(files)}, 间隔: {delay}s
(async function() {{
  const BDSTOKEN = '{bdstoken}';
  const JSTOKEN = '{jstoken}';
  const DELAY = {int(delay * 1000)};
  const paths = {paths_json};

  const results = [];

  for (let i = 0; i < paths.length; i++) {{
    const filePath = paths[i];
    const fileName = filePath.split('/').pop();
    console.log(`[${{i+1}}/${{paths.length}}] ${{fileName}}`);

    try {{
      const params = new URLSearchParams({{
        app_id: '250528', clienttype: '0', channel: 'chunlei', web: '1',
        isplayer: '1', check_blue: '1', bdstoken: BDSTOKEN,
        path: filePath, vip: '2', jsToken: JSTOKEN,
        type: 'M3U8_SUBTITLE_SRT',
      }});

      const resp = await fetch('/api/streaming?' + params);
      const text = await resp.text();

      if (text.startsWith('{{')) {{
        const data = JSON.parse(text);
        if (data.errno === 31066) {{
          results.push({{ path: filePath, status: 'not_transcoded' }});
        }} else {{
          results.push({{ path: filePath, status: 'error', errno: data.errno }});
        }}
      }} else {{
        const subUrl = text.split('\\n').find(l => l.includes('netdisk-subtitle'));
        if (subUrl) {{
          const srtResp = await fetch(subUrl.trim());
          const srt = await srtResp.text();
          results.push({{ path: filePath, status: 'ok', srt: srt }});
        }} else {{
          results.push({{ path: filePath, status: 'no_subtitle' }});
        }}
      }}
    }} catch (e) {{
      results.push({{ path: filePath, status: 'error', message: e.message }});
    }}

    if (i < paths.length - 1) await new Promise(r => setTimeout(r, DELAY));
  }}

  // 汇总
  const ok = results.filter(r => r.status === 'ok');
  const notTrans = results.filter(r => r.status === 'not_transcoded');
  const noSub = results.filter(r => r.status === 'no_subtitle');
  const err = results.filter(r => r.status === 'error');

  console.log(`完成! 成功:${{ok.length}} 未转码:${{notTrans.length}} 无字幕:${{noSub.length}} 失败:${{err.length}}`);

  // 返回结果供外部处理
  window.__subtitleResults = results;
  return JSON.stringify({{
    summary: {{ ok: ok.length, not_transcoded: notTrans.length, no_subtitle: noSub.length, error: err.length }},
    results: results.map(r => ({{
      path: r.path,
      status: r.status,
      srt_length: r.srt ? r.srt.length : 0,
      errno: r.errno
    }}))
  }});
}})();
"""

    console.print(f"[bold]生成 JS 批量提取代码: {len(files)} 个文件[/bold]")
    console.print(f"[dim]复制以下代码到 Puppeteer evaluate 或浏览器控制台执行[/dim]\n")

    # 保存到文件
    js_file = DATA_DIR / "batch_extract.js"
    js_file.write_text(js_code, encoding="utf-8")
    console.print(f"[green]已保存到: {js_file}[/green]")

    # 同时输出到终端
    console.print(Panel(js_code[:2000] + "..." if len(js_code) > 2000 else js_code, title="JavaScript 代码"))


@cli.command(name="import-results")
@click.option("--file", "results_file", required=True, help="从 JSON 文件导入结果")
def import_results(results_file):
    """导入浏览器端提取的结果

    将 window.__subtitleResults 的 JSON 数据保存为文件后导入。
    """
    data = json.loads(Path(results_file).read_text())
    progress = load_progress()
    output_dir = DATA_DIR / "subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = data if isinstance(data, list) else data.get("results", data.get("__subtitleResults", []))

    saved = 0
    for r in results:
        file_path = r.get("path", "")
        status = r.get("status", "")

        if status == "ok" and r.get("srt"):
            srt = r["srt"]
            filename = file_path.rsplit("/", 1)[-1]
            rel_dir = file_path.rsplit("/", 1)[0].lstrip("/")
            save_dir = output_dir / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)

            srt_name = Path(filename).stem + ".srt"
            txt_name = Path(filename).stem + ".txt"
            (save_dir / srt_name).write_text(srt, encoding="utf-8")
            text = srt_to_text(srt)
            (save_dir / txt_name).write_text(text, encoding="utf-8")

            progress.setdefault("completed", {})[file_path] = {
                "srt_length": len(srt),
                "text_length": len(text),
                "extracted_at": int(time.time()),
            }
            saved += 1

        elif status == "not_transcoded":
            progress.setdefault("not_transcoded", [])
            if file_path not in progress["not_transcoded"]:
                progress["not_transcoded"].append(file_path)

        elif status == "no_subtitle":
            progress.setdefault("no_subtitle", [])
            if file_path not in progress["no_subtitle"]:
                progress["no_subtitle"].append(file_path)

        elif status == "error":
            progress.setdefault("failed", {})[file_path] = {
                "error": r.get("message", f"errno={r.get('errno', '?')}"),
                "failed_at": int(time.time()),
            }

    save_progress(progress)
    console.print(f"[bold green]导入完成[/bold green]")
    console.print(f"  保存字幕: {saved} 个")
    console.print(f"  输出目录: {output_dir}")


@cli.command(name="reset-failed")
@click.option("--include-not-transcoded", is_flag=True, help="同时重置未转码记录")
def reset_failed(include_not_transcoded):
    """重置失败记录（允许重试）"""
    progress = load_progress()
    failed_count = len(progress.get("failed", {}))
    progress["failed"] = {}

    nt_count = 0
    if include_not_transcoded:
        nt_count = len(progress.get("not_transcoded", []))
        progress["not_transcoded"] = []

    save_progress(progress)
    console.print(f"[green]已重置 {failed_count} 条失败记录[/green]")
    if nt_count:
        console.print(f"[green]已重置 {nt_count} 条未转码记录[/green]")


# ── 音频 Whisper 转录辅助命令 ──

@cli.command(name="audio-stats")
@click.option("--path", default=None, help="限制目录路径（如 /A学科库/国学）")
def audio_stats(path):
    """统计音频文件和 Whisper 转录进度

    读取 audio_transcript_progress.json（与 whisper_transcribe.py 共享），
    展示音频文件的转录完成情况。
    """
    files = get_media_files(path_filter=path, ext_filter=AUDIO_EXTS)

    # 加载音频转录专用进度文件
    audio_progress_file = DATA_DIR / "audio_transcript_progress.json"
    if audio_progress_file.exists():
        audio_progress = json.loads(audio_progress_file.read_text())
    else:
        audio_progress = {"completed": {}, "failed": {}}

    # 也检查视频字幕进度中是否有音频（早期可能混在一起）
    video_progress = load_progress()

    total = len(files)
    completed = 0
    failed = 0
    for f in files:
        p = f["path"]
        if p in audio_progress.get("completed", {}) or p in video_progress.get("completed", {}):
            completed += 1
        elif p in audio_progress.get("failed", {}) or p in video_progress.get("failed", {}):
            failed += 1
    pending = total - completed - failed

    total_size = sum(f["size"] for f in files)

    # 按扩展名统计
    ext_counts = {}
    for f in files:
        ext = f.get("extension", Path(f["filename"]).suffix).lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    # 按顶层目录统计
    dir_counts = {}
    for f in files:
        parts = f["path"].strip("/").split("/")
        top_dir = "/" + parts[0] if parts else "/"
        dir_counts[top_dir] = dir_counts.get(top_dir, 0) + 1

    table = Table(title=f"音频文件转录统计 {path or '(全部)'}")
    table.add_column("状态", style="bold")
    table.add_column("文件数", justify="right")
    table.add_column("占比", justify="right")
    table.add_row("已完成", str(completed), f"{completed/total*100:.1f}%" if total else "-", style="green")
    table.add_row("失败", str(failed), f"{failed/total*100:.1f}%" if total else "-", style="red")
    table.add_row("待处理", str(pending), f"{pending/total*100:.1f}%" if total else "-", style="cyan")
    table.add_row("总计", str(total), f"{total_size/1024**3:.1f} GB", style="bold")
    console.print(table)

    # 扩展名分布
    ext_table = Table(title="格式分布")
    ext_table.add_column("扩展名")
    ext_table.add_column("数量", justify="right")
    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
        ext_table.add_row(ext, str(count))
    console.print(ext_table)

    # 目录分布（前 10）
    dir_table = Table(title="目录分布 (Top 10)")
    dir_table.add_column("目录")
    dir_table.add_column("数量", justify="right")
    for d, count in sorted(dir_counts.items(), key=lambda x: -x[1])[:10]:
        dir_table.add_row(d, str(count))
    console.print(dir_table)


@cli.command(name="generate-m3u8-js")
@click.option("--path", default=None, help="限制目录路径")
@click.option("--limit", default=200, help="单批最大文件数 (默认 200)")
@click.option("--bdstoken", required=True, help="bdstoken（从浏览器提取）")
@click.option("--jstoken", required=True, help="jsToken（从浏览器提取）")
@click.option("--server-port", default=18766, help="本地接收服务器端口 (默认 18766)")
def generate_m3u8_js(path, limit, bdstoken, jstoken, server_port):
    """生成浏览器端批量获取 M3U8 URL 的 JavaScript 代码

    在 pan.baidu.com 页面执行，对每个音频文件调用 streaming API 获取 M3U8 播放列表，
    然后 POST 到本地 HTTP 服务器，供 whisper_transcribe.py 消费。

    \b
    工作流:
      1. python subtitle_extractor.py generate-m3u8-js --bdstoken xxx --jstoken yyy
      2. 在 pan.baidu.com 执行生成的 JS
      3. python whisper_transcribe.py /tmp/audio_batch_*.json --workers 2
    """
    files = get_media_files(path_filter=path, ext_filter=AUDIO_EXTS)

    # 加载音频转录进度
    audio_progress_file = DATA_DIR / "audio_transcript_progress.json"
    if audio_progress_file.exists():
        audio_progress = json.loads(audio_progress_file.read_text())
    else:
        audio_progress = {"completed": {}, "failed": {}}

    # 过滤已完成的
    done = set(audio_progress.get("completed", {}).keys())
    files = [f for f in files if f["path"] not in done]

    if limit > 0:
        files = files[:limit]

    if not files:
        console.print("[yellow]没有待处理的音频文件[/yellow]")
        return

    paths_json = json.dumps([f["path"] for f in files], ensure_ascii=False)

    js_code = f"""
// === 百度网盘音频 M3U8 批量获取 ===
// 文件数: {len(files)}, 在 pan.baidu.com 页面执行
// 获取 M3U8 播放列表内容，POST 到本地 HTTP 服务器
(async function() {{
  const BDSTOKEN = '{bdstoken}';
  const JSTOKEN = '{jstoken}';
  const SERVER = 'http://127.0.0.1:{server_port}';
  const DELAY = 500;  // 请求间隔 ms
  const BATCH_POST_SIZE = 20;  // 每 20 个 POST 一次

  const paths = {paths_json};
  const results = [];
  let okCount = 0, failCount = 0;

  for (let i = 0; i < paths.length; i++) {{
    const filePath = paths[i];
    const fileName = filePath.split('/').pop();

    try {{
      const params = new URLSearchParams({{
        app_id: '250528', clienttype: '0', channel: 'chunlei', web: '1',
        isplayer: '1', check_blue: '1', bdstoken: BDSTOKEN,
        path: filePath, vip: '2', jsToken: JSTOKEN,
        type: 'M3U8_HLS_MP3_128',
      }});

      const resp = await fetch('/api/streaming?' + params);
      const text = await resp.text();

      if (text.startsWith('{{')) {{
        const data = JSON.parse(text);
        results.push({{ path: filePath, status: 'error', errno: data.errno, message: 'API error' }});
        failCount++;
        console.log(`[${{i+1}}/${{paths.length}}] FAIL ${{fileName}} errno=${{data.errno}}`);
      }} else if (text.includes('#EXTM3U')) {{
        results.push({{ path: filePath, status: 'ok', m3u8: text }});
        okCount++;
        console.log(`[${{i+1}}/${{paths.length}}] OK ${{fileName}} (${{text.length}} bytes)`);
      }} else {{
        results.push({{ path: filePath, status: 'error', message: 'invalid response', preview: text.substring(0, 100) }});
        failCount++;
        console.log(`[${{i+1}}/${{paths.length}}] FAIL ${{fileName}} (invalid response)`);
      }}
    }} catch (e) {{
      results.push({{ path: filePath, status: 'error', message: e.message }});
      failCount++;
      console.log(`[${{i+1}}/${{paths.length}}] ERROR ${{fileName}}: ${{e.message}}`);
    }}

    // 每 BATCH_POST_SIZE 个或最后一批，POST 到本地服务器
    if (results.length >= BATCH_POST_SIZE || i === paths.length - 1) {{
      const okItems = results.filter(r => r.status === 'ok');
      if (okItems.length > 0) {{
        try {{
          await fetch(SERVER + '/save_batch', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              action: 'save_batch',
              results: okItems.map(r => ({{ path: r.path, m3u8: r.m3u8 }})),
            }}),
          }});
        }} catch (e) {{
          console.warn('POST 到本地服务器失败:', e.message);
          console.warn('可在完成后手动保存 window.__m3u8Results');
        }}
      }}
      results.length = 0;  // 清空已发送的
    }}

    if (i < paths.length - 1) await new Promise(r => setTimeout(r, DELAY));
  }}

  console.log(`\\n=== 完成 ===`);
  console.log(`成功: ${{okCount}}, 失败: ${{failCount}}`);

  // 发送完成信号
  try {{
    await fetch(SERVER + '/done', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ action: 'done', ok: okCount, fail: failCount }}),
    }});
  }} catch (e) {{
    console.warn('完成信号发送失败:', e.message);
  }}

  return JSON.stringify({{ ok: okCount, fail: failCount }});
}})();
"""

    # 同时生成本地接收服务器脚本
    receiver_code = f"""#!/usr/bin/env python3
\"\"\"M3U8 接收服务器 — 接收浏览器 POST 的 M3U8 数据并保存为 JSON 批次文件\"\"\"

import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = {server_port}
OUTPUT_DIR = Path("{DATA_DIR}")
batch_items = []
batch_count = 0


class M3U8Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(body)
            action = data.get('action', '')

            if action == 'save_batch':
                results = data.get('results', [])
                batch_items.extend(results)
                self._respond(200, {{'saved': len(results), 'total': len(batch_items)}})
                print(f'  收到 {{len(results)}} 个 M3U8, 累计 {{len(batch_items)}}')

            elif action == 'done':
                # 保存批次文件
                global batch_count
                batch_count += 1
                output_file = OUTPUT_DIR / f'audio_m3u8_batch_{{batch_count}}.json'
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                output_file.write_text(json.dumps(batch_items, ensure_ascii=False, indent=2))
                print(f'\\n保存批次文件: {{output_file}} ({{len(batch_items)}} 个)')
                self._respond(200, {{'file': str(output_file), 'total': len(batch_items)}})

                # 延迟关闭
                import threading
                threading.Timer(1.0, lambda: os._exit(0)).start()

            else:
                self._respond(400, {{'error': 'unknown action'}})

        except Exception as e:
            self._respond(500, {{'error': str(e)}})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        print(f'[M3U8] {{args[0]}}')


if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', PORT), M3U8Receiver)
    print(f'M3U8 Receiver 启动: http://127.0.0.1:{{PORT}}')
    print(f'等待浏览器 POST M3U8 数据...')
    server.serve_forever()
"""

    # 保存文件
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    js_file = DATA_DIR / "batch_m3u8_fetch.js"
    js_file.write_text(js_code, encoding="utf-8")

    receiver_file = Path(__file__).parent / "m3u8_receiver.py"
    receiver_file.write_text(receiver_code, encoding="utf-8")

    console.print(f"[bold]生成 M3U8 批量获取代码: {len(files)} 个音频文件[/bold]")
    console.print(f"[dim]目录: {path or '(全部)'}, 限制: {limit}[/dim]\n")
    console.print(f"[bold]使用步骤:[/bold]")
    console.print(f"  1. 启动接收服务器: [cyan]python m3u8_receiver.py[/cyan]")
    console.print(f"  2. 在 pan.baidu.com 执行: [cyan]{js_file}[/cyan]")
    console.print(f"  3. 转录: [cyan]python whisper_transcribe.py data/audio_m3u8_batch_1.json --workers 2[/cyan]\n")
    console.print(f"[green]JS 代码已保存到: {js_file}[/green]")
    console.print(f"[green]接收服务器已保存到: {receiver_file}[/green]")


if __name__ == "__main__":
    cli()
