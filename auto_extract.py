#!/usr/bin/env python3
"""百度网盘字幕提取统一工具

整合了 JS 脚本生成、HTTP 接收服务器、进度管理三大功能。

工作流（3 步）:
  1. python auto_extract.py run --path /目录
     → 查库筛选 → 生成 JS → 启动接收服务器 → 等待浏览器执行
  2. 在 pan.baidu.com 控制台粘贴执行生成的 JS
  3. 自动接收结果并保存

其他命令:
  python auto_extract.py stats [--path /目录]   # 统一进度统计
  python auto_extract.py retry [--path /目录]    # 重试失败文件
"""

import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from subtitle_extractor import get_media_files, VIDEO_EXTS, MEDIA_EXTS

console = Console()

DATA_DIR = Path(__file__).parent / "data"
SUBTITLES_DIR = DATA_DIR / "subtitles"
PROGRESS_FILE = DATA_DIR / "video_extract_progress.json"
OLD_PROGRESS_FILE = DATA_DIR / "subtitle_progress.json"

_progress_lock = threading.Lock()


# ── 工具函数 ──

def srt_to_text(srt: str) -> str:
    """SRT 字幕转纯文本"""
    return '\n'.join(
        line.strip() for line in srt.strip().split('\n')
        if line.strip() and not line.strip().isdigit()
        and '-->' not in line and '此字幕由AI自动生成' not in line
    )


def _migrate_old_progress(progress: dict) -> bool:
    """从 subtitle_progress.json 迁移数据，返回是否执行了迁移"""
    if not OLD_PROGRESS_FILE.exists():
        return False

    old = json.loads(OLD_PROGRESS_FILE.read_text())
    migrated = 0

    for key in ("completed", "failed"):
        old_data = old.get(key, {})
        if isinstance(old_data, dict):
            for path, info in old_data.items():
                if path not in progress.get(key, {}):
                    progress.setdefault(key, {})[path] = info
                    migrated += 1

    for key in ("not_transcoded", "no_subtitle"):
        old_list = old.get(key, [])
        if isinstance(old_list, list):
            existing = set(progress.get(key, []))
            for path in old_list:
                if path not in existing:
                    progress.setdefault(key, []).append(path)
                    migrated += 1

    if migrated > 0:
        OLD_PROGRESS_FILE.rename(OLD_PROGRESS_FILE.with_suffix('.json.bak'))
        console.print(f"[yellow]已迁移 {migrated} 条旧进度记录，原文件重命名为 .bak[/yellow]")
        return True
    return False


def load_progress() -> dict:
    """加载进度（线程安全，首次自动迁移旧文件）"""
    with _progress_lock:
        if PROGRESS_FILE.exists():
            progress = json.loads(PROGRESS_FILE.read_text())
        else:
            progress = {}

        # 确保所有字段存在
        progress.setdefault("completed", {})
        progress.setdefault("failed", {})
        progress.setdefault("not_transcoded", [])
        progress.setdefault("no_subtitle", [])
        progress.setdefault("courses_done", [])

        if _migrate_old_progress(progress):
            _save_progress_unlocked(progress)

        return progress


