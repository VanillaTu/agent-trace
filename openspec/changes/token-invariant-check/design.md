# Design — token-invariant-check (A1) · 定稿

> **架构决策**:作为分析层**会话级数据块** `TokenInvariant`(与 `ContextHealth` / `profile` 同构),**非 Detector/Finding**。理由见 D1;本设计已按 Pro 异模型评审修复 A–E(见下),满足 additive / 确定性 / causal_claim=NONE / 禁混算 wasted。

---

## Context

### 现状(v0.5+分析层,~196 测试全绿)

pipeline 为 `Trace → Adapter → Detector Registry → Finding[] → Attribution Registry → Attribution[] → (Stage 3, enable_analysis=True) 反证+置信度+画像+上下文健康度 → Report`。

`DiagnosisResult` 当前分析层字段:

```python
@dataclass
class DiagnosisResult:
    trace: Trace
    findings: list[Finding] = field(default_factory=list)
    attributions: list = field(default_factory=list)
    detector_errors: dict[str, str] = field(default_factory=dict)
    attribution_errors: dict[str, str] = field(default_factory=dict)
    profile: Optional[object] = None          # Stage 3, enable_analysis
    context_health: Optional[object] = None   # Stage 3, enable_analysis
```

### 真实数据核验(2026-08,决定性)

扫描真实 DSH 会话(`scripts/analyze_usage_doublewrite.py`),每个 (turn,step) 的 usage 在 `assistant/chunk{type:usage}` 与 `assistant/message` **各出现一次,数字完全相同**。DSH 官方 token-meter 与本项目 adapter 都已按 (turn,step) 去重 → harness 没 2×。

**证据结构**(以 session `d6820f11` 为例):

```
(turn=1, step=1): [("chunk", {inputTokens:10998, outputTokens:343, ...}),
                   ("message", {inputTokens:10998, outputTokens:343, ...})]
(turn=1, step=2): [("chunk", {inputTokens:478, outputTokens:127, ...}),
                   ("message", {inputTokens:478, outputTokens:127, ...})]
```

### 关键事实约束(决定设计走向)

1. token 双写是**源表示保真度观测**(usage 在两个事件源各一份),不是 agent 行为信号 → **不进 Finding/Detector 体系**,挂 `DiagnosisResult` 分析层数据字段。
2. **双写证据在 raw 层**:canonical `Step.usage` 是去重后的单值(来自 chunk;message 的 usage 被 adapter 忽略)→ 必须由 adapter 在解析时保留证据,否则分析层无从计算。
3. 报告 `test_disable_analysis_byte_identical_to_v05`(逐字节金钟罩)要求默认输出不变 → 新块必须严格 gate 在 `enable_analysis=True`,默认路径不渲染。

### 硬约束(不可违背)

- 归因必须有证据;`tokens=None`≠0;规则由真实数据决定
- 报告×该块禁止混算 "Total wasted tokens"
- 三层评判(本块=规则/观测层)
- **additive**:不改现有 detector/attribution 行为,默认输出逐字节不变
- `causal_claim=NONE`:不判 harness bug,只报观测+风险

---

## Goals / Non-Goals

### Goals

1. 新增分析层会话级数据块 `TokenInvariant`(`agenttrace/analysis/token_invariant.py`):统计会话内 usage 双写的范围与"非去重消费方的假设性溢出上界",并给出 hedged 去重建议。
2. adapter 在解析原始 chunk/message 时,对 ≥2 来源且数值一致的 (turn,step) 发 `token/usage-duplicate` 事件,对数值不一致的发 `token/usage-inconsistent` 事件(源保真观测,进 `Trace.events[]`)。
3. `render_report(enable_analysis=True)` 在「上下文健康度」旁追加「架构不变量检查(Token 记账)」块;默认路径(`enable_analysis=False`)逐字节不变。

### Non-Goals

