"""Attribution Engine 的数据结构与接口。

边界(固定不变):
- Detector 不负责算钱:只输出"存在什么候选缺陷"(Finding)
- Attribution Engine 不负责发现缺陷:只负责把成本归因到 Finding

v0.2 只回答三个问题:
1. Direct:这个 finding 直接关联哪些 execution step?(baseline + duplicates)
2. Conservative Propagation:duplicate 导致后续额外 step,只归入能从 trace 明确证明的部分
3. Cost Attribution:输出 direct / propagated / unattributed / total / confidence

铁律:
- 不把 input_tokens 全额算成 defect cost(大量是历史 context)
- 只有能明确定位到 defect-induced execution 的 step 才进入 propagated cost
- 无法拆分的 context token 标记为 unattributed,不强行归因
- 不做 counterfactual("如果没有 defect 模型肯定只花 X")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ..detectors.base import Finding


@dataclass
class DirectAttribution:
    """Direct:finding 直接关联的 steps 与其资源/成本。

    tokens 语义:
    - kind=cost:候选可避免成本(direct tokens)
    - kind=observation:观测资源量(CMP shadowed)
    - kind=flag:观测强度(THINK reasoning)
    - kind=reliability:not applicable(0/None)
    tokens 为 None 表示 not applicable,不等于 0。
    """

    baseline_step_ids: list[int] = field(default_factory=list)
    candidate_step_ids: list[int] = field(default_factory=list)
    tokens: Optional[int] = 0  # None = not applicable


@dataclass
class PropagatedAttribution:
    """Conservative Propagation:缺陷导致的后续额外 step。"""

    step_ids: list[int] = field(default_factory=list)
    tokens: Optional[int] = 0  # None = not applicable


@dataclass
class Attribution:
    """一个 finding 的完整归因。

    kind 描述"归因到了什么证据/资源"(Attribution.kind ≠ Finding.kind):
    - "cost":候选可避免成本(TOOL-001)
    - "observation":观测资源量(CMP shadowed / RETRY 事件 / THINK reasoning)
    """

    finding_id: str
    rule_id: str
    finding_idx: int = 0  # 对应 finding 在 findings 列表中的位置(用于报告配对)
    kind: str = "observation"  # cost | observation | flag | reliability(归因语义)
    direct: DirectAttribution = field(default_factory=DirectAttribution)
    propagated: PropagatedAttribution = field(default_factory=PropagatedAttribution)
    unattributed_tokens: Optional[int] = 0  # None = not applicable
    confidence: float = 0.0

    @property
    def total_tokens(self) -> int:
        """合计 tokens(None 按 0 处理,但调用方需区分 not applicable)。"""
        return (self.direct.tokens or 0) + (self.propagated.tokens or 0)

    def render(self) -> str:
        if self.kind == "cost":
            label = "候选可避免成本"
        elif self.kind == "observation":
            label = "观测资源量(非 avoidable)"
        elif self.kind == "reliability":
            label = "可靠性事件(无 token cost)"
        else:  # flag
            label = "观测强度标记(非 avoidable)"
        if self.kind == "reliability":
            return f"[{self.rule_id}] {label}(direct=NA, propagated=NA, confidence={self.confidence:.2f})"
        return (
            f"[{self.rule_id}] {label}: {self.total_tokens} tokens "
            f"(direct={self.direct.tokens}, propagated={self.propagated.tokens}, "
            f"unattributed={self.unattributed_tokens}, confidence={self.confidence:.2f})"
        )


class AttributionEngine(Protocol):
    """Attribution Engine 统一接口。

    detect(trace) 已由 Detector 完成;这里输入 trace + findings,输出 attribution。
    """

    def attribute(self, trace, findings: list[Finding]) -> list[Attribution]: ...
