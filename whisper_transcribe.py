#!/usr/bin/env python3
"""Whisper 音频转录流水线 — 基于百度网盘 M3U8 流媒体 + 本地 faster-whisper

不下载音频文件到本地，而是通过 M3U8 流媒体地址用 ffmpeg 解码为 WAV，
再用 faster-whisper 转录为 SRT + TXT。

输入: JSON 文件，格式:
[
  {"path": "/A学科库/国学/xxx.mp3", "m3u8": "<M3U8播放列表内容>"},
  ...
]

用法:
    python whisper_transcribe.py <batch_json> [--workers 2] [--model small]
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from state_store import load_json_state, save_json_state

DATA_DIR = Path(__file__).parent / "data"
SUBTITLES_DIR = DATA_DIR / "subtitles"
PROGRESS_FILE = DATA_DIR / "audio_transcript_progress.json"


# ── 进度管理（复用现有格式） ──

def load_progress() -> dict:
    return load_json_state(PROGRESS_FILE, {"completed": {}, "failed": {}, "quota_exceeded": []})


def save_progress(progress: dict):
    save_json_state(PROGRESS_FILE, progress)


# ── SRT 工具函数 ──

def seconds_to_srt_time(seconds: float) -> str:
    """秒数 → SRT 时间格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list[dict]) -> str:
    """whisper segments → SRT 格式"""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seconds_to_srt_time(seg["start"])
        end = seconds_to_srt_time(seg["end"])
        text = seg["text"].strip()
        if text:
            lines.append(str(i))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")
    return "\n".join(lines)


def segments_to_text(segments: list[dict]) -> str:
    """whisper segments → 纯文本"""
    return "".join(seg["text"].strip() for seg in segments if seg["text"].strip())


# ── 核心处理函数 ──

def ffmpeg_m3u8_to_wav(m3u8_content: str, wav_path: str, timeout: int = 300) -> bool:
    """将 M3U8 内容通过 ffmpeg 解码为 16kHz 单声道 WAV

    Returns: True 成功, False 失败
    """
    # 写 M3U8 到临时文件
    m3u8_hash = hashlib.md5(m3u8_content.encode()).hexdigest()[:12]
    m3u8_path = f"/tmp/whisper_m3u8_{m3u8_hash}.m3u8"

    try:
        with open(m3u8_path, "w") as f:
            f.write(m3u8_content)

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                "-i", m3u8_path,
                "-ac", "1",        # 单声道
                "-ar", "16000",    # 16kHz（whisper 要求）
                "-f", "wav",
                wav_path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            # 提取关键错误信息
            stderr = result.stderr[-500:] if len(result.stderr) > 500 else result.stderr
            raise RuntimeError(f"ffmpeg 失败 (code={result.returncode}): {stderr}")

        # 检查输出文件是否有效
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
            raise RuntimeError(f"ffmpeg 输出文件无效: size={os.path.getsize(wav_path) if os.path.exists(wav_path) else 0}")

        return True

    finally:
        if os.path.exists(m3u8_path):
            os.remove(m3u8_path)


def whisper_transcribe_file(model, wav_path: str, language: str = "zh") -> list[dict]:
    """用 faster-whisper 转录 WAV 文件，返回 segments 列表"""
    segments, info = model.transcribe(
        wav_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    result = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            result.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": text,
            })
    return result


def process_single_file(item: dict, model, language: str = "zh") -> dict:
    """处理单个音频文件：M3U8 → WAV → Whisper → SRT/TXT

    Returns: {"path": ..., "status": "ok"|"error", ...}
    """
    path = item["path"]
    m3u8_content = item["m3u8"]
    filename = path.rsplit("/", 1)[-1]
    file_hash = hashlib.md5(path.encode()).hexdigest()[:12]
    wav_path = f"/tmp/whisper_wav_{file_hash}.wav"

    start_time = time.time()

    try:
        # Step 1: M3U8 → WAV
        ffmpeg_m3u8_to_wav(m3u8_content, wav_path)

        # Step 2: Whisper 转录
        segments = whisper_transcribe_file(model, wav_path, language=language)

        if not segments:
            return {
                "path": path,
                "status": "error",
                "message": "转录结果为空",
                "duration": time.time() - start_time,
            }

        # Step 3: 生成 SRT + TXT
        srt_content = segments_to_srt(segments)
        text_content = segments_to_text(segments)

        # Step 4: 保存文件
        rel_dir = path.rsplit("/", 1)[0].lstrip("/")
        stem = os.path.splitext(filename)[0]
        save_dir = SUBTITLES_DIR / rel_dir
        save_dir.mkdir(parents=True, exist_ok=True)

        (save_dir / f"{stem}.srt").write_text(srt_content, encoding="utf-8")
        (save_dir / f"{stem}.txt").write_text(text_content, encoding="utf-8")

        return {
            "path": path,
            "status": "ok",
            "segments_count": len(segments),
            "srt_length": len(srt_content),
            "text_length": len(text_content),
            "duration": time.time() - start_time,
        }

    except Exception as e:
        return {
            "path": path,
            "status": "error",
            "message": str(e),
            "duration": time.time() - start_time,
        }

    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)


# ── 批量处理（多 worker 时不能共享模型，需串行或每 worker 一个模型） ──