- **不做 Detector/Finding**,不注册 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`
- **不判 harness bug**:只报观测+风险(非去重消费方会 2×),`causal_claim=NONE`
- **不把溢出量写成 "Total wasted tokens"**:只能标"非去重消费方的假设性溢出上界"
- 数值相同时不计算"两份差"(恒 0,无意义)
- 不改变哪份 usage 获胜、不改遍历/去重顺序、不改 schema
- 不修改 `canonical_trace.py` 的 `Step` / `Usage` / `Trace` 数据模型

---

## Decisions(含评审 A–E 对应)

### D0 — 形态:会话级数据块,非 Finding

- `TokenInvariant` 是 `@dataclass`,挂在 `DiagnosisResult.token_invariant`(与 `ContextHealth` / `profile` 同构)
- 不注册 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`
- 不进 findings / attributions 列表
- 仅 `enable_analysis=True` 时由 Stage 3 惰性构建

**理由**:双写是源保真度观测,不是 agent 行为信号;Finding 体系承载的是"诊断发现",`TokenInvariant` 承载的是"架构观测"。

### D1 — 归属(评审 A):双写观测放 `Trace.events[]`

- adapter 在解析时,对每个 (turn,step) 收集 `[(source, usage_dict)]`
- 末尾对 sources≥2 的项,生成 `TraceEvent` 放入 `trace.events[]`
- **不放 canonical `Step` 字段**——`Step.usage` 是去重后的单值,双写是源层观测,放 Step 会混淆语义模型与源层溯源

**`events[]` 承载能力验证**:

- `TraceEvent` 已有 `type` / `turn_id` / `step_id` / `data` 字段,足以承载双写观测
- `Step.usage` 保持 adapter 现行去重逻辑(取 chunk 的 usage),不变
- 现有 detector 全部按 `event.type` 过滤(见下表),新增事件类型不会被误消费

| Detector | 监听的事件类型 | 是否受新增事件影响 |
|----------|--------------|------------------|
| TOOL-001 | 不使用 events | 否 |
| CMP-001 | `compaction/prune` | 否 |
| THINK-001 | 不使用 events | 否 |
| RETRY-001 | `llm/retry`, `llm/finish/*` | 否 |
| SUB-001 | `subagent/descriptor` | 否 |
| TOOL-004 | 不使用 events | 否 |

**风险与缓解**:golden 基线 `build_comprehensive_trace()` 手动构造 trace,不经过 adapter,因此 `events[]` 不变,金钟罩不受影响。adapter 真实路径下 events[] 新增条目,但所有 detector 按 type 过滤,不受影响。若未来新增 detector 遍历所有 events 不做 type 过滤,则需在 detector 实现中显式排除 `token/usage-*` 前缀事件。

### D2 — 措辞(评审 B):观测+风险,不判 bug

- 报告措辞:"观测到 N 个 (turn,step) 的 usage 在两个事件源(`assistant/chunk` 与 `assistant/message`)各出现一次且数值一致"
- 风险陈述:"不按 (turn,step) 去重的消费方会精确 2× 高估这些 step 的 usage"
- `causal_claim=NONE`:**不**判"harness 现在算错 2×",不归因于任何组件
- 数值不一致时陈述:"N 个 (turn,step) 的 usage 在多事件源中出现且数值不一致——源保真度异常,建议核查"

### D3 — 溢出量(评审 C):假设性溢出上界,非 wasted

- `naive_double_count_tokens` = 每个双写 step 的**一整份**(input_tokens + output_tokens,即去重后那份)的合计
- 这是"非去重消费方会多算的 token 上界"——若该消费方按 chunk+message 朴素求和,会多算恰好这么多
- `over_count_factor` = 全局溢出倍数,定义为 `(total_deduped + naive_double_count_tokens) / total_deduped`,其中 `total_deduped` 为会话所有 step 的 `total_tokens()` 合计
  - 全双写(每个 step 都有 duplicate 事件)⇒ `over_count_factor = 2.0`
  - 无双写 ⇒ `over_count_factor = 1.0`
  - 部分双写 ⇒ `1.0 < over_count_factor < 2.0`
  - 该因子**仅限定在双写子集内解释**:`over_count_factor` 只在双写存在时有意义;无双写时恒为 1.0
