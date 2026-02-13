"""自动整理归档模块"""

from datetime import datetime
from pathlib import PurePosixPath

from rich.console import Console
from rich.table import Table

from api import BaiduPanAPI
from db import get_all_files

console = Console()


def _is_under_dir(path: str, dir_path: str) -> bool:
    """检查 path 是否在 dir_path 目录下（路径组件精确匹配）"""
    return path == dir_path or path.startswith(dir_path.rstrip("/") + "/")


def organize(api: BaiduPanAPI, config: dict, dry_run: bool = False):
    """执行自动整理"""
    org_config = config.get("organize", {})
    if not org_config.get("enabled", True):
        console.print("[yellow]自动整理已禁用，请在 config.yaml 中启用[/yellow]")
        return

    # 如果启用了分类体系，使用 classifier 替代原有规则
    if org_config.get("use_taxonomy", False):
        from classifier import classify_all, save_classification_results
        console.print("[bold]使用知识分类体系进行整理...[/bold]")
        results = classify_all(config)
        if results:
            save_classification_results(results)
            console.print(f"已生成 {len(results)} 条分类建议，请使用 migrate 命令执行迁移")
        else:
            console.print("[green]无需整理[/green]")
        return

    source_dir = org_config.get("source_dir", "/")
    exclude_dirs = set(org_config.get("exclude_dirs", []))
    type_rules = org_config.get("type_rules", {})
    date_rules = org_config.get("date_rules", [])
    keyword_rules = org_config.get("keyword_rules", [])

    files = get_all_files(include_dirs=False)
    if not files:
        console.print("[yellow]索引为空，请先运行: python manager.py scan[/yellow]")
        return

    # 收集所有移动操作
    moves = []  # [(原路径, 目标目录, 新文件名)]

    for f in files:
        path = f["path"]

        # 检查是否在源目录下
        if not path.startswith(source_dir):
            continue

        # 检查是否在排除目录中
        if any(_is_under_dir(path, ex) for ex in exclude_dirs):
            continue

        filename = f["filename"]
        ext = f["extension"].lower()

        # 1. 日期规则（优先级最高）
        matched = False
        for dr in date_rules:
            rule_source = dr.get("source_dir", "")
            rule_exts = [e.lower() for e in dr.get("extensions", [])]
            target_pattern = dr.get("target_pattern", "")

            if rule_source and path.startswith(rule_source) and ext in rule_exts:
                mtime = f.get("server_mtime", 0)
                if mtime > 0:
                    dt = datetime.fromtimestamp(mtime)
                    target_dir = target_pattern.format(
                        year=dt.strftime("%Y"),
                        month=dt.strftime("%m"),
                        day=dt.strftime("%d"),
                    )
                    current_dir = str(PurePosixPath(path).parent)
                    if current_dir != target_dir:
                        moves.append((path, target_dir, filename))
                        matched = True
                break

        if matched:
            continue

        # 2. 关键词规则
        for kr in keyword_rules:
            keyword = kr.get("keyword", "")
            target = kr.get("target", "")
            if keyword and keyword in filename:
                current_dir = str(PurePosixPath(path).parent)
                if current_dir != target:
                    moves.append((path, target, filename))
                    matched = True
                break

        if matched:
            continue

        # 3. 文件类型规则
        for category_name, rule in type_rules.items():
            rule_exts = [e.lower() for e in rule.get("extensions", [])]
            target = rule.get("target", "")
            if ext in rule_exts:
                current_dir = str(PurePosixPath(path).parent)
                if current_dir != target:
                    moves.append((path, target, filename))
                break

    if not moves:
        console.print("[green]所有文件已整理完毕，无需移动。[/green]")
        return

    # 显示整理计划
    table = Table(title=f"整理计划（共 {len(moves)} 个文件）")
    table.add_column("原路径", style="dim")
    table.add_column("目标目录", style="green")

    display_limit = 50
    for src, dest, name in moves[:display_limit]:
        table.add_row(_truncate(src, 60), dest)
    if len(moves) > display_limit:
        table.add_row(f"... 还有 {len(moves) - display_limit} 个文件", "")

    console.print(table)

    if dry_run:
        console.print(f"\n[yellow]试运行模式，不会实际移动文件。[/yellow]")
        return

    # 确认执行
    confirm = input(f"\n确认移动 {len(moves)} 个文件？(y/N): ").strip().lower()
    if confirm != "y":
        console.print("[yellow]已取消。[/yellow]")
        return

    # 批量执行移动
    console.print("\n[bold]开始整理...[/bold]")
    created_dirs = set()
    success = 0
    failed = 0

    # 按批次处理（百度 API 单次最多操作约 100 个文件）
    batch_size = 50
    for i in range(0, len(moves), batch_size):
        batch = moves[i:i + batch_size]

        # 确保目标目录存在
        for _, dest, _ in batch:
            if dest not in created_dirs:
                try:
                    api.mkdir(dest)
                    created_dirs.add(dest)
                except Exception:
                    created_dirs.add(dest)  # 可能已存在

        # 构建移动请求
        file_list = [
            {"path": src, "dest": dest, "newname": name}
            for src, dest, name in batch
        ]

        try:
            api.move(file_list)
            success += len(batch)
            console.print(f"  进度: {success}/{len(moves)}")
        except Exception as e:
            console.print(f"  [red]批量移动失败: {e}[/red]")
            # 降级为逐个移动
            for src, dest, name in batch:
                try:
                    api.move([{"path": src, "dest": dest, "newname": name}])
                    success += 1
                except Exception as e2:
                    failed += 1
                    console.print(f"  [red]移动失败 {src}: {e2}[/red]")

    console.print(f"\n[bold green]整理完成！成功 {success} 个，失败 {failed} 个。[/bold green]")
    if success > 0:
        console.print("[yellow]提示：请重新运行 scan 更新索引。[/yellow]")


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else "..." + s[-(max_len - 3):]
