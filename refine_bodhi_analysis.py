#!/usr/bin/env python3
"""Second-pass refinement for Bodhi transcript analysis.

Outputs:
1) Per-episode refined analysis for all 38 episodes.
2) Standardized glossary and cross-episode topic map.
3) Sync subset refined files to 24菩提道/知识萃取/二次精修.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TRANSCRIPT_DIR = Path("data/subtitles/A学科库/国学/2024吴/菩提道（视）")
DEFAULT_CLOUD_DIR = Path("data/subtitles/A学科库/国学/24菩提道/知识萃取")
DEFAULT_SUBSET_DIR = Path("data/subtitles/A学科库/国学/24菩提道")

TRANSCRIPT_DIR = DEFAULT_TRANSCRIPT_DIR
CLOUD_DIR = DEFAULT_CLOUD_DIR
REFINE_DIR = TRANSCRIPT_DIR / "多Agent梳理" / "二次精修"
SUBSET_DIR = DEFAULT_SUBSET_DIR
SUBSET_REFINE_DIR = CLOUD_DIR / "二次精修"


KEY_TERMS = [
    "菩提心",
    "发心",
    "下士道",
    "中士道",
    "上士道",
    "亲近善知识",
    "暇满",
    "无常",
    "业果",
    "归依",
    "戒",
    "定",
    "慧",
    "止",
    "观",
    "奢摩他",
    "毗钵舍那",
    "止观双运",
    "出离心",
    "慈悲",
    "自他互换",
    "六度",
    "四摄",
    "空性",
    "无我",
    "法无我",
    "十二因缘",
    "四念住",
    "生死",
    "涅槃",
    "解脱",
    "方便",
    "世俗谛",
    "胜义谛",
    "宗喀巴",
    "阿底峡",
    "瑜伽师地论",
    "菩提道次第",
]

PRACTICE_KEYS = ["修", "观", "止", "持戒", "归依", "发心", "次第", "觉知", "练习", "对治", "听闻", "思维"]
RISK_KEYS = ["不要", "不能", "误", "错", "偏", "执", "散乱", "昏沉", "问题", "反对"]
LOGIC_KEYS = ["因为", "所以", "因此", "如果", "那么", "先", "再", "然后", "最后", "不是", "而是", "由此"]


TERM_DEFS = {
    "菩提心": "以成佛利他为目标的发心，是上士道的核心发动机。",
    "发心": "修行动机的确立，决定道次第走向与果位方向。",
    "下士道": "以业果、归依、离恶向善为基础的修学阶段。",
    "中士道": "以出离生死、修戒定慧求解脱为中心的阶段。",
    "上士道": "以菩提心与六度万行为核心，走向成佛之道。",
    "戒": "行为边界与修定基础，稳住身口意的第一层护栏。",
    "定": "心一境性的安住能力，是观慧生起的稳定底盘。",
    "慧": "如实观照的智慧，决定能否破除无明与执著。",
    "止": "令心安住不散乱的训练，偏向稳定与收摄。",
    "观": "对法义如理观察与照见，偏向洞察与明辨。",
    "止观双运": "止与观和合平等俱转，既安住又明照。",
    "空性": "诸法无自性、缘起性空的核心见地。",
    "无我": "破除对固定自我的执取，通向解脱关键。",
    "十二因缘": "解释生死流转机制的因果链条模型。",
    "四念住": "身受心法四个维度的持续觉察修法。",
    "出离心": "深见生死过患后，真实生起求解脱之心。",
}


@dataclass
class Episode:
    ep: int
    name: str
    path: Path
    raw: str
    lines: list[str]
    sentences: list[str]
    term_freq: dict[str, int]
    cloud_path: Path | None


def ep_num(name: str) -> int:
    m = re.search(r"菩提道次第(\d+)", name)
    if not m:
        raise ValueError(f"cannot parse episode: {name}")
    return int(m.group(1))


def normalize_lines(text: str) -> list[str]:
    out: list[str] = []
    for ln in text.splitlines():
        s = re.sub(r"\s+", "", ln.strip())
        if not s:
            continue
        if re.fullmatch(r"[\d:：.,，。！？?（）\-\s]+", s):
            continue
        out.append(s)
    return out


def clean_sentence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"[。]{2,}", "。", s)
    # mild cleanup for spoken fillers while keeping semantics
    s = s.replace("嗯，", "，").replace("啊，", "，").replace("对吧，", "，")
    return s


def split_sentences(lines: list[str]) -> list[str]:
    text = "".join(lines)
    segs = re.split(r"(?<=[。！？?])", text)
    out: list[str] = []
    seen: set[str] = set()
    for seg in segs:
        s = clean_sentence(seg)
        if len(s) < 10 or len(s) > 120:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def term_freq(text: str) -> dict[str, int]:
    return {t: text.count(t) for t in KEY_TERMS}


def pick_sentences(sentences: list[str], keys: list[str], limit: int, min_len: int = 12) -> list[str]:
    out: list[str] = []
    used: set[str] = set()
    for s in sentences:
        if len(s) < min_len:
            continue
        if not any(k in s for k in keys):
            continue
        if s in used:
            continue
        out.append(s)
        used.add(s)
        if len(out) >= limit:
            break
    return out


def score_sentence(s: str, terms: list[str]) -> float:
    score = 0.0
    score += sum(2.0 for t in terms if t in s)
    score += sum(1.2 for k in LOGIC_KEYS if k in s)
    if any(k in s for k in PRACTICE_KEYS):
        score += 1.0
    if any(k in s for k in RISK_KEYS):
        score += 0.8
    score += min(len(s) / 45.0, 2.0)
    return score


def top_k_sentences(sentences: list[str], terms: list[str], k: int = 8) -> list[str]:
    ranked = sorted(sentences, key=lambda s: score_sentence(s, terms), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for s in ranked:
        if s in seen:
            continue
        out.append(s)
        seen.add(s)
        if len(out) >= k:
            break
    return out


def cloud_file_for(ep: int) -> Path | None:
    p = CLOUD_DIR / f"菩提道次第{ep:02d}_萃取.md"
    if p.exists():
        return p
    return None


def extract_cloud_core(path: Path) -> list[str]:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    # Prefer legacy section "### 2. 核心法义"
    m = re.search(r"###\s*2\..*?核心法义(.*?)(?:\n###\s*3\.|\Z)", txt, flags=re.S)
    block = m.group(1) if m else txt[:1800]
    points: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if re.match(r"^\d+\.\s*", line):
            points.append(re.sub(r"^\d+\.\s*", "", line))
        elif line.startswith("- "):
            points.append(line[2:].strip())
        if len(points) >= 5:
            break
    return points


def stage_label(ep: int, top_terms: list[str]) -> str:
    if ep <= 5:
        return "导论与入门定位"
    if any(t in top_terms for t in ["下士道", "中士道", "上士道", "发心", "菩提心"]):
        return "三士道与发心推进"
    if any(t in top_terms for t in ["戒", "定", "慧", "止", "观", "奢摩他", "毗钵舍那"]):
        return "戒定慧与止观修学"
    if any(t in top_terms for t in ["空性", "无我", "法无我", "世俗谛", "胜义谛"]):
        return "空性见与慧观深化"
    return "综合串讲与实修提醒"


def fmt_list(items: list[str], empty: str = "（未检出，建议人工复核）") -> str:
    if not items:
        return f"- {empty}\n"
    return "".join(f"- {x}\n" for x in items)


def build_episode_md(epi: Episode, all_eps: list[Episode]) -> str:
    sorted_terms = sorted(epi.term_freq.items(), key=lambda kv: kv[1], reverse=True)
    top_terms = [t for t, c in sorted_terms if c > 0][:8]
    top_terms_desc = [f"{t}({epi.term_freq[t]})" for t in top_terms]

    core_lines = top_k_sentences(epi.sentences, top_terms, k=8)
    practice = pick_sentences(epi.sentences, PRACTICE_KEYS, limit=8)
    risks = pick_sentences(epi.sentences, RISK_KEYS, limit=6)
    quotes = [s for s in epi.sentences if 18 <= len(s) <= 85][:120]
    quote_pick = top_k_sentences(quotes, top_terms, k=6)

    idx = all_eps.index(epi)
    prev_ep = all_eps[idx - 1] if idx > 0 else None
    next_ep = all_eps[idx + 1] if idx < len(all_eps) - 1 else None

    def shared(a: Episode | None, b: Episode) -> str:
        if a is None:
            return "（无）"
        at = {t for t, c in sorted(a.term_freq.items(), key=lambda kv: kv[1], reverse=True)[:8] if c > 0}
        bt = {t for t, c in sorted(b.term_freq.items(), key=lambda kv: kv[1], reverse=True)[:8] if c > 0}
        inter = sorted(at & bt)
        return "、".join(inter[:6]) if inter else "（弱关联）"

    cloud_block = "- 历史Cloud萃取: 未找到同讲历史文件。\n"
    if epi.cloud_path is not None:
        cpoints = extract_cloud_core(epi.cloud_path)
        cloud_block = (
            f"- 历史Cloud萃取: `{epi.cloud_path}`\n"
            + fmt_list([f"Cloud核心点: {p}" for p in cpoints[:4]], empty="Cloud文件存在，但未抽取到结构化核心点。")
        )

    label = stage_label(epi.ep, top_terms)

    return f"""# 菩提道次第{epi.ep:02d} 二次精修梳理

