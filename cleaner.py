"""空间清理模块"""

import time
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from api import BaiduPanAPI
from db import (
    find_duplicates,
    find_large_files,
    find_expired_files,
    find_empty_dirs,
    delete_records,
)

console = Console()


def _is_under_dir(path: str, dir_path: str) -> bool:
    """检查 path 是否在 dir_path 目录下"""
    return path == dir_path or path.startswith(dir_path.rstrip("/") + "/")


def generate_report(config: dict) -> dict:
    """生成清理报告"""
    clean_config = config.get("clean", {})
    threshold_mb = clean_config.get("large_file_threshold_mb", 500)
    expire_days = clean_config.get("expire_days", 365)
    exclude_dirs = set(clean_config.get("exclude_dirs", []))

    report = {}

    # 1. 重复文件
    console.print("[bold]检测重复文件...[/bold]")
    all_dups = find_duplicates()
    # 过滤排除目录
    duplicates = {}
    for md5, files in all_dups.items():
        filtered = [f for f in files if not any(_is_under_dir(f["path"], ex) for ex in exclude_dirs)]
        if len(filtered) > 1:
            duplicates[md5] = filtered
    report["duplicates"] = duplicates

    keep_policy = clean_config.get("duplicate_keep", "keep_shortest_path")
    dup_count = sum(len(files) - 1 for files in duplicates.values())
    dup_size = 0
    for md5, files in duplicates.items():
        if keep_policy == "keep_shortest_path":
            keep_idx = min(range(len(files)), key=lambda j: len(files[j]["path"]))
        else:
            keep_idx = 0
        dup_size += sum(f["size"] for j, f in enumerate(files) if j != keep_idx)
    console.print(f"  发现 {len(duplicates)} 组重复文件，可释放 {_format_size(dup_size)}")

    # 2. 大文件
    console.print("[bold]扫描大文件...[/bold]")
    threshold_bytes = threshold_mb * 1024 * 1024
    large_files = find_large_files(threshold_bytes)
    large_files = [f for f in large_files if not any(_is_under_dir(f["path"], ex) for ex in exclude_dirs)]
    report["large_files"] = large_files
    large_total = sum(f["size"] for f in large_files)
    console.print(f"  发现 {len(large_files)} 个大文件（>{threshold_mb}MB），共 {_format_size(large_total)}")

    # 3. 过期文件
    console.print("[bold]扫描过期文件...[/bold]")
    expire_seconds = expire_days * 86400
    expired = find_expired_files(expire_seconds)
    expired = [f for f in expired if not any(_is_under_dir(f["path"], ex) for ex in exclude_dirs)]
    report["expired"] = expired
    expired_total = sum(f["size"] for f in expired)
    console.print(f"  发现 {len(expired)} 个过期文件（>{expire_days}天），共 {_format_size(expired_total)}")

    # 4. 空目录
    console.print("[bold]扫描空目录...[/bold]")
    empty_dirs = find_empty_dirs()
    empty_dirs = [d for d in empty_dirs if not any(_is_under_dir(d["path"], ex) for ex in exclude_dirs)]
    report["empty_dirs"] = empty_dirs
    console.print(f"  发现 {len(empty_dirs)} 个空目录")

    # 汇总
    total_saveable = dup_size + expired_total
    console.print()
    console.print(Panel(
        f"[bold]清理报告汇总[/bold]\n\n"
        f"  重复文件:  {dup_count} 个，可释放 {_format_size(dup_size)}\n"
        f"  大文件:    {len(large_files)} 个，共 {_format_size(large_total)}\n"
        f"  过期文件:  {len(expired)} 个，共 {_format_size(expired_total)}\n"
        f"  空目录:    {len(empty_dirs)} 个\n"
        f"\n  [green]预计可释放空间: {_format_size(total_saveable)}（不含大文件）[/green]",
        title="清理报告",
        border_style="blue",
    ))

    return report


