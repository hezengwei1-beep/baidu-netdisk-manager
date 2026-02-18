"""四阶段迁移执行器"""

import uuid
import time

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

from pathlib import PurePosixPath

from api import BaiduPanAPI
from db import (
    get_classifications, update_classification_status,
    log_migration, get_all_files, find_empty_dirs, delete_records,
)
from taxonomy import load_taxonomy

console = Console()


def generate_plan(config: dict):
    """生成迁移计划摘要"""
    classifications = get_classifications(status="pending")
    if not classifications:
        console.print("[yellow]无待迁移内容，请先运行 classify 生成分类结果[/yellow]")
        return

    high = [c for c in classifications if c["confidence"] >= 0.9]
    medium = [c for c in classifications if 0.5 <= c["confidence"] < 0.9]
    low = [c for c in classifications if c["confidence"] < 0.5]

    console.print("\n[bold]迁移计划摘要[/bold]")
    console.print(f"\n[bold green]阶段1[/bold green] - 创建目录结构")
    target_dirs = set()
    for c in classifications:
        target_dirs.add(c["target_path"])
    console.print(f"  需创建 {len(target_dirs)} 个目标目录")

    console.print(f"\n[bold green]阶段2[/bold green] - 高置信度迁移（自动）")
    console.print(f"  {len(high)} 个目录，"
                  f"{sum(c['file_count'] for c in high)} 个文件，"
                  f"{_fmt_size(sum(c['total_size'] for c in high))}")

    console.print(f"\n[bold yellow]阶段3[/bold yellow] - 交互审核")
    console.print(f"  中置信度: {len(medium)} 个目录，"
                  f"{_fmt_size(sum(c['total_size'] for c in medium))}")
    console.print(f"  低置信度: {len(low)} 个目录，"
                  f"{_fmt_size(sum(c['total_size'] for c in low))}")

    console.print(f"\n[bold dim]阶段4[/bold dim] - 清理空目录")

    # 显示详细清单
    if high:
        table = Table(title="\n阶段2 高置信度迁移清单")
        table.add_column("源目录", style="dim", max_width=40)
        table.add_column("目标", style="green", max_width=35)
        table.add_column("置信度", justify="right")
        table.add_column("文件数", justify="right")
        table.add_column("大小", justify="right")

        for c in sorted(high, key=lambda x: x["total_size"], reverse=True):
            table.add_row(
                _truncate(c["source_path"], 40),
                _truncate(c["target_path"], 35),
                f"{c['confidence']:.2f}",
                str(c["file_count"]),
                _fmt_size(c["total_size"]),
            )
        console.print(table)


def execute_phase(api: BaiduPanAPI, config: dict, phase: int, dry_run: bool = False):
    """执行指定阶段"""
    if phase == 1:
        _phase1_create_dirs(api, config, dry_run)
    elif phase == 2:
        _phase2_move_high_confidence(api, config, dry_run)
    elif phase == 3:
        _phase3_interactive_review(api, config, dry_run)
    elif phase == 4:
        _phase4_cleanup(api, config, dry_run)
    else:
        console.print(f"[red]无效阶段: {phase}，有效范围 1-4[/red]")


def _phase1_create_dirs(api: BaiduPanAPI, config: dict, dry_run: bool):
    """阶段1：创建目标目录结构"""
    taxonomy = load_taxonomy(config)
    all_paths = taxonomy.all_paths()

    console.print(f"\n[bold]阶段1：创建目标目录结构[/bold]")
    console.print(f"  共 {len(all_paths)} 个目录")

    if dry_run:
        for p in sorted(all_paths):
            console.print(f"  [dim]mkdir {p}[/dim]")
        console.print(f"\n[yellow]试运行模式，未实际创建[/yellow]")
        return

    batch_id = str(uuid.uuid4())[:8]
    success = 0
    failed = 0

    for p in sorted(all_paths):
        try:
            api.mkdir(p)
            log_migration(batch_id, 1, "", p, "success")
            success += 1
        except Exception as e:
            err_msg = str(e)
            if "already exist" in err_msg.lower() or "31061" in err_msg:
                success += 1  # 目录已存在算成功
                log_migration(batch_id, 1, "", p, "exists")
            else:
                failed += 1
                log_migration(batch_id, 1, "", p, "failed", err_msg)
                console.print(f"  [red]创建失败 {p}: {e}[/red]")

    console.print(f"\n[bold green]阶段1完成：成功 {success}，失败 {failed}[/bold green]")