def _save_progress_unlocked(progress: dict):
    """内部保存（调用者需持有锁）"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2))


def save_progress(progress: dict):
    """保存进度（线程安全）"""
    with _progress_lock:
        _save_progress_unlocked(progress)


def save_srt_results(items: list[dict]) -> dict:
    """保存 SRT 结果列表，返回统计 {ok, not_transcoded, no_subtitle, error}"""
    progress = load_progress()
    counts = {"ok": 0, "not_transcoded": 0, "no_subtitle": 0, "error": 0}

    for item in items:
        path = item.get("path", "")
        status = item.get("status", "")
        srt = item.get("srt", "")

        if status == "ok" and srt:
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
            counts["ok"] += 1

        elif status == "not_transcoded":
            if path not in progress["not_transcoded"]:
                progress["not_transcoded"].append(path)
            counts["not_transcoded"] += 1

        elif status == "no_subtitle":
            if path not in progress["no_subtitle"]:
                progress["no_subtitle"].append(path)
            counts["no_subtitle"] += 1

        else:
            progress["failed"][path] = {
                "error": item.get("message", f"errno={item.get('errno', '?')}"),
                "failed_at": int(time.time()),
                "retries": progress.get("failed", {}).get(path, {}).get("retries", 0),
            }
            counts["error"] += 1

    save_progress(progress)
    return counts


# ── 内嵌 HTTP 接收服务器 ──

class _SRTReceiverHandler(BaseHTTPRequestHandler):
    """接收浏览器 POST 的 SRT 批次数据"""

    server_ref = None  # 由 start_receiver_server 设置

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')

        try:
            data = json.loads(body)
            action = data.get("action", "")

            if action == "save_batch":
                results = data.get("results", [])
                counts = save_srt_results(results)
                self.server_ref.batch_count += 1
                self.server_ref.total_received += len(results)

                console.print(
                    f"  [green]批次 {self.server_ref.batch_count}[/green]: "
                    f"收到 {len(results)} 个, "
                    f"成功 {counts['ok']}, 未转码 {counts['not_transcoded']}, "
                    f"无字幕 {counts['no_subtitle']}, 失败 {counts['error']}"
                )
                self._respond(200, {
                    "saved": counts["ok"],
                    "total_received": self.server_ref.total_received,
                })

            elif action == "done":
                summary = {
                    k: data.get(k, 0)
                    for k in ("ok", "not_transcoded", "no_subtitle", "error")
                }
                self.server_ref.done_summary = summary
                self._respond(200, {"status": "done", "total_received": self.server_ref.total_received})
                self.server_ref.done_event.set()

            else:
                self._respond(400, {"error": f"unknown action: {action}"})

        except Exception as e:
            self._respond(500, {"error": str(e)})

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
        # 静默 HTTP 日志
        pass


class ReceiverServer(HTTPServer):
    """带事件通知的 HTTP 服务器"""

    def __init__(self, port: int):
        super().__init__(('127.0.0.1', port), _SRTReceiverHandler)
        self.done_event = threading.Event()
        self.done_summary = {}
        self.batch_count = 0
        self.total_received = 0
        _SRTReceiverHandler.server_ref = self


def start_receiver_server(port: int) -> ReceiverServer:
    """启动后台接收服务器（daemon 线程）"""
    server = ReceiverServer(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ── JS 生成 ──

def generate_js_code(paths: list[str], delay_ms: int, port: int) -> str:
    """生成浏览器端字幕提取 JS 脚本（自动获取 token）"""
    paths_json = json.dumps(paths, ensure_ascii=False)
    batch_size = 20

    return f"""\
