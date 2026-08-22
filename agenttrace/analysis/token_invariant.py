"""会话级 Token 记账不变量观测(analysis/token_invariant.py,A1)。

分析层数据块,与 ContextHealth / profile 同构,挂在 DiagnosisResult.token_invariant:
- 从 trace.events[] 读取 adapter 追加的 token/usage-duplicate / token/usage-inconsistent 事件
- 统计会话内 usage 双写范围与"非去重消费方的假设性溢出上界"
- 不做 Detector/Finding:不注册 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES,
  不进入 findings/attributions,不判因果、不做成本归因
- 仅 enable_analysis=True 时由 pipeline Stage 3 调用;默认关闭 → 零影响

确定性铁律:全部指标由 trace.events[] 确定性计算;空会话返回全零块,不虚构。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.canonical_trace import Trace


@dataclass
class TokenInvariant:
    """Token 记账不变量观测(会话级数据块,非 finding)。

    全部字段带默认值:空会话 / 无双写事件 → 全零块,不虚构数值。
    """

    duplicate_usage_steps: int = 0
    """双写步数:≥2 来源且数值一致的 (turn,step) 数。"""

    total_deduped_tokens: int = 0
    """会话内所有 step 的去重后 token 合计(input+output,按 Step.usage.total_tokens())。"""

    naive_double_count_tokens: int = 0
    """非去重消费方假设性溢出上界:每个双写 step 一整份(input+output)的合计。
    即不按 (turn,step) 去重的消费方会多算的 token 上界。
    仅含数值一致的重复 step;不一致 step 被排除,因此该值为下界而非上界。"""

    over_count_factor: float = 1.0
    """全局稀释溢出倍数 = (total_deduped + naive_double_count_tokens) / total_deduped。
    无双写时恒为 1.0;全双写时 = 2.0;部分双写时 ∈ (1.0, 2.0)。
    这是会话级摘要:非双写 step 的 1× 被混入,会稀释双写子集的真实信号。
    诊断"双写 step 被高估"应使用 double_write_multiplier(恒 2.0),不要用本字段。"""

    double_write_multiplier: float = 2.0
    """双写子集内的溢出乘数,恒 = 2.0(每个双写 step 被朴素求和恰好 2× 高估)。
    这是诊断核心信号,不被全局分母稀释。仅当 duplicate_usage_steps > 0 时有效。"""

    inconsistent_usage_steps: int = 0
    """≥2 来源但数值不一致的 (turn,step) 数(单独观测,不参与溢出计算)。"""

    dedup_required: bool = False
    """建议按 (turn,step) 去重(hedged 推荐,双写子集存在 ⇒ True,非无条件断言)。"""


def build_token_invariant(trace: Trace) -> TokenInvariant:
    """从 trace.events[] 计算会话级 Token 记账不变量观测(纯函数,确定性)。

    空会话 / 无双写事件 → 返回全零块,不虚构数值。
    不做成本归因;causal_claim=NONE。
    """
    duplicate_events = [e for e in trace.events if e.type == "token/usage-duplicate"]
    inconsistent_events = [
        e for e in trace.events if e.type == "token/usage-inconsistent"
    ]

    dup_steps = len(duplicate_events)
    inc_steps = len(inconsistent_events)

    total_deduped = sum(s.usage.total_tokens() for s in trace.all_steps())

    if dup_steps == 0:
        # 无双写:返回全零块(总去重量仍保留,供报告展示会话规模)
        return TokenInvariant(
            duplicate_usage_steps=0,
            total_deduped_tokens=total_deduped,
            naive_double_count_tokens=0,
            over_count_factor=1.0,
            double_write_multiplier=2.0,
            inconsistent_usage_steps=inc_steps,
            dedup_required=False,
        )

    naive_double = sum(e.data.get("total_tokens", 0) for e in duplicate_events)
    factor = (
        (total_deduped + naive_double) / total_deduped if total_deduped > 0 else 1.0
    )

    return TokenInvariant(
        duplicate_usage_steps=dup_steps,
        total_deduped_tokens=total_deduped,
        naive_double_count_tokens=naive_double,
        over_count_factor=round(factor, 4),  # 确定性精度:4 位小数
        double_write_multiplier=2.0,          # 恒 2.0,双写子集内乘数
        inconsistent_usage_steps=inc_steps,
        dedup_required=True,  # hedged:双写子集存在 ⇒ 建议去重
    )