## 1. 本讲定位
- 讲次文件: `{epi.path}`
- 阶段标签: {label}
- 核心术语: {", ".join(top_terms_desc) if top_terms_desc else "（术语信号较弱）"}
- 一句话总结: 本讲围绕“{top_terms[0] if top_terms else "修行次第"}”展开，以“{top_terms[1] if len(top_terms)>1 else "实践落地"}”为主线推进。

## 2. 核心脉络（精修版）
{fmt_list(core_lines, empty="原文可用句不足，建议人工补录关键段。")}

## 3. 修学实践动作
{fmt_list(practice)}

## 4. 易错点与对治提醒
{fmt_list(risks, empty="显式风险语句较少，建议结合前后讲补充“执著/散乱/偏修”类提醒。")}

## 5. 可复用原话（去口语噪声）
{fmt_list(quote_pick)}

## 6. 与前后讲关联
- 上一讲: `{prev_ep.name if prev_ep else "（无）"}` | 共享术语: {shared(prev_ep, epi)}
- 下一讲: `{next_ep.name if next_ep else "（无）"}` | 共享术语: {shared(next_ep, epi)}

## 7. Cloud Code 对照与增量
{cloud_block}
- 本次新增: 结构化“术语频次、跨讲关联、风险提醒、实践动作”四类信息，便于后续持续精修。