// === 百度网盘字幕批量提取 ===
// 文件数: {len(paths)}, 间隔: {delay_ms}ms, POST 到 http://127.0.0.1:{port}
(async function() {{
  // 自动获取 token
  const BDSTOKEN = (window.yunData && window.yunData.MYBDSTOKEN) || '';
  const JSTOKEN = (window.YZB_TOKEN && window.YZB_TOKEN.token) || '';

  if (!BDSTOKEN) {{ console.error('未找到 BDSTOKEN，请确认在 pan.baidu.com 页面'); return; }}
  if (!JSTOKEN) {{ console.error('未找到 JSTOKEN，请确认在 pan.baidu.com 页面'); return; }}
  console.log('Token 获取成功: bdstoken=' + BDSTOKEN.substring(0, 8) + '...');

  const SERVER = 'http://127.0.0.1:{port}';
  const DELAY = {delay_ms};
  const BATCH_SIZE = {batch_size};
  const paths = {paths_json};

  const batch = [];
  let okCount = 0, ntCount = 0, nsCount = 0, errCount = 0;

  for (let i = 0; i < paths.length; i++) {{
    const filePath = paths[i];
    const fileName = filePath.split('/').pop();

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
          batch.push({{ path: filePath, status: 'not_transcoded' }});
          ntCount++;
          console.log('[' + (i+1) + '/' + paths.length + '] SKIP ' + fileName + ' (未转码)');
        }} else {{
          batch.push({{ path: filePath, status: 'error', errno: data.errno, message: 'errno=' + data.errno }});
          errCount++;
          console.log('[' + (i+1) + '/' + paths.length + '] FAIL ' + fileName + ' errno=' + data.errno);
        }}
      }} else {{
        const subUrl = text.split('\\n').find(l => l.includes('netdisk-subtitle'));
        if (subUrl) {{
          const srtResp = await fetch(subUrl.trim());
          const srt = await srtResp.text();
          batch.push({{ path: filePath, status: 'ok', srt: srt }});
          okCount++;
          console.log('[' + (i+1) + '/' + paths.length + '] OK ' + fileName + ' (' + srt.length + ' chars)');
        }} else {{
          batch.push({{ path: filePath, status: 'no_subtitle' }});
          nsCount++;
          console.log('[' + (i+1) + '/' + paths.length + '] SKIP ' + fileName + ' (无字幕)');
        }}
      }}
    }} catch (e) {{
      batch.push({{ path: filePath, status: 'error', message: e.message }});
      errCount++;
      console.log('[' + (i+1) + '/' + paths.length + '] ERROR ' + fileName + ': ' + e.message);
    }}

    // 每 BATCH_SIZE 个或最后一批，POST 到本地服务器
    if (batch.length >= BATCH_SIZE || i === paths.length - 1) {{
      try {{
        await fetch(SERVER, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ action: 'save_batch', results: batch }}),
        }});
      }} catch (e) {{
        console.warn('POST 失败:', e.message, '(请确认接收服务器已启动)');
      }}
      batch.length = 0;
    }}

    if (i < paths.length - 1) await new Promise(r => setTimeout(r, DELAY));
  }}

  // 发送完成信号
  const summary = {{ ok: okCount, not_transcoded: ntCount, no_subtitle: nsCount, error: errCount }};
  console.log('\\n=== 完成 === 成功:' + okCount + ' 未转码:' + ntCount + ' 无字幕:' + nsCount + ' 失败:' + errCount);
  try {{
    await fetch(SERVER, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ action: 'done', ...summary }}),
    }});
  }} catch (e) {{
    console.warn('完成信号发送失败:', e.message);
  }}
  return JSON.stringify(summary);
}})();"""


# ── CLI ──

@click.group()
def cli():
    """百度网盘字幕提取统一工具"""
    pass


@cli.command()
@click.option("--path", default=None, help="限制目录路径（如 /17、湛卢阅读）")
@click.option("--verbose", is_flag=True, help="显示目录级别详情")
@click.option("--video-only", is_flag=True, help="仅视频（不含音频）")
def stats(path, verbose, video_only):
    """统一进度统计"""
    ext_filter = VIDEO_EXTS if video_only else None
    files = get_media_files(path_filter=path, ext_filter=ext_filter)
    progress = load_progress()

    completed_set = set(progress.get("completed", {}).keys())
    failed_set = set(progress.get("failed", {}).keys())
    nt_set = set(progress.get("not_transcoded", []))
    ns_set = set(progress.get("no_subtitle", []))

    total = len(files)
    completed = sum(1 for f in files if f["path"] in completed_set)
    failed = sum(1 for f in files if f["path"] in failed_set)
    not_transcoded = sum(1 for f in files if f["path"] in nt_set)
    no_sub = sum(1 for f in files if f["path"] in ns_set)
    pending = total - completed - failed - not_transcoded - no_sub

    total_size = sum(f["size"] for f in files)

    table = Table(title=f"字幕提取统计 {path or '(全部)'}")
    table.add_column("状态", style="bold")
    table.add_column("文件数", justify="right")
    table.add_column("占比", justify="right")

    def pct(n):
        return f"{n/total*100:.1f}%" if total else "-"

    table.add_row("已完成", str(completed), pct(completed), style="green")
    table.add_row("未转码(31066)", str(not_transcoded), pct(not_transcoded), style="yellow")
    table.add_row("无字幕", str(no_sub), pct(no_sub), style="dim")
    table.add_row("失败", str(failed), pct(failed), style="red")
    table.add_row("待处理", str(pending), pct(pending), style="cyan")
    table.add_row("总计", str(total), f"{total_size/1024**3:.1f} GB", style="bold")
    console.print(table)

    if verbose:
        # 按顶层目录分组统计
        dir_stats = {}
        for f in files:
            parts = f["path"].strip("/").split("/")
            top_dir = "/" + parts[0] if parts else "/"
            d = dir_stats.setdefault(top_dir, {"total": 0, "done": 0, "pending": 0})
            d["total"] += 1
            if f["path"] in completed_set:
                d["done"] += 1
            elif f["path"] not in failed_set and f["path"] not in nt_set and f["path"] not in ns_set:
                d["pending"] += 1

        dir_table = Table(title="目录明细")
        dir_table.add_column("目录")
        dir_table.add_column("总计", justify="right")
        dir_table.add_column("完成", justify="right")
        dir_table.add_column("待处理", justify="right")
        for d, s in sorted(dir_stats.items(), key=lambda x: -x[1]["pending"]):
            if s["total"] > 0:
                dir_table.add_row(d, str(s["total"]), str(s["done"]), str(s["pending"]))
        console.print(dir_table)


@cli.command()
@click.option("--path", default=None, help="限制目录路径")
@click.option("--limit", default=100, help="单批最大文件数 (默认 100)")
@click.option("--delay", default=0.8, help="请求间隔秒 (默认 0.8)")
@click.option("--port", default=18765, help="HTTP 接收端口 (默认 18765)")
@click.option("--video-only", is_flag=True, help="仅视频")
@click.option("--timeout", default=600, help="等待超时秒 (默认 600)")
def run(path, limit, delay, port, video_only, timeout):
    """一键提取：筛选 → 生成 JS → 启动服务器 → 等待结果"""
    # Step 1: 查库筛选待处理文件
    ext_filter = VIDEO_EXTS if video_only else None
    files = get_media_files(path_filter=path, ext_filter=ext_filter)
    progress = load_progress()

    done = (
        set(progress.get("completed", {}).keys())
        | set(progress.get("no_subtitle", []))
        | set(progress.get("not_transcoded", []))
    )
    pending = [f for f in files if f["path"] not in done]

    if not pending:
        console.print("[yellow]没有待处理的文件[/yellow]")
        return

    if limit > 0:
        pending = pending[:limit]

    console.print(f"[bold]筛选结果[/bold]: 总 {len(files)} 个, 已处理 {len(done)}, 本批 {len(pending)} 个")

    # Step 2: 生成 JS 脚本
    paths = [f["path"] for f in pending]
    delay_ms = int(delay * 1000)
    js_code = generate_js_code(paths, delay_ms, port)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    js_file = DATA_DIR / "batch_extract.js"
    js_file.write_text(js_code, encoding="utf-8")
    console.print(f"[green]JS 脚本已生成[/green]: {js_file}")

    # Step 3: 启动后台接收服务器
    try:
        server = start_receiver_server(port)
    except OSError as e:
        console.print(f"[red]服务器启动失败[/red]: {e}")
        console.print(f"[dim]端口 {port} 可能被占用，尝试 --port 指定其他端口[/dim]")
        return

    console.print(f"[green]接收服务器已启动[/green]: http://127.0.0.1:{port}")

    # Step 4: 提示用户
    console.print(Panel(
        f"[bold]请在 pan.baidu.com 控制台执行以下操作：[/bold]\n\n"
        f"1. 打开 pan.baidu.com 并登录\n"
        f"2. F12 打开开发者工具 → Console\n"
        f"3. 粘贴 [cyan]{js_file}[/cyan] 的内容并回车执行\n\n"
        f"[dim]脚本会自动获取 bdstoken/jsToken，无需手动提取[/dim]\n"
        f"[dim]结果将自动 POST 到本地服务器，Ctrl+C 可中断等待[/dim]",
        title="操作提示",
        border_style="blue",
    ))

    # Step 5: 阻塞等待
    try:
        console.print("[dim]等待浏览器执行结果...[/dim]\n")
        done = server.done_event.wait(timeout=timeout)
    except KeyboardInterrupt:
        console.print("\n[yellow]用户中断[/yellow]")
        done = server.total_received > 0

    server.shutdown()

    # Step 6: 统计报告
    if not done and server.total_received == 0:
        console.print("[yellow]超时未收到任何数据[/yellow]")
        return

    progress = load_progress()
    result_table = Table(title="本次提取结果")
    result_table.add_column("指标", style="bold")
    result_table.add_column("数值", justify="right")
    result_table.add_row("本批文件", str(len(paths)))
    result_table.add_row("收到批次", str(server.batch_count))
    result_table.add_row("收到条目", str(server.total_received))

    if server.done_summary:
        s = server.done_summary
        result_table.add_row("成功", str(s.get("ok", 0)), style="green")
        result_table.add_row("未转码", str(s.get("not_transcoded", 0)), style="yellow")
        result_table.add_row("无字幕", str(s.get("no_subtitle", 0)), style="dim")
        result_table.add_row("失败", str(s.get("error", 0)), style="red")

    result_table.add_row("累计已完成", str(len(progress.get("completed", {}))), style="bold green")
    console.print(result_table)


@cli.command()
@click.option("--path", default=None, help="限制目录路径")
@click.option("--limit", default=50, help="重试最大文件数 (默认 50)")
@click.option("--include-not-transcoded", is_flag=True, help="同时重试未转码文件")
def retry(path, limit, include_not_transcoded):
    """重试失败文件（重置后提示执行 run）"""
    progress = load_progress()

    # 收集要重试的路径
    retry_paths = list(progress.get("failed", {}).keys())
    if include_not_transcoded:
        retry_paths.extend(progress.get("not_transcoded", []))

    # 路径过滤
    if path:
        prefix = path.rstrip("/") + "/"
        retry_paths = [p for p in retry_paths if p.startswith(prefix)]

    if not retry_paths:
        console.print("[yellow]没有可重试的文件[/yellow]")
        return

    # 重置这些记录
    reset_failed = 0
    reset_nt = 0
    for p in retry_paths:
        if p in progress.get("failed", {}):
            old = progress["failed"].pop(p)
            reset_failed += 1
        if p in progress.get("not_transcoded", []):
            progress["not_transcoded"].remove(p)
            reset_nt += 1

    save_progress(progress)

    total_reset = reset_failed + reset_nt
    console.print(f"[green]已重置 {total_reset} 条记录[/green] (失败: {reset_failed}, 未转码: {reset_nt})")

    # 提示下一步
    path_arg = f" --path {path}" if path else ""
    limit_str = f" --limit {min(limit, total_reset)}" if total_reset > 0 else ""
    console.print(f"\n下一步执行:\n  [cyan]python auto_extract.py run{path_arg}{limit_str}[/cyan]")


if __name__ == "__main__":
    cli()
