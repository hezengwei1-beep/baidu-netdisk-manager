#!/usr/bin/env python3
"""百度网盘专家级管理工具 - 主入口"""

import time
from datetime import datetime

import click
from rich.console import Console
from rich.panel import Panel

from auth import load_config, do_auth, ensure_token
from api import BaiduPanAPI
from db import init_db, batch_upsert, get_stats, log_scan

console = Console()


def _is_under_dir(path: str, dir_path: str) -> bool:
    return path == dir_path or path.startswith(dir_path.rstrip("/") + "/")


def get_api(config: dict) -> BaiduPanAPI:
    """获取已认证的 API 客户端"""
    token = ensure_token(config)
    return BaiduPanAPI(token)


@click.group()
def cli():
    """百度网盘专家级管理工具"""
    init_db()


@cli.command()
def auth():
    """授权登录百度网盘"""
    config = load_config()
    do_auth(config)


@cli.command()
@click.option("--path", default=None, help="扫描目录（默认使用 config.yaml 中的配置）")
def scan(path):
    """扫描网盘建立文件索引"""
    config = load_config()
    api = get_api(config)

    scan_config = config.get("scan", {})
    scan_dir = path or scan_config.get("root_dir", "/")
    exclude_dirs = set(scan_config.get("exclude_dirs", []))

    console.print(f"[bold]开始扫描: {scan_dir}[/bold]")
    started_at = int(time.time())

    def on_batch(total, current_dir):
        console.print(f"  已扫描 {total} 个文件/目录，当前: {current_dir[:60]}")

    try:
        # 先尝试递归列表（快），如果结果不完整则用逐目录遍历
        files = api.list_all(scan_dir, recursion=1)
        if not files:
            raise RuntimeError("list_all 返回空，尝试逐目录遍历")
        # listall API 有分页上限（约 15000 条），如果刚好达到上限说明数据不完整
        if len(files) % 1000 == 0 and len(files) >= 10000:
            console.print(f"[yellow]递归列表返回 {len(files)} 条，可能不完整，切换到逐目录遍历...[/yellow]")
            raise RuntimeError("结果可能不完整")
    except Exception:
        console.print("[yellow]切换到逐目录遍历模式...[/yellow]")
        try:
            files = api.walk_dir(scan_dir, on_batch=on_batch)
        except Exception as e:
            console.print(f"[red]扫描失败: {e}[/red]")
            return

    # 过滤排除目录
    filtered = [f for f in files if not any(_is_under_dir(f["path"], ex) for ex in exclude_dirs)]

    console.print(f"获取到 {len(filtered)} 个文件/目录（已排除 {len(files) - len(filtered)} 个）")

    # 分批获取文件详细元信息（含 MD5）
    file_items = [f for f in filtered if not f.get("isdir", 0)]
    dir_items = [f for f in filtered if f.get("isdir", 0)]

    console.print(f"其中文件 {len(file_items)} 个，目录 {len(dir_items)} 个")
    console.print("[bold]获取文件详细信息（含 MD5）...[/bold]")

    batch_size = 100
    detailed_files = []
    for i in range(0, len(file_items), batch_size):
        batch = file_items[i:i + batch_size]
        fsids = [f["fs_id"] for f in batch]
        try:
            metas = api.file_meta(fsids)
            detailed_files.extend(metas)
        except Exception as e:
            console.print(f"  [yellow]获取元信息失败（批次 {i // batch_size + 1}）: {e}[/yellow]")
            detailed_files.extend(batch)  # 回退使用基本信息

        progress = min(i + batch_size, len(file_items))
        console.print(f"  进度: {progress}/{len(file_items)}")

    # 保存到数据库
    console.print("[bold]保存索引...[/bold]")
    all_items = detailed_files + dir_items
    batch_upsert(all_items)

    finished_at = int(time.time())
    log_scan(scan_dir, len(all_items), started_at, finished_at)

    elapsed = finished_at - started_at
    console.print(f"\n[bold green]扫描完成！[/bold green]")
    console.print(f"  索引文件: {len(detailed_files)} 个")
    console.print(f"  索引目录: {len(dir_items)} 个")
    console.print(f"  耗时: {elapsed} 秒")


@cli.command()
@click.option("--dry-run", is_flag=True, help="试运行，不实际移动文件")
def organize(dry_run):
    """自动整理归档文件"""
    config = load_config()
    api = get_api(config)

    from organizer import organize as do_organize
    do_organize(api, config, dry_run=dry_run)


@cli.command()
@click.option("--up", "direction", flag_value="up", help="本地→网盘上传备份")
@click.option("--down", "direction", flag_value="down", help="网盘→本地下载备份")
@click.option("--dry-run", is_flag=True, help="试运行")
def sync(direction, dry_run):
    """备份同步"""
    if not direction:
        console.print("[red]请指定同步方向: --up（上传） 或 --down（下载）[/red]")
        return

    config = load_config()
    api = get_api(config)

    from sync import sync_up, sync_down
    if direction == "up":
        sync_up(api, config, dry_run=dry_run)
    else:
        sync_down(api, config, dry_run=dry_run)


