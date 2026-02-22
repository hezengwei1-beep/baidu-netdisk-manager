#!/usr/bin/env python3
"""百度网盘音频逐字稿批量提取工具

支持两种转写方式：
1. 在线精转：利用百度「简单听记」API（消耗 SVIP 额度）
2. 本地转写：使用 faster-whisper（免费，基于 Whisper large-v3）

本地转写流程：
  python audio_transcript.py whisper-transcribe --path /A学科库/国学 --model large-v3

在线精转流程（已不推荐，消耗额度）：
  1. generate-js 生成浏览器端脚本
  2. 在 tingji.baidu.com/embed/listennote 执行
  3. import-results 导入结果
"""

import json
import os
import tempfile
import time
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, TimeElapsedColumn
from rich.table import Table

from api import BaiduPanAPI
from auth import load_config, ensure_token
from db import get_connection
from state_store import load_json_state, save_json_state

console = Console()

AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.ape', '.m4a'}

DATA_DIR = Path(__file__).parent / "data"
PROGRESS_FILE = DATA_DIR / "audio_transcript_progress.json"


def load_progress() -> dict:
    """加载提取进度"""
    return load_json_state(PROGRESS_FILE, {"completed": {}, "failed": {}, "quota_exceeded": []})


def save_progress(progress: dict):
    """保存提取进度"""
    save_json_state(PROGRESS_FILE, progress)


def get_audio_files(path_filter: str = None) -> list[dict]:
    """从本地索引获取音频文件列表"""
    conn = get_connection()
    placeholders = ",".join("?" for _ in AUDIO_EXTS)
    sql = f"SELECT fsid, path, filename, size FROM files WHERE isdir=0 AND extension IN ({placeholders})"
    params = list(AUDIO_EXTS)
    if path_filter:
        sql += " AND path LIKE ?"
        params.append(path_filter.rstrip("/") + "/%")
    sql += " ORDER BY path"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def scripts_to_text(scripts: list[dict]) -> str:
    """将 scripts 数组转换为纯文本"""
    return "".join(s.get("content", "") for s in scripts)


def scripts_to_srt(scripts: list[dict]) -> str:
    """将 scripts 数组转换为 SRT 格式"""
    lines = []
    for i, s in enumerate(scripts, 1):
        start = _seconds_to_srt_time(s.get("start", 0))
        end = _seconds_to_srt_time(s.get("end", 0))
        content = s.get("content", "").strip()
        if content:
            lines.append(f"{i}")
            lines.append(f"{start} --> {end}")
            lines.append(content)
            lines.append("")
    return "\n".join(lines)


def _seconds_to_srt_time(seconds: float) -> str:
    """将秒数转换为 SRT 时间格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── CLI ──

@click.group()
def cli():
    """百度网盘音频逐字稿批量提取工具（基于简单听记 API）"""
    pass


@cli.command()
@click.option("--path", default=None, help="限制目录路径（如 /A学科库/国学）")
def stats(path):
    """统计音频文件和逐字稿提取进度"""
    files = get_audio_files(path_filter=path)
    progress = load_progress()

    total = len(files)
    completed = sum(1 for f in files if f["path"] in progress.get("completed", {}))
    failed = sum(1 for f in files if f["path"] in progress.get("failed", {}))
    pending = total - completed - failed

    total_size = sum(f["size"] for f in files)

    table = Table(title=f"音频逐字稿提取统计 {path or '(全部)'}")
    table.add_column("状态", style="bold")
    table.add_column("文件数", justify="right")
    table.add_column("占比", justify="right")
    table.add_row("已完成", str(completed), f"{completed/total*100:.1f}%" if total else "-", style="green")
    table.add_row("失败", str(failed), f"{failed/total*100:.1f}%" if total else "-", style="red")
    table.add_row("待处理", str(pending), f"{pending/total*100:.1f}%" if total else "-", style="cyan")
    table.add_row("总计", str(total), f"{total_size/1024**3:.1f} GB", style="bold")
    console.print(table)


@cli.command(name="list-remote")
@click.option("--path", required=True, help="网盘目录路径（如 /A学科库/国学）")
@click.option("--bdstoken", required=True, help="bdstoken（从浏览器提取）")
@click.option("--recursive/--no-recursive", default=True, help="是否递归子目录")
def list_remote(path, bdstoken, recursive):
    """生成 JS 代码在 pan.baidu.com 列出音频文件

    Step 1: 在 pan.baidu.com 执行此脚本获取文件列表 JSON
    Step 2: 保存结果后用 generate-js --file-list 生成提取脚本
    """
    js_code = f"""
