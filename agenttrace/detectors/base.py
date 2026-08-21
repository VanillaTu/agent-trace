"""Detector 统一接口与公共 abstraction。

原则(v0.3):
- Detection ≠ Attribution:Detector 只输出"存在什么现象",不判断成本
- Finding.kind 描述"为什么这个 finding 存在"(语义)
- Attribution.kind 描述"它归因到了什么"(证据)
- **两者不强制绑定**:如 THINK-001 (finding=flag, attribution=observation)

kind 语义(v0.3):
    cost           候选可避免成本(TOOL-001)
    observation    观测资源量(CMP-001 shadowed)
    flag           统计强度标记(THINK-001 reasoning intensity)
    reliability    可靠性事件(RETRY-001 retry)

tokens 语义:
    not_applicable ≠ 0。0 表示"确实为 0",None 表示"不适用/未观测"。
    (RETRY-001: finding exists, attribution exists, tokens = not applicable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

from ..core.canonical_trace import Trace

# Finding.kind 允许的语义
FindingKind = Literal["cost", "observation", "flag", "reliability"]


@dataclass
class Evidence:
    """单个证据:指向具体 trace 位置 + 说明。公共 abstraction。"""

    step_id: int
    turn_id: int
    detail: str
    # 可选:观测到的值/资源(用于 Attribution 归因)
    observed_value: Optional[float] = None


@dataclass
class EvidenceChain:
    """从 trace 到 finding 的可解释证据链。所有 detector 统一产出。

    每个 link 指向一个事件/step,附带观测值 + 解释。
    """

    links: list[Evidence] = field(default_factory=list)

    def add(self, step_id: int, turn_id: int, detail: str, observed_value=None) -> None:
        self.links.append(Evidence(step_id, turn_id, detail, observed_value))

    def render(self) -> str:
        if not self.links:
            return "(no evidence)"
        lines = []
        for link in self.links:
            val = f" = {link.observed_value}" if link.observed_value is not None else ""
            lines.append(
                f"step {link.step_id} (turn {link.turn_id}): {link.detail}{val}"
            )
        lines.append("→ finding")
        return "\n".join(lines)


@dataclass
class CounterEvidence:
    """一条反证:描述"可能推翻此 finding"的证据方向。

    - direction: 反证方向描述(如"间隔大,可能是有意操作")
    - source: 来源。"rule" = 规则层静态生成;"semantic" = 未来语义层补充
      (设计预留,本次不实现)
    - detail: 可选,支撑该反证的具体观测(如间隔步数)

    语义边界:反证是"分析"不是"归因",不参与成本归因、不发明 token 成本。
    """

    direction: str
    source: str = "rule"
    detail: str = ""


@dataclass
class Finding:
    """Detector 的产出:一个诊断发现(候选缺陷/观测/标记/事件)。"""

    rule_id: str
    type: str
    severity: str  # info / low / medium / high / warning
    confidence: float  # 0.0 - 1.0
    occurrences: int
    # 语义:为什么这个 finding 存在
    kind: FindingKind = "cost"
    # 该 rule 内的序号(pipeline 赋值,用于 report 与 attribution 精确配对)
    finding_idx: int = 0
    evidence: list[Evidence] = field(default_factory=list)
    fingerprint: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    # 候选可避免 tokens(仅 kind=cost 时有意义;None = not applicable)
    # 注意:Finding 不负责算钱,这个字段由 Attribution Engine 填充
    estimated_avoidable_tokens: Optional[int] = None
    # 反证列表:分析层开启时由确定性规则填充;默认空(不改变默认行为)
    counter_evidence: list[CounterEvidence] = field(default_factory=list)


class Detector(Protocol):
    rule_id: str
    version: str

    def detect(self, trace: Trace) -> list[Finding]: ...