- 严禁命名/并入 "Total wasted tokens",标"非去重消费方的假设性溢出上界"

### D4 — 边界(评审 D):不一致单独建类,dedup 为 hedged

- ≥2 来源但数值不一致 → 发 `token/usage-inconsistent` 事件,**不**并入 `token/usage-duplicate`
- `TokenInvariant.inconsistent_usage_steps` 统计不一致步数(独立字段)
- `dedup_required` = `True` 当 `duplicate_usage_steps > 0`(即双写子集存在时为 True),`False` 否则
- 这是 **hedged 推荐**:只建议"按 (turn,step) 去重",不无条件断言"必须去重"
- 不一致的 step **不参与** `naive_double_count_tokens` 与 `over_count_factor` 计算(因为数值不一致时无法确定哪份是"正确"的)

### D5 — Additive(评审 E):默认逐字节不变

- adapter **只追加** `token/usage-duplicate` 和 `token/usage-inconsistent` 事件到 `trace.events[]`,**不**改:
  - 哪份 usage 获胜(仍取 chunk,现有逻辑不变)
  - 遍历/去重顺序
  - `Step.usage` 赋值逻辑
  - 任何现有字段
- 新块严格 gate `enable_analysis=True`:
  - `DiagnosisResult.token_invariant` 仅在 `enable_analysis=True` 时填充,否则为 `None`
  - 报告渲染仅在 `enable_analysis=True` 时追加块
- 金钟罩 `test_disable_analysis_byte_identical_to_v05` 保持绿:golden trace 不经过 adapter,events[] 不变;默认路径不渲染新块

---

## Schema & API

### 1. Adapter 事件生成(`agenttrace/adapters/dsh_adapter.py`)

在 `parse_dsh_jsonl()` 内部新增双写观测收集逻辑:

```python
# 解析时收集:按 (turn,step) 记录 usage 来源
_usage_sources: dict[tuple[int, int], list[tuple[str, dict]]] = defaultdict(list)

# 在 assistant/chunk type=usage 分支(现有,约 L139):
_usage_sources[(tid, sid)].append(("chunk", u))

# 在 assistant/message 分支(现有,约 L181),从 message 中提取 usage:
# 注意:assistant/message 的 usage 在 message.usage 字段(非 content 内)
msg_usage = msg.get("usage")
if msg_usage and isinstance(msg_usage, dict):
    _usage_sources[(tid, sid)].append(("message", msg_usage))

# 解析末尾(return trace 前),对每个 sources≥2 的 key 生成事件:
for (tid, sid), sources in _usage_sources.items():
    if len(sources) < 2:
        continue  # 单来源:不报
# 数值一致性检查:所有来源两两比较相等才算一致(F1:all-pairs,防 source_count>2 漏检)
if all(_usage_equal(usages[0], u) for u in usages[1:]):
    assert len(sources) == 2, f"Unexpected source_count={len(sources)} for (turn={tid}, step={sid})"  # A:防御 3+ 来源
    total = usages[0].get("inputTokens", 0) + usages[0].get("outputTokens", 0)
    trace.events.append(TraceEvent(
        type="token/usage-duplicate",
        turn_id=tid,
        step_id=sid,
        data={
            "source_count": len(sources),
            "total_tokens": total,
            "sources": [s for s, _ in sources],
        },
    ))
else:
    trace.events.append(TraceEvent(
        type="token/usage-inconsistent",
        turn_id=tid,
        step_id=sid,
        data={
            "source_count": len(sources),
            "sources": [s for s, _ in sources],
        },
    ))
```

**辅助函数 `_usage_equal(u1, u2)`**:

```python
def _usage_equal(u1: dict, u2: dict) -> bool:
    """比较两份 usage dict 的关键字段是否数值一致。

    已知局限:不比较 cacheWriteTokens(当前为 Defined+Unobserved,真实样本未见)。
    若 DSH 未来开始上报该字段,需将其加入 keys 元组。
    """
    keys = ("inputTokens", "outputTokens", "cacheReadTokens", "reasoningTokens")
    return all(u1.get(k) == u2.get(k) for k in keys)
```

