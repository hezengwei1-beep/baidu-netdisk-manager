#!/usr/bin/env python3
"""Munger OS Agent MVP.

Capabilities:
1) Problem framing
2) Inversion / pre-mortem
3) Mental model retrieval
4) Incentive and bias analysis
5) Evidence retrieval with citations
6) Decision memo rendering
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()

DEFAULT_SOURCE = Path("data/knowledge/思维-面向芒格能力全集的智能Agent工程化研究报告.md")


@dataclass(frozen=True)
class ModelCard:
    name: str
    english: str
    summary: str
    boundary: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class BiasCard:
    name: str
    trigger_keywords: tuple[str, ...]
    signal: str
    debias_action: str


@dataclass
class ProblemCard:
    query: str
    goal: str
    constraints: list[str]
    time_horizon: str
    risk_appetite: str
    resources: list[str]
    no_go: list[str]
    assumptions: list[dict[str, str]]


@dataclass
class FailureMode:
    title: str
    signal: str
    mitigation: str
    score: int


@dataclass
class IncentiveRisk:
    issue: str
    evidence: str
    fix: str


@dataclass
class BiasFinding:
    bias: str
    signal: str
    action: str
    score: int


@dataclass
class EvidenceChunk:
    path: str
    start_line: int
    end_line: int
    score: int
    snippet: str


@dataclass
class AgentResult:
    generated_at: str
    problem_card: ProblemCard
    conclusion: str
    model_set: list[ModelCard]
    failure_map: list[FailureMode]
    incentive_risks: list[IncentiveRisk]
    bias_findings: list[BiasFinding]
    evidence: list[EvidenceChunk]
    next_experiments: list[str]


MODEL_CARDS: tuple[ModelCard, ...] = (
    ModelCard(
        name="模型格栅",
        english="Latticework of Mental Models",
        summary="把事实挂在模型网络上，避免碎片化结论。",
        boundary="仅在模型覆盖足够时有效；陌生领域必须补模型。",
        keywords=("模型", "框架", "跨学科", "结构化", "复杂问题"),
    ),
    ModelCard(
        name="反向思考",
        english="Inversion",
        summary="先列出如何失败，再倒推避免失败。",
        boundary="不等于悲观主义，仍需正向实验收敛。",
        keywords=("失败", "风险", "预演", "事故", "防错"),
    ),
    ModelCard(
        name="激励优先",
        english="Incentives",
        summary="先看激励机制，再看口号和宣言。",
        boundary="激励解释力强，但不能忽视文化和能力约束。",
        keywords=("激励", "KPI", "奖金", "考核", "代理", "刷指标"),
    ),
    ModelCard(
        name="反证优先",
        english="Disconfirming Evidence",
        summary="主动寻找能推翻结论的证据，抑制确认偏误。",
        boundary="反证要针对关键假设，不是无限怀疑。",
        keywords=("反证", "证伪", "确认偏误", "假设", "证据"),
    ),
    ModelCard(
        name="能力圈",
        english="Circle of Competence",
        summary="明确认知边界，边界外降权或求助专家。",
        boundary="边界可扩展，但必须有学习与验证机制。",
        keywords=("边界", "专家", "不确定", "跨界", "能力圈"),
    ),
    ModelCard(
        name="机会成本",
        english="Opportunity Cost",
        summary="任何投入都意味着放弃其他更优方案。",
        boundary="需要有可比较替代项；否则容易形式化。",
        keywords=("机会成本", "取舍", "资源", "排序", "预算"),
    ),
    ModelCard(
        name="概率与基准率",
        english="Probability & Base Rate",
        summary="用基准率替代纯直觉判断，避免可得性偏差。",
        boundary="历史分布变化快时要动态更新基准率。",
        keywords=("概率", "基准率", "风险", "分布", "预测"),
    ),
    ModelCard(
        name="复利",
        english="Compounding",
        summary="把时间作为杠杆，优先累积长期优势。",
        boundary="短期生存压力高时需平衡长期与短期。",
        keywords=("长期", "复利", "积累", "信誉", "学习"),
    ),
    ModelCard(
        name="可靠性与信誉",
        english="Reliability",
        summary="稳定兑现承诺会形成长期信任资产。",
        boundary="高承诺低交付会反向损害复利。",
        keywords=("信誉", "承诺", "交付", "信任", "可靠"),
    ),
    ModelCard(
        name="复合叠加效应",
        english="Lollapalooza Effect",
        summary="多个偏误和激励叠加会触发系统性极端结果。",
        boundary="需要同时分析多个驱动因素而非单点归因。",
        keywords=("叠加", "系统性", "偏误", "共振", "极端"),
    ),
)


BIAS_CARDS: tuple[BiasCard, ...] = (
    BiasCard(
        name="确认偏误",
        trigger_keywords=("证明", "早就", "肯定", "只要", "已经说明"),
        signal="只搜集支持观点的材料，忽略反例。",
        debias_action="强制列出3条反证问题并执行二次检索。",
    ),
    BiasCard(
        name="权威误导",
        trigger_keywords=("领导说", "专家说", "权威", "董事会", "老板"),
        signal="以权威身份替代事实核验。",
        debias_action="把意见拆成可验证断言，并要求数据/案例支持。",
    ),
    BiasCard(
        name="社会认同",
        trigger_keywords=("大家都", "行业都", "都这么做", "主流做法"),
        signal="以多数行为替代独立判断。",
        debias_action="增加反向样本与非共识方案评估。",
    ),
    BiasCard(
        name="可得性偏差",
        trigger_keywords=("最近", "刚发生", "印象深刻", "一次事故"),
        signal="用近期个案替代整体分布。",
        debias_action="回到基准率与历史样本分布。",
    ),
    BiasCard(
        name="过度自信",
        trigger_keywords=("肯定能", "不会出错", "百分百", "稳了"),
        signal="低估失败概率和执行摩擦。",
        debias_action="加最坏场景预演和安全余量。",
    ),
    BiasCard(
        name="激励致偏",
        trigger_keywords=("奖金", "提成", "冲指标", "考核", "绩效"),
        signal="利益绑定导致建议偏向对自己有利。",
        debias_action="引入独立复核和反激励条款。",
    ),
)


FAILURE_LIBRARY: tuple[FailureMode, ...] = (
    FailureMode("目标定义模糊", "团队对成功标准解释不一致", "把目标量化成2-4个验收指标", 0),
    FailureMode("激励错配", "局部最优行为增多，整体结果变差", "改为平衡指标并加反作弊约束", 0),
    FailureMode("忽略反证", "方案评审里只有支持证据", "设置反方审稿人和必答反证清单", 0),
    FailureMode("超出能力圈", "关键问题依赖未经验证的新能力", "标记高不确定项并引入外部专家", 0),
    FailureMode("单点指标驱动", "指标好看但用户体验恶化", "加入质量和长期指标作为共同约束", 0),
    FailureMode("执行节奏失衡", "里程碑延误且无法追责", "拆解周节奏并建立里程碑复盘", 0),
    FailureMode("数据质量不足", "决策依据来自不完整样本", "建立数据质量门禁与抽检", 0),
    FailureMode("组织阻力低估", "地方/部门表面配合实则消极执行", "提前做利益相关方地图和沟通计划", 0),
    FailureMode("风险预警缺失", "问题爆发前无预警信号", "为每个失败模式绑定领先指标", 0),
    FailureMode("复盘机制缺位", "同类问题重复出现", "上线AAR模板并绑定责任人", 0),
    FailureMode("外部约束忽视", "法规/供应链变化导致计划中断", "建立外部风险观察清单", 0),
)


def tokenize(text: str) -> list[str]:
    clean = text.lower()
    terms = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,4}", clean)
    return [t for t in terms if len(t.strip()) > 0]


def keyword_hits(text: str, keywords: Iterable[str]) -> int:
    return sum(1 for kw in keywords if kw and kw.lower() in text.lower())


def unique_keep_order(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def build_problem_card(
    query: str,
    goal: str,
    constraints: tuple[str, ...],
    time_horizon: str,
    risk_appetite: str,
    resources: tuple[str, ...],
    no_go: tuple[str, ...],
) -> ProblemCard:
    normalized_constraints = [c.strip() for c in constraints if c.strip()]
    normalized_resources = [r.strip() for r in resources if r.strip()]
    normalized_no_go = [n.strip() for n in no_go if n.strip()]

    effective_goal = goal.strip() if goal.strip() else f"围绕“{query[:48]}”形成可执行决策方案"

    assumptions = [
        {
            "assumption": "目标相关方具备执行窗口（资源和授权可调动）",
            "falsify": "访谈关键角色，若关键资源不可调配则该假设失效。",
        },
        {
            "assumption": "当前问题可通过流程/激励/信息改造显著改善",
            "falsify": "做小范围试点，若核心指标无改善则假设失效。",
        },
        {
            "assumption": "已有数据足以支持第一轮决策",
            "falsify": "抽样核对数据来源，若关键字段缺失率高于20%则失效。",
        },
    ]

    if risk_appetite == "low":
        assumptions.append(
            {
                "assumption": "可通过保守策略把下行风险控制在可接受区间",
                "falsify": "压力测试若出现不可承受损失场景则失效。",
            }
        )
    if "跨部门" in query or "总部" in query:
        assumptions.append(
            {
                "assumption": "跨部门协同摩擦可通过明确责任边界降低",
                "falsify": "试运行后若责任争议持续高发则失效。",
            }
        )

    return ProblemCard(
        query=query.strip(),
        goal=effective_goal,
        constraints=normalized_constraints,
        time_horizon=time_horizon,
        risk_appetite=risk_appetite,
        resources=normalized_resources,
        no_go=normalized_no_go,
        assumptions=assumptions[:7],
    )


def retrieve_models(problem: ProblemCard, top_k: int) -> list[ModelCard]:
    text = " ".join(
        [
            problem.query,
            problem.goal,
            " ".join(problem.constraints),
            " ".join(problem.no_go),
        ]
    )
    scored: list[tuple[int, ModelCard]] = []
    for card in MODEL_CARDS:
        score = keyword_hits(text, card.keywords)
        # 通用高价值模型小幅加权，避免召回过窄。
        if card.english in {"Latticework of Mental Models", "Inversion", "Incentives"}:
            score += 1
        scored.append((score, card))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [card for score, card in scored if score > 0][:top_k]
    if len(selected) < min(5, top_k):
        defaults = [c for c in MODEL_CARDS if c not in selected]
        selected.extend(defaults[: max(0, min(5, top_k) - len(selected))])
    return selected[:top_k]


def build_failure_map(problem: ProblemCard, top_n: int = 10) -> list[FailureMode]:
    text = " ".join([problem.query, problem.goal, " ".join(problem.constraints)]).lower()
    scored: list[FailureMode] = []
    for fm in FAILURE_LIBRARY:
        score = 1
        score += keyword_hits(text, [fm.title, fm.signal, fm.mitigation])
        if "kpi" in text or "绩效" in text:
            if "激励" in fm.title or "单点指标" in fm.title:
                score += 2
        if "总部" in text or "组织" in text:
            if "组织阻力" in fm.title:
                score += 2
        if "新业务" in text or "跨界" in text:
            if "能力圈" in fm.title:
                score += 2
        scored.append(FailureMode(fm.title, fm.signal, fm.mitigation, score))

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_n]


def analyze_incentives(problem: ProblemCard, kpi_text: str) -> list[IncentiveRisk]:
    source = f"{problem.query}\n{kpi_text}".strip().lower()
    findings: list[IncentiveRisk] = []

    checks = [
        (
            ("只看", "单一", "唯一指标", "kpi"),
            "单一指标驱动，容易刷指标",
            "文本出现单一指标导向描述",
            "改为平衡指标：效率+质量+长期结果，并加入反作弊规则。",
        ),
        (
            ("短期", "当月", "季度冲刺"),
            "短期激励挤压长期价值",
            "文本包含短周期强激励表达",
            "增加长期指标权重（留存、复购、稳定性）并设置延迟兑现。",
        ),
        (
            ("提成", "奖金", "返利"),
            "利益绑定导致建议偏置",
            "奖励规则与建议方收益高度相关",
            "引入独立复核角色，关键决策采取双签机制。",
        ),
        (
            ("处罚", "扣分", "惩罚"),
            "过强惩罚可能诱发数据隐瞒",
            "惩罚性措辞高频出现",
            "把惩罚转为纠偏闭环：预警-辅导-复盘，减少瞒报激励。",
        ),
    ]

    for keys, issue, evidence, fix in checks:
        if any(k in source for k in keys):
            findings.append(IncentiveRisk(issue=issue, evidence=evidence, fix=fix))

    if not findings:
        findings.append(
            IncentiveRisk(
                issue="激励风险信息不足",
                evidence="未提供明确 KPI/奖惩文本，当前仅能给通用检查。",
                fix="补充指标、奖金和处罚规则后重新体检。",
            )
        )
    return findings


def analyze_bias(text: str, top_n: int = 6) -> list[BiasFinding]:
    source = text.lower()
    findings: list[BiasFinding] = []
    for card in BIAS_CARDS:
        score = keyword_hits(source, card.trigger_keywords)
        if score > 0:
            findings.append(BiasFinding(card.name, card.signal, card.debias_action, score))
    findings.sort(key=lambda x: x.score, reverse=True)
    if not findings:
        findings = [
            BiasFinding(
                "潜在确认偏误",
                "当前文本中缺少主动反证表述。",
                "在下一轮分析中强制增加反证检索步骤。",
                1,
            )
        ]
    return findings[:top_n]


def load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def build_chunks(path: Path) -> list[EvidenceChunk]:
    raw = load_text(path)
    if not raw:
        return []
    lines = raw.splitlines()
    chunks: list[EvidenceChunk] = []
    buffer: list[str] = []
    start = 1

    def flush(end_line: int) -> None:
        if not buffer:
            return
        text = " ".join([b.strip() for b in buffer]).strip()
        if not text:
            return
        snippet = text[:220] + ("..." if len(text) > 220 else "")
        chunks.append(EvidenceChunk(str(path), start, end_line, 0, snippet))

    for idx, line in enumerate(lines, start=1):
        if line.strip() == "":
            flush(idx)
            buffer = []
            start = idx + 1
            continue
        if line.startswith("#") and buffer:
            flush(idx - 1)
            buffer = [line]
            start = idx
            continue
        if not buffer:
            start = idx
        buffer.append(line)
    flush(len(lines))
    return chunks


def retrieve_evidence(
    query: str,
    model_set: list[ModelCard],
    sources: list[Path],
    top_k: int,
) -> list[EvidenceChunk]:
    query_terms = tokenize(
        " ".join([query, " ".join([m.name + " " + m.english for m in model_set])])
    )
    if not query_terms:
        return []

    all_chunks: list[EvidenceChunk] = []
    for path in sources:
        all_chunks.extend(build_chunks(path))

    scored: list[EvidenceChunk] = []
    for chunk in all_chunks:
        chunk_terms = tokenize(chunk.snippet)
        overlap = len(set(query_terms).intersection(set(chunk_terms)))
        if overlap <= 0:
            continue
        scored.append(
            EvidenceChunk(
                path=chunk.path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                score=overlap,
                snippet=chunk.snippet,
            )
        )
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:top_k]


def make_conclusion(problem: ProblemCard, models: list[ModelCard]) -> str:
    if not models:
        return f"围绕“{problem.query}”先做小范围验证，再逐步扩展。"
    lead_model = models[0]
    return (
        f"优先用“{lead_model.name}”框架处理“{problem.query}”，"
        "先做低成本试点并同步建立反证与预警机制。"
    )


def build_next_experiments(problem: ProblemCard) -> list[str]:
    steps = [
        "在 48 小时内完成 Problem Card 对齐，确认目标、约束、禁区和责任人。",
        "用一周做最小试点，覆盖一个团队或一个流程节点。",
        "上线失败模式预警看板，至少跟踪 3 个领先指标。",
        "安排一次反证评审会，专门讨论如何推翻当前方案。",
        "第 14 天输出 AAR 复盘，决定扩展、调整或终止。",
    ]
    if problem.risk_appetite == "low":
        steps.insert(2, "增加合规与安全审查闸门，关键变更先灰度再放量。")
    return steps[:5]


def render_markdown(result: AgentResult) -> str:
    pc = result.problem_card
    lines: list[str] = [
        "# 芒格能力全集 Agent - 决策备忘录",
        "",
        f"- 生成时间: {result.generated_at}",
        f"- 查询问题: {pc.query}",
        f"- 目标: {pc.goal}",
        f"- 时间范围: {pc.time_horizon}",
        f"- 风险偏好: {pc.risk_appetite}",
        "",
        "## 1) 结论",
        "",
        result.conclusion,
        "",
        "## 2) 关键假设（含证伪方式）",
        "",
    ]
    for idx, item in enumerate(pc.assumptions, start=1):
        lines.append(f"{idx}. 假设: {item['assumption']}")
        lines.append(f"   证伪: {item['falsify']}")

    lines.extend(["", "## 3) 反向思考失败地图（Top-10）", ""])
    for idx, fm in enumerate(result.failure_map, start=1):
        lines.append(f"{idx}. 失败模式: {fm.title}")
        lines.append(f"   预警信号: {fm.signal}")
        lines.append(f"   规避动作: {fm.mitigation}")

    lines.extend(["", "## 4) 调用模型（5-9）", ""])
    for idx, model in enumerate(result.model_set, start=1):
        lines.append(f"{idx}. {model.name} ({model.english})")
        lines.append(f"   用法: {model.summary}")
        lines.append(f"   边界: {model.boundary}")

    lines.extend(["", "## 5) 激励体检", ""])
    for idx, risk in enumerate(result.incentive_risks, start=1):
        lines.append(f"{idx}. 风险: {risk.issue}")
        lines.append(f"   依据: {risk.evidence}")
        lines.append(f"   建议: {risk.fix}")

    lines.extend(["", "## 6) 偏误门诊", ""])
    for idx, bias in enumerate(result.bias_findings, start=1):
        lines.append(f"{idx}. 偏误: {bias.bias}")
        lines.append(f"   信号: {bias.signal}")
        lines.append(f"   纠偏: {bias.action}")

    lines.extend(["", "## 7) 证据与引用", ""])
    if result.evidence:
        for idx, ev in enumerate(result.evidence, start=1):
            lines.append(
                f"{idx}. 证据片段（score={ev.score}）: `{ev.path}:{ev.start_line}`"
            )
            lines.append(f"   摘录: {ev.snippet}")
    else:
        lines.append("1. 暂未命中有效本地证据，请补充 source 文件。")

    lines.extend(["", "## 8) 下一步最小实验", ""])
    for idx, step in enumerate(result.next_experiments, start=1):
        lines.append(f"{idx}. {step}")
    lines.append("")
    return "\n".join(lines)


class MungerOSAgent:
    """Rule-based MVP agent based on Munger mental model workflow."""

    def __init__(self, source_files: list[Path]) -> None:
        self.source_files = source_files

    def run(
        self,
        query: str,
        goal: str,
        constraints: tuple[str, ...],
        time_horizon: str,
        risk_appetite: str,
        resources: tuple[str, ...],
        no_go: tuple[str, ...],
        kpi_text: str,
        context_text: str,
        top_k_models: int,
        top_k_evidence: int,
    ) -> AgentResult:
        problem = build_problem_card(
            query=query,
            goal=goal,
            constraints=constraints,
            time_horizon=time_horizon,
            risk_appetite=risk_appetite,
            resources=resources,
            no_go=no_go,
        )
        model_set = retrieve_models(problem, top_k=top_k_models)
        failure_map = build_failure_map(problem, top_n=10)
        incentive_risks = analyze_incentives(problem, kpi_text)
        bias_findings = analyze_bias("\n".join([query, goal, kpi_text, context_text]), top_n=6)
        evidence = retrieve_evidence(
            query="\n".join([query, goal, context_text]),
            model_set=model_set,
            sources=self.source_files,
            top_k=top_k_evidence,
        )
        conclusion = make_conclusion(problem, model_set)
        next_experiments = build_next_experiments(problem)

        return AgentResult(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            problem_card=problem,
            conclusion=conclusion,
            model_set=model_set,
            failure_map=failure_map,
            incentive_risks=incentive_risks,
            bias_findings=bias_findings,
            evidence=evidence,
            next_experiments=next_experiments,
        )


def parse_source_files(source_file: tuple[Path, ...]) -> list[Path]:
    paths = [p for p in source_file if p.exists()]
    if not paths and DEFAULT_SOURCE.exists():
        paths = [DEFAULT_SOURCE]
    dedup = unique_keep_order(str(p) for p in paths)
    return [Path(p) for p in dedup]


def load_context_files(paths: tuple[Path, ...]) -> str:
    merged: list[str] = []
    for path in paths:
        text = load_text(path)
        if not text:
            continue
        merged.append(f"[{path}]\n{text[:8000]}")
    return "\n\n".join(merged)


@click.group()
def cli() -> None:
    """芒格能力全集 Agent（MVP）"""


@cli.command()
@click.option("--query", required=True, help="要分析的问题")
@click.option("--goal", default="", help="目标描述")
@click.option("--constraint", "constraints", multiple=True, help="约束条件，可多次传入")
@click.option("--time-horizon", default="90天", help="决策时间范围")
@click.option(
    "--risk-appetite",
    default="medium",
    type=click.Choice(["low", "medium", "high"], case_sensitive=False),
    help="风险偏好",
)
@click.option("--resource", "resources", multiple=True, help="可用资源，可多次传入")
@click.option("--no-go", "no_go", multiple=True, help="不可触碰项，可多次传入")
@click.option("--kpi-text", default="", help="激励/KPI/奖惩相关文本")
@click.option(
    "--context-file",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="额外上下文文件，可多次传入",
)
@click.option(
    "--source-file",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="证据检索语料文件，可多次传入（默认自动加载芒格研究报告）",
)
@click.option("--top-k-models", default=7, show_default=True, help="召回模型数量")
@click.option("--top-k-evidence", default=6, show_default=True, help="召回证据数量")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), help="保存 Markdown 报告路径")
@click.option("--json-output", type=click.Path(dir_okay=False, path_type=Path), help="保存 JSON 结果路径")
def run(
    query: str,
    goal: str,
    constraints: tuple[str, ...],
    time_horizon: str,
    risk_appetite: str,
    resources: tuple[str, ...],
    no_go: tuple[str, ...],
    kpi_text: str,
    context_file: tuple[Path, ...],
    source_file: tuple[Path, ...],
    top_k_models: int,
    top_k_evidence: int,
    output: Path | None,
    json_output: Path | None,
) -> None:
    """执行一次结构化分析并输出决策备忘录。"""
    sources = parse_source_files(source_file)
    context_text = load_context_files(context_file)
    agent = MungerOSAgent(sources)
    result = agent.run(
        query=query,
        goal=goal,
        constraints=constraints,
        time_horizon=time_horizon,
        risk_appetite=risk_appetite,
        resources=resources,
        no_go=no_go,
        kpi_text=kpi_text,
        context_text=context_text,
        top_k_models=max(5, min(top_k_models, 9)),
        top_k_evidence=max(1, top_k_evidence),
    )
    markdown = render_markdown(result)
    console.print(Panel("Munger OS Agent 分析完成", border_style="green"))
    console.print(Markdown(markdown))

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        console.print(f"[green]已写入 Markdown:[/green] {output}")
    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(result)
        json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]已写入 JSON:[/green] {json_output}")


@cli.command()
@click.option(
    "--source-file",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="证据检索语料文件，可多次传入",
)
@click.option(
    "--memory-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("data/munger_agent_chat_memory.json"),
    show_default=True,
    help="会话记忆文件",
)
def chat(source_file: tuple[Path, ...], memory_file: Path) -> None:
    """交互式会话模式（输入 exit 结束）。"""
    sources = parse_source_files(source_file)
    agent = MungerOSAgent(sources)

    history: list[dict[str, str]] = []
    if memory_file.exists():
        try:
            history = json.loads(memory_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            history = []

    console.print(
        Panel(
            "进入 chat 模式：输入问题后回车。输入 exit 结束。",
            title="Munger OS Agent",
            border_style="blue",
        )
    )

    while True:
        query = click.prompt("你", prompt_suffix=": ").strip()
        if query.lower() in {"exit", "quit", "q"}:
            break

        context = "\n".join([f"Q:{x['q']}\nA:{x['a']}" for x in history[-3:]])
        result = agent.run(
            query=query,
            goal="",
            constraints=(),
            time_horizon="90天",
            risk_appetite="medium",
            resources=(),
            no_go=(),
            kpi_text="",
            context_text=context,
            top_k_models=7,
            top_k_evidence=4,
        )
        markdown = render_markdown(result)
        console.print(Markdown(markdown))
        history.append({"q": query, "a": result.conclusion, "ts": result.generated_at})
        history = history[-30:]

    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]会话记忆已保存:[/green] {memory_file}")


if __name__ == "__main__":
    cli()
