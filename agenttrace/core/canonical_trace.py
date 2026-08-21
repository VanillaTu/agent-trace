"""Canonical Trace v0.1 — 项目内部分析契约。

Canonical Trace 是 Detector 唯一面对的数据模型,与具体平台的原始日志(DSH JSONL /
Langfuse / OpenTelemetry)解耦。任何新平台接入只需新增一个 Adapter,输出统一的
Canonical Trace,Detector 无需改动。

设计原则:
- 只保留 Detector 真正需要的语义层,不搬入 DSH 的所有原始字段
- 一个 step 可有多个 tool_calls(数组,已验证真实数据最多 2 个)
- usage 是 per-step 精确计量(inputTokens 为完整上下文,非增量)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Usage:
    """单个 step 的 token 计量(对应 DSH assistant/chunk type=usage)。

    字段来源(源码 TokenUsage 5 字段):
    - input_tokens / output_tokens / cache_read_tokens:Observed + Supported
    - cache_write_tokens / reasoning_tokens:Defined + Unobserved(当前样本未见)

    原则:missing ≠ 0。
    cache_write_tokens / reasoning_tokens 用 Optional[int](None 表示"未上报"),
    不要用默认 0 伪装成"观测到了 0"。Detector 遇到 None 必须能优雅处理,
    不能因缺失而报错。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: Optional[int] = None  # Defined + Unobserved
    reasoning_tokens: Optional[int] = None  # Defined + Unobserved

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def billed_input_tokens(self) -> int:
        """计费 input = uncached input + cacheRead + cacheWrite(源码注释定义)。

        缺失字段按 0 参与计算(这里是计费语义,None 视为未产生计费)。
        """
        return (
            self.input_tokens
            + (self.cache_read_tokens or 0)
            + (self.cache_write_tokens or 0)
        )


@dataclass
class TraceEvent:
    """非 step 的独立事件层(compaction/retry/workflow/subagent 等)。

    不塞进 Step,因为它们的生命周期跨越 step/turn,且 token-meter 按
    (turn, step) 去重时无法归到单一 step。
    """

    type: str  # 如 "compaction/start"、"llm/retry"
    time: int = 0
    seq: int = 0
    turn_id: Optional[int] = None
    step_id: Optional[int] = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """一次工具调用及其结果(通过 call_id 关联调用与结果)。"""

    call_id: str
    tool_name: str
    arguments: str  # 原始参数字符串(JSON)
    result: str = ""
    is_error: bool = False
    truncated: bool = False
    # 检测时填充:指纹(见 normalize.py)
    fingerprint: Optional[str] = None


@dataclass
class Step:
    """一个 step = 一次模型调用 + 工具执行,是归因的最小单元。"""

    step_id: int
    turn_id: int
    start_time: int = 0
    end_time: int = 0
    usage: Usage = field(default_factory=Usage)
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""  # thinking 内容(可算 thinking 占比)
    text: str = ""  # 正文输出


@dataclass
class Turn:
    """一个 turn = 一轮用户输入到 agent 完成响应的过程,含一个或多个 step。"""

    turn_id: int
    start_time: int = 0
    end_time: int = 0
    steps: list[Step] = field(default_factory=list)

    def all_tool_calls(self) -> list[ToolCall]:
        return [tc for st in self.steps for tc in st.tool_calls]


@dataclass
class Trace:
    """完整的 Canonical Trace。"""

    session_id: str
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    # 独立事件层(compaction/retry/workflow/subagent 等,跨 step 生命周期)
    events: list[TraceEvent] = field(default_factory=list)

    def all_steps(self) -> list[Step]:
        return [st for t in self.turns for st in t.steps]

    def all_tool_calls(self) -> list[ToolCall]:
        return [tc for st in self.all_steps() for tc in st.tool_calls]

    def total_usage(self) -> Usage:
        total = Usage()
        for st in self.all_steps():
            total.input_tokens += st.usage.input_tokens
            total.output_tokens += st.usage.output_tokens
            total.cache_read_tokens += st.usage.cache_read_tokens
        return total
