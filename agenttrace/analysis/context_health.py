"""会话级"上下文健康度"观测(analysis/context_health.py,CTX-001)。

分析层数据块,与 profile(会话画像)同构,挂在 `DiagnosisResult.context_health`:
- 从 trace 计算会话级观测指标:当前上下文 tokens(末 step input + cache_read)、
  峰值、turn 数、重复工具调用操作率。
- 不做 Detector/Finding:不注册 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES,
  不进入 findings/attributions,不判因果、不做成本归因。
- 数据驱动铁律:量化"上下文压力"仅在窗口字段真实已知时给出
  (trace.metadata["context_window"]),否则占用率/压力标记置 not applicable,
  不虚构窗口、不产虚假压力结论。
- 仅 `enable_analysis=True` 时由 pipeline Stage 3 调用;默认关闭 → 零影响。

确定性铁律:全部指标由 trace 数据确定性计算;重复组排序含确定性 tie-break,
保证两次运行结果逐字段一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core.canonical_trace import Trace
from ..core.normalize import call_fingerprint

# metadata 中真实的上下文窗口字段(adapter 未来落该字段后自动启用)
WINDOW_METADATA_KEY = "context_window"
# 占位待校准常量:仅窗口真实已知时参与判定(当前无窗口数据源,恒不触发)
OCCUPANCY_HIGH_WATERMARK = 0.70


@dataclass
class ContextHealth:
    """上下文健康度数据块(会话级观测,非 finding)。

    全部字段带默认值:空会话直接 `ContextHealth()` 即全 not-applicable。
    `None` 语义 = not applicable(无工具调用 / 无真实窗口字段),不是 0。
    """

    current_context_tokens: int = 0       # 末 step 上下文(input + cache_read,M1 口径)
    peak_context_tokens: int = 0          # 所有 step 中 input + cache_read 最大值
    turn_count: int = 0
    total_tool_calls: int = 0
    repeated_tool_calls: int = 0
    repeat_rate: Optional[float] = None   # None = not applicable(total==0)
    window_tokens: Optional[int] = None   # None = 无真实窗口字段(unknown)
    window_source: str = "unknown"        # "metadata" / "unknown"
    occupancy_ratio: Optional[float] = None  # None = 窗口未知,不虚构
    pressure_high: bool = False           # 窗口已知且超阈值时才可能 True,否则恒 False
    stats_repeated_groups: list = field(default_factory=list)  # (fingerprint, 重复数, tool_name)


def _resolve_window(trace: Trace) -> tuple[Optional[int], str]:
    """解析上下文窗口:只认 trace.metadata["context_window"](真实已知)。

    B1 修复:无真实窗口字段 → (None, "unknown"),**不兜底、不虚构窗口**。
    """
    m = (trace.metadata or {}).get(WINDOW_METADATA_KEY)
    if isinstance(m, int) and m > 0:
        return m, "metadata"
    return None, "unknown"


def _ctx_tokens(step) -> int:
    """上下文口径(M1):input_tokens + cache_read_tokens(排除 cache_write)。

    `Usage.input_tokens` 语义为"uncached input";prompt caching 场景下
    cache_read 部分已占用上下文但未计入 input,必须加回。cache_write 是
    "待写入缓存",非"当前已占用",排除。
    """
    return step.usage.input_tokens + (step.usage.cache_read_tokens or 0)


def build_context_health(trace: Trace) -> ContextHealth:
    """从 trace 计算会话级上下文健康度观测(纯函数,确定性)。

    空会话(无 step)返回全 not-applicable 块,不虚构数值。
    不做成本归因;tokens=None 表示 not applicable,不是 0。
    """
    steps = trace.all_steps()
    if not steps:
        return ContextHealth()

    current = _ctx_tokens(steps[-1])
    peak = max(_ctx_tokens(s) for s in steps)
    turn_count = len(trace.turns)

    calls = trace.all_tool_calls()
    groups: dict[str, list] = {}
    for tc in calls:
        groups.setdefault(
            call_fingerprint(tc.tool_name, tc.arguments), []
        ).append(tc)

    total = len(calls)
    repeated = sum(len(g) - 1 for g in groups.values() if len(g) > 1)
    repeat_rate = (repeated / total) if total > 0 else None  # None = not applicable

    # 重复组确定性排序:重复数降序 → tool_name 升序 → fingerprint 升序(M7 tie-break)
    stats_repeated_groups = sorted(
        [(fp, len(g), g[0].tool_name) for fp, g in groups.items() if len(g) > 1],
        key=lambda t: (-t[1], t[2], t[0]),
    )

    window, source = _resolve_window(trace)
    occupancy = (current / window) if window else None  # window None → None,不虚构
    pressure = occupancy is not None and occupancy > OCCUPANCY_HIGH_WATERMARK

    return ContextHealth(
        current_context_tokens=current,
        peak_context_tokens=peak,
        turn_count=turn_count,
        total_tool_calls=total,
        repeated_tool_calls=repeated,
        repeat_rate=repeat_rate,
        window_tokens=window,
        window_source=source,
        occupancy_ratio=occupancy,
        pressure_high=pressure,
        stats_repeated_groups=stats_repeated_groups,
    )
