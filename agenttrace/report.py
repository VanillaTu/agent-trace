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
    - 每条 finding 追加 Recommendation(调优建议,补全四元组"建议"维)+ Confidence + Counter-evidence(反证)
    - Summary 追加"综合判断"块(会话画像 top-3 + 健康度概述)
    - Summary 追加"上下文健康度"块(CTX-001,会话级观测,非 finding)
       —— 窗口字段未知时占用显示 not applicable,不虚构压力结论

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
# recommendation 是分析层(enable_analysis=True)追加的"调优建议"段,补全四元组"建议"维。
# 全部确定性、行动导向,并守归因边界:不虚构成本、不为无成本(flag/reliability)推荐"能回收 token"。
RULE_META = {
    "TOOL-001": {
        "signal": "重复工具调用:同一工具+等价参数被执行多次",
        "interpretation": "成本缺陷(候选可避免)——第 2..N 次调用可能冗余,建议核查循环/缓存逻辑",
        "recommendation": "核查第 2..N 次调用的语义:若为幂等调用可由缓存/去重消除;非幂等需显式控制并发。可回收性取决于调用语义,非保证。",
    },
    "CMP-001": {
        "signal": "上下文压缩(compaction/prune)发生,shadowed 一批 token",
        "interpretation": "观测(非缺陷)——压缩可能是必要的上下文管理,记录 shadowed 量供容量分析",
        "recommendation": "记录 shadowed 量作容量基线;若压缩高发,评估精简注入上下文/记忆策略以降低压缩触发。",
    },
    "THINK-001": {
        "signal": "推理强度异常高(reasoning tokens 超过 baseline 分位)",
        "interpretation": "统计标记(非缺陷)——仅表明该 step 推理消耗高,不能证明其不必要",
        "recommendation": "抽查该 step 推理是否对应复杂任务(非异常);仅统计标记,不构成缺陷,慎做成本结论。",
    },
    "RETRY-001": {
        "signal": "模型调用发生重试(retry event)",
        "interpretation": "可靠性事件(非缺陷)——指示 provider/网络/配额问题,无 token 归因(失败尝试 usage=0)",
        "recommendation": "检查 provider/网络/配额;失败尝试无 token 归因,重试属容错,重点关注是否需重试退避/限流策略。",
    },
    "SUB-001": {
        "signal": "发生 subagent 委托(descriptor 事件)",
        "interpretation": "拓扑观测(非缺陷)——记录委托模式(mode/provider),不判断使用是否合理",
        "recommendation": "确认子代理委托是否必要(mode/provider);频繁 fork 时评估开销,不判断使用合理性。",
    },
    "TOOL-004": {
        "signal": "无效参数重试:工具调用因参数错误失败,同类重试成功",
        "interpretation": "模式标记(可避免的失败尝试)——失败 attempt 无 usage,不估算 token 成本;建议核查参数构造逻辑",
        "recommendation": "核查工具参数构造逻辑(为何首次参数错);失败 attempt 无 token 归因,属可避免的失败尝试,重点在防错而非回收成本。",
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
    if "error_pattern" in d:
        parts.append(
            f"tool={d['tool_name']} error={d['error_pattern']} retry={d['retry_evidence']}"
        )
    return ", ".join(parts) if parts else "—"


def _attribution_line(att) -> str:
    """归因段:kind 语义,不越界。"""
    if att is None:
        return "无归因"
    if att.kind == "cost":
        return f"候选可避免成本 {att.total_tokens} tokens(direct={att.direct.tokens}, propagated={att.propagated.tokens}, unattributed={att.unattributed_tokens})"
    if att.kind == "reliability":
        return "无 token 归因(失败尝试 usage=0)"
    if att.kind == "flag":
        # 模式标记(TOOL-004):失败 attempt 无 usage,tokens=not applicable,
        # 不是 0;不把成功重试的 usage 算进失败 attempt。
        return "无 token 归因(失败 attempt 无 usage,tokens=not applicable)"
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


def _render_context_health_block(ch) -> list[str]:
    """渲染上下文健康度块(CTX-001,分析层观测)。

    纯函数、确定性。语义边界:
    - 无工具调用时重复率显示"无工具调用",非 "0%"(None = not applicable,不是 0);
    - 无真实窗口字段时窗口/占用率显示 not applicable,不虚构窗口、不产虚假压力;
    - 块内不出现 token 成本数字或因果断言("浪费"/"导致"/"Total wasted"),
      压力行只陈述"占用高关联退化风险(相关性非因果)"。
    """
    lines: list[str] = ["", "### 上下文健康度(CTX-001)"]

    rate_str = (
        "无工具调用" if ch.repeat_rate is None else f"{ch.repeat_rate:.1%}"
    )
    window_str = (
        f"{ch.window_tokens} tokens(来源 {ch.window_source})"
        if ch.window_tokens is not None
        else f"not applicable(来源 {ch.window_source})"
    )
    occupancy_str = (
        "not applicable" if ch.occupancy_ratio is None else f"{ch.occupancy_ratio:.1%}"
    )

    lines.append(
        f"- 当前上下文: {ch.current_context_tokens} tokens(input + cache_read)"
    )
    lines.append(f"- 峰值上下文: {ch.peak_context_tokens} tokens")
    lines.append(f"- turn 数: {ch.turn_count}")
    lines.append(
        f"- 重复工具调用操作率: {rate_str}(重复 {ch.repeated_tool_calls}/{ch.total_tool_calls})"
    )
    lines.append(f"- 上下文窗口: {window_str}")
    lines.append(f"- 占用率: {occupancy_str}")
    if ch.pressure_high:
        lines.append(
            f"⚠ 上下文压力高,建议压缩(占用 {ch.occupancy_ratio:.1%} > 阈值 70%;"
            "占用高仅关联退化风险,非因果)"
        )
    return lines


def _render_token_invariant_block(ti) -> list[str]:
    """渲染架构不变量检查块(A1,分析层观测)。

    纯函数、确定性。语义边界:
    - 无双写时显示"未检测到双写";
    - 有双写时陈述观测 + 风险(非去重消费方会 2×),causal_claim=NONE;
    - 先报双写 step 精确 2×,再报全局稀释因子(B/C 修复);
    - 不出现 "wasted" / "Total wasted" / "harness bug" 等措辞;
    - 不一致步数独立显示,不并入溢出计算。
    """
    lines: list[str] = ["", "### 架构不变量检查 — Token 记账(A1)"]

    if ti is None or ti.duplicate_usage_steps == 0:
        lines.append("")
        lines.append("未检测到 usage 双写。")
        if ti is not None and ti.inconsistent_usage_steps > 0:
            lines.append(
                f"⚠ 发现 {ti.inconsistent_usage_steps} 个 (turn,step) "
                "在多事件源中 usage 数值不一致——源保真度异常,建议核查。"
            )
        return lines

    # 双写观测
    lines.append("")
    lines.append(
        f"- **双写观测**:{ti.duplicate_usage_steps} 个 (turn,step) 的 usage "
        "在 `assistant/chunk` 与 `assistant/message` 两个事件源中各出现一次且数值一致。"
    )

    # 溢出上界
    lines.append(
        f"- **去重后会话总量**:{ti.total_deduped_tokens} tokens(input+output)"
    )
    lines.append(
        f"- **非去重消费方的假设性溢出上界**:{ti.naive_double_count_tokens} tokens"
    )

    # 双写子集内乘数(核心诊断信号,先报,不被全局稀释)
    lines.append(
        f"- **双写子集内乘数**:每个双写 step 被朴素求和**精确 2× 高估**"
        f"(共 {ti.duplicate_usage_steps} 个双写 step,恒 {ti.double_write_multiplier:.0f}×)"
    )
    lines.append(
        f"- **全局稀释后溢出倍数**:{ti.over_count_factor:.2f}×"
        f"({'全双写,全局即 2×' if ti.over_count_factor >= 1.99 else '部分双写,被非双写 step 稀释'})"
    )

    # 风险陈述(守 D2:不判 bug;先报子集 2×,再报全局稀释)
    lines.append(
        f"- **风险**:不按 (turn,step) 去重的消费方(朴素 chunk+message 求和)"
        f"会对 {ti.duplicate_usage_steps} 个双写 step **精确 2× 高估** usage;"
        f"全局稀释后溢出倍数为 {ti.over_count_factor:.2f}×。"
        f"Harness 官方 token-meter 与本项目 adapter 已按 (turn,step) 去重,不受影响。"
    )

    # 不一致
    if ti.inconsistent_usage_steps > 0:
        lines.append(
            f"- ⚠ **不一致**:{ti.inconsistent_usage_steps} 个 (turn,step) "
            "在多事件源中 usage 数值不一致——源保真度异常,建议核查。"
        )

    # 去重建议(hedged,固定措辞,F2 修复去掉恒真条件分支)
    lines.append(
        f"- **去重建议**:建议按 (turn,step) 去重(hedged 推荐,非无条件断言)"
    )

    return lines


def render_report(
    trace: Trace,
    findings: list[Finding],
    attributions,
    enable_analysis: bool = False,
    profile=None,
    context_health=None,
    token_invariant=None,
) -> str:
    """渲染诊断报告。

    enable_analysis(默认 False):开启分析层渲染(per-finding 置信度/反证 +
    Summary 综合判断块 + 上下文健康度块 + 架构不变量检查块)。关闭时输出与
    v0.5 逐字节一致。
    profile:会话画像;开启且为 None 时惰性计算(调用方通常从 pipeline 传入)。
    context_health:上下文健康度观测(CTX-001);开启且为 None 时惰性计算。
    token_invariant:Token 记账不变量观测(A1);开启且为 None 时惰性计算。
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
        if enable_analysis and context_health is None:
            from .analysis.context_health import build_context_health
            context_health = build_context_health(trace)
        if enable_analysis and token_invariant is None:
            from .analysis.token_invariant import build_token_invariant
            token_invariant = build_token_invariant(trace)
        if enable_analysis:
            lines.extend(_render_profile_block(profile))
            lines.extend(_render_context_health_block(context_health))
            lines.extend(_render_token_invariant_block(token_invariant))
        return "\n".join(lines)

    if enable_analysis and profile is None:
        from .analysis.profile import build_profile
        profile = build_profile(findings, attributions)
    if enable_analysis and context_health is None:
        from .analysis.context_health import build_context_health
        context_health = build_context_health(trace)
    if enable_analysis and token_invariant is None:
        from .analysis.token_invariant import build_token_invariant
        token_invariant = build_token_invariant(trace)

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
    flag_n = by_rule_count.get("THINK-001", 0) + by_rule_count.get("TOOL-004", 0)
    lines.append(f"- 可归因成本(仅 cost): {cost_total} tokens")
    lines.append(f"- 可靠性事件: {reliability_n}  |  观测信号: {obs_n}  |  统计标记: {flag_n}")
    # evidence 覆盖率
    with_ev = sum(1 for f in findings if f.evidence)
    cov = with_ev / len(findings) * 100 if findings else 0
    lines.append(f"- Evidence 覆盖率: {cov:.0f}%({with_ev}/{len(findings)})")
    # 综合判断块 + 上下文健康度块 + 架构不变量检查块(仅分析层开启时渲染)
    if enable_analysis:
        lines.extend(_render_profile_block(profile))
        lines.extend(_render_context_health_block(context_health))
        lines.extend(_render_token_invariant_block(token_invariant))
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
            # 建议维(Recommendation,补全四元组"建议") + 置信度 + 反证(仅分析层开启时渲染)
            if enable_analysis:
                lines.append(f"**Recommendation:** {meta.get('recommendation', '—')}")
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
