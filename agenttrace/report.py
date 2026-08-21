"""报告生成器 v0.5:五段式输出 + summary 概览。

每个 finding 输出五段,让工程师拿到就能判断"这个值不值得调查":

    Signal        —— 诊断信号(一句话说明检测到了什么现象)
    Evidence      —— 证据定位(session/turn/step)
    Observed      —— 观测到的具体值
    Attribution   —— 归因(可避免成本 / 观测 / 无成本)
    Interpretation—— 解释(这是缺陷/观测/标记/可靠性,如何行动)

summary 头部:
    - 每个 rule 的 finding 数
    - 可归因成本(仅 cost kind)
    - reliability/observation 计数
    - evidence 覆盖率

分析层(enable_analysis=True,默认关闭):
    - 每条 finding 追加 Confidence + Counter-evidence(反证)
    - Summary 追加"综合判断"块(会话画像 top-3 + 健康度概述)

铁律保持:
    - 四组语义隔离,禁止 "Total wasted tokens"
    - causal_claim = NONE(Interpretation 段明确不越界)
    - 确定性铁律:enable_analysis=False 时输出与 v0.5 逐字节一致
"""

from __future__ import annotations

from collections import defaultdict

from .core.canonical_trace import Trace
from .detectors.base import Finding

KIND_ORDER = ("cost", "observation", "flag", "reliability")
KIND_LABELS = {
    "cost": "Cost defects (候选可避免成本)",
    "observation": "Resource observations (观测资源量)",
    "flag": "Statistical flags (统计强度标记)",
    "reliability": "Reliability events (可靠性事件)",
}

# 每个 rule 的五段式模板(Signal / Interpretation 是规则语义,写死在报告层)
RULE_META = {
    "TOOL-001": {
        "signal": "重复工具调用:同一工具+等价参数被执行多次",
        "interpretation": "成本缺陷(候选可避免)——第 2..N 次调用可能冗余,建议核查循环/缓存逻辑",
    },
    "CMP-001": {
        "signal": "上下文压缩(compaction/prune)发生,shadowed 一批 token",
        "interpretation": "观测(非缺陷)——压缩可能是必要的上下文管理,记录 shadowed 量供容量分析",
    },
    "THINK-001": {
        "signal": "推理强度异常高(reasoning tokens 超过 baseline 分位)",
        "interpretation": "统计标记(非缺陷)——仅表明该 step 推理消耗高,不能证明其不必要",
    },
    "RETRY-001": {
        "signal": "模型调用发生重试(retry event)",
        "interpretation": "可靠性事件(非缺陷)——指示 provider/网络/配额问题,无 token 归因(失败尝试 usage=0)",
    },
    "SUB-001": {
        "signal": "发生 subagent 委托(descriptor 事件)",
        "interpretation": "拓扑观测(非缺陷)——记录委托模式(mode/provider),不判断使用是否合理",
    },
}


def _evidence_loc(f: Finding) -> str:
    """证据定位:从 evidence 提取 turn/step。"""
    if f.evidence:
        e = f.evidence[0]
        return f"turn {e.turn_id} step {e.step_id}"
    return "turn ? step ?"


def _observed(f: Finding, att) -> str:
    """观测值:从 details + attribution 提取。"""
    d = f.details
    parts = []
    if "shadowed_token_count" in d:
        parts.append(f"shadowed={d['shadowed_token_count']} tokens")
    if "reasoning_tokens" in d:
        parts.append(f"reasoning={d['reasoning_tokens']} tokens")
    if "retry_count" in d:
        parts.append(f"retry_count={d['retry_count']}")
    if "occurrence_indexes" in d:
        parts.append(f"occurrences={f.occurrences}")
    if "mode" in d:
        parts.append(f"mode={d['mode']} provider={d['provider']}")
    if "error_code" in d:
        parts.append(f"error={d['error_code']} outcome={d.get('outcome')}")
    return ", ".join(parts) if parts else "—"


def _attribution_line(att) -> str:
    """归因段:kind 语义,不越界。"""
    if att is None:
        return "无归因"
    if att.kind == "cost":
        return f"候选可避免成本 {att.total_tokens} tokens(direct={att.direct.tokens}, propagated={att.propagated.tokens}, unattributed={att.unattributed_tokens})"
    if att.kind == "reliability":
        return "无 token 归因(失败尝试 usage=0)"
    return f"观测资源 {att.total_tokens} tokens(非 avoidable)"


def _confidence_line(f: Finding) -> str:
    """置信度行(标注语义:证据强度,非成本可信度)。"""
    return f"**Confidence:** {f.confidence:.2f}(证据强度,非成本可信度)"


def _counter_evidence_lines(f: Finding) -> list[str]:
    """反证行:明确标注"可能推翻此发现的方向",与 Evidence 段分离。"""
    if not f.counter_evidence:
        return ["**Counter-evidence(可能推翻此发现的方向):** 无反证"]
    lines = ["**Counter-evidence(可能推翻此发现的方向):**"]
    for ce in f.counter_evidence:
        suffix = f"({ce.detail})" if ce.detail else ""
        lines.append(f"  - {ce.direction}{suffix} [来源: {ce.source}]")
    return lines