def _phase2_move_high_confidence(api: BaiduPanAPI, config: dict, dry_run: bool):
    """阶段2：移动高置信度内容"""
    threshold = config.get("classifier", {}).get("high_confidence_threshold", 0.9)
    classifications = get_classifications(status="pending", min_confidence=threshold)

    if not classifications:
        console.print("[yellow]无高置信度待迁移内容[/yellow]")
        return

    console.print(f"\n[bold]阶段2：高置信度迁移（≥{threshold}）[/bold]")
    console.print(f"  共 {len(classifications)} 个目录待移动")

    if dry_run:
        table = Table(title="预览：高置信度迁移")
        table.add_column("源目录", style="dim", max_width=45)
        table.add_column("目标", style="green", max_width=35)
        table.add_column("文件数", justify="right")
        table.add_column("大小", justify="right")

        for c in sorted(classifications, key=lambda x: x["total_size"], reverse=True):
            table.add_row(
                _truncate(c["source_path"], 45),
                _truncate(c["target_path"], 35),
                str(c["file_count"]),
                _fmt_size(c["total_size"]),
            )
        console.print(table)
        console.print(f"\n[yellow]试运行模式，未实际移动[/yellow]")
        return

    if not Confirm.ask(f"确认移动 {len(classifications)} 个目录？"):
        console.print("[yellow]已取消[/yellow]")
        return

    batch_id = str(uuid.uuid4())[:8]
    batch_size = config.get("migration", {}).get("batch_size", 50)

    success = 0
    failed = 0

    for c in classifications:
        source = c["source_path"]
        target = c["target_path"]
        req = _build_move_request(source, target)
        final_path = req["dest"].rstrip("/") + "/" + req["newname"]

        try:
            api.move([req])
            update_classification_status(source, "migrated")
            log_migration(batch_id, 2, source, final_path, "success")
            success += 1
            console.print(f"  [green]✓[/green] {source} → {final_path}")
        except Exception as e:
            err_msg = str(e)
            log_migration(batch_id, 2, source, final_path, "failed", err_msg)
            failed += 1
            console.print(f"  [red]✗ {source}: {e}[/red]")

    console.print(f"\n[bold green]阶段2完成：成功 {success}，失败 {failed}[/bold green]")
    if success > 0:
        console.print("[yellow]提示：迁移完成后请运行 scan 更新索引[/yellow]")


def _phase3_interactive_review(api: BaiduPanAPI, config: dict, dry_run: bool):
    """阶段3：交互审核中低置信度内容"""
    threshold = config.get("classifier", {}).get("high_confidence_threshold", 0.9)
    classifications = get_classifications(status="pending")
    # 过滤出低于高置信度阈值的
    to_review = [c for c in classifications if c["confidence"] < threshold]

    if not to_review:
        console.print("[yellow]无需审核的内容[/yellow]")
        return

    console.print(f"\n[bold]阶段3：交互审核（{len(to_review)} 个目录）[/bold]")

    if dry_run:
        for c in to_review:
            console.print(f"  {c['source_path']} → {c['target_path']} "
                          f"(置信度: {c['confidence']:.2f}, {c['reason']})")
        console.print(f"\n[yellow]试运行模式[/yellow]")
        return

    batch_id = str(uuid.uuid4())[:8]
    approved = 0
    rejected = 0
    skipped = 0

    for i, c in enumerate(to_review, 1):
        console.print(f"\n[bold]({i}/{len(to_review)})[/bold]")
        console.print(f"  源目录: [cyan]{c['source_path']}[/cyan]")
        console.print(f"  建议目标: [green]{c['target_path']}[/green]")
        console.print(f"  置信度: {c['confidence']:.2f} ({c['confidence_level']})")
        console.print(f"  规则: {c['rule_name']}")
        console.print(f"  原因: {c['reason']}")
        console.print(f"  文件数: {c['file_count']}，大小: {_fmt_size(c['total_size'])}")

        choice = Prompt.ask(
            "操作",
            choices=["y", "n", "s", "q"],
            default="s",
        )

        if choice == "y":
            # 执行移动
            source = c["source_path"]
            target = c["target_path"]
            req = _build_move_request(source, target)
            final_path = req["dest"].rstrip("/") + "/" + req["newname"]
            try:
                api.move([req])
                update_classification_status(source, "migrated")
                log_migration(batch_id, 3, source, final_path, "success")
                approved += 1
                console.print(f"  [green]已移动[/green]")
            except Exception as e:
                log_migration(batch_id, 3, source, final_path, "failed", str(e))
                console.print(f"  [red]移动失败: {e}[/red]")
        elif choice == "n":
            update_classification_status(c["source_path"], "rejected")
            rejected += 1
        elif choice == "q":
            console.print("[yellow]退出审核[/yellow]")
            break
        else:
            skipped += 1

    console.print(f"\n[bold]审核结果：[/bold] 通过 {approved}，拒绝 {rejected}，跳过 {skipped}")