// === 列出百度网盘音频文件 ===
// 在 pan.baidu.com 页面执行
(async function() {{
  const BDSTOKEN = '{bdstoken}';
  const ROOT = '{path}';
  const RECURSIVE = {'true' if recursive else 'false'};
  const AUDIO_EXTS = /\\.(mp3|m4a|wav|flac|aac|ogg|wma|ape)$/i;

  async function listDir(dir) {{
    const files = [];
    let start = 0;
    while (true) {{
      const resp = await fetch(
        '/api/list?dir=' + encodeURIComponent(dir) +
        '&order=name&start=' + start + '&limit=1000&web=web&folder=0&bdstoken=' + BDSTOKEN
      );
      const data = await resp.json();
      if (data.errno !== 0 || !data.list || data.list.length === 0) break;

      for (const item of data.list) {{
        if (item.isdir && RECURSIVE) {{
          const subFiles = await listDir(item.path);
          files.push(...subFiles);
        }} else if (!item.isdir && AUDIO_EXTS.test(item.server_filename)) {{
          files.push({{
            fsid: item.fs_id,
            name: item.server_filename,
            path: item.path,
            size: item.size,
          }});
        }}
      }}

      if (data.list.length < 1000) break;
      start += 1000;
      await new Promise(r => setTimeout(r, 200));
    }}
    return files;
  }}

  console.log('开始扫描:', ROOT);
  const audioFiles = await listDir(ROOT);
  console.log('找到音频文件:', audioFiles.length);

  window.__audioFileList = audioFiles;
  return JSON.stringify({{ total: audioFiles.length, files: audioFiles }});
}})();
"""
    js_file = DATA_DIR / "list_audio_files.js"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    js_file.write_text(js_code, encoding="utf-8")
    console.print(f"[bold]生成音频文件列表 JS 代码[/bold]")
    console.print(f"[dim]目标目录: {path}, 递归: {recursive}[/dim]")
    console.print(f"[dim]在 pan.baidu.com 页面执行后，将 window.__audioFileList 保存为 JSON 文件[/dim]")
    console.print(f"[green]已保存到: {js_file}[/green]")


@cli.command(name="generate-js")
@click.option("--path", default=None, help="限制目录路径（从本地数据库）")
@click.option("--file-list", "file_list_path", default=None, help="从 JSON 文件读取文件列表（替代数据库）")
@click.option("--limit", default=50, help="单批最大文件数")
@click.option("--batch-size", default=5, help="每个并发批次的文件数")
@click.option("--no-exact", is_flag=True, help="仅导入不精转（不消耗额度，但无逐字稿）")
def generate_js(path, file_list_path, limit, batch_size, no_exact):
    """生成浏览器端批量提取 JavaScript 代码

    在 Puppeteer 中导航到 tingji.baidu.com/embed/listennote 后执行。

    文件来源（二选一）：
    - --path: 从本地数据库读取
    - --file-list: 从 JSON 文件读取（由 list-remote 生成）
    """
    if file_list_path:
        raw = json.loads(Path(file_list_path).read_text())
        files_from_list = raw.get("files", raw) if isinstance(raw, dict) else raw
        files = [{"fsid": f["fsid"], "path": f["path"], "filename": f.get("name", f["path"].rsplit("/", 1)[-1]), "size": f.get("size", 0)} for f in files_from_list]
    else:
        files = get_audio_files(path_filter=path)

    progress = load_progress()

    # 过滤已处理的
    done = set(progress.get("completed", {}).keys())
    files = [f for f in files if f["path"] not in done]

    if limit > 0:
        files = files[:limit]

    if not files:
        console.print("[yellow]没有待处理的音频文件[/yellow]")
        return

    # 构建文件列表 [{fsid, name, path}]
    file_list = []
    for f in files:
        file_list.append({
            "fsid": f["fsid"],
            "name": f.get("filename", f.get("name", "")),
            "path": f["path"],
        })

    files_json = json.dumps(file_list, ensure_ascii=False)

    js_code = f"""