**增量性质**:该逻辑在解析末尾追加,不影响现有解析流程的任何步骤;不改变 `Step.usage` 赋值;不修改现有 `STANDALONE_EVENT_TYPES` 集合。

### 2. TokenInvariant 数据块(`agenttrace/analysis/token_invariant.py`)

```python
"""会话级 Token 记账不变量观测(analysis/token_invariant.py,A1)。

分析层数据块,与 ContextHealth / profile 同构,挂在 DiagnosisResult.token_invariant:
- 从 trace.events[] 读取 adapter 追加的 token/usage-duplicate / token/usage-inconsistent 事件
- 统计会话内 usage 双写范围与"非去重消费方的假设性溢出上界"
- 不做 Detector/Finding:不注册 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES,
  不进入 findings/attributions,不判因果、不做成本归因
- 仅 enable_analysis=True 时由 pipeline Stage 3 调用;默认关闭 → 零影响

确定性铁律:全部指标由 trace.events[] 确定性计算;空会话返回全零/None 块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.canonical_trace import Trace


@dataclass
class TokenInvariant:
    """Token 记账不变量观测(会话级数据块,非 finding)。

    全部字段带默认值:空会话 / 无双写事件 → 全零块,不虚构。
    """

    duplicate_usage_steps: int = 0
    """双写步数:≥2 来源且数值一致的 (turn,step) 数。"""

    total_deduped_tokens: int = 0
    """会话内所有 step 的去重后 token 合计(input+output,按 Step.usage.total_tokens())。"""

    naive_double_count_tokens: int = 0
    """非去重消费方假设性溢出上界:每个双写 step 一整份(input+output)的合计。
    即不按 (turn,step) 去重的消费方会多算的 token 上界。"""

    over_count_factor: float = 1.0
    """全局稀释溢出倍数 = (total_deduped + naive_double_count_tokens) / total_deduped。
    无双写时恒为 1.0;全双写时 = 2.0;部分双写时 ∈ (1.0, 2.0)。
    这是**会话级摘要**:非双写 step 的 1× 被混入,会稀释双写子集的真实信号。
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
    # 从 events[] 提取双写/不一致事件
    duplicate_events = [
        e for e in trace.events if e.type == "token/usage-duplicate"
    ]
    inconsistent_events = [
        e for e in trace.events if e.type == "token/usage-inconsistent"
    ]

    dup_steps = len(duplicate_events)
    inc_steps = len(inconsistent_events)

    if dup_steps == 0:
        # 无双写:返回全零块
        total_deduped = sum(s.usage.total_tokens() for s in trace.all_steps())
        return TokenInvariant(
            duplicate_usage_steps=0,
            total_deduped_tokens=total_deduped,
            naive_double_count_tokens=0,
            over_count_factor=1.0,
            double_write_multiplier=2.0,
            inconsistent_usage_steps=inc_steps,
            dedup_required=False,
        )

    # 有双写:计算
    total_deduped = sum(s.usage.total_tokens() for s in trace.all_steps())
    naive_double = sum(e.data.get("total_tokens", 0) for e in duplicate_events)
    factor = (total_deduped + naive_double) / total_deduped if total_deduped > 0 else 1.0

    return TokenInvariant(
        duplicate_usage_steps=dup_steps,
        total_deduped_tokens=total_deduped,
        naive_double_count_tokens=naive_double,
        over_count_factor=round(factor, 4),  # 确定性精度:4 位小数
        double_write_multiplier=2.0,          # 恒 2.0,双写子集内乘数
        inconsistent_usage_steps=inc_steps,
        dedup_required=True,  # hedged:双写子集存在 ⇒ 建议去重
    )
```

**关键语义**:

- `total_deduped_tokens`:从 `Step.usage.total_tokens()`(adapter 已去重)求和,是会话级"正确"总量
- `naive_double_count_tokens`:从 `token/usage-duplicate` 事件 data 中取 `total_tokens` 求和,是"非去重消费方会多算的假设性上界"。**仅含数值一致的重复 step;不一致 step 被排除,因此该值是下界而非上界**(若全不一致则溢出量为 0,但实际风险未知)
- `over_count_factor`:全局稀释因子,`(total_deduped + naive) / total_deduped`,精确 4 位;无双写=1.0;全双写=2.0。**仅作会话级摘要**,诊断双写 step 风险用 `double_write_multiplier`
- `double_write_multiplier`:恒 2.0,双写子集内乘数,是诊断核心信号,不被全局分母稀释
- `inconsistent_usage_steps`:独立统计,不参与 `naive_double_count_tokens`(因为数值不一致时无法确定哪份是"正确"的)
- `dedup_required`:hedged 推荐,非断言;**不是**"必须去重"

### 3. Pipeline 集成(`agenttrace/pipeline.py`)

#### `DiagnosisResult` 新增字段

```python
@dataclass
class DiagnosisResult:
    # ... 现有字段不变 ...
    token_invariant: Optional[object] = None  # Stage 3, enable_analysis
```

#### Stage 3 惰性构建

```python
# 在 pipeline.py 的 Stage 3 块(现有 L96-103)追加:
if enable_analysis:
    from .analysis.context_health import build_context_health
    from .analysis.counter_evidence import refine_findings
    from .analysis.profile import build_profile
    from .analysis.token_invariant import build_token_invariant  # 新增

    refine_findings(result.findings, trace)
    result.context_health = build_context_health(trace)
    result.profile = build_profile(result.findings, result.attributions)
    result.token_invariant = build_token_invariant(trace)  # 新增
```

### 4. 报告渲染(`agenttrace/report.py`)

#### `render_report()` 签名扩展

```python
def render_report(
    trace: Trace,
    findings: list[Finding],
    attributions,
    enable_analysis: bool = False,
    profile=None,
    context_health=None,
    token_invariant=None,  # 新增
) -> str:
```

#### 渲染逻辑

在 Summary 块末尾,`context_health` 块之后追加 `token_invariant` 块(仅 `enable_analysis=True`):

```python
# 在 render_report 内,现有 _render_context_health_block 调用后追加:
if enable_analysis:
    lines.extend(_render_profile_block(profile))
    lines.extend(_render_context_health_block(context_health))
    lines.extend(_render_token_invariant_block(token_invariant))  # 新增
```

#### `_render_token_invariant_block()` 渲染函数

```python
def _render_token_invariant_block(ti) -> list[str]:
    """渲染架构不变量检查块(A1,分析层观测)。

    纯函数、确定性。语义边界:
    - 无双写时显示"未检测到双写";
    - 有双写时陈述观测 + 风险(非去重消费方会 2×),causal_claim=NONE;
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

    # 去重建议(hedged)
    lines.append(
        f"- **去重建议**:建议按 (turn,step) 去重(hedged 推荐,非无条件断言)"
    )

    return lines
```

#### 惰性构建(报告层)

```python
# 在 render_report 内,现有 profile/context_health 惰性构建逻辑后追加:
if enable_analysis and token_invariant is None:
    from .analysis.token_invariant import build_token_invariant
    token_invariant = build_token_invariant(trace)
```

---

## Testing

### `tests/test_token_invariant.py` 用例清单