def _phase4_cleanup(api: BaiduPanAPI, config: dict, dry_run: bool):
    """阶段4：清理空的旧目录"""
    empty_dirs = find_empty_dirs()

    # 过滤：只清理旧分类体系下的空目录
    old_prefixes = ["/A技能库", "/A身体库", "/A学科库", "/待整理", "/- 学习暂存"]
    to_clean = [
        d for d in empty_dirs
        if any(d["path"].startswith(p) for p in old_prefixes)
    ]

    if not to_clean:
        console.print("[green]没有需要清理的空目录[/green]")
        return

    console.print(f"\n[bold]阶段4：清理空目录[/bold]")
    console.print(f"  找到 {len(to_clean)} 个空目录")

    if dry_run:
        for d in to_clean[:30]:
            console.print(f"  [dim]rm {d['path']}[/dim]")
        if len(to_clean) > 30:
            console.print(f"  ... 还有 {len(to_clean) - 30} 个")
        console.print(f"\n[yellow]试运行模式[/yellow]")
        return

    if not Confirm.ask(f"确认删除 {len(to_clean)} 个空目录？"):
        console.print("[yellow]已取消[/yellow]")
        return

    batch_id = str(uuid.uuid4())[:8]
    paths = [d["path"] for d in to_clean]

    # 从深到浅删除（先删子目录）
    paths.sort(key=lambda p: p.count("/"), reverse=True)

    batch_size = 100
    success = 0
    for i in range(0, len(paths), batch_size):
        batch = paths[i:i + batch_size]
        try:
            api.delete(batch)
            delete_records(batch)
            success += len(batch)
            for p in batch:
                log_migration(batch_id, 4, p, "", "deleted")
        except Exception as e:
            # 逐个删除回退
            for p in batch:
                try:
                    api.delete([p])
                    delete_records([p])
                    success += 1
                    log_migration(batch_id, 4, p, "", "deleted")
                except Exception as e2:
                    log_migration(batch_id, 4, p, "", "failed", str(e2))
                    console.print(f"  [red]删除失败 {p}: {e2}[/red]")

    console.print(f"\n[bold green]阶段4完成：删除 {success} 个空目录[/bold green]")


def rollback(api: BaiduPanAPI, batch_id: str):
    """回滚指定批次的迁移操作"""
    from db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM migration_log WHERE batch_id=? AND status='success' AND phase IN (2,3) ORDER BY executed_at DESC",
        (batch_id,),
    ).fetchall()
    conn.close()

    if not rows:
        console.print(f"[yellow]未找到批次 {batch_id} 的可回滚记录[/yellow]")
        return

    console.print(f"[bold]回滚批次 {batch_id}：共 {len(rows)} 个操作[/bold]")

    if not Confirm.ask("确认回滚？"):
        console.print("[yellow]已取消[/yellow]")
        return

    success = 0
    for row in rows:
        target = dict(row)["target_path"]
        source_dir = dict(row)["source_path"].rsplit("/", 1)[0] or "/"
        dir_name = dict(row)["source_path"].rsplit("/", 1)[-1]
        try:
            api.move([{"path": target, "dest": source_dir, "newname": dir_name}])
            success += 1
            console.print(f"  [green]✓[/green] {target} → {dict(row)['source_path']}")
        except Exception as e:
            console.print(f"  [red]✗ 回滚失败 {target}: {e}[/red]")

    console.print(f"\n[bold]回滚完成：{success}/{len(rows)}[/bold]")


