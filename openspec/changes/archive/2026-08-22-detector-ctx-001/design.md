# Design — detector-ctx-001

> **架构决策(评审后 B 形态)**:CTX-001 不做 Detector/Finding,而是与分析层画像(`profile`)同构的**会话级数据块** `ContextHealth`。理由见 D1;本设计已按 Pro 评审修复 B1(虚构窗口)、M1(cache_read 口径)、M2(阈值未校准)、M3(信号稀释)。

## Context

现状(v0.5,~114 测试全绿):pipeline 为 `Trace → Detector Registry(ALL_DETECTORS) → Finding[] → Attribution Registry → Attribution[] → (Stage 3, enable_analysis=True) 反证+置信度+画像 → Report`;硬约束见下。

盲区(proposal.md — Why):长会话上下文膨胀的退化风险(重复操作、工作记忆错误、用推测替代验证)是已观测事实,但现有 5 个 detector 无一会话级"上下文健康度"指标——TOOL-001 只抓重复现象、THINK-001 只抓单步推理强度、CMP-001 只记 shadowed 量。CTX-001 补上这个**观测性统计**,与 change#2 画像(profile)同为会话级数据块。

**关键事实约束(决定本设计走向):**

1. CTX-001 是会话级聚合指标,**每个含 step 的会话都可能给出**(任何会话都有 context/turn/工具调用),且**不以"检出缺陷"为目的**——它是"上下文健康度观测",不是"信号"。因此它**不进入 Finding/Detector 体系**,而是挂到 `DiagnosisResult` 的分析层数据字段(和 `profile` 并列)。
2. **Canonical Trace 当前没有上下文窗口字段**:`dsh_adapter.py` 只落 `metadata={"cwd","agentPreset"}`。占用率"窗口"必须确定性解析,且**无窗口字段时不得虚构**——这直接决定 B1(B1 是评审 Blocker,必须修复)。
3. 报告 `test_enable_analysis_per_finding_rendering` 断言 `report.count("**Confidence:**") == len(findings)`(五段式渲染按 findings 计算)。CTX-001 若做成 finding 会破坏该不变量(见 M3/M4),做成独立数据块则天然隔离。

硬约束(不可违背):

- 归因边界:没有证据就不做成本归因;`tokens=None` 表示 not applicable,不是 0。
- 规则不是假设,由真实数据决定(数据驱动);correlation ≠ causation;禁止混算 total wasted。
- 三层评判:确定性规则 → 统计证据 → LLM 语义(LLM 层未实现,off by default);本 change 只落在**规则层(分析层观测)**。
- additive:新增能力不改变现有 detector 行为,现有 114 测试全绿保持。

## Goals / Non-Goals

**Goals:**

- 新增分析层数据块 `ContextHealth`:从 trace 计算会话级观测指标(当前上下文 tokens、峰值、turn 数、重复工具调用操作率)。
- 报告 `enable_analysis=True` 时渲染"上下文健康度"块(与"综合判断"块并列);**量化"上下文压力"仅在窗口字段真实已知时给出**,否则显示 not applicable。
- 全确定性、additive;默认(不开分析层)输出与现状逐字节一致。

**Non-Goals:**

- **不做 Detector/Finding**,不新增对 `ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES` 的注册。
- 不判因果:不输出"上下文大导致退化",只输出"占用高 → 关联退化风险 → 建议压缩"(且仅在窗口真实已知时)。
- 不把 context tokens/重复调用数换算成 token 成本;不做成本归因。
- 不改变 adapter schema(不新增 `metadata["context_window"]` 写入;其落地列入 Open Questions)。

## Decisions

### D1. 架构:ContextHealth 数据块(不做 Detector/Finding)

CTX-001 做成 `DiagnosisResult.context_health`(一个数据块),由分析层模块生成,与 change#2 的 `profile` 同构。**不**做 `detectors/ctx_001.py -> list[Finding]`,注册进新导出 `ANALYSIS_DETECTORS`。

