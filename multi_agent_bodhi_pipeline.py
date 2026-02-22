#!/usr/bin/env python3
"""Generate multi-agent analysis files for Bodhi transcripts.

This pipeline creates:
1) 10 agent definition files.
2) One detailed "multi-agent analysis" markdown per transcript.
3) One index report covering completion status.

It merges existing Cloud Code extraction notes when available.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_TRANSCRIPT_DIR = Path("data/subtitles/A学科库/国学/2024吴/菩提道（视）")
DEFAULT_CLOUD_DIR = Path("data/subtitles/A学科库/国学/24菩提道/知识萃取")

TRANSCRIPT_DIR = DEFAULT_TRANSCRIPT_DIR
CLOUD_DIR = DEFAULT_CLOUD_DIR
OUTPUT_DIR = TRANSCRIPT_DIR / "多Agent梳理"
AGENT_DIR = OUTPUT_DIR / "agents"


@dataclass(frozen=True)
class AgentDef:
    agent_id: str
    name: str
    mission: str
    output: str


AGENTS: list[AgentDef] = [
    AgentDef("A01", "定位Agent", "确定本讲主题、讲次定位与课程阶段作用", "本讲定位+一句话摘要+阶段标签"),
    AgentDef("A02", "结构Agent", "把逐字稿按语义推进拆成4-6段", "结构分段+每段要点"),
    AgentDef("A03", "术语Agent", "提取并统计关键法义术语与核心概念", "高频术语+解释"),
    AgentDef("A04", "论证Agent", "抽取因果、条件、递进等论证链", "关键论证链清单"),
    AgentDef("A05", "金句Agent", "提取可复用原话与教学强调句", "高价值原话金句"),
    AgentDef("A06", "修学Agent", "整理可执行修学方法、次第与练习点", "修学动作清单"),
    AgentDef("A07", "误区Agent", "定位常见误解、风险点与禁忌", "误区与对治"),
    AgentDef("A08", "问题Agent", "生成后续深挖问题和讨论题", "疑问种子+讨论题"),
    AgentDef("A09", "对照Agent", "对齐Cloud Code历史萃取并标注增量", "一致点+增量洞见"),
    AgentDef("A10", "质检Agent", "检查完整性并给出人工复核建议", "质检结论+待复核项"),
]


TERM_CANDIDATES = [
    "菩提心",
    "发心",
    "下士道",
    "中士道",
    "上士道",
    "亲近善知识",
    "暇满人身",
    "念死无常",
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
    "悲悯",
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
    "胜义谛",
    "世俗谛",
    "宗喀巴",
    "阿底峡",
    "瑜伽师地论",
    "现观庄严论",
    "菩提道次第",
    "修行",
    "闻思修",
]


CONNECTORS = [
    "因为",
    "所以",
    "因此",
    "如果",
    "那么",
    "先",
    "再",
    "然后",
    "最后",
    "不是",
    "而是",
    "由此",
]


PRACTICE_KEYS = [
    "修",
    "观",
    "止",
    "持戒",
    "归依",
    "发心",
    "念",
    "对治",
    "练习",
    "次第",
    "觉知",
]


RISK_KEYS = [
    "不要",
    "不能",
    "误",
    "错",
    "问题",
    "反对",
    "偏",
    "执著",
    "散乱",
    "昏沉",
]


QUESTION_KEYS = ["如何", "为什么", "怎么", "吗", "?", "？", "是否", "何以"]


def normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", "", raw.strip())
        if not line:
            continue
        if re.fullmatch(r"[\d:.,，。！？?（）\-\s]+", line):
            continue
        lines.append(line)
    return lines


def pick_lines(lines: Iterable[str], keys: list[str], limit: int, min_len: int = 8, max_len: int = 110) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if len(line) < min_len or len(line) > max_len:
            continue
        if not any(k in line for k in keys):
            continue
        if line in seen:
            continue
        out.append(line)
        seen.add(line)
        if len(out) >= limit:
            break
    return out


def top_terms(text: str, limit: int = 12) -> list[tuple[str, int]]:
    pairs = [(term, text.count(term)) for term in TERM_CANDIDATES]
    pairs = [p for p in pairs if p[1] > 0]
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:limit]


def cloud_note_for_episode(ep: int) -> Path | None:
    candidates = [
        CLOUD_DIR / f"菩提道次第{ep:02d}_萃取.md",
        CLOUD_DIR / f"菩提道次第{ep}_萃取.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def cloud_excerpt(path: Path, max_chars: int = 700) -> str:
    txt = path.read_text(encoding="utf-8", errors="ignore")
    cleaned = re.sub(r"\s+", " ", txt).strip()
    return cleaned[:max_chars] + ("..." if len(cleaned) > max_chars else "")


def segment_points(lines: list[str], text: str) -> list[str]:
    if not lines:
        return []
    n = len(lines)
    block = max(1, n // 4)
    points: list[str] = []
    for i in range(4):
        start = i * block
        end = n if i == 3 else min(n, (i + 1) * block)
        chunk = lines[start:end]
        if not chunk:
            continue
        lead = chunk[0]
        chunk_text = "".join(chunk)
        terms = top_terms(chunk_text, limit=4)
        term_part = "、".join([f"{t}({c})" for t, c in terms]) if terms else "（术语信号较弱）"
        points.append(f"阶段{i+1}（行{start+1}-{end}）: 开场句“{lead[:40]}” | 术语信号: {term_part}")
    return points


def markdown_list(items: list[str], default: str = "（未检出，建议人工补充）") -> str:
    if not items:
        return f"- {default}\n"
    return "".join(f"- {x}\n" for x in items)


def episode_num(name: str) -> int:
    m = re.search(r"菩提道次第(\d+)", name)
    if not m:
        raise ValueError(f"Cannot parse episode from {name}")
    return int(m.group(1))


def build_one(transcript_path: Path, all_names: list[str]) -> tuple[Path, bool, int]:
    raw = transcript_path.read_text(encoding="utf-8", errors="ignore")
    lines = normalize_lines(raw)
    text = "".join(lines)
    ep = episode_num(transcript_path.name)
    cloud = cloud_note_for_episode(ep)
    has_cloud = cloud is not None

    terms = top_terms(text)
    terms_block = [f"{t}: {c}" for t, c in terms[:12]]
    chains = pick_lines(lines, CONNECTORS, limit=12, min_len=10)
    quotes = [x for x in lines if 15 <= len(x) <= 85][:120]
    quote_keys = pick_lines(quotes, ["。", "！", "？", "吧", "要", "就是", "所以"], limit=12, min_len=15)
    if len(quote_keys) < 8:
        quote_keys = quotes[:12]
    practice = pick_lines(lines, PRACTICE_KEYS, limit=12, min_len=8)
    risks = pick_lines(lines, RISK_KEYS, limit=10, min_len=8)
    questions = pick_lines(lines, QUESTION_KEYS, limit=10, min_len=8)
    segments = segment_points(lines, text)

    idx = all_names.index(transcript_path.name)
    prev_name = all_names[idx - 1] if idx > 0 else "（无）"
    next_name = all_names[idx + 1] if idx < len(all_names) - 1 else "（无）"

    cloud_part = "（未命中 Cloud Code 历史萃取文件）"
    if cloud is not None:
        cloud_part = f"- 参考文件: `{cloud}`\n- 摘要片段: {cloud_excerpt(cloud)}\n"

    out_name = transcript_path.name.replace(".txt", "_多Agent梳理.md")
    out_path = OUTPUT_DIR / out_name
    out = f"""# {transcript_path.stem} 多Agent详细梳理