def rollback_all(api: BaiduPanAPI, dry_run: bool = False):
    """全量回滚：将所有已迁移的文件恢复到原始位置"""
    from db import get_connection, update_classification_status
    conn = get_connection()

    # 按时间倒序取出所有成功的移动记录（后执行的先回滚）
    rows = conn.execute(
        "SELECT * FROM migration_log WHERE status='success' AND phase IN (2,3) ORDER BY executed_at DESC"
    ).fetchall()
    conn.close()

    if not rows:
        console.print("[yellow]没有可回滚的迁移记录[/yellow]")
        return

    # 按批次分组统计
    batches = {}
    for r in rows:
        d = dict(r)
        bid = d["batch_id"]
        if bid not in batches:
            batches[bid] = []
        batches[bid].append(d)

    console.print(f"\n[bold]全量回滚概览[/bold]")
    console.print(f"  共 {len(rows)} 个操作，涉及 {len(batches)} 个批次\n")

    table = Table(title="回滚计划")
    table.add_column("批次 ID", style="cyan")
    table.add_column("操作数", justify="right")
    table.add_column("示例", style="dim", max_width=60)

    for bid, ops in batches.items():
        example = f"{ops[0]['target_path']} → {ops[0]['source_path']}"
        table.add_row(bid, str(len(ops)), _truncate(example, 60))
    console.print(table)

    if dry_run:
        console.print(f"\n[bold]详细回滚列表：[/bold]")
        for r in rows:
            d = dict(r)
            console.print(f"  [dim]{d['target_path']}[/dim] → [green]{d['source_path']}[/green]")
        console.print(f"\n[yellow]试运行模式，未实际执行[/yellow]")
        return

    if not Confirm.ask(f"确认回滚全部 {len(rows)} 个操作？此操作会把文件移回原始位置"):
        console.print("[yellow]已取消[/yellow]")
        return

    rollback_batch_id = "rb-" + str(uuid.uuid4())[:8]
    success = 0
    failed = 0

    for r in rows:
        d = dict(r)
        target = d["target_path"]
        source = d["source_path"]
        source_dir = source.rsplit("/", 1)[0] or "/"
        dir_name = source.rsplit("/", 1)[-1]

        try:
            api.move([{"path": target, "dest": source_dir, "newname": dir_name}])
            log_migration(rollback_batch_id, 0, target, source, "rollback")
            success += 1
            console.print(f"  [green]✓[/green] {_truncate(target, 45)} → {source}")
        except Exception as e:
            err_msg = str(e)
            # 如果目标已不存在（可能已手动移回），标记跳过
            if "31066" in err_msg or "not exist" in err_msg.lower():
                console.print(f"  [yellow]⊘ 跳过（已不存在）: {target}[/yellow]")
                log_migration(rollback_batch_id, 0, target, source, "skipped", err_msg)
            else:
                log_migration(rollback_batch_id, 0, target, source, "failed", err_msg)
                failed += 1
                console.print(f"  [red]✗ {target}: {e}[/red]")

    # 回滚成功后，把分类状态重置为 pending
    if success > 0:
        conn = get_connection()
        conn.execute("UPDATE classifications SET status='pending' WHERE status='migrated'")
        conn.commit()
        conn.close()

    console.print(f"\n[bold green]全量回滚完成：成功 {success}，失败 {failed}，共 {len(rows)} 个[/bold green]")
    if success > 0:
        console.print("[yellow]提示：回滚完成后建议运行 scan 更新索引[/yellow]")


def _build_move_request(source_path: str, target_path: str) -> dict:
    """构建正确的百度 API 移动请求。

    百度 move API 语义：将 path 移入 dest 目录，重命名为 newname。
    - 如果 target_path 已包含源目录名（前缀匹配生成），用 parent 作 dest
    - 如果 target_path 是分类目录（直接映射），保留源目录名
    """
    src_name = PurePosixPath(source_path).name
    target_name = PurePosixPath(target_path).name

    if target_name == src_name:
        # 前缀匹配：target 已含源目录名，拆分为 parent + basename
        dest = str(PurePosixPath(target_path).parent)
        newname = target_name
    else:
        # 直接映射：target 是分类目录，源目录移入其中保留原名
        dest = target_path
        newname = src_name

    return {"path": source_path, "dest": dest, "newname": newname}


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else "..." + s[-(max_len - 3):]