def print_report_detail(report: dict, config: dict):
    """打印清理报告详情"""
    keep_policy = config.get("clean", {}).get("duplicate_keep", "keep_shortest_path")

    # 重复文件详情
    duplicates = report.get("duplicates", {})
    if duplicates:
        console.print("\n[bold underline]重复文件详情[/bold underline]")
        for i, (md5, files) in enumerate(list(duplicates.items())[:20], 1):
            console.print(f"\n  [cyan]组 {i}[/cyan] (MD5: {md5[:12]}..., 大小: {_format_size(files[0]['size'])})")
            # 标记保留项
            if keep_policy == "keep_shortest_path":
                keep_idx = min(range(len(files)), key=lambda j: len(files[j]["path"]))
            else:
                keep_idx = 0
            for j, f in enumerate(files):
                mark = "[green]保留[/green]" if j == keep_idx else "[red]删除[/red]"
                console.print(f"    {mark} {f['path']}")

        if len(duplicates) > 20:
            console.print(f"\n  ... 还有 {len(duplicates) - 20} 组重复文件")

    # 大文件 Top 20
    large_files = report.get("large_files", [])
    if large_files:
        table = Table(title="\n大文件 Top 20")
        table.add_column("#", style="dim", width=4)
        table.add_column("路径")
        table.add_column("大小", justify="right", style="red")
        table.add_column("修改时间", style="dim")
        for i, f in enumerate(large_files[:20], 1):
            mtime = datetime.fromtimestamp(f["server_mtime"]).strftime("%Y-%m-%d") if f["server_mtime"] else "未知"
            table.add_row(str(i), _truncate(f["path"], 70), _format_size(f["size"]), mtime)
        console.print(table)

    # 过期文件
    expired = report.get("expired", [])
    if expired:
        table = Table(title="\n过期文件（部分展示）")
        table.add_column("路径")
        table.add_column("大小", justify="right")
        table.add_column("最后修改", style="dim")
        for f in expired[:20]:
            mtime = datetime.fromtimestamp(f["server_mtime"]).strftime("%Y-%m-%d") if f["server_mtime"] else "未知"
            table.add_row(_truncate(f["path"], 70), _format_size(f["size"]), mtime)
        if len(expired) > 20:
            table.add_row(f"... 还有 {len(expired) - 20} 个", "", "")
        console.print(table)


def execute_clean(api: BaiduPanAPI, report: dict, config: dict):
    """执行清理操作"""
    keep_policy = config.get("clean", {}).get("duplicate_keep", "keep_shortest_path")
    to_delete = []

    # 收集重复文件中需要删除的
    for md5, files in report.get("duplicates", {}).items():
        if keep_policy == "keep_shortest_path":
            keep_idx = min(range(len(files)), key=lambda j: len(files[j]["path"]))
        else:
            keep_idx = 0
        for j, f in enumerate(files):
            if j != keep_idx:
                to_delete.append(f["path"])

    # 收集空目录
    for d in report.get("empty_dirs", []):
        to_delete.append(d["path"])

    if not to_delete:
        console.print("[green]没有需要清理的内容。[/green]")
        return

    console.print(f"\n[bold]即将删除 {len(to_delete)} 个文件/目录[/bold]")
    console.print("[yellow]注意：大文件和过期文件需要手动选择删除，此处仅自动清理重复文件和空目录。[/yellow]")

    confirm = input(f"\n确认删除？此操作不可逆！(输入 YES 确认): ").strip()
    if confirm != "YES":
        console.print("[yellow]已取消。[/yellow]")
        return

    console.print("\n[bold]开始清理...[/bold]")
    batch_size = 100
    success = 0
    failed = 0

    for i in range(0, len(to_delete), batch_size):
        batch = to_delete[i:i + batch_size]
        try:
            api.delete(batch)
            success += len(batch)
            console.print(f"  进度: {success}/{len(to_delete)}")
        except Exception as e:
            console.print(f"  [red]批量删除失败: {e}[/red]")
            for path in batch:
                try:
                    api.delete([path])
                    success += 1
                except Exception as e2:
                    failed += 1
                    console.print(f"  [red]删除失败 {path}: {e2}[/red]")

    # 更新本地索引
    deleted_paths = to_delete[:success]
    if deleted_paths:
        delete_records(deleted_paths)

    console.print(f"\n[bold green]清理完成！成功 {success}，失败 {failed}[/bold green]")


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else "..." + s[-(max_len - 3):]