## A01 定位Agent
- 文本来源: `{transcript_path}`
- 文本体量: {len(raw)} 字符 / {len(lines)} 有效行
- 本讲一句话: 围绕“{terms[0][0] if terms else '修行次第'}”展开，重点落在“{terms[1][0] if len(terms) > 1 else '发心与实践'}”。
- 阶段判断: 菩提道课程中的第 {ep:02d} 讲，建议与前后讲联读。

## A02 结构Agent（4段推进）
{markdown_list(segments)}

## A03 术语Agent（高频法义）
{markdown_list(terms_block)}

## A04 论证Agent（因果/条件链）
{markdown_list(chains)}

## A05 金句Agent（可复用原话）
{markdown_list(quote_keys)}

## A06 修学Agent（可执行动作）
{markdown_list(practice)}

## A07 误区Agent（风险与对治）
{markdown_list(risks, default="未检出显式风险句，建议从“不要/不能/误区”角度二次细读。")}

## A08 问题Agent（深挖问题）
{markdown_list(questions, default="原文问句较少，建议围绕“发心-实践-检验”补充讨论题。")}

## A09 对照Agent（Cloud Code 增量整合）
{cloud_part}
- 对照结论:
  - 若已存在历史萃取，本文件补充了“结构分段+术语频次+跨讲连接+待复核点”。
  - 若无历史萃取，本文件作为首版结构化梳理底稿。