```python
# agenttrace/analysis/context_health.py(新增)
@dataclass
class ContextHealth:
    current_context_tokens: int          # 末 step 上下文(input + cache_read)
    peak_context_tokens: int
    turn_count: int
    total_tool_calls: int
    repeated_tool_calls: int
    repeat_rate: Optional[float]         # None = not applicable(total==0)
    window_tokens: Optional[int]         # None = 无真实窗口字段(unknown)
    window_source: str                   # "metadata" / "unknown"
    occupancy_ratio: Optional[float]     # None = 窗口未知(不虚构)
    pressure_high: bool                  # 窗口已知且超过阈值时才可能 True,否则恒 False
    stats_repeated_groups: list          # 排序后的重复组(确定性 tie-break)
    # 可选:evidence/定位信息(末 step + 最重重复组)

def build_context_health(trace) -> ContextHealth: ...
```

```python
# agenttrace/pipeline.py —— DiagnosisResult 加字段
@dataclass
class DiagnosisResult:
    ...
    profile: Optional[object] = None                  # change#2
    context_health: Optional[ContextHealth] = None    # 本 change

# Stage 3(enable_analysis=True)
if enable_analysis:
    from .analysis.context_health import build_context_health
    from .analysis.profile import build_profile
    from .analysis.counter_evidence import refine_findings
    refine_findings(result.findings, trace)
    result.context_health = build_context_health(trace)   # 不进 findings
    result.profile = build_profile(result.findings, result.attributions)
```