// === 百度网盘音频逐字稿批量提取 ===
// 文件数: {len(files)}, 批次大小: {batch_size}, 精转: {'否' if no_exact else '是'}
// 执行前请确保已导航到 tingji.baidu.com/embed/listennote
(async function() {{
  const BATCH_SIZE = {batch_size};
  const DO_EXACT = {'false' if no_exact else 'true'};
  const POLL_INTERVAL = 5000;  // 5秒
  const MAX_POLL = 120;        // 最多轮询120次（10分钟）
  const API_BASE = '';         // 相对路径，在 tingji.baidu.com 上执行
  const PARAMS = 'clienttype=0&app_id=250528&web=1&channel=chunlei';

  const files = {files_json};
  const results = [];
  let totalProcessed = 0;

  // 分批处理
  for (let batchStart = 0; batchStart < files.length; batchStart += BATCH_SIZE) {{
    const batch = files.slice(batchStart, batchStart + BATCH_SIZE);
    const batchNum = Math.floor(batchStart / BATCH_SIZE) + 1;
    const totalBatches = Math.ceil(files.length / BATCH_SIZE);
    console.log(`\\n=== 批次 ${{batchNum}}/${{totalBatches}} (${{batch.length}} 个文件) ===`);

    // Step 1: 批量创建笔记
    const createParam = batch.map(f => ({{ fsid: f.fsid, name: f.name }}));
    console.log('Step 1: 创建笔记...');
    let createData;
    try {{
      const createResp = await fetch(
        API_BASE + '/api/ainote/create?' + PARAMS +
        '&batch_param=' + encodeURIComponent(JSON.stringify(createParam)) +
        '&type=5'
      );
      createData = await createResp.json();
    }} catch (e) {{
      console.error('创建笔记失败:', e);
      batch.forEach(f => results.push({{ path: f.path, status: 'error', message: '创建失败: ' + e.message }}));
      continue;
    }}

    if (createData.errno !== 0) {{
      console.error('创建笔记 errno:', createData.errno);
      batch.forEach(f => results.push({{ path: f.path, status: 'error', message: 'errno=' + createData.errno }}));
      continue;
    }}

    const successNotes = createData.data?.batch_result?.success_datas || [];
    const failNotes = createData.data?.batch_result?.fail_datas || [];

    // 映射 fsid -> path
    const fsidToPath = {{}};
    batch.forEach(f => fsidToPath[f.fsid] = f.path);

    // 记录失败的
    failNotes.forEach(fn => {{
      const p = fsidToPath[fn.fsid] || 'unknown';
      results.push({{ path: p, status: 'error', message: 'create_fail: ' + JSON.stringify(fn) }});
    }});

    if (successNotes.length === 0) {{
      console.log('本批次全部创建失败');
      continue;
    }}

    const noteIds = successNotes.map(n => n.note_id);
    const noteIdToFsid = {{}};
    successNotes.forEach(n => noteIdToFsid[n.note_id] = n.fsid);
    console.log(`  创建成功: ${{successNotes.length}} 个, note_ids: ${{noteIds.join(',')}}`);

    if (!DO_EXACT) {{
      // 仅导入模式，不触发精转
      successNotes.forEach(n => {{
        const p = fsidToPath[n.fsid] || 'unknown';
        results.push({{ path: p, status: 'imported', note_id: n.note_id }});
      }});
      continue;
    }}

    // Step 2: 批量触发精转
    console.log('Step 2: 触发精转...');
    const exactParam = noteIds.map(id => ({{ note_id: id }}));
    try {{
      const exactResp = await fetch(API_BASE + '/api/ainote/exact?' + PARAMS, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
        body: 'type=1&batch_param=' + encodeURIComponent(JSON.stringify(exactParam)) +
              '&language=1&scene=0&script_server_control=true'
      }});
      const exactData = await exactResp.json();
      if (exactData.errno !== 0) {{
        console.error('精转触发失败, errno:', exactData.errno);
        // 不中断，可能部分成功
      }} else {{
        console.log('  精转已触发');
      }}
    }} catch (e) {{
      console.error('精转触发异常:', e);
    }}

    // Step 3: 轮询等待完成
    console.log('Step 3: 等待精转完成...');
    const pendingIds = new Set(noteIds);
    const completedNotes = {{}};  // note_id -> list result

    for (let poll = 0; poll < MAX_POLL && pendingIds.size > 0; poll++) {{
      await new Promise(r => setTimeout(r, POLL_INTERVAL));

      try {{
        const idsStr = Array.from(pendingIds).join(',');
        const listResp = await fetch(
          API_BASE + '/api/ainote/list?' + PARAMS +
          '&note_ids=' + idsStr + '&show_web_recording=true'
        );
        const listData = await listResp.json();
        const noteList = listData.data?.result || [];

        for (const note of noteList) {{
          if (note.status === 2) {{
            pendingIds.delete(note.note_id);
            completedNotes[note.note_id] = note;
          }} else if (note.status === 0 && poll > 30) {{
            // 超过 2.5 分钟仍是 status=0，可能有问题
            pendingIds.delete(note.note_id);
            const p = fsidToPath[noteIdToFsid[note.note_id]] || 'unknown';
            results.push({{ path: p, status: 'error', note_id: note.note_id, message: 'stuck_at_status_0' }});
          }}
        }}

        if (poll % 6 === 0) {{
          console.log(`  轮询 #${{poll+1}}: 剩余 ${{pendingIds.size}}/${{noteIds.length}}`);
        }}
      }} catch (e) {{
        console.error('轮询异常:', e);
      }}
    }}

    // 超时仍未完成的
    pendingIds.forEach(id => {{
      const p = fsidToPath[noteIdToFsid[id]] || 'unknown';
      results.push({{ path: p, status: 'error', note_id: id, message: 'poll_timeout' }});
    }});

    // Step 4: 获取逐字稿
    const completedIds = Object.keys(completedNotes).map(Number);
    console.log(`Step 4: 获取逐字稿 (${{completedIds.length}} 个)...`);

    for (const noteId of completedIds) {{
      const fsid = noteIdToFsid[noteId];
      const filePath = fsidToPath[fsid] || 'unknown';

      try {{
        const detailResp = await fetch(
          API_BASE + '/api/ainote/detail?' + PARAMS +
          '&note_id=' + noteId + '&type=1&need_meta=true'
        );
        const detailData = await detailResp.json();

        if (detailData.errno !== 0) {{
          results.push({{ path: filePath, status: 'error', note_id: noteId, message: 'detail_errno=' + detailData.errno }});
          continue;
        }}

        const scripts = detailData.data?.scripts || [];
        if (scripts.length === 0) {{
          results.push({{ path: filePath, status: 'error', note_id: noteId, message: 'empty_scripts' }});
          continue;
        }}

        results.push({{
          path: filePath,
          status: 'ok',
          note_id: noteId,
          duration: detailData.data?.duration || 0,
          scripts: scripts,
        }});

        totalProcessed++;
        console.log(`  [${{totalProcessed}}] ${{filePath.split('/').pop()}} - ${{scripts.length}} 条`);
      }} catch (e) {{
        results.push({{ path: filePath, status: 'error', note_id: noteId, message: 'detail_error: ' + e.message }});
      }}

      // 每个 detail 请求间隔 200ms，避免太快
      await new Promise(r => setTimeout(r, 200));
    }}

    // 批次间等待 2 秒
    if (batchStart + BATCH_SIZE < files.length) {{
      console.log('批次间等待 2 秒...');
      await new Promise(r => setTimeout(r, 2000));
    }}
  }}

  // 汇总
  const ok = results.filter(r => r.status === 'ok');
  const imported = results.filter(r => r.status === 'imported');
  const err = results.filter(r => r.status === 'error');

  console.log(`\\n=== 完成 ===`);
  console.log(`成功: ${{ok.length}}, 仅导入: ${{imported.length}}, 失败: ${{err.length}}`);
  if (err.length > 0) {{
    console.log('失败详情:', err.map(e => e.path.split('/').pop() + ': ' + e.message).join('; '));
  }}

  window.__audioTranscriptResults = results;
  return JSON.stringify({{
    summary: {{ ok: ok.length, imported: imported.length, error: err.length }},
    results: results.map(r => ({{
      path: r.path,
      status: r.status,
      note_id: r.note_id,
      duration: r.duration || 0,
      scripts_count: r.scripts ? r.scripts.length : 0,
      scripts: r.scripts || [],
      message: r.message || '',
    }}))
  }});
}})();
"""

    console.print(f"[bold]生成音频逐字稿提取 JS 代码: {len(files)} 个文件[/bold]")
    console.print(f"[dim]批次大小: {batch_size}, 精转: {'否' if no_exact else '是'}[/dim]")
    console.print(f"[dim]请在 Puppeteer 中导航到 tingji.baidu.com/embed/listennote 后执行[/dim]\n")

    js_file = DATA_DIR / "batch_audio_transcript.js"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    js_file.write_text(js_code, encoding="utf-8")
    console.print(f"[green]已保存到: {js_file}[/green]")
    console.print(f"[dim]文件大小: {len(js_code)} 字节[/dim]")


@cli.command(name="import-results")
@click.option("--file", "results_file", required=True, help="从 JSON 文件导入结果")
def import_results(results_file):
    """导入浏览器端提取的结果

    将 window.__audioTranscriptResults 的 JSON 数据保存为文件后导入。
    """
    data = json.loads(Path(results_file).read_text())
    progress = load_progress()
    output_dir = DATA_DIR / "subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = data if isinstance(data, list) else data.get("results", [])

    saved = 0
    for r in results:
        file_path = r.get("path", "")
        status = r.get("status", "")

        if status == "ok" and r.get("scripts"):
            scripts = r["scripts"]
            filename = file_path.rsplit("/", 1)[-1]
            rel_dir = file_path.rsplit("/", 1)[0].lstrip("/")
            save_dir = output_dir / rel_dir
            save_dir.mkdir(parents=True, exist_ok=True)

            stem = Path(filename).stem

            # 保存 SRT 格式（带时间戳）
            srt_content = scripts_to_srt(scripts)
            (save_dir / f"{stem}.srt").write_text(srt_content, encoding="utf-8")

            # 保存纯文本
            text_content = scripts_to_text(scripts)
            (save_dir / f"{stem}.txt").write_text(text_content, encoding="utf-8")

            progress.setdefault("completed", {})[file_path] = {
                "note_id": r.get("note_id"),
                "duration": r.get("duration", 0),
                "scripts_count": len(scripts),
                "text_length": len(text_content),
                "extracted_at": int(time.time()),
            }
            saved += 1

        elif status == "error":
            progress.setdefault("failed", {})[file_path] = {
                "error": r.get("message", "unknown"),
                "note_id": r.get("note_id"),
                "failed_at": int(time.time()),
            }

    save_progress(progress)
    console.print(f"[bold green]导入完成[/bold green]")
    console.print(f"  保存逐字稿: {saved} 个")
    console.print(f"  输出目录: {output_dir}")


@cli.command(name="reset-failed")
def reset_failed():
    """重置失败记录（允许重试）"""
    progress = load_progress()
    failed_count = len(progress.get("failed", {}))
    progress["failed"] = {}
    save_progress(progress)
    console.print(f"[green]已重置 {failed_count} 条失败记录[/green]")


# ── Whisper 本地转写 ──

def _load_whisper_model(model_size: str, device: str, compute_type: str):
    """加载 faster-whisper 模型（仅加载一次）"""
    from faster_whisper import WhisperModel
    # 国内网络可能无法直接访问 HuggingFace，使用镜像
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    console.print(f"[bold]加载 Whisper 模型: {model_size}[/bold] (device={device}, compute={compute_type})")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    console.print("[green]模型加载完成[/green]")
    return model


def _transcribe_audio(model, audio_path: str, language: str = "zh") -> list[dict]:
    """用 faster-whisper 转写音频文件，返回 scripts 格式"""
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    scripts = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            scripts.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "content": text,
            })
    return scripts


def _get_dlinks_batch(api: BaiduPanAPI, fsids: list[int]) -> dict[int, str]:
    """批量获取下载链接，返回 {fsid: dlink}"""
    result = {}
    # file_meta API 每次最多 100 个 fsid
    for i in range(0, len(fsids), 100):
        batch = fsids[i:i + 100]
        try:
            metas = api.file_meta(batch)
            for m in metas:
                fsid = m.get("fs_id", 0)
                dlink = m.get("dlink", "")
                if dlink:
                    result[fsid] = dlink
        except Exception as e:
            console.print(f"[red]获取 dlink 失败 (batch {i}): {e}[/red]")
        if i + 100 < len(fsids):
            time.sleep(0.3)
    return result


@cli.command(name="whisper-transcribe")
@click.option("--path", default="/A学科库/国学", help="限制目录路径")
@click.option("--model", "model_size", default="large-v3",
              help="Whisper 模型 (tiny/base/small/medium/large-v3)")
@click.option("--device", default="cpu", help="计算设备 (cpu/cuda)")
@click.option("--compute-type", "compute_type", default="int8",
              help="计算精度 (int8/float16/float32)")
@click.option("--language", default="zh", help="音频语言 (zh/en/ja 等)")
@click.option("--limit", default=0, help="最多处理文件数（0=不限）")
@click.option("--skip-failed/--no-skip-failed", default=True, help="跳过之前失败的文件")
@click.option("--tmp-dir", default=None, help="临时下载目录（默认系统临时目录）")
def whisper_transcribe(path, model_size, device, compute_type, language, limit, skip_failed, tmp_dir):
    """使用 faster-whisper 本地转写音频文件

    流程：从百度网盘下载音频 → faster-whisper 本地转写 → 保存 SRT + TXT

    \b
    示例：
      # 转写国学目录下的音频（默认 large-v3 模型）
      python audio_transcript.py whisper-transcribe --path /A学科库/国学
      # 用小模型快速测试
      python audio_transcript.py whisper-transcribe --path /A学科库/国学 --model base --limit 3
    """
    # 1. 准备
    config = load_config()
    access_token = ensure_token(config)
    api = BaiduPanAPI(access_token)

    files = get_audio_files(path_filter=path)
    if not files:
        console.print(f"[yellow]未找到音频文件: {path}[/yellow]")
        return

    progress = load_progress()
    done_paths = set(progress.get("completed", {}).keys())
    failed_paths = set(progress.get("failed", {}).keys())

    pending = []
    for f in files:
        if f["path"] in done_paths:
            continue
        if skip_failed and f["path"] in failed_paths:
            continue
        pending.append(f)

    if limit > 0:
        pending = pending[:limit]

    if not pending:
        console.print("[green]所有文件已处理完成！[/green]")
        return

    total_size = sum(f["size"] for f in pending)
    console.print(f"\n[bold]Whisper 本地转写[/bold]")
    console.print(f"  目录: {path}")
    console.print(f"  待处理: {len(pending)} / {len(files)} 个文件 ({total_size / 1024**3:.1f} GB)")
    console.print(f"  已完成: {len(done_paths)} 个, 失败: {len(failed_paths)} 个")
    console.print(f"  模型: {model_size}, 设备: {device}, 精度: {compute_type}\n")

    # 2. 加载模型
    model = _load_whisper_model(model_size, device, compute_type)

    # 3. 准备输出目录
    output_dir = DATA_DIR / "subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 4. 逐文件处理
    ok_count = 0
    fail_count = 0
    tmp_base = tmp_dir or tempfile.gettempdir()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("转写进度", total=len(pending))

        for idx, f in enumerate(pending):
            file_path = f["path"]
            filename = f.get("filename", file_path.rsplit("/", 1)[-1])
            fsid = f["fsid"]
            prog.update(task, description=f"[cyan]{filename}[/cyan]")

            tmp_audio = os.path.join(tmp_base, f"whisper_dl_{fsid}{Path(filename).suffix}")

            try:
                # 4a. 获取下载链接
                dlink = api.get_dlink(fsid)

                # 4b. 下载音频
                prog.update(task, description=f"[cyan]下载 {filename}[/cyan]")
                api.download_file(dlink, tmp_audio)

                # 4c. 转写
                prog.update(task, description=f"[cyan]转写 {filename}[/cyan]")
                scripts = _transcribe_audio(model, tmp_audio, language=language)

                if not scripts:
                    raise RuntimeError("转写结果为空")

                # 4d. 保存 SRT + TXT
                rel_dir = file_path.rsplit("/", 1)[0].lstrip("/")
                save_dir = output_dir / rel_dir
                save_dir.mkdir(parents=True, exist_ok=True)
                stem = Path(filename).stem

                srt_content = scripts_to_srt(scripts)
                (save_dir / f"{stem}.srt").write_text(srt_content, encoding="utf-8")

                text_content = scripts_to_text(scripts)
                (save_dir / f"{stem}.txt").write_text(text_content, encoding="utf-8")

                # 4e. 更新进度
                progress.setdefault("completed", {})[file_path] = {
                    "method": "whisper",
                    "model": model_size,
                    "scripts_count": len(scripts),
                    "text_length": len(text_content),
                    "extracted_at": int(time.time()),
                }
                save_progress(progress)
                ok_count += 1
                prog.console.print(
                    f"  [green]✓[/green] [{ok_count}] {filename} - {len(scripts)} 段, "
                    f"{len(text_content)} 字"
                )

            except Exception as e:
                progress.setdefault("failed", {})[file_path] = {
                    "error": str(e),
                    "method": "whisper",
                    "failed_at": int(time.time()),
                }
                save_progress(progress)
                fail_count += 1
                prog.console.print(f"  [red]✗[/red] {filename}: {e}")

            finally:
                # 清理临时文件
                if os.path.exists(tmp_audio):
                    os.remove(tmp_audio)

            prog.advance(task)

    # 5. 汇总
    console.print(f"\n[bold]转写完成[/bold]")
    console.print(f"  成功: [green]{ok_count}[/green]")
    console.print(f"  失败: [red]{fail_count}[/red]")
    console.print(f"  输出目录: {output_dir}")


if __name__ == "__main__":
    cli()
