"""文件分类引擎 — 三级规则分类"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from db import get_all_files, save_classifications
from taxonomy import Taxonomy, load_taxonomy

console = Console()


@dataclass
class ClassificationResult:
    """分类结果"""
    source_path: str
    target_path: str
    confidence: float
    rule_name: str  # directory_mapping / keyword_match / content_analysis
    reason: str
    alternatives: list[dict] = field(default_factory=list)  # [{target_path, confidence, reason}]
    file_count: int = 0
    total_size: int = 0

    @property
    def confidence_level(self) -> str:
        if self.confidence >= 0.9:
            return "high"
        elif self.confidence >= 0.5:
            return "medium"
        else:
            return "low"


def classify_all(config: dict) -> list[ClassificationResult]:
    """对所有目录执行分类"""
    taxonomy = load_taxonomy(config)
    classifier_config = config.get("classifier", {})
    directory_mappings = classifier_config.get("directory_mappings", {})
    frozen_dirs = set(config.get("migration", {}).get("frozen_dirs", []))

    console.print("  加载文件索引...")
    all_files = get_all_files(include_dirs=False)
    if not all_files:
        console.print("[yellow]未找到需要分类的目录，请先运行 scan[/yellow]")
        return []

    console.print(f"  共 {len(all_files)} 个文件，正在聚合目录统计...")

    # 收集有精确映射的一级目录，这些目录整体移动而非拆分到二级
    mapped_top_dirs = set()
    for mapped_src in directory_mappings:
        top = "/" + mapped_src.strip("/").split("/")[0]
        mapped_top_dirs.add(top)

    # 一次遍历，同时聚合目录统计和扩展名分布
    dir_stats, dir_extensions = _aggregate_dir_stats(all_files, mapped_top_dirs)

    console.print(f"  发现 {len(dir_stats)} 个待分类目录")

    results = []
    for src_dir, stats in dir_stats.items():
        # 跳过冻结目录
        if any(src_dir == f or src_dir.startswith(f.rstrip("/") + "/") for f in frozen_dirs):
            continue

        # 跳过已在目标分类体系中的目录
        if taxonomy.find_node(src_dir):
            continue

        ext_counter = dir_extensions.get(src_dir, Counter())
        result = _classify_directory(src_dir, stats, directory_mappings, taxonomy, ext_counter)
        if result:
            results.append(result)

    return results


def _aggregate_dir_stats(all_files: list[dict], mapped_top_dirs: set) -> tuple[dict, dict]:
    """一次遍历聚合目录统计 + 扩展名分布

    对于有精确目录映射的一级目录（如 /来自：iPhone, /照片存档, /A身体库 等），
    聚合到二级目录级别（课程级别）。
    对于没有映射的一级目录（如零散文件），聚合到一级目录。
    """
    dir_stats = defaultdict(lambda: {"file_count": 0, "total_size": 0})
    dir_extensions = defaultdict(Counter)
    top_dir_stats = defaultdict(lambda: {"file_count": 0, "total_size": 0})

    for f in all_files:
        path = f["path"]
        size = f.get("size", 0)
        ext = f.get("extension", "").lower()
        parts = path.strip("/").split("/")

        if not parts:
            continue

        top_key = "/" + parts[0]

        # 一级目录统计（始终收集）
        top_dir_stats[top_key]["file_count"] += 1
        top_dir_stats[top_key]["total_size"] += size
        if ext:
            dir_extensions[top_key][ext] += 1

        # 如果在已映射的顶级目录下且有子目录结构，聚合到二级
        if len(parts) >= 3 and top_key in mapped_top_dirs:
            key = "/" + "/".join(parts[:2])
            dir_stats[key]["file_count"] += 1
            dir_stats[key]["total_size"] += size
            if ext:
                dir_extensions[key][ext] += 1

    # 合并策略：
    # - 有二级子目录的映射目录：用二级目录统计（一级已包含在 top_dir_stats 中不额外添加）
    # - 没有二级子目录或非映射目录：用一级目录统计
    result_stats = dict(dir_stats)
    for k, v in top_dir_stats.items():
        # 一级目录：如果它有精确映射，也加入（用于整体移动）
        # 或者它没有任何二级目录条目
        has_sub = any(sk.startswith(k + "/") for sk in dir_stats)
        if not has_sub:
            result_stats[k] = dict(v)

    return result_stats, dict(dir_extensions)


def _classify_directory(
    source_path: str,
    stats: dict,
    directory_mappings: dict,
    taxonomy: Taxonomy,
    ext_counter: Counter,
) -> ClassificationResult | None:
    """对单个目录执行分类"""

    # 规则1：目录精确映射（confidence 0.95）
    result = _rule_directory_mapping(source_path, stats, directory_mappings)
    if result:
        return result

    # 规则2：路径关键词匹配（confidence 0.5-0.85）
    result = _rule_keyword_match(source_path, stats, taxonomy)
    if result:
        return result

    # 规则3：内容分析（confidence 0.3-0.6）
    result = _rule_content_analysis(source_path, stats, ext_counter)
    if result:
        return result

    # 未匹配 → 待归类
    return ClassificationResult(
        source_path=source_path,
        target_path="/待归类",
        confidence=0.1,
        rule_name="unmatched",
        reason="未匹配任何规则",
        file_count=stats.get("file_count", 0),
        total_size=stats.get("total_size", 0),
    )


def _rule_directory_mapping(
    source_path: str,
    stats: dict,
    directory_mappings: dict,
) -> ClassificationResult | None:
    """规则1：目录精确映射"""
    # 精确匹配
    if source_path in directory_mappings:
        target = directory_mappings[source_path]
        return ClassificationResult(
            source_path=source_path,
            target_path=target,
            confidence=0.95,
            rule_name="directory_mapping",
            reason=f"精确映射: {source_path} → {target}",
            file_count=stats.get("file_count", 0),
            total_size=stats.get("total_size", 0),
        )

    # 前缀匹配：检查源目录的父目录是否有映射
    for mapped_src, mapped_target in directory_mappings.items():
        if source_path.startswith(mapped_src.rstrip("/") + "/"):
            # 保留子路径结构
            sub_path = source_path[len(mapped_src):]
            target = mapped_target + sub_path
            return ClassificationResult(
                source_path=source_path,
                target_path=target,
                confidence=0.90,
                rule_name="directory_mapping_prefix",
                reason=f"前缀映射: {mapped_src} → {mapped_target}",
                file_count=stats.get("file_count", 0),
                total_size=stats.get("total_size", 0),
            )

    return None


def _rule_keyword_match(
    source_path: str,
    stats: dict,
    taxonomy: Taxonomy,
) -> ClassificationResult | None:
    """规则2：路径关键词匹配"""
    dir_name = source_path.rstrip("/").rsplit("/", 1)[-1]
    path_text = source_path.lower()
    dir_text = dir_name.lower()

    best_score = 0.0
    best_target = ""
    best_reason = ""
    alternatives = []

    def _walk_nodes(nodes):
        nonlocal best_score, best_target, best_reason, alternatives
        for node in nodes:
            if node.frozen:
                continue
            score = 0.0
            matched_keywords = []

            for kw in node.keywords:
                kw_lower = kw.lower()
                if kw_lower == dir_text:
                    score += 0.4
                    matched_keywords.append(f"{kw}(精确)")
                elif kw_lower in dir_text:
                    score += 0.25
                    matched_keywords.append(f"{kw}(子串)")
                elif kw_lower in path_text:
                    score += 0.15
                    matched_keywords.append(f"{kw}(路径)")

            if len(matched_keywords) > 1:
                score += 0.1 * (len(matched_keywords) - 1)

            score = min(score, 0.85)

            if score > 0.3:
                reason = f"关键词: {', '.join(matched_keywords)}"
                if score > best_score:
                    if best_target:
                        alternatives.append({
                            "target_path": best_target,
                            "confidence": best_score,
                            "reason": best_reason,
                        })
                    best_score = score
                    best_target = node.path
                    best_reason = reason
                else:
                    alternatives.append({
                        "target_path": node.path,
                        "confidence": score,
                        "reason": reason,
                    })

            if node.children:
                _walk_nodes(node.children)

    _walk_nodes(taxonomy.roots)

    if best_score >= 0.3 and best_target:
        alternatives = sorted(alternatives, key=lambda x: x["confidence"], reverse=True)[:3]
        return ClassificationResult(
            source_path=source_path,
            target_path=best_target,
            confidence=best_score,
            rule_name="keyword_match",
            reason=best_reason,
            alternatives=alternatives,
            file_count=stats.get("file_count", 0),
            total_size=stats.get("total_size", 0),
        )

    return None


def _rule_content_analysis(
    source_path: str,
    stats: dict,
    ext_counter: Counter,
) -> ClassificationResult | None:
    """规则3：内容分析（根据预计算的扩展名分布推断类型）"""
    total_count = sum(ext_counter.values())
    if total_count == 0:
        return None

    top_exts = ext_counter.most_common(5)
    ext_ratios = {ext: count / total_count for ext, count in top_exts}

    # 摄影：大量 RAW/DNG/CR3
    photo_exts = {".cr3", ".cr2", ".dng", ".nef", ".arw", ".raw", ".raf"}
    photo_ratio = sum(ext_ratios.get(e, 0) for e in photo_exts)
    if photo_ratio > 0.5:
        return ClassificationResult(
            source_path=source_path,
            target_path="/摄影影像/摄影技巧",
            confidence=0.6,
            rule_name="content_analysis",
            reason=f"RAW文件占比 {photo_ratio:.0%}",
            file_count=stats.get("file_count", 0),
            total_size=stats.get("total_size", 0),
        )

    # 视频课程：大量 mp4 + pdf
    video_ratio = ext_ratios.get(".mp4", 0) + ext_ratios.get(".avi", 0) + ext_ratios.get(".mkv", 0)
    pdf_ratio = ext_ratios.get(".pdf", 0)
    if video_ratio > 0.5 and pdf_ratio > 0.05:
        return ClassificationResult(
            source_path=source_path,
            target_path="/待归类",
            confidence=0.4,
            rule_name="content_analysis",
            reason=f"视频课程特征(video:{video_ratio:.0%}, pdf:{pdf_ratio:.0%})",
            file_count=stats.get("file_count", 0),
            total_size=stats.get("total_size", 0),
        )

    # 音乐：大量音频文件
    audio_exts = {".mp3", ".flac", ".wav", ".aac", ".ape", ".m4a", ".ogg", ".wma"}
    audio_ratio = sum(ext_ratios.get(e, 0) for e in audio_exts)
    if audio_ratio > 0.6:
        return ClassificationResult(
            source_path=source_path,
            target_path="/个人空间/音乐",
            confidence=0.5,
            rule_name="content_analysis",
            reason=f"音频文件占比 {audio_ratio:.0%}",
            file_count=stats.get("file_count", 0),
            total_size=stats.get("total_size", 0),
        )

    # 手机照片：大量 HEIC/JPG
    phone_exts = {".heic", ".heif"}
    phone_ratio = sum(ext_ratios.get(e, 0) for e in phone_exts)
    if phone_ratio > 0.3:
        return ClassificationResult(
            source_path=source_path,
            target_path="/个人空间/照片-手机",
            confidence=0.45,
            rule_name="content_analysis",
            reason=f"HEIC照片占比 {phone_ratio:.0%}",
            file_count=stats.get("file_count", 0),
            total_size=stats.get("total_size", 0),
        )

    return None


def print_classification_report(results: list[ClassificationResult], detail: bool = False):
    """打印分类报告"""
    if not results:
        console.print("[yellow]无分类结果[/yellow]")
        return

    high = [r for r in results if r.confidence_level == "high"]
    medium = [r for r in results if r.confidence_level == "medium"]
    low = [r for r in results if r.confidence_level == "low"]

    console.print(f"\n[bold]分类报告[/bold]")
    console.print(f"  高置信度 (>=0.9): {len(high)} 个目录，"
                  f"{_fmt_size(sum(r.total_size for r in high))}")
    console.print(f"  中置信度 (0.5-0.9): {len(medium)} 个目录，"
                  f"{_fmt_size(sum(r.total_size for r in medium))}")
    console.print(f"  低置信度 (<0.5): {len(low)} 个目录，"
                  f"{_fmt_size(sum(r.total_size for r in low))}")

    if detail:
        for level_name, level_results, style in [
            ("高置信度", high, "green"),
            ("中置信度", medium, "yellow"),
            ("低置信度", low, "red"),
        ]:
            if not level_results:
                continue
            table = Table(title=f"\n{level_name}分类详情")
            table.add_column("源目录", style="dim", max_width=40)
            table.add_column("目标", style=style, max_width=30)
            table.add_column("置信度", justify="right")
            table.add_column("规则", max_width=18)
            table.add_column("文件数", justify="right")
            table.add_column("大小", justify="right")
            table.add_column("原因", max_width=35)

            for r in sorted(level_results, key=lambda x: x.total_size, reverse=True):
                table.add_row(
                    _truncate(r.source_path, 40),
                    _truncate(r.target_path, 30),
                    f"{r.confidence:.2f}",
                    r.rule_name,
                    str(r.file_count),
                    _fmt_size(r.total_size),
                    _truncate(r.reason, 35),
                )

            console.print(table)
    else:
        target_summary = {}
        for r in results:
            target = r.target_path.split("/")[1] if "/" in r.target_path.strip("/") else r.target_path
            if target not in target_summary:
                target_summary[target] = {"count": 0, "size": 0, "dirs": 0}
            target_summary[target]["count"] += r.file_count
            target_summary[target]["size"] += r.total_size
            target_summary[target]["dirs"] += 1

        table = Table(title="\n分类汇总（按目标分类）")
        table.add_column("目标分类")
        table.add_column("目录数", justify="right")
        table.add_column("文件数", justify="right")
        table.add_column("总大小", justify="right")

        for target in sorted(target_summary, key=lambda x: target_summary[x]["size"], reverse=True):
            s = target_summary[target]
            table.add_row(target, str(s["dirs"]), str(s["count"]), _fmt_size(s["size"]))

        console.print(table)


def save_classification_results(results: list[ClassificationResult]):
    """保存分类结果到数据库"""
    records = []
    for r in results:
        records.append({
            "source_path": r.source_path,
            "target_path": r.target_path,
            "confidence": r.confidence,
            "confidence_level": r.confidence_level,
            "rule_name": r.rule_name,
            "reason": r.reason,
            "file_count": r.file_count,
            "total_size": r.total_size,
            "status": "pending",
        })
    save_classifications(records)
    console.print(f"[green]已保存 {len(records)} 条分类结果到数据库[/green]")


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else "..." + s[-(max_len - 3):]