@cli.command()
@click.option("--report", is_flag=True, help="生成清理报告")
@click.option("--execute", is_flag=True, help="执行清理")
@click.option("--detail", is_flag=True, help="显示详细报告")
def clean(report, execute, detail):
    """空间清理"""
    config = load_config()

    from cleaner import generate_report, print_report_detail, execute_clean

    if not report and not execute:
        console.print("[red]请指定操作: --report（生成报告） 或 --execute（执行清理）[/red]")
        return

    rpt = generate_report(config)

    if detail or report:
        print_report_detail(rpt, config)

    if execute:
        api = get_api(config)
        execute_clean(api, rpt, config)


@cli.command()
def info():
    """空间概览"""
    config = load_config()
    api = get_api(config)

    # 用户信息
    try:
        uinfo = api.uinfo()
        username = uinfo.get("baidu_name", "未知")
        vip_type = {0: "普通用户", 1: "普通会员", 2: "超级会员"}.get(uinfo.get("vip_type", 0), "未知")
    except Exception:
        username = "未知"
        vip_type = "未知"

    # 空间用量
    try:
        quota = api.quota()
        total = quota.get("total", 0)
        used = quota.get("used", 0)
        free = total - used
        usage_pct = (used / total * 100) if total > 0 else 0
    except Exception as e:
        console.print(f"[red]获取空间信息失败: {e}[/red]")
        return

    # 本地索引统计
    stats = get_stats()

    console.print(Panel(
        f"[bold]用户:[/bold] {username} ({vip_type})\n\n"
        f"[bold]空间用量:[/bold]\n"
        f"  总容量:   {_format_size(total)}\n"
        f"  已用:     {_format_size(used)} ({usage_pct:.1f}%)\n"
        f"  剩余:     {_format_size(free)}\n\n"
        f"[bold]本地索引:[/bold]\n"
        f"  文件数:   {stats['total_files']}\n"
        f"  目录数:   {stats['total_dirs']}\n"
        f"  索引大小: {_format_size(stats['total_size'])}\n"
        f"  最后扫描: {_format_time(stats['last_scan'])}",
        title="百度网盘空间概览",
        border_style="blue",
    ))


@cli.command()
@click.option("--show", is_flag=True, help="显示分类体系树")
def taxonomy(show):
    """知识分类体系"""
    if not show:
        console.print("[red]请指定操作: --show（显示分类树）[/red]")
        return

    config = load_config()
    from taxonomy import load_taxonomy, print_taxonomy_tree
    tx = load_taxonomy(config)

    errors = tx.validate()
    if errors:
        for e in errors:
            console.print(f"[red]验证错误: {e}[/red]")
        return

    print_taxonomy_tree(tx)
    console.print(f"\n  共 {len(tx.all_paths())} 个分类节点，"
                  f"{len(tx.all_leaf_paths())} 个叶子节点")


@cli.command()
@click.option("--detail", is_flag=True, help="显示详细分类报告")
def classify(detail):
    """生成文件分类报告"""
    config = load_config()

    from classifier import classify_all, print_classification_report, save_classification_results

    console.print("[bold]正在分析文件分类...[/bold]")
    results = classify_all(config)

    if not results:
        console.print("[yellow]无分类结果[/yellow]")
        return

    print_classification_report(results, detail=detail)
    save_classification_results(results)


@cli.command()
@click.option("--plan", is_flag=True, help="生成迁移计划")
@click.option("--execute", "phase", type=int, default=None, help="执行指定阶段 (1-4)")
@click.option("--dry-run", is_flag=True, help="试运行")
@click.option("--rollback", "rollback_id", default=None, help="回滚指定批次")
def migrate(plan, phase, dry_run, rollback_id):
    """知识库迁移"""
    config = load_config()

    from migration import generate_plan, execute_phase, rollback as do_rollback

    if rollback_id:
        api = get_api(config)
        do_rollback(api, rollback_id)
        return

    if plan:
        generate_plan(config)
        return

    if phase is not None:
        api = get_api(config)
        execute_phase(api, config, phase, dry_run=dry_run)
        return

    console.print("[red]请指定操作: --plan / --execute <阶段> / --rollback <批次ID>[/red]")


@cli.command()
@click.option("--report", is_flag=True, help="生成去重报告")
@click.option("--execute-safe", is_flag=True, help="执行安全去重")
def dedup(report, execute_safe):
    """重复文件管理"""
    config = load_config()

    from dedup import generate_dedup_report, print_dedup_report, execute_safe_dedup

    if not report and not execute_safe:
        console.print("[red]请指定操作: --report（报告） 或 --execute-safe（安全去重）[/red]")
        return

    rpt = generate_dedup_report(config)

    if report:
        print_dedup_report(rpt)

    if execute_safe:
        api = get_api(config)
        execute_safe_dedup(api, rpt)


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _format_time(ts: int) -> str:
    if ts == 0:
        return "从未扫描"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    cli()
