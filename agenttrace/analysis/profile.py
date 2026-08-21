"""会话级综合画像(analysis/profile.py)。

输入:分析阶段后的 findings + attributions。
输出:按 "可归因成本 × 置信度" 确定性排序的 top-3 + 一句话健康度概述。

归因边界:
- 成本数字只聚合 attribution 已产出的数字(仅 kind == "cost"),不发明 token。
- 排序键用 Finding.confidence(精化后),非 attribution 拷贝值(Y2)。
- 非 cost finding 成本维度按 0 计,排在有成本 finding 之后。
- 不跨 kind 求和,不做因果断言。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..detectors.base import Finding

# 每个 rule 的一句话理由(画像 top-N 展示用)
REASON_BY_RULE = {
    "TOOL-001": "重复工具调用(候选可避免成本)",
    "CMP-001": "上下文压缩 shadowed 观测",
    "THINK-001": "推理强度统计标记",
    "RETRY-001": "模型重试可靠性事件",
    "SUB-001": "subagent 委托拓扑观测",
    "TOOL-004": "无效参数重试(可避免失败尝试标记)",
}


@dataclass
class ProfileItem:
    """画像 top-N 中的一条。"""

    rule_id: str
    finding_idx: int
    attributable_cost: int  # 仅 kind==cost,否则 0
    confidence: float      # Finding.confidence(精化后)
    reason: str            # 一句话理由(规则表 interpretation)
    counter_evidence_count: int


@dataclass
class SessionProfile:
    """会话级画像:top-N 条目 + 健康度概述。"""

    items: list[ProfileItem] = field(default_factory=list)
    health_summary: str = ""


def _attribution_by_key(attributions) -> dict[tuple[str, int], object]:
    return {(a.rule_id, a.finding_idx): a for a in attributions}


def _health_summary(findings: list[Finding], attributions) -> str:
    """一句话健康度概述(确定性模板,禁止因果断言)。"""
    n_cost = sum(1 for f in findings if f.kind == "cost")
    n_obs = sum(1 for f in findings if f.kind == "observation")
    n_flag = sum(1 for f in findings if f.kind == "flag")
    n_rel = sum(1 for f in findings if f.kind == "reliability")

    cost_total = sum(a.total_tokens for a in attributions if a.kind == "cost")
    n_ce = sum(len(f.counter_evidence) for f in findings)

    return (
        f"detector 信号分布(cost 缺陷 {n_cost} 处"
        f"(候选可避免 ~{cost_total} tokens)、观测 {n_obs} 处、"
        f"统计标记 {n_flag} 处、可靠性 {n_rel} 处;反证 {n_ce} 条)"
    )


def build_profile(findings: list[Finding], attributions) -> SessionProfile:
    """构建会话级综合画像(纯函数,确定性)。

    排序键:score = attributable_cost × confidence 降序;
    tie-break:confidence 降序 → rule_id 升序 → finding_idx 升序。
    """
    att_by_key = _attribution_by_key(attributions)

    items: list[ProfileItem] = []
    for f in findings:
        att = att_by_key.get((f.rule_id, f.finding_idx))
        cost = 0
        if att is not None and getattr(att, "kind", None) == "cost":
            # cost kind 的 total_tokens 恒为 int(attribution/base.py),
            # `or 0` 仅防御性兜底;None(not applicable)不会出现在此分支。
            cost = getattr(att, "total_tokens", 0) or 0
        items.append(
            ProfileItem(
                rule_id=f.rule_id,
                finding_idx=f.finding_idx,
                attributable_cost=cost,
                confidence=f.confidence,
                reason=REASON_BY_RULE.get(f.rule_id, f.rule_id),
                counter_evidence_count=len(f.counter_evidence),
            )
        )

    # 确定性排序:tie-break 全确定(无随机)
    items.sort(
        key=lambda it: (
            -(it.attributable_cost * it.confidence),
            -it.confidence,
            it.rule_id,
            it.finding_idx,
        )
    )

    top = items[:3]
    health = _health_summary(findings, attributions)
    if top:
        # design D4 模板收尾"建议优先核查 {top_rule}"(Pro 评审对齐项)
        health += f";建议优先核查 {top[0].rule_id}"
    return SessionProfile(items=top, health_summary=health)