def batch_transcribe(
    batch_file: str,
    workers: int = 1,
    model_size: str = "small",
    language: str = "zh",
    device: str = "cpu",
    compute_type: str = "int8",
):
    """批量转录音频文件

    Args:
        batch_file: JSON 文件路径，格式 [{"path": ..., "m3u8": ...}]
        workers: 并发 worker 数（每个 worker 加载独立模型）
        model_size: whisper 模型大小
        language: 音频语言
        device: 计算设备
        compute_type: 计算精度
    """
    items = json.loads(Path(batch_file).read_text())
    progress = load_progress()

    # 跳过已完成的
    todo = [it for it in items if it["path"] not in progress.get("completed", {})]

    print(f"总计 {len(items)} 个, 跳过已完成 {len(items) - len(todo)} 个, 待处理 {len(todo)} 个")
    print(f"模型: {model_size}, 设备: {device}, 精度: {compute_type}, Workers: {workers}")
    print(f"语言: {language}")

    if not todo:
        print("全部已完成!")
        return

    # 加载模型
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

    from faster_whisper import WhisperModel

    print(f"\n加载 Whisper 模型: {model_size} ...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    print("模型加载完成\n")

    ok = 0
    fail = 0
    total_audio_seconds = 0

    batch_start = time.time()

    if workers <= 1:
        # 单线程：简单遍历
        for idx, item in enumerate(todo):
            filename = item["path"].rsplit("/", 1)[-1]
            print(f"  [{idx+1}/{len(todo)}] {filename} ...", end=" ", flush=True)

            result = process_single_file(item, model, language=language)
            path = result["path"]

            if result["status"] == "ok":
                progress.setdefault("completed", {})[path] = {
                    "method": "whisper_m3u8",
                    "model": model_size,
                    "segments_count": result["segments_count"],
                    "text_length": result["text_length"],
                    "extracted_at": int(time.time()),
                }
                ok += 1
                print(f"OK ({result['segments_count']} 段, {result['text_length']} 字, {result['duration']:.1f}s)")
            else:
                progress.setdefault("failed", {})[path] = {
                    "error": result.get("message", "unknown"),
                    "method": "whisper_m3u8",
                    "failed_at": int(time.time()),
                }
                fail += 1
                print(f"FAIL: {result.get('message', '?')}")

            # 每处理一个就保存进度（防止中断丢失）
            save_progress(progress)

    else:
        # 多线程：每个 worker 用同一个模型（faster-whisper 转录是线程安全的）
        # 但 ffmpeg 解码是独立进程，可以并行
        # 注意：whisper 推理本身是 CPU/GPU 密集型，多线程并行效果取决于硬件
        print(f"使用 {workers} 个 workers 并行处理...")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_single_file, item, model, language): item
                for item in todo
            }

            for future in as_completed(futures):
                result = future.result()
                path = result["path"]
                filename = path.rsplit("/", 1)[-1]

                if result["status"] == "ok":
                    progress.setdefault("completed", {})[path] = {
                        "method": "whisper_m3u8",
                        "model": model_size,
                        "segments_count": result["segments_count"],
                        "text_length": result["text_length"],
                        "extracted_at": int(time.time()),
                    }
                    ok += 1
                    print(f"  OK [{ok+fail}/{len(todo)}] {filename} "
                          f"({result['segments_count']} 段, {result['text_length']} 字, {result['duration']:.1f}s)")
                else:
                    progress.setdefault("failed", {})[path] = {
                        "error": result.get("message", "unknown"),
                        "method": "whisper_m3u8",
                        "failed_at": int(time.time()),
                    }
                    fail += 1
                    print(f"  FAIL [{ok+fail}/{len(todo)}] {filename}: {result.get('message', '?')}")

                save_progress(progress)

    elapsed = time.time() - batch_start
    print(f"\n{'='*50}")
    print(f"完成: {ok} 成功, {fail} 失败")
    print(f"总耗时: {elapsed/60:.1f} 分钟")
    if ok > 0:
        print(f"平均每文件: {elapsed/ok:.1f} 秒")


# ── CLI ──

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Whisper 音频转录流水线（M3U8 流媒体 → SRT/TXT）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python whisper_transcribe.py /tmp/audio_batch_1.json
  python whisper_transcribe.py /tmp/audio_batch_1.json --workers 2 --model small
  python whisper_transcribe.py /tmp/audio_batch_1.json --model base --language en
        """,
    )
    parser.add_argument("batch_file", help="JSON 批次文件路径")
    parser.add_argument("--workers", type=int, default=1, help="并发 worker 数 (默认 1)")
    parser.add_argument("--model", dest="model_size", default="small",
                        help="Whisper 模型 (tiny/base/small/medium/large-v3, 默认 small)")
    parser.add_argument("--language", default="zh", help="音频语言 (默认 zh)")
    parser.add_argument("--device", default="cpu", help="计算设备 (cpu/cuda, 默认 cpu)")
    parser.add_argument("--compute-type", dest="compute_type", default="int8",
                        help="计算精度 (int8/float16/float32, 默认 int8)")

    args = parser.parse_args()

    if not os.path.exists(args.batch_file):
        print(f"错误: 文件不存在: {args.batch_file}")
        sys.exit(1)

    batch_transcribe(
        batch_file=args.batch_file,
        workers=args.workers,
        model_size=args.model_size,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
    )


if __name__ == "__main__":
    main()