## 8. 质检
- 文本体量: {len(epi.raw)} 字符 / {len(epi.lines)} 有效行 / {len(epi.sentences)} 候选句
- 待人工复核:
  1. 术语解释与课堂语境是否完全一致。
  2. 原话是否需进一步做语义合并（减少ASR断句影响）。
  3. 本讲与上下文课程主线的衔接是否清晰。
"""


def build_glossary(eps: list[Episode]) -> str:
    def sample_for(term: str) -> str:
        for e in eps:
            for s in e.sentences:
                if term in s and 16 <= len(s) <= 88:
                    return s
        return "（未检出典型原句，建议人工补充）"

    rows = []
    for term, dfn in TERM_DEFS.items():
        cover = sum(1 for e in eps if e.term_freq.get(term, 0) > 0)
        row = f"| {term} | {dfn} | {cover} | {sample_for(term)} |"
        rows.append(row)
    return "\n".join(
        [
            "# 菩提道术语词典（标准化）",
            "",
            "| 术语 | 课程语境解释 | 覆盖讲数 | 示例原句 |",
            "|---|---|---:|---|",
            *rows,
            "",
            "说明: 本词典用于统一梳理口径，具体定义以课程上下文与原典为准。",
        ]
    ) + "\n"


def build_topic_map(eps: list[Episode]) -> str:
    lines = [
        "# 菩提道跨讲主题地图",
        "",
        "## 1) 术语覆盖图",
        "| 术语 | 出现讲数 | 高频讲次Top8 |",
        "|---|---:|---|",
    ]
    for term in ["发心", "菩提心", "下士道", "中士道", "上士道", "戒", "定", "慧", "止", "观", "空性", "无我", "十二因缘", "四念住", "止观双运"]:
        seq = [(e.ep, e.term_freq.get(term, 0)) for e in eps if e.term_freq.get(term, 0) > 0]
        seq.sort(key=lambda x: x[1], reverse=True)
        top = "、".join([f"{ep:02d}({cnt})" for ep, cnt in seq[:8]]) if seq else "—"
        lines.append(f"| {term} | {len(seq)} | {top} |")

    lines += [
        "",
        "## 2) 讲次推进视图（按主题自动归类）",
    ]
    buckets: dict[str, list[int]] = {}
    for e in eps:
        st = stage_label(e.ep, [t for t, c in sorted(e.term_freq.items(), key=lambda kv: kv[1], reverse=True)[:8] if c > 0])
        buckets.setdefault(st, []).append(e.ep)
    for k, vals in buckets.items():
        joined = "、".join(f"{x:02d}" for x in vals)
        lines.append(f"- {k}: {joined}")

    lines += [
        "",
        "## 3) 使用建议",
        "- 先按“讲次推进视图”纵向读，再按“术语覆盖图”横向串讲。",
        "- 准备讲义时先取本表高频讲次，再回看对应精修文件的“核心脉络+实践动作”。",
    ]
    return "\n".join(lines) + "\n"


def build_batch_report(eps: list[Episode], out_files: list[Path]) -> str:
    cloud_hits = sum(1 for e in eps if e.cloud_path is not None)
    total_chars = sum(len(e.raw) for e in eps)
    return "\n".join(
        [
            "# 二次精修批次报告",
            "",
            f"- 处理讲次: {len(eps)}",
            f"- 输出文件: {len(out_files)}",
            f"- 总字符量: {total_chars}",
            f"- 命中Cloud历史文件: {cloud_hits}",
            f"- 输出目录: `{REFINE_DIR}`",
            "",
            "## 产物清单",
            "- 逐讲精修文件（38讲）",
            "- 术语词典（标准化）",
            "- 跨讲主题地图",
            "- 批次报告",
            "",
            "## 注意",
            "- 本批次是“可复核的结构化精修稿”，不是最终出版文稿。",
            "- 对关键法义建议做一轮人工校读后再外发。",
        ]
    ) + "\n"


def load_episodes() -> list[Episode]:
    files = sorted(TRANSCRIPT_DIR.glob("菩提道次第*.txt"), key=lambda p: ep_num(p.name))
    eps: list[Episode] = []
    for p in files:
        raw = p.read_text(encoding="utf-8", errors="ignore")
        lines = normalize_lines(raw)
        sentences = split_sentences(lines)
        text = "".join(lines)
        ep = ep_num(p.name)
        eps.append(
            Episode(
                ep=ep,
                name=p.name,
                path=p,
                raw=raw,
                lines=lines,
                sentences=sentences,
                term_freq=term_freq(text),
                cloud_path=cloud_file_for(ep),
            )
        )
    return eps


def sync_subset_refined(eps: list[Episode]) -> None:
    SUBSET_REFINE_DIR.mkdir(parents=True, exist_ok=True)
    subset_eps = {ep_num(p.name) for p in SUBSET_DIR.glob("菩提道次第*.txt")}
    for e in eps:
        if e.ep not in subset_eps:
            continue
        src = REFINE_DIR / f"菩提道次第{e.ep:02d}_二次精修.md"
        if src.exists():
            dst = SUBSET_REFINE_DIR / f"菩提道次第{e.ep:02d}_二次精修.md"
            dst.write_text(src.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Second-pass refinement for transcript analysis.")
    parser.add_argument("--transcript-dir", default=str(DEFAULT_TRANSCRIPT_DIR), help="Input transcript directory")
    parser.add_argument("--cloud-dir", default=str(DEFAULT_CLOUD_DIR), help="Cloud extraction reference directory")
    parser.add_argument("--subset-dir", default=str(DEFAULT_SUBSET_DIR), help="Subset transcript directory for sync")
    parser.add_argument("--refine-dir", default=None, help="Refined output directory")
    parser.add_argument("--subset-refine-dir", default=None, help="Subset sync output directory")
    return parser.parse_args()


def configure_paths(args: argparse.Namespace) -> None:
    global TRANSCRIPT_DIR, CLOUD_DIR, REFINE_DIR, SUBSET_DIR, SUBSET_REFINE_DIR
    TRANSCRIPT_DIR = Path(args.transcript_dir)
    CLOUD_DIR = Path(args.cloud_dir)
    SUBSET_DIR = Path(args.subset_dir)
    REFINE_DIR = Path(args.refine_dir) if args.refine_dir else (TRANSCRIPT_DIR / "多Agent梳理" / "二次精修")
    SUBSET_REFINE_DIR = Path(args.subset_refine_dir) if args.subset_refine_dir else (CLOUD_DIR / "二次精修")


def main() -> None:
    args = parse_args()
    configure_paths(args)

    if not TRANSCRIPT_DIR.exists():
        raise FileNotFoundError(f"transcript_dir does not exist: {TRANSCRIPT_DIR}")

    REFINE_DIR.mkdir(parents=True, exist_ok=True)
    eps = load_episodes()

    out_files: list[Path] = []
    for e in eps:
        out = REFINE_DIR / f"菩提道次第{e.ep:02d}_二次精修.md"
        out.write_text(build_episode_md(e, eps), encoding="utf-8")
        out_files.append(out)

    (REFINE_DIR / "00_术语词典_标准化.md").write_text(build_glossary(eps), encoding="utf-8")
    (REFINE_DIR / "00_跨讲主题地图.md").write_text(build_topic_map(eps), encoding="utf-8")
    (REFINE_DIR / "00_批次报告_二次精修.md").write_text(build_batch_report(eps, out_files), encoding="utf-8")

    sync_subset_refined(eps)
    print(f"done: refined={len(out_files)} dir={REFINE_DIR}")


if __name__ == "__main__":
    main()