- **为什么数据块而非 Finding**:CTX-001 是会话级**观测指标**,不是"检出的现象/缺陷"。硬做成 `kind=flag, occurrences=1` 的 finding 会引发连锁:必须门控、必须双渲染(健康度块+五段式)、必须 profile 排除、confidence 语义漂移(评审 M3/M4)。项目已把"会话级综合判断"正确做成 `profile` 数据块(change#2 先例),CTX-001 同构处理,分析层统一。
- **为什么挂在 enable_analysis 后**:默认关闭 → 默认输出逐字节不变(additive);开启后与分析层并列展示。
- **为什么不进 findings**:不进 `findings` 列表就不会被 `report` 的每个 finding 五段式渲染误处理,`count("**Confidence:**")==len(findings)` 不变量(评审 M3)天然不受影响。

### D2. 指标计算:四项观测 + 空会话边界 + 正确的上下文口径(M1)

```python
def build_context_health(trace) -> ContextHealth:
    steps = trace.all_steps()
    if not steps:
        return ContextHealth(current=0, peak=0, turn_count=0,
                             total_tool_calls=0, repeated_tool_calls=0,
                             repeat_rate=None, window_tokens=None,
                             window_source="unknown", occupancy_ratio=None,
                             pressure_high=False, stats_repeated_groups=[])
    last = steps[-1].usage
    # M1 修复:上下文 = uncached input + cache_read(排除 cache_write,是"待写入"非"已占用")
    current = last.input_tokens + (last.cache_read_tokens or 0)
    peak = max(s.usage.input_tokens + (s.usage.cache_read_tokens or 0) for s in steps)
    calls = trace.all_tool_calls()
    groups: dict[str, list] = {}
    for tc in calls:
        groups.setdefault(call_fingerprint(tc.tool_name, tc.arguments), []).append(tc)
    total = len(calls)
    repeated = sum(len(g) - 1 for g in groups.values() if len(g) > 1)
    repeat_rate = (repeated / total) if total > 0 else None   # None = not applicable
    window, source = _resolve_window(trace)
    occupancy = (current / window) if (window and current is not None) else None
    pressure = occupancy is not None and occupancy > OCCUPANCY_HIGH_WATERMARK
    ...
```

- **为什么 current 取末 step 的 input+cache_read**:`Usage.input_tokens`(`canonical_trace.py:10`)语义"完整上下文,非增量";但 `billed_input_tokens()`(`:42-51`)把 input 视为"uncached input"。评审 M1 指出:prompt caching 场景下 `input_tokens` 只算未缓存部分,会漏掉已在上下文、被缓存重读的 token → 低估占用。正确口径 = `input_tokens + cache_read_tokens`(排除 cache_write_token,那是"待写入缓存",不是"当前已占用")。
- **为什么重复率复用 `call_fingerprint` 而非依赖 TOOL-001 finding**:additive + 单一数据源,与 TOOL-001 解耦(不消费其 finding);复用 `normalize.py` 保证一致性。
- **为什么 `total==0` 时 repeat_rate=None 而非 0**:与铁律 `tokens=None` 同构——无工具调用时重复率"不适用",报告显示"无工具调用"而非"0%"。
- **为什么空会话返回"全 0 + repeat_rate=None + 窗口 unknown"块而非 skip**:分析层块应始终可渲染(空会话显示"无上下文可度量"),但所有指标置 not applicable,不虚构。

### D3. 窗口解析:不虚构(B1 修复)

```python
WINDOW_METADATA_KEY = "context_window"
OCCUPANCY_HIGH_WATERMARK = 0.70   # 仅窗口真实已知时参与判定(见 M2 校准)

def _resolve_window(trace) -> tuple[Optional[int], str]:
    m = (trace.metadata or {}).get(WINDOW_METADATA_KEY)
    if isinstance(m, int) and m > 0:
        return m, "metadata"
    return None, "unknown"   # 无真实窗口字段 → 不虚构
```

- **B1 修复(评审 Blocker)**:**移除"默认兜底窗口 128000 参与压力判定"**。当前 adapter 不落 `context_window`,`metadata` 档在真实数据下命中不了 → 若用 128000 兜底算占空比,就是**用假设而非数据**,且能驱动 `warning` 压力标记(违反"数据驱动+无证据不判")。
  - 修复后:无真实窗口字段 → `window_source="unknown"`、`occupancy_ratio=None`、`pressure_high=False`(**永不触发**压力正告),只输出真实观测指标(current/peak/turn/repeat_rate),占用部分显示 **not applicable**。
  - 这比"保守兜底"更符合铁律:真正诚实是"没有窗口数据就不产出占用率",而非"用拍脑袋常数假装知道"。
- **为什么保留 `occupancy/pressure` 概念**:它们仅在窗口真实已知时(metadata 档,未来 adapter 落该字段后自然启用)才被计算;先落结构,数据源就绪即生效,detector 无需改动。

### D4. 重复组排序:确定性 tie-break(M7)

`stats_repeated_groups` 为重复 fingerprint 组列表,供"最重重复组"展示。排序 key 确定性:

```python
stats_repeated_groups = sorted(
    [(fp, len(g), g[0].tool_name) for fp, g in groups.items() if len(g) > 1],
    key=lambda t: (-t[1], t[2], t[0])   # len 降序 → tool_name 升序 → fingerprint 升序
)
```

- **为什么显式排序**:存在多个重复组、且 repeated 数相等时,"最重重复组"若无确定性排序,会依赖 dict 迭代序,威胁"两次运行逐字段一致"的确定性铁律(评审 M7)。
- **为什么 key 含 tool_name/fingerprint** 作 tie-break:覆盖 repeated 相等情形,全确定。

### D5. 阈值与校准(M2)

`OCCUPANCY_HIGH_WATERMARK = 0.70` 仅作为 **metadata 档(真实窗口)** 的压力阈值,**标注占位待校准**。因真实会话当前无窗口字段 → pressure_high 恒 False,该阈值在现状下不产生任何 warning 结论,只在窗口数据源就绪后生效。

- **为什么可接受**:阈值不驱动任何"虚构"结论(无窗口必不判压力),未来有真实窗口分布数据后按 THINK-001 的分位手法校准(`think_001.py:25-27`)。
- **为什么不提前拍死阈值**:真实窗口分布不可得,先固化为"可配置常量 + 待校准标注",不伪造数据支撑。

### D6. 报告集成:专属健康度块(不进五段式 flags 节)

`agenttrace/report.py` 在 `enable_analysis=True` 分支、`_render_profile_block` 之后调用 `_render_context_health_block(context_health)`:

```
### 上下文健康度(CTX-001)
- 当前上下文: {current} tokens(input + cache_read)
- 峰值上下文: {peak} tokens
- turn 数: {n}
- 重复工具调用操作率: {rate}(重复 {repeated}/{total})
- 上下文窗口: {window} tokens(window_source={source})   # unknown 时该项显示 not applicable
⚠ 上下文压力高,建议压缩(占用 {pct} > 阈值 {70%})   # 仅 pressure_high=True 时出现
```

- **为什么专属块而非五段式**:CTX-001 不是 finding,不进入五段式渲染;块是它的唯一呈现,与"综合判断"块并列。`count("**Confidence:**")==len(findings)` 不变量不受影响(CTX-001 不在 findings)。
- **为什么加在 `_render_profile_block` 之后**:与分析层其他数据块(profile)集中渲染,统一在 `enable_analysis` 门内,默认路径逐字节不变。
- **为什么 occupancy/pressure 部分在 unknown 时显示 not applicable**:无窗口字段不虚构(与 B1 一致)。

### D7. 测试策略

新增 `tests/test_context_health.py`,构造 Trace 覆盖:

1. **空会话**:所有指标置 not applicable(current=0/peak=0/turn=0/repeat_rate=None/window=unknown/occupancy=None)。
2. **指标精确值**:current 含 cache_read(`input+cache_read`,验证 M1 口径)、peak、turn_count、total/repeated/repeat_rate(含同 fingerprint 重复、跨 turn 重复)。
3. **窗口**:metadata 提供真窗口 → 算 occupancy + 可按阈值判 pressure;无 metadata → unknown → occupancy=None + pressure_high=False。
4. **压力标记**:仅窗口已知且 occupancy>0.70 时 pressure_high=True;unknown 时恒 False。
5. **确定性**:同一 trace 两次 `build_context_health` / 两次 `diagnose(enable_analysis=True)` 逐字段一致(含重复组 tie-break)。
6. **归因边界**:`ALL_ATTRIBUTION_ENGINES` 无 "CTX-001";`diagnose(enable_analysis=True)` 的 attributions 无 CTX-001;无成本数字。
7. **additive**:默认路径(`enable_analysis=False`)在 golden 基线 trace 上报告逐字节不变;`ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES` 仍为 5 个。
8. **报告集成**:`enable_analysis=True` 时报告含"上下文健康度"块;`count("**Confidence:**")==len(findings)` 仍成立。

**必改既有测试:0 处**。CTX-001 不进 `ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES`,不触碰 registry 快照测试;默认路径零改动。

## Risks / Trade-offs

- [分析层块默认关闭,用户看不到健康度] → 与综合判断块同一闸门,`--analysis` 开关可开。
- [真实会话无窗口字段,pressure 结论永不出现] → 这是**刻意且诚实**的取舍(不虚构);未来 adapter 落 `metadata["context_window"]` 后自动启用,且阈值待校准。
- [重复率含无状态工具(web_search/get_time)] → 输出原始重复率并标注"未区分状态性";STATELESS_TOOLS 排除列入 Open Questions,避免过度设计。

## Migration Plan

无 breaking、无数据迁移、无 schema 变更:

1. 新增 `agenttrace/analysis/context_health.py`(`ContextHealth` + `build_context_health`)。
2. `pipeline.py` `DiagnosisResult` 加 `context_health` 字段;Stage 3 生成。
3. `report.py` 加 `_render_context_health_block` + 调用。
4. 新增 `tests/test_context_health.py`。
5. 回归:全量 pytest,现有 ~114 全绿(0 处必改)+ 新增全绿 + 默认路径逐字节对比全绿。

回滚:删除 `context_health` 字段与新增文件即回滚;无默认输出负担。

## Open Questions

- 是否引入 `MODEL_CONTEXT_WINDOWS`(model→窗口映射,来自真实数据)作为窗口真实来源(替代/补充 metadata 字段);当前 unknown 不判压力已足够诚实。
- `OCCUPANCY_HIGH_WATERMARK`(0.70)待真实窗口分布校准(仅在窗口数据源就绪后生效)。
- 重复率是否排除 `STATELESS_TOOLS`(无状态工具重复不等于退化信号)。
- adapter 是否应落 `metadata["context_window"]`(DSH session 头是否有该字段待调研)。
- `list-detectors` 是否应列出分析层数据块(当前只列 ALL_DETECTORS;可延后)。
