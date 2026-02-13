"""保守去重模块"""

from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm

from api import BaiduPanAPI
from db import find_duplicates, delete_records
from taxonomy import load_taxonomy

console = Console()


def generate_dedup_report(config: dict) -> dict:
    """生成去重报告，分三类"""
    taxonomy = load_taxonomy(config)
    taxonomy_paths = taxonomy.all_paths()
    exclude_dirs = set(config.get("dedup", {}).get("exclude_dirs", []))

    duplicates = find_duplicates()
    if not duplicates:
        console.print("[green]未发现重复文件[/green]")
        return {"safe": [], "review": [], "manual": []}

    safe = []     # 跨顶级分类重复 → 安全删除
    review = []   # 同分类内重复 → 需确认
    manual = []   # 同课程目录内重复 → 不自动处理

    for md5, files in duplicates.items():
        # 过滤排除目录
        files = [f for f in files if not any(
            f["path"] == ex or f["path"].startswith(ex.rstrip("/") + "/")
            for ex in exclude_dirs
        )]
        if len(files) < 2:
            continue

        # 判断重复类型
        top_dirs = set()
        common_prefix_len = _common_prefix_depth(files)

        for f in files:
            parts = f["path"].strip("/").split("/")
            if parts:
                top_dirs.add("/" + parts[0])

        if len(top_dirs) > 1:
            # 跨顶级目录 → safe
            keep = _pick_best(files, taxonomy_paths)
            to_delete = [f for f in files if f["path"] != keep["path"]]
            safe.append({
                "md5": md5,
                "size": files[0]["size"],
                "keep": keep,
                "delete": to_delete,
                "files": files,
            })
        elif common_prefix_len >= 3:
            # 在同一课程目录树深处 → manual
            manual.append({
                "md5": md5,
                "size": files[0]["size"],
                "files": files,
            })
        else:
            # 同分类但不同子目录 → review
            keep = _pick_best(files, taxonomy_paths)
            to_delete = [f for f in files if f["path"] != keep["path"]]
            review.append({
                "md5": md5,
                "size": files[0]["size"],
                "keep": keep,
                "delete": to_delete,
                "files": files,
            })

    return {"safe": safe, "review": review, "manual": manual}


def print_dedup_report(report: dict):
    """打印去重报告"""
    safe = report["safe"]
    review = report["review"]
    manual = report["manual"]

    total_groups = len(safe) + len(review) + len(manual)
    if total_groups == 0:
        console.print("[green]未发现重复文件[/green]")
        return

    safe_save = sum(sum(f["size"] for f in g["delete"]) for g in safe)
    review_save = sum(sum(f["size"] for f in g["delete"]) for g in review)
    manual_count = sum(len(g["files"]) for g in manual)

    console.print(f"\n[bold]去重报告[/bold]")
    console.print(f"  总重复组: {total_groups}")
    console.print(f"\n  [green]safe（安全删除）[/green]: {len(safe)} 组，"
                  f"可释放 {_fmt_size(safe_save)}")
    console.print(f"  [yellow]review（需确认）[/yellow]: {len(review)} 组，"
                  f"可释放 {_fmt_size(review_save)}")
    console.print(f"  [dim]manual（不自动处理）[/dim]: {len(manual)} 组，"
                  f"{manual_count} 个文件")

    # safe 详情（前20组）
    if safe:
        table = Table(title=f"\nsafe 去重详情（前 20 组 / 共 {len(safe)} 组）")
        table.add_column("保留", style="green", max_width=50)
        table.add_column("删除数", justify="right")
        table.add_column("单文件大小", justify="right")
        table.add_column("可释放", justify="right")

        for g in sorted(safe, key=lambda x: x["size"] * len(x["delete"]), reverse=True)[:20]:
            table.add_row(
                _truncate(g["keep"]["path"], 50),
                str(len(g["delete"])),
                _fmt_size(g["size"]),
                _fmt_size(g["size"] * len(g["delete"])),
            )
        console.print(table)

    # review 详情（前10组）
    if review:
        table = Table(title=f"\nreview 去重详情（前 10 组 / 共 {len(review)} 组）")
        table.add_column("保留建议", style="yellow", max_width=50)
        table.add_column("删除数", justify="right")
        table.add_column("可释放", justify="right")

        for g in sorted(review, key=lambda x: x["size"] * len(x["delete"]), reverse=True)[:10]:
            table.add_row(
                _truncate(g["keep"]["path"], 50),
                str(len(g["delete"])),
                _fmt_size(g["size"] * len(g["delete"])),
            )
        console.print(table)


def execute_safe_dedup(api: BaiduPanAPI, report: dict):
    """执行安全去重（仅删除 safe 组）"""
    safe = report.get("safe", [])
    if not safe:
        console.print("[green]无安全可删除的重复文件[/green]")
        return

    total_delete = sum(len(g["delete"]) for g in safe)
    total_save = sum(g["size"] * len(g["delete"]) for g in safe)

    console.print(f"\n[bold]安全去重[/bold]")
    console.print(f"  将删除 {total_delete} 个重复文件")
    console.print(f"  预计释放 {_fmt_size(total_save)}")

    if not Confirm.ask("确认执行安全去重？"):
        console.print("[yellow]已取消[/yellow]")
        return

    success = 0
    failed = 0
    batch_size = 100

    all_delete_paths = []
    for g in safe:
        for f in g["delete"]:
            all_delete_paths.append(f["path"])

    for i in range(0, len(all_delete_paths), batch_size):
        batch = all_delete_paths[i:i + batch_size]
        try:
            api.delete(batch)
            delete_records(batch)
            success += len(batch)
            console.print(f"  进度: {success}/{total_delete}")
        except Exception:
            for p in batch:
                try:
                    api.delete([p])
                    delete_records([p])
                    success += 1
                except Exception as e:
                    failed += 1
                    console.print(f"  [red]删除失败 {_truncate(p, 50)}: {e}[/red]")

    console.print(f"\n[bold green]去重完成：删除 {success}，失败 {failed}，"
                  f"释放约 {_fmt_size(total_save)}[/bold green]")


def _pick_best(files: list[dict], taxonomy_paths: list[str]) -> dict:
    """选择最佳保留文件"""
    scored = []
    for f in files:
        score = 0
        path = f["path"]
        # 优先保留在正确分类位置的文件
        for tp in taxonomy_paths:
            if path.startswith(tp):
                score += 100
                break
        # 其次保留最短路径
        score -= len(path)
        # 再次考虑最新时间戳
        score += f.get("server_mtime", 0) / 1e10
        scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _common_prefix_depth(files: list[dict]) -> int:
    """计算文件路径的公共前缀深度"""
    if not files:
        return 0
    parts_list = [f["path"].strip("/").split("/") for f in files]
    min_len = min(len(p) for p in parts_list)
    depth = 0
    for i in range(min_len):
        if len(set(p[i] for p in parts_list)) == 1:
            depth += 1
        else:
            break
    return depth


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else "..." + s[-(max_len - 3):]