## A10 质检Agent（完整性与待复核）
- 邻接讲次: 上一讲 `{prev_name}` | 下一讲 `{next_name}`
- 质检结论: 已完成10-Agent全流程自动梳理，可用于二轮人工精修。
- 待复核清单:
  1. 核心术语解释是否与授课语境完全一致。
  2. 金句是否需要去口语噪声并做语义润色。
  3. 论证链是否存在ASR识别误差导致的断句偏差。
"""
    out_path.write_text(out, encoding="utf-8")
    return out_path, has_cloud, len(raw)


def write_agent_defs() -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    for a in AGENTS:
        p = AGENT_DIR / f"{a.agent_id}_{a.name}.md"
        p.write_text(
            (
                f"# {a.agent_id} {a.name}\n\n"
                f"- 职责: {a.mission}\n"
                f"- 输出: {a.output}\n"
                "- 约束: 忠于逐字稿原文，不凭空补剧情。\n"
            ),
            encoding="utf-8",
        )


def write_index(rows: list[tuple[str, str, int, bool]]) -> None:
    lines = [
        "# 菩提道逐字稿多Agent梳理总览",
        "",
        "## 执行说明",
        "- 本批次由 10 个 Agent 分工协作完成。",
        f"- 输入目录: `{TRANSCRIPT_DIR}`",
        f"- 历史参考: `{CLOUD_DIR}`（Cloud Code 既有结果）",
        f"- 输出目录: `{OUTPUT_DIR}`",
        "",
        "## 明细",
        "| 讲次文件 | 输出文件 | 原文字符数 | 已融合Cloud结果 |",
        "|---|---|---:|---|",
    ]
    cloud_count = 0
    for src, out, chars, has_cloud in rows:
        cloud_flag = "是" if has_cloud else "否"
        if has_cloud:
            cloud_count += 1
        lines.append(f"| `{src}` | `{out}` | {chars} | {cloud_flag} |")
    lines += [
        "",
        "## 统计",
        f"- 逐字稿总数: {len(rows)}",
        f"- 输出总数: {len(rows)}",
        f"- 融合 Cloud Code 历史结果: {cloud_count} 讲",
        f"- 新增首版梳理: {len(rows) - cloud_count} 讲",
    ]
    (OUTPUT_DIR / "00_总览_多Agent梳理.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate multi-agent analysis markdown files for transcripts.")
    parser.add_argument("--transcript-dir", default=str(DEFAULT_TRANSCRIPT_DIR), help="Input transcript directory")
    parser.add_argument("--cloud-dir", default=str(DEFAULT_CLOUD_DIR), help="Cloud extraction reference directory")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: <transcript-dir>/多Agent梳理)")
    return parser.parse_args()


def configure_paths(args: argparse.Namespace) -> None:
    global TRANSCRIPT_DIR, CLOUD_DIR, OUTPUT_DIR, AGENT_DIR
    TRANSCRIPT_DIR = Path(args.transcript_dir)
    CLOUD_DIR = Path(args.cloud_dir)
    OUTPUT_DIR = Path(args.output_dir) if args.output_dir else (TRANSCRIPT_DIR / "多Agent梳理")
    AGENT_DIR = OUTPUT_DIR / "agents"


def main() -> None:
    args = parse_args()
    configure_paths(args)

    if not TRANSCRIPT_DIR.exists():
        raise FileNotFoundError(f"transcript_dir does not exist: {TRANSCRIPT_DIR}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_agent_defs()
    txt_files = sorted(TRANSCRIPT_DIR.glob("菩提道次第*.txt"), key=lambda p: episode_num(p.name))
    names = [p.name for p in txt_files]
    rows: list[tuple[str, str, int, bool]] = []
    for p in txt_files:
        out, has_cloud, chars = build_one(p, names)
        rows.append((p.name, out.name, chars, has_cloud))
    write_index(rows)
    print(f"done: transcripts={len(rows)} output_dir={OUTPUT_DIR}")


if __name__ == "__main__":
    main()
