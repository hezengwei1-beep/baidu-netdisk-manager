"""备份同步模块"""

import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath

from rich.console import Console
from rich.table import Table
from tqdm import tqdm

from api import BaiduPanAPI

console = Console()


def _md5_local(filepath: str) -> str:
    """计算本地文件 MD5"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _should_exclude(filename: str, patterns: list[str]) -> bool:
    """检查文件是否应被排除"""
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


def sync_up(api: BaiduPanAPI, config: dict, dry_run: bool = False):
    """本地 → 网盘单向备份"""
    sync_config = config.get("sync", {})
    local_dir = os.path.expanduser(sync_config.get("local_dir", "~/baidu-backup"))
    remote_dir = sync_config.get("remote_dir", "/同步备份")
    exclude_patterns = sync_config.get("exclude_patterns", [])
    max_files = sync_config.get("max_files", 1000)

    if not os.path.isdir(local_dir):
        console.print(f"[red]本地目录不存在: {local_dir}[/red]")
        console.print(f"请创建目录或修改 config.yaml 中的 sync.local_dir")
        return

    console.print(f"[bold]扫描本地目录: {local_dir}[/bold]")

    # 扫描本地文件
    local_files = {}
    for root, dirs, files in os.walk(local_dir):
        for f in files:
            if _should_exclude(f, exclude_patterns):
                continue
            abs_path = os.path.join(root, f)
            rel_path = os.path.relpath(abs_path, local_dir)
            local_files[rel_path] = {
                "abs_path": abs_path,
                "size": os.path.getsize(abs_path),
                "mtime": int(os.path.getmtime(abs_path)),
            }

    console.print(f"本地文件数: {len(local_files)}")

    # 获取网盘远端文件列表
    console.print(f"[bold]获取网盘目录: {remote_dir}[/bold]")
    try:
        remote_files_raw = api.list_all(remote_dir, recursion=1)
    except Exception:
        remote_files_raw = []

    remote_files = {}
    for rf in remote_files_raw:
        if rf.get("isdir", 0):
            continue
        rel = rf["path"][len(remote_dir):].lstrip("/")
        remote_files[rel] = {
            "path": rf["path"],
            "size": rf.get("size", 0),
            "md5": rf.get("md5", ""),
            "mtime": rf.get("server_mtime", 0),
        }

    console.print(f"网盘文件数: {len(remote_files)}")

    # 对比找出需要上传的文件
    to_upload = []
    for rel_path, local_info in local_files.items():
        remote_path = f"{remote_dir}/{rel_path}"
        if rel_path not in remote_files:
            to_upload.append((rel_path, local_info, "新增"))
        else:
            ri = remote_files[rel_path]
            if local_info["size"] != ri["size"]:
                to_upload.append((rel_path, local_info, "大小不同"))
            elif ri["md5"] and _md5_local(local_info["abs_path"]) != ri["md5"]:
                to_upload.append((rel_path, local_info, "MD5不同"))

    if not to_upload:
        console.print("[green]所有文件已同步，无需上传。[/green]")
        return

    to_upload = to_upload[:max_files]

    table = Table(title=f"待上传文件（共 {len(to_upload)} 个）")
    table.add_column("文件", style="cyan")
    table.add_column("大小", justify="right")
    table.add_column("原因", style="yellow")

    for rel, info, reason in to_upload[:30]:
        table.add_row(rel, _format_size(info["size"]), reason)
    if len(to_upload) > 30:
        table.add_row(f"... 还有 {len(to_upload) - 30} 个", "", "")
    console.print(table)

    total_size = sum(info["size"] for _, info, _ in to_upload)
    console.print(f"总上传大小: {_format_size(total_size)}")

    if dry_run:
        console.print("[yellow]试运行模式，不会实际上传。[/yellow]")
        return

    confirm = input(f"\n确认上传 {len(to_upload)} 个文件？(y/N): ").strip().lower()
    if confirm != "y":
        console.print("[yellow]已取消。[/yellow]")
        return

    success = 0
    failed = 0
    for rel, info, _ in to_upload:
        remote_path = f"{remote_dir}/{rel.replace(os.sep, '/')}"
        # 确保远端目录存在
        remote_parent = str(PurePosixPath(remote_path).parent)
        try:
            api.mkdir(remote_parent)
        except Exception:
            pass

        try:
            api.upload_file(info["abs_path"], remote_path)
            success += 1
        except Exception as e:
            failed += 1
            console.print(f"[red]上传失败 {rel}: {e}[/red]")

    console.print(f"\n[bold green]上传完成！成功 {success}，失败 {failed}[/bold green]")


def sync_down(api: BaiduPanAPI, config: dict, dry_run: bool = False):
    """网盘 → 本地单向下载"""
    sync_config = config.get("sync", {})
    local_dir = os.path.expanduser(sync_config.get("local_dir", "~/baidu-backup"))
    remote_dir = sync_config.get("remote_dir", "/同步备份")
    exclude_patterns = sync_config.get("exclude_patterns", [])
    max_files = sync_config.get("max_files", 1000)

    os.makedirs(local_dir, exist_ok=True)

    console.print(f"[bold]获取网盘目录: {remote_dir}[/bold]")
    try:
        remote_files_raw = api.list_all(remote_dir, recursion=1)
    except Exception as e:
        console.print(f"[red]获取远端目录失败: {e}[/red]")
        return

    remote_files = {}
    for rf in remote_files_raw:
        if rf.get("isdir", 0):
            continue
        rel = rf["path"][len(remote_dir):].lstrip("/")
        if _should_exclude(Path(rel).name, exclude_patterns):
            continue
        remote_files[rel] = {
            "path": rf["path"],
            "fsid": rf.get("fs_id", 0),
            "size": rf.get("size", 0),
            "md5": rf.get("md5", ""),
            "mtime": rf.get("server_mtime", 0),
        }

    console.print(f"网盘文件数: {len(remote_files)}")

    # 对比
    to_download = []
    for rel, ri in remote_files.items():
        local_path = os.path.join(local_dir, rel)
        if not os.path.exists(local_path):
            to_download.append((rel, ri, "新增"))
        else:
            local_size = os.path.getsize(local_path)
            if local_size != ri["size"]:
                to_download.append((rel, ri, "大小不同"))
            elif ri["md5"] and _md5_local(local_path) != ri["md5"]:
                to_download.append((rel, ri, "MD5不同"))

    if not to_download:
        console.print("[green]所有文件已同步，无需下载。[/green]")
        return

    to_download = to_download[:max_files]

    table = Table(title=f"待下载文件（共 {len(to_download)} 个）")
    table.add_column("文件", style="cyan")
    table.add_column("大小", justify="right")
    table.add_column("原因", style="yellow")

    for rel, info, reason in to_download[:30]:
        table.add_row(rel, _format_size(info["size"]), reason)
    if len(to_download) > 30:
        table.add_row(f"... 还有 {len(to_download) - 30} 个", "", "")
    console.print(table)

    total_size = sum(info["size"] for _, info, _ in to_download)
    console.print(f"总下载大小: {_format_size(total_size)}")

    if dry_run:
        console.print("[yellow]试运行模式，不会实际下载。[/yellow]")
        return

    confirm = input(f"\n确认下载 {len(to_download)} 个文件？(y/N): ").strip().lower()
    if confirm != "y":
        console.print("[yellow]已取消。[/yellow]")
        return

    success = 0
    failed = 0
    for rel, info, _ in to_download:
        local_path = os.path.join(local_dir, rel)
        try:
            dlink = api.get_dlink(info["fsid"])
            api.download_file(dlink, local_path)
            success += 1
        except Exception as e:
            failed += 1
            console.print(f"[red]下载失败 {rel}: {e}[/red]")

    console.print(f"\n[bold green]下载完成！成功 {success}，失败 {failed}[/bold green]")


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