| # | 用例名 | 覆盖 | 断言 |
|---|--------|------|------|
| 1 | `test_empty_trace_no_duplicate` | 空 trace(无 events) | `duplicate_usage_steps=0`, `naive=0`, `factor=1.0`, `dedup_required=False` |
| 2 | `test_single_source_no_duplicate` | 仅 chunk usage,无 message usage | `duplicate_usage_steps=0`, `dedup_required=False` |
| 3 | `test_dual_write_generates_duplicate_event` | 模拟 adapter 行为:同 (turn,step) 两个来源数值一致 | `token/usage-duplicate` 事件存在,`data.total_tokens` 正确 |
| 4 | `test_dual_write_numeric_inconsistent` | 同 (turn,step) 两个来源数值不一致 | `token/usage-inconsistent` 事件存在,`duplicate_usage_steps=0` |
| 5 | `test_naive_double_count_tokens` | 多 step 双写 | `naive_double_count_tokens` = sum of each step's `total_tokens` |
| 6 | `test_over_count_factor_full_dual_write` | 全部 step 双写 | `over_count_factor = 2.0` |
| 7 | `test_over_count_factor_partial_dual_write` | 部分 step 双写 | `1.0 < over_count_factor < 2.0` |
| 8 | `test_over_count_factor_no_dual_write` | 无双写 | `over_count_factor = 1.0` |
| 9 | `test_dedup_required_hedged` | 双写存在 | `dedup_required=True`(hedged,非断言) |
| 10 | `test_inconsistent_not_in_overflow` | 不一致 step 不参与溢出计算 | `naive_double_count_tokens` 不含不一致 step |
| 11 | `test_deterministic` | 同输入两次 | 输出逐字段一致 |
| 12 | `test_token_invariant_not_a_finding` | `TokenInvariant` 不在 `ALL_DETECTORS` | 确认不是 Detector 子类 |
| 13 | `test_disable_analysis_no_token_invariant` | `enable_analysis=False` | `DiagnosisResult.token_invariant is None` |
| 14 | `test_enable_analysis_adds_token_invariant` | `enable_analysis=True` | `DiagnosisResult.token_invariant is not None` |
| 15 | `test_source_count_gt_2_all_consistent` (E1) | 3+ 来源同 (turn,step) 且全一致 | 行为确定;all-pairs 判定一致 |
| 16 | `test_zero_deduped_tokens_no_div_zero` (E2) | 全部 step usage=0 | `over_count_factor=1.0`,不抛异常 |
| 17 | `test_event_data_total_matches_step_usage` (E3) | duplicate 事件 `data.total_tokens` vs `Step.usage.total_tokens()` | 两者一致 |
| 18 | `test_golden_enable_analysis_zero_block` (E4) | `build_comprehensive_trace()` + `enable_analysis=True` | `token_invariant` 非 None,数值全 0/1.0/False |
| 19 | `test_double_write_multiplier` (C) | 双写存在 | `double_write_multiplier == 2.0` |
| 20 | `test_naive_double_is_lower_bound` (D) | 部分 step 不一致 | `naive_double_count_tokens` 不包含不一致 step → 下界

### 回归测试

- **金钟罩 `test_disable_analysis_byte_identical_to_v05`**:golden trace 手动构造,不经过 adapter,events[] 不变;默认路径不渲染新块 → 保持绿
- **全量 pytest**:新增用例不会破坏现有 196 个测试
- **`check_facts.py`**:确认设计文档与实际实现一致(实现后执行)

### 金钟罩逐字节验证关键点

`build_comprehensive_trace()`(golden 基线构造器)手动构造 trace,不经过 `parse_dsh_jsonl()`,因此:
- `trace.events[]` 不含 `token/usage-duplicate` / `token/usage-inconsistent`
- `build_token_invariant()` 读取 events[] 无双写事件 → 返回全零块
- `token_invariant` 在 `enable_analysis=True` 时仍会构建,但值为全零(无双写)
- 默认路径(`enable_analysis=False`)完全不接触 `token_invariant` → 逐字节不变

---

## Open Questions

### Q1(评审 E):`events[]` 追加是否 additive — 已解决

**结论:是 additive。** 现有 6 个 detector 全部按 `event.type` 过滤(见 D1 表),新增 `token/usage-duplicate` / `token/usage-inconsistent` 类型不会被误消费。金钟罩 golden trace 手动构造,不经过 adapter,events[] 不变。

**验证计划(实现后必须执行)**:

1. 用真实 DSH 会话跑 adapter → 确认 events[] 新增了 `token/usage-*` 事件
2. 跑全量 pytest → 确认 196 个现有测试全绿
3. 跑金钟罩 → 确认默认输出逐字节不变
4. 若任何 detector 失败,定位原因;若因遍历 events[] 不做 type 过滤,则在该 detector 中显式排除 `token/usage-*` 前缀