def _render_profile_block(profile) -> list[str]:
    """渲染综合判断块(top-3 + 健康度概述)。"""
    lines: list[str] = ["", "### 综合判断"]
    if not profile.items:
        lines.append("无可调查项。")
    else:
        for i, item in enumerate(profile.items, 1):
            cost_str = (
                f"{item.attributable_cost} tokens"
                if item.attributable_cost > 0
                else "无成本维度"
            )
            ce_str = (
                f"(反证 {item.counter_evidence_count} 条)"
                if item.counter_evidence_count
                else ""
            )
            lines.append(
                f"{i}. `{item.rule_id}#{item.finding_idx}` {item.reason}{ce_str} "
                f"— 可归因成本 {cost_str},置信度 {item.confidence:.2f}"
            )
    lines.append(f"**健康度概述:** {profile.health_summary}")
    return lines


def render_report(
    trace: Trace,
    findings: list[Finding],
    attributions,
    enable_analysis: bool = False,
    profile=None,
) -> str:
    """渲染诊断报告。

    enable_analysis(默认 False):开启分析层渲染(per-finding 置信度/反证 +
    Summary 综合判断块)。关闭时输出与 v0.5 逐字节一致。
    profile:会话画像;开启且为 None 时惰性计算(调用方通常从 pipeline 传入)。
    """
    lines: list[str] = []
    lines.append("# AgentTrace Diagnostic Report")
    lines.append("")
    lines.append(f"会话: `{trace.session_id}`  模型: `{trace.model or 'unknown'}`")
    lines.append(
        f"turns: {len(trace.turns)}  steps: {len(trace.all_steps())}  "
        f"tool_calls: {len(trace.all_tool_calls())}"
    )
    lines.append("")

    if not findings:
        lines.append("未检出。")
        # 空 findings 时:分析层开启仍渲染"综合判断"块(0 条 → 无可调查项),
        # 满足 session-profile spec 的 0 条场景;关闭时保持 v0.5 逐字节一致。
        if enable_analysis and profile is None:
            from .analysis.profile import build_profile
            profile = build_profile([], [])
        if enable_analysis:
            lines.extend(_render_profile_block(profile))
        return "\n".join(lines)

    if enable_analysis and profile is None:
        from .analysis.profile import build_profile
        profile = build_profile(findings, attributions)

    # attribution 配对
    att_by_key: dict[tuple[str, int], object] = {}
    for a in attributions:
        att_by_key[(a.rule_id, a.finding_idx)] = a

    # ===== Summary 头部 =====
    by_rule_count: dict[str, int] = defaultdict(int)
    for f in findings:
        by_rule_count[f.rule_id] += 1

    lines.append("## Summary")
    for rule in sorted(by_rule_count):
        lines.append(f"- {rule}: {by_rule_count[rule]} 个 finding")
    # 可归因成本(仅 cost kind)
    cost_total = sum(
        a.total_tokens for a in attributions if a.kind == "cost"
    )
    reliability_n = by_rule_count.get("RETRY-001", 0)
    obs_n = by_rule_count.get("CMP-001", 0) + by_rule_count.get("SUB-001", 0)
    flag_n = by_rule_count.get("THINK-001", 0)
    lines.append(f"- 可归因成本(仅 cost): {cost_total} tokens")
    lines.append(f"- 可靠性事件: {reliability_n}  |  观测信号: {obs_n}  |  统计标记: {flag_n}")
    # evidence 覆盖率
    with_ev = sum(1 for f in findings if f.evidence)
    cov = with_ev / len(findings) * 100 if findings else 0
    lines.append(f"- Evidence 覆盖率: {cov:.0f}%({with_ev}/{len(findings)})")
    # 综合判断块(仅分析层开启时渲染)
    if enable_analysis:
        lines.extend(_render_profile_block(profile))
    lines.append("")

    # ===== 按 kind 分组的五段式 =====
    by_kind: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_kind[f.kind].append(f)

    for kind in KIND_ORDER:
        items = by_kind.get(kind)
        if not items:
            continue
        lines.append(f"## {KIND_LABELS[kind]}")
        items.sort(key=lambda f: f.rule_id)
        for f in items:
            att = att_by_key.get((f.rule_id, f.finding_idx))
            meta = RULE_META.get(f.rule_id, {"signal": "—", "interpretation": "—"})
            lines.append("")
            lines.append(f"### {f.rule_id} `{f.type}`")
            lines.append("")
            lines.append(f"**Signal:** {meta['signal']}")
            lines.append(f"**Evidence:** {_evidence_loc(f)}")
            lines.append(f"**Observed:** {_observed(f, att)}")
            lines.append(f"**Attribution:** {_attribution_line(att)}")
            lines.append(f"**Interpretation:** {meta['interpretation']}")
            # 置信度 + 反证(仅分析层开启时渲染)
            if enable_analysis:
                lines.append(_confidence_line(f))
                lines.extend(_counter_evidence_lines(f))
            # 证据链(若有)
            chain = f.details.get("evidence_chain")
            if chain is not None:
                lines.append("")
                lines.append("证据链:")
                for line in chain.render().split("\n"):
                    lines.append(f"  {line}")

    return "\n".join(lines)