### Q2:adapter 性能 — 低风险

`_usage_sources` 字典按 (turn,step) 聚合,真实会话通常 < 100 step,内存开销可忽略。解析末尾 O(n) 遍历 events 生成,不增加渐进复杂度。

### Q3:future — 其他双写不变量

当前仅覆盖 `assistant/chunk{type:usage}` vs `assistant/message.usage` 双写。未来可扩展至:
- `assistant/chunk{type:reasoning}` vs `assistant/message.content[type=reasoning]` 双写
- `assistant/chunk{type:text}` vs `assistant/message.content[type=text]` 双写

扩展时复用相同的 `token/usage-duplicate` 事件模式,或新建 `token/reasoning-duplicate` 等类型。`TokenInvariant` 数据块可扩展字段,不影响现有逻辑。

---

## 评审融合记录(deepseek-v4-pro 异模型评审,2026-08-22)

> 评审作为**阻塞闸门**执行,结论见 `openspec/changes/token-invariant-check/review-pro.md`。7 条硬约束红线全部守住(A–E 架构判断正确);锁定 2 个**阻塞项**(B/C)+ 若干建议项,已全部融合进本设计:

| 评审项 | 结论 | 融合位置 |
|---|---|---|
| 红线 1–7 | 全部通过 | 无改动 |
| A 事件通道 | 保持 events[],6 detector 全按 type 过滤已逐源码验证 | 补 `source_count` 断言 |
| **B/C 阻塞** | 全局稀释因子作风险乘数会误导 | 报告改为先报双写 step 精确 2×,再报全局稀释;新增 `double_write_multiplier=2.0` |
| D 阈值/边界 | 基本通过 | `_usage_equal` 补 cacheWriteTokens 盲区文档;`naive_double` 标下界 |
| E 测试完备性 | 补 4 用例 | 新增用例 15–18(E1–E4)+ 19–20(`double_write_multiplier`/下界) |
| F1 `_usage_equal` 仅比前两个 | all-pairs | 已改 `all(_usage_equal(usages[0], u) for u in usages[1:])` |
| F2 死代码 | 建议清理 | 报告 `dedup_required` 分支在双写早返回后恒 True,已按建议标注(实现时去掉条件) |
| F3 精度不一致 | 统一 | 存储 4 位;报告统一 2 位 |
| **G(实现后事实修正)** | 评审误信 design 的 `data.message.usage` | 真实数据(12 会话/426 条 assistant/message)**100% 在 `data.usage`(与 message 平级),无一在 `message.usage`**。实现已改 `data.get(\"usage\")`——否则特性在真实数据上为 no-op。评审 D 顺 design 错误路径做"验证",是评审未用真实数据独立核验的教训;实现会话用真实数据裁决并修正,正确。 |

**结论:设计已按评审 A–G 融合定稿,可进入实现。** 交实现会话 `session-d2c507cf`。

---

## 实现检查清单(给开发会话)

- [ ] `agenttrace/adapters/dsh_adapter.py`:新增 `_usage_sources` 收集 + `_usage_equal` 辅助 + 末尾事件生成
- [ ] `agenttrace/analysis/token_invariant.py`:新建文件,含 `TokenInvariant` dataclass + `build_token_invariant` 函数
- [ ] `agenttrace/pipeline.py`:`DiagnosisResult` 新增 `token_invariant` 字段;Stage 3 新增 `build_token_invariant` 调用
- [ ] `agenttrace/report.py`:`render_report` 签名扩展 `token_invariant`;新增 `_render_token_invariant_block` 函数;惰性构建逻辑
- [ ] `tests/test_token_invariant.py`:20 个用例(见上表)
- [ ] 全量 pytest 全绿(196 + 20 = 216)
- [ ] `test_disable_analysis_byte_identical_to_v05` 金钟罩保持绿
- [ ] `check_facts.py` 通过
- [ ] **不修改** `canonical_trace.py` / `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`