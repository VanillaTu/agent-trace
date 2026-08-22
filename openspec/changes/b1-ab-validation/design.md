# Design — b1-ab-validation (B1)

> 版本：定稿（基于 evidence.md 109 会话实测 + proposal.md 核心设计要点）
> 设计模型：deepseek-v4-pro
> 状态：待评审（阻塞闸门）

---

## Context

### 背景

AgentTrace v0.5 实现了 TOOL-001（重复工具调用）和 TOOL-004（无效参数重试）两个 detector，并输出"候选可避免"建议，但从未**实测**这些建议是否真的能带来可观测的下降。09 文档第 29 行"修复前后 A/B 验证建议有效性"当前未达成。

真实数据调研（evidence.md，109 会话）已证实：
- TOOL-001：244 finding / 45 会话；可省 tool-call **394**（占全部 13,275 的 2.97%）；按保守修复可删 290 个"仅重复"step，可归因 output token 约 **81,844**（粗口径 126,313）
- TOOL-004：10 finding / 10 会话；可省 10 次工具调用 + 10 次工具级重试往返；失败生成 step 合计 usage **21,833**（output 10,831）
- 3 个代表会话 original vs fixed 保守对比：tool-call 下降 2–38 次/会话，token total 下降 4.7k–29k/会话
- `llm/retry` 事件在修复前后**不变**（4→4→0），证明 TOOL-001/004 修复只影响工具调用层，不触动模型 API 重试

### 诚实边界（evidence §5，铁律）

1. **causal_claim = NONE**：这是描述性前后对比（把同一真实会话的 trace 分别以 original 与 fixed 口径重述），不是实验组/对照组的因果实验。只报"观测到的可省量"，不判"修复后一定省 X"。
2. **指标分层，禁止混算**：硬指标（tool-call 下降、删除 step 数）确定性可复现；token 可信子指标是 output token；input token 是上下文（占可省 ~95%），不得计入"省"。
3. **retry 严格分开**：TOOL-001/004 只省工具调用层重试，不省 `llm/retry` 事件（实测 0 变化）。严禁把"工具级重试"与"模型 API 重试（RETRY-001）"混为同一指标。
4. **语义判断显式隔离**：轮询型工具（`list_*`/`get_state`/`session_status` 等）标记 `semantic=debated`，不计入硬可省；B1 的"省"数字基于确定性重复子集。
5. **报告口径必须标注**：每条数字写清是"整 step 可删"还是"含冗余 occurrence 即计"；token 是"input+output"还是"仅 output"；是"静态重述"还是"真实重跑"。
6. **TOOL-004 样本不足**（10 条）：定位为机制/成因说明，不单独放大其量化效果。

### 分析层定位

B1 ABValidation 是**分析层会话级数据块**，与 `ContextHealth` / `TokenInvariant` / `SessionLineage` 同构：

- 不注册 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`
- 不进入 findings/attributions
- 复用 TOOL-001/TOOL-004 的检出结果（不改其行为），在分析层做"修复前后重述"
- 仅 `enable_analysis=True` 时由 pipeline Stage 3 调用
- 默认关闭 → 零影响（additive 铁律）

### 硬约束（不可违背，来自 proposal + evidence）

1. **additive**：不改现有 detector/attribution 行为；默认输出（enable_analysis=False）逐字节不变
2. **causal_claim = NONE**：只报观测可省量，不判"修复后一定省 X"
3. **禁混算 Total wasted**：不把 input token 当省（上下文，占 ~95%）；可信子指标是 output token
4. **retry 严格分开**：TOOL-001/004 只省工具调用层重试，不省 llm/retry（模型 API 重试）；严禁混为同一指标
5. **语义判断显式隔离**：轮询型工具标 `semantic=debated`，不计入硬可省；省的数字基于确定性重复子集
6. **报告口径标注**：每条数字写明"整 step 可删"/"含冗余 occurrence 即计"、"仅 output"/"input+output"、"静态重述"/"真实重跑"
7. **复杂系统只测局部封闭不变量**：设计权衡为 hedged 建议

---

## Goals & Non-Goals

### Goals

1. 新增 `ABResult` dataclass（分析层数据块），与 TokenInvariant/ContextHealth 同构
2. 实现 `build_ab_validation(trace)` 纯函数：对单个真实会话构建 original（全量）与 fixed（删除冗余后）两种重述
3. 量化 tool-call 下降、删 step 数、output token 下降、input token 变化（标注为上下文）、retry 分开（工具级 vs llm/retry）
4. 区分语义：确定性重复子集 vs 轮询型 `semantic=debated`，分口径报告
5. 固定验证集：一组代表性真实会话（含 session_id），作为可复现 before_after 报告输入
6. 集成到 pipeline Stage 3（enable_analysis=True）+ report 渲染 + CLI（`analyze --ab` 模式）
7. 全量回归 + 金钟罩测试（enable_analysis=False 逐字节不变）

### Non-Goals

- **不改现有 detector/attribution 行为**：TOOL-001/TOOL-004 源码零改动
- **不做新增 detector**：B1 是验证/测量，不注册新的 `ALL_DETECTORS`
- **不做真实模型重跑实验**：级联效应不可静态测量，B1 只做静态反事实重述
- **不把 input token 当 defect cost**：不混算 Total wasted
- **不判因果**：causal_claim=NONE
- **不把 TOOL-004 的量化效果放大**：样本不足（10 条），仅作机制说明
- **不测量 Task Success Rate（评审 F）**：任务成功率是语义判断（需理解任务目标 + 判断完成质量），静态反事实重述无法测量。若修复后模型行为改变导致任务失败，属级联效应（需真实重跑才能观测），不在 B1 静态重述范围内。B1 只覆盖可确定性测量的三项：Tool Call Reduction / Token Reduction(仅 output) / Retry Rate(区分工具级与模型 API 级)。

---

## Decisions

### D0：ABResult 是分析层数据块，不进 findings/attributions

**决定**：`ABResult` 是 `@dataclass` 分析层数据块，与 `TokenInvariant` / `ContextHealth` / `SessionLineage` 同构。挂在 `DiagnosisResult.ab_result`（`Optional[ABResult]`），仅 `enable_analysis=True` 时由 pipeline Stage 3 构建。默认关闭 → 零影响。

**理由**：
- B1 是"验证/测量"，不是"检测/归因"——不进 Registry 是正确语义
- 与现有分析层（context_health / token_invariant）同构，最小化集成摩擦
- 默认关闭保证 additive 铁律

**备选被否**：作为独立 CLI 工具（`b1-ab` 命令）——虽然 proposal 提及可复用 CLI，但作为分析层数据块挂 pipeline 比独立 CLI 更统一（复用 diagnose 流程、复用 report 渲染、复用 enable_analysis 门控）。独立 CLI 作为**额外入口**（见 D5），不替代分析层主路径。

---

### D1：fixed trace 的构造规则（保守模型 + 确定性）

**决定**：`fixed` trace 是对 `original` trace 的**静态反事实重述**，不重跑模型。构造规则：

**TOOL-001 修复**：
1. 对 trace 中所有 tool_call，按 `call_fingerprint(tool_name, arguments)` 分组
2. 每组内，保留**首次**出现的 tool_call（按全局顺序），标记后续出现的为"冗余 occurrence"
3. 一个 step 是"整 step 可删"当且仅当：该 step 上**所有** tool_call 均为冗余 occurrence
4. fixed trace = 去掉所有"整 step 可删"的 step，保留其余 step 不变

**TOOL-004 修复**：
1. 对每个 TOOL-004 finding（失败 attempt + 成功重试配对），取其 `failed_index`（即 `(turn_id, step_id)` 复合键）
2. 该失败 attempt 所在的 step 是"整 step 可删"（实测 10/10 均为独立 step，n_calls=1）
3. fixed trace = 去掉所有 TOOL-004 失败 attempt 所在 step

**并集去重**：若同一个 step 同时被 TOOL-001 和 TOOL-004 标记为可删，只删一次（不重复计数）。

**理由**：
- 规则纯确定性（基于 fingerprint 分组 + step 级判断），无随机、无 LLM
- 保守模型（整 step 全冗余才删）是唯一可复现、无歧义的口径
- 粗模型（含任一冗余 occurrence 即计）仅在**报告**中作为参考口径披露，fixed trace 构造只用保守模型
- 不引入"部分删 step"概念（无法从 step 级 usage 中拆分单个 tool_call 的 token 贡献）

**备选被否**：粗模型构造 fixed trace（部分删 step 内 tool_call）——step 级 usage 无法拆分到单个 tool_call，退 token 不精确；保守模型虽然低估，但每条数字可复现、可验证。

---

### D2：语义隔离——轮询型工具标记 `semantic=debated`

**决定**：定义 `SEMANTIC_DEBATED_TOOLS` 集合。指纹分组中，若 `tool_name` 属于该集合，该组的冗余 occurrence 计入 `semantic_debated` 计数，**不计入硬可省**（tool-call 下降、删 step 数、output token 下降）。B1 的"省"数字基于**确定性重复子集**（即不在 `SEMANTIC_DEBATED_TOOLS` 中的工具）。

**`SEMANTIC_DEBATED_TOOLS` 定义**（基于 evidence 实测，可配置扩展）：

```python
SEMANTIC_DEBATED_TOOLS: frozenset[str] = frozenset({
    # 轮询/状态读取型：反复读取当前状态可能合法且必要
    "list_agents",
    "list_sessions",
    "session_status",
    "mcp__browser_use__browser_get_state",
    "job_list",
    "job_output",
    "read_session",       # 读取会话历史，可能合法重读
    "memory_list",        # 记忆列表，可能合法刷新
    "mcp__browser_use__browser_navigate",  # 浏览器导航，可能合法重定向
})
```

**注**：`read` / `write` / `edit` / `glob` / `pwsh` 等同参重复调用**不在** debated 集合中——同文件同参数重复读写是确定性冗余。`memory_delete` 同 id 重复删除也是确定性冗余。

**理由**：
- evidence §1.1 警示：高倍率 finding 大量集中在轮询型工具（`list_agents` N=10、`browser_get_state` N=10×3），这些工具在长任务中"反复读取当前状态"可能合法且必要
- 若不隔离，轮询型重复会严重高估"可省量"（如 `session-a79579f3` 的 27 finding 中大量是轮询型）
- 分离口径后，硬可省数字更保守、更可信；debated 数字单独披露供人工判断

**备选被否**：不区分语义，全部 finding 计入硬可省——会高估，违反 evidence 诚实边界。

---

### D3：ABResult 字段定义（完整 dataclass）

**决定**：`ABResult` 是 `@dataclass`，全部字段带默认值。空会话 / 无 TOOL-001/TOOL-004 finding → 全零/None 块，不虚构数值。

```python
@dataclass
class ABResult:
    """修复前后 A/B 对比验证数据块（会话级观测，非 finding）。

    全部字段带默认值：空会话 / 无 TOOL-001/TOOL-004 finding → 全零块。
    `None` 语义 = not applicable（无重复调用 / 无 TOOL-004），不是 0。
    """

    # ── 规模基线（original 口径） ──
    original_steps: int = 0
    original_tool_calls: int = 0
    original_output_tokens: int = 0
    original_input_tokens: int = 0
    original_total_tokens: int = 0

    # ── 规模基线（fixed 口径，保守模型） ──
    fixed_steps: int = 0
    fixed_tool_calls: int = 0
    fixed_output_tokens: int = 0
    fixed_input_tokens: int = 0
    fixed_total_tokens: int = 0

    # ── 硬指标（确定性、口径无关） ──
    tool_call_reduction: int = 0
    """工具调用下降数：fixed 比 original 少的 tool-call 数（确定性重复子集）。"""

    deleted_steps: int = 0
    """删除的 step 数：保守模型下整 step 可删的 step 数。"""

    # ── token 可信子指标 ──
    output_token_reduction: int = 0
    """output token 下降：被删 step 的 output_tokens 合计（确定性重复子集）。
    这是 token 维度最可信的子指标。"""

    input_token_change: int = 0
    """input token 变化：被删 step 的 input_tokens 合计。
    标注为"上下文变化，非可省成本"——不判定为节省。"""

    total_token_change: int = 0
    """input+output 合计变化。标注为"含上下文，非纯省"。"""

    # ── retry 严格分开 ──
    tool_level_retries_saved: int = 0
    """工具调用层重试可省数：TOOL-004 的失败 attempt 数（fixed 中不再发生）。
    仅工具级重试，不包含 llm/retry 事件。"""

    llm_retry_original: int = 0
    """original 中的 llm/retry 事件数（模型 API 重试，RETRY-001 对象）。"""

    llm_retry_fixed: int = 0
    """fixed 中的 llm/retry 事件数。实测不变（0 变化），但保留字段供验证。"""

    llm_retry_change: int = 0
    """llm/retry 事件数变化 = fixed - original。实测恒为 0。"""

    # ── 语义隔离 ──
    semantic_debated_occurrences: int = 0
    """语义存疑的冗余 occurrence 数：轮询型工具（SEMANTIC_DEBATED_TOOLS）的重复调用。
    不计入硬可省，单独披露供人工判断。"""

    semantic_debated_steps: int = 0
    """语义存疑的可删 step 数（轮询型工具整 step 可删）。不计入硬可省。"""

    # ── TOOL-004 专项 ──
    tool004_failed_attempts: int = 0
    """TOOL-004 失败 attempt 数（finding 数）。"""

    tool004_failed_step_output_tokens: int = 0
    """TOOL-004 失败 attempt 所在 step 的 output_tokens 合计。"""

    # ── 口径标注 ──
    model: str = "conservative"
    """修复口径：fixed = conservative（整 step 全冗余才删）。"""

    causal_claim: str = "NONE"
    """恒为 "NONE"：描述性前后对比，非因果实验。"""

    method: str = "static_restatement"
    """恒为 "static_restatement"：静态反事实重述，非真实重跑模型。"""

    # ── 原始 finding 计数（供报告引用） ──
    tool001_finding_count: int = 0
    """TOOL-001 finding 总数（确定性重复子集）。"""

    tool001_finding_count_debated: int = 0
    """TOOL-001 finding 中语义存疑（轮询型）的数量。"""

    tool004_finding_count: int = 0
    """TOOL-004 finding 总数。"""
```

**理由**：
- 字段分层清晰：基线 → 硬指标 → token 子指标 → retry → 语义 → TOOL-004 → 口径标注
- 全部字段默认值安全：空会话直接 `ABResult()` 即全零块
- `input_token_change` 标注为"上下文变化，非可省成本"，不混入报告 positive 数字
- `causal_claim` / `method` 硬编码为 `"NONE"` / `"static_restatement"`，报告渲染时据此自动标注口径
- 保留 `llm_retry_original` / `llm_retry_fixed` 字段供验证（实测不变），但报告可用其证明"retry 严格分开"

---

### D4：`build_ab_validation(trace)` 算法（纯函数、确定性）

**决定**：`build_ab_validation(trace: Trace) -> ABResult` 是纯函数，不依赖外部状态，不调用 detector（直接计算，复用 detector 的 fingerprint/判定逻辑）。

**算法流程**：

```
build_ab_validation(trace):
    1. 构建 original 基线：
       original_steps = len(trace.all_steps())
       original_tool_calls = len(trace.all_tool_calls())
       original_output_tokens = sum(s.usage.output_tokens for s in trace.all_steps())
       original_input_tokens = sum(s.usage.input_tokens for s in trace.all_steps())
       original_total_tokens = original_input_tokens + original_output_tokens

    2. 按 fingerprint 分组所有 tool_call（复用 call_fingerprint）：
       groups: dict[fp, list[(step, tc, global_pos)]]
       遍历 trace.all_steps()，对每个 step 的每个 tc：
         fp = call_fingerprint(tc.tool_name, tc.arguments)
         groups[fp].append((step, tc, global_pos))

    3. 对每个 group（count > 1）：
       tool_name = first tc.tool_name
       保留首次出现（global_pos 最小），标记后续为"冗余"：
         redundant_steps_set.add(step)  # 仅当该 step 上所有 tc 均为冗余时
         redundant_tool_calls_count += (len(group) - 1)
       若 tool_name in SEMANTIC_DEBATED_TOOLS：
         debated_occurrences += (len(group) - 1)
         若该 step 上所有 tc 均为冗余 → debated_steps += 1
       否则（确定性重复）：
         hard_occurrences += (len(group) - 1)
         若该 step 上所有 tc 均为冗余 → hard_deleted_steps += 1

    4. TOOL-004 修复：
       遍历 trace.all_steps()，对每个 step 的每个 tc：
         若 tc.is_error 且 _match_param_error(tc) is not None（复用 tool_004 判定逻辑）：
           该 step 标记为"TOOL-004 失败 step"
           tool004_failed += 1，tool004_failed_output += step.usage.output_tokens
       加入到 redundant_steps_set（并集去重，与 TOOL-001 重复的 step 不重复计数）

    5. 构建 fixed trace 指标：
       deleted_steps = len(redundant_steps_set)
       fixed_steps = original_steps - deleted_steps
       fixed_tool_calls = original_tool_calls - hard_occurrences - tool004_failed
       （debated 的 tool-call 不删，因为 fixed 是保守模型）
       fixed_output_tokens = original_output_tokens - sum(deleted_step.output_tokens)
       fixed_input_tokens = original_input_tokens - sum(deleted_step.input_tokens)
       fixed_total_tokens = fixed_input_tokens + fixed_output_tokens

    6. llm/retry 计数（评审 C：加防御性扫描）：
       llm_retry_original = count(trace.events where type == "llm/retry")
       llm_retry_fixed = llm_retry_original  # events 与 step 解耦,不随 step 删
       llm_retry_change = 0
       # 防御性扫描(评审 C):检查是否有 llm/retry 事件关联到被删 step。
       # 若有,记录 warning(已知局限:静态重述无法模拟该级联效应,真实重跑可能不同),
       # 但不改变 llm_retry_change=0(静态重述口径)。
       for ev in trace.events:
           if ev.type == "llm/retry" and (ev.turn_id, ev.step_id) in redundant_steps_set:
               warn(f"llm/retry event on deleted step ({ev.turn_id},{ev.step_id})")

    7. 组装 ABResult 返回。
```

**确定性保证**：
- 分组按 fingerprint（SHA-256），确定性
- 组内排序按 global_pos（遍历顺序），确定性（trace 的 step 顺序固定）
- 冗余判定：step 上所有 tc 均为冗余 → 确定性布尔
- 无随机、无 LLM、无外部服务

**防重复计数**：
- 同一个 step 同时被 TOOL-001 和 TOOL-004 标记为可删 → `redundant_steps_set`（set 自动去重）
- `deleted_steps` 是并集大小，不重复计数
- output_token_reduction 基于 `redundant_steps_set` 中的 step usage 加总

**语义隔离**：
- `semantic_debated_occurrences` 和 `semantic_debated_steps` 单独计数
- 不计入 `tool_call_reduction`、`deleted_steps`、`output_token_reduction`
- `tool001_finding_count` 只计确定性重复子集的 finding 数
- `tool001_finding_count_debated` 计 debated 子集的 finding 数

---

### D5：CLI / pipeline / report 集成（additive）

**决定**：三层集成，全部 additive。

#### 5a. Pipeline（`agenttrace/pipeline.py`）

- `DiagnosisResult` 新增字段：`ab_result: Optional[ABResult] = None`
- `diagnose()` 的 Stage 3（`enable_analysis=True`）中，在 `build_token_invariant` 之后调用 `build_ab_validation(trace)`
- 默认关闭（`enable_analysis=False`）→ `ab_result` 保持 `None`，零影响

```python
# pipeline.py Stage 3 新增（enabled_analysis=True 时）
if enable_analysis:
    from .analysis.ab_validation import build_ab_validation
    # ... existing analysis steps ...
    result.ab_result = build_ab_validation(trace)
```

#### 5b. Report（`agenttrace/report.py`）

- 新增 `_render_ab_validation_block(ab: ABResult) -> list[str]` 渲染函数
- 在 `render_report()` 中，`enable_analysis=True` 且 `ab_result is not None` 时渲染 AB 块
- AB 块放在 Summary 的综合判断块之后、上下文健康度块之前
- 渲染规则：
  - 无 TOOL-001/TOOL-004 finding → 显示"未检测到可对比的重复调用/无效参数重试，AB 验证不适用"
  - 有 finding → 渲染 original vs fixed 对比表 + 口径标注
  - 所有数字标注口径（"整 step 可删"、"仅 output"、"静态重述"）
  - `input_token_change` 标注为"上下文变化，非可省成本"
  - `semantic_debated_*` 单独披露
  - `causal_claim=NONE` 显式标注

**AB 块 Markdown 模板**：

```markdown
### A/B 验证 — 修复前后对比 (B1)

> **口径**: 保守模型（整 step 全冗余才删）| 静态反事实重述 | causal_claim=NONE
> **"可省" = 观测值，非因果断言。** 以下数字是"这些调用/step 在会话中确实出现过、且按定义属于可去掉的那一类"。

#### 确定性重复子集（硬可省，排除轮询型工具）

| 指标 | original | fixed | 差（观测可省量） |
|------|----------|-------|-------------------|
| steps | {original_steps} | {fixed_steps} | −{deleted_steps} |
| tool-calls | {original_tool_calls} | {fixed_tool_calls} | −{tool_call_reduction} |
| output tokens | {original_output_tokens} | {fixed_output_tokens} | −{output_token_reduction} |
| input tokens | {original_input_tokens} | {fixed_input_tokens} | −{input_token_change} ⚠ |
| total tokens | {original_total_tokens} | {fixed_total_tokens} | −{total_token_change} |

> ⚠ **input token 变化是上下文变化，非可省成本**（input_tokens 是完整上下文，大部分是任务必需的历史 context）。
> **可信子指标：output token 下降 {output_token_reduction} tokens。**

#### retry 严格分开

| 指标 | original | fixed | 变化 |
|------|----------|-------|------|
| 工具级重试（TOOL-004 失败 attempt） | {tool004_failed_attempts} | 0 | −{tool_level_retries_saved} |
| 模型 API 重试（llm/retry 事件） | {llm_retry_original} | {llm_retry_fixed} | **{llm_retry_change}（静态重述下不变；真实重跑中若被删 step 关联 retry 事件,该 retry 可能不再发生）** |

> TOOL-001/004 修复只影响工具调用层，不影响模型 API 重试（RETRY-001）。两类重试严格分开。

#### 语义存疑子集（轮询型工具，不计入硬可省）

- 轮询型冗余 occurrence：{semantic_debated_occurrences} 次
- 轮询型可删 step：{semantic_debated_steps} 个
- 涉及工具：list_agents, list_sessions, session_status, browser_get_state, job_list, job_output, read_session, memory_list, browser_navigate

> 这些工具在长任务中"反复读取当前状态"可能合法且必要。同 fingerprint 重复 ≠ 一定浪费。不计入硬可省。

#### TOOL-004 机制说明

- 失败 attempt 数：{tool004_failed_attempts}
- 失败生成 step 的 output tokens：{tool004_failed_step_output_tokens}

> TOOL-004 样本不足（10 条），以上为机制说明，非强计量结论。
```

#### 5c. CLI（`agenttrace/cli.py`）

- `analyze` 命令新增 `--ab` flag（`action="store_true"`）
- `--ab` 自动启用 `--analysis`（因为 AB 验证依赖 enable_analysis）
- 用法：`python -m agenttrace.cli analyze <session_dir> --ab [--out report.md]`
- 等价于 `--analysis` + 额外渲染 AB 块（report 层自动渲染）

```python
p_analyze.add_argument(
    "--ab",
    action="store_true",
    help="开启 A/B 修复前后对比验证（B1，自动启用 --analysis）",
)
# cmd_analyze 中：
if args.ab:
    args.analysis = True  # AB 验证依赖分析层
```

**备选被否**：独立 `b1-ab` 子命令——proposal 提及可复用 CLI，但挂 `--ab` flag 到现有 `analyze` 命令更简洁（用户只需一个命令分析会话，AB 对比是分析的增强模式）。独立 CLI 工具留给未来（如批量验证集跑批）。

---

### D6：验证集结构（固定代表性会话）

**决定**：验证集是一个 Python 模块 `agenttrace/analysis/ab_validation_set.py`，导出 `AB_VALIDATION_SESSIONS: list[dict]`。每个条目包含：

```python
AB_VALIDATION_SESSIONS: list[dict] = [
    {
        "session_id": "session-a79579f3-f897-4a2c-aae7-e3910a206186",
        "label": "高密度 TOOL-001 + TOOL-004 共现",
        "expected_tool001_min": 20,       # TOOL-001 finding 数下界
        "expected_tool004_min": 1,        # TOOL-004 finding 数下界
        "expected_tool_call_reduction_min": 30,  # tool-call 下降下界
        "note": "27 TOOL-001 + 1 TOOL-004；代表性长会话",
    },
    {
        "session_id": "session-1491c2c7-3cf8-4405-97cf-6c70159660f5",
        "label": "高倍率 TOOL-001（N=11 memory_list），无 TOOL-004",
        "expected_tool001_min": 15,
        "expected_tool004_min": 0,
        "expected_tool_call_reduction_min": 25,
        "note": "18 TOOL-001；N=11 的 memory_list 高倍率组",
    },
    {
        "session_id": "session-112ce518-4d26-4e86-8a54-69c98175c2dd",
        "label": "低密度小会话（TOOL-001=1 + TOOL-004=1）",
        "expected_tool001_min": 1,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 1,
        "note": "最小可观测影响；锚点会话",
    },
    {
        "session_id": "session-4ee09ecf-7629-4067-a058-dcfef827ccb3",
        "label": "高密度 TOOL-001（19 finding）+ TOOL-004 共现",
        "expected_tool001_min": 15,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 25,
        "note": "19 TOOL-001 + 1 TOOL-004；send_session_message 失败",
    },
    {
        "session_id": "session-5cdccd44-fb56-4d7e-ba82-adc2eaa40d0f",
        "label": "高密度 TOOL-001（19 finding）+ TOOL-004 共现",
        "expected_tool001_min": 15,
        "expected_tool004_min": 1,
        "expected_tool_call_reduction_min": 25,
        "note": "19 TOOL-001 + 1 TOOL-004；list_agents 高倍率",
    },
]
```

**验证集用途**：
- `tests/test_b1_ab_validation.py` 中的验证集测试：遍历 `AB_VALIDATION_SESSIONS`，对每个 session_id 解析真实 trace → 跑 `build_ab_validation()` → 断言下界
- 报告生成：`python -m agenttrace.cli analyze --session-id <id> --ab --out b1_report.md` 对每个验证集会话生成独立报告
- 可复现性：每次跑同一 session_id 得到相同结果（确定性 guarantee）

**fixture 固化（评审 E，B1 实现的一部分，非可选）**：
- 验证集会话必须**导出 Canonical Trace 为 JSON fixture**，固化在 `tests/fixtures/b1_validation_sessions/{session_id}.json`，否则换机即失效、验证集形同虚设。
- 每个 fixture 用 `CanonicalTrace.to_dict()` 序列化；附 `tests/fixtures/b1_validation_sessions/README.md` 说明会话来源。
- `build_ab_validation` 的**回归测试**（T32）直接用这些 fixture，而非依赖本机真实数据——这才是"可复现"（跨机一致），而非"本机数据快照"。
- **保留本机 E2E** 作为额外验证，但 fixture 是权威可复现路径。

**理由**：
- 5 个会话覆盖：高密度 TOOL-001+TOOL-004（3 个）、纯 TOOL-001 高倍率（1 个）、低密度小会话（1 个）
- 全部来自 evidence.md 实测数据，有明确的 session_id
- 下界断言保证验证集不会因"本机无此会话"而静默通过（session 缺失应报错，而非静默 skip）
- 固化 fixture 后才真正跨机可复现（评审 E 强建议）

**⚠️ 实现对齐说明（2026-08-22，实现会话事实对齐）**：D6 示例的 `expected_*` 下界最初引用 evidence §4 的**未隔离语义**数字（含轮询型工具的重复都计入硬可省），但与 D2 的 `SEMANTIC_DEBATED_TOOLS` 语义隔离冲突（D2 是核心诚实边界）。实现会话按 **D2 语义隔离口径**重设了下界（如 `session-a79579f3`:17/23/7299;`112ce518`:1/1/1249;`4ee09ecf`/`5cdccd44` 均下调），使下界 < evidence §4 值（因 `list_sessions`/`memory_list`/`list_agents` 归为 debated）。这反映了"**开分析层必须守语义隔离**"的设计意图，是合理事实对齐。`ab_validation_set.py` 注释与本说明已记录。

---

### D7：TOOL-004 参数错误判定逻辑复用（评审 A：内联实现，不动 detector 源码）

**决定**：`build_ab_validation` 中 TOOL-004 的参数错误判定逻辑**不 import detector 的私有函数、不改 tool_004.py 源码**（守 proposal"源码零改动"承诺），改为在 `agenttrace/analysis/ab_validation.py` 中**内联**等价逻辑：

```python
# ab_validation.py 内联(与 tool_004.py 保持同步)
_PARAM_ERROR_KEYWORDS = (
    "invalid argument", "missing required", "invalid_request",
    "invalid request", "invalid parameter", "required parameter",
    "required argument", "missing parameter", "missing argument",
    "unexpected argument", "unexpected keyword",
)

def _is_param_error(tc) -> str | None:
    """内联自 tool_004._match_param_error,与源头保持同步。
    若 tool_004.py 更新此逻辑,需同步更新本函数。
    """
    if not tc.is_error:
        return None
    text = (tc.result or "").lower()
    for kw in _PARAM_ERROR_KEYWORDS:
        if kw in text:
            return kw
    args = tc.arguments
    if args is None or args.strip() in ("", "{}", "null", "None"):
        if tc.tool_name not in STATELESS_TOOLS:
            return "empty_args"
    return None
```

**理由**：
- 避免重复实现导致 drift（维持同步注释）。
- **不触碰 `tool_004.py` 源码**(proposal 承诺"源码零改动")。若评审允许改名,备选是提升 `_match_param_error` 为公开,但当前采用内联更符合 additive 承诺。
- `_is_param_error` 是纯函数,内联在分析层无副作用。

---

## Schema & API

### 新增文件

```
agenttrace/analysis/ab_validation.py        # ABResult dataclass + build_ab_validation()
agenttrace/analysis/ab_validation_set.py    # AB_VALIDATION_SESSIONS 验证集
tests/test_b1_ab_validation.py              # 测试（~25 用例）
```

### 修改文件

```
agenttrace/analysis/__init__.py             # 导出 ABResult, build_ab_validation
agenttrace/pipeline.py                      # DiagnosisResult 加 ab_result 字段，Stage 3 调用
agenttrace/report.py                        # _render_ab_validation_block() + render_report 集成
agenttrace/cli.py                           # --ab flag
agenttrace/detectors/tool_004.py            # _match_param_error → match_param_error（公开）
```

### 公开 API

```python
from agenttrace.analysis.ab_validation import ABResult, build_ab_validation

# 纯函数，确定性
result: ABResult = build_ab_validation(trace)

# 字段访问
result.tool_call_reduction       # int
result.output_token_reduction    # int
result.semantic_debated_occurrences  # int
result.causal_claim              # "NONE"
```

### 不变式

- `ABResult()` 全零块：`tool_call_reduction == 0`, `output_token_reduction == 0`, `causal_claim == "NONE"`
- `build_ab_validation(trace)` 是纯函数：同一 trace 两次调用返回逐字段相等的结果
- `enable_analysis=False` 时 `DiagnosisResult.ab_result is None`
- `causal_claim` 恒为 `"NONE"`，`method` 恒为 `"static_restatement"`

---

## Testing

### 测试文件：`tests/test_b1_ab_validation.py`

| # | 用例 | 类别 | 覆盖要点 |
|---|------|------|----------|
| T1 | `test_ab_result_defaults_all_zero` | 数据块 | `ABResult()` 全部字段为 0/None/"NONE" |
| T2 | `test_empty_trace_returns_zero_block` | original/fixed 重述 | 空 Trace → ABResult 全零，不抛异常 |
| T3 | `test_no_duplicates_returns_zero_reduction` | original/fixed 重述 | 无重复调用 → tool_call_reduction=0，fixed=original |
| T4 | `test_single_duplicate_pair_tool_call_reduction` | tool-call 下降 | 1 组 N=2 确定性重复 → tool_call_reduction=1 |
| T5 | `test_duplicate_group_n3_reduction` | tool-call 下降 | 1 组 N=3 → tool_call_reduction=2 |
| T6 | `test_multiple_fingerprint_groups` | tool-call 下降 | 多组不同 fingerprint → 各自减 N-1 |
| T7 | `test_whole_step_deleted_conservative` | 删 step 数 | step 上仅 1 个 tc 且为冗余 → 整 step 可删，deleted_steps=1 |
| T8 | `test_shared_step_not_deleted` | 删 step 数 | step 上有 2 个 tc，仅 1 个冗余 → 整 step 不可删，deleted_steps=0 |
| T9 | `test_output_token_reduction` | output token 下降 | 被删 step 的 output_tokens 正确加总 |
| T10 | `test_input_token_not_claimed_as_saving` | input token 分离 | input_token_change 有值但标注为上下文，不被计入 output_token_reduction |
| T11 | `test_llm_retry_unchanged` | retry 严格分开 | 有 llm/retry 事件的 trace → llm_retry_change=0 |
| T12 | `test_tool_level_retry_separate_from_llm` | retry 严格分开 | TOOL-004 失败 attempt → tool_level_retries_saved > 0，但 llm_retry_change=0 |
| T13 | `test_semantic_debated_not_in_hard_savings` | 语义隔离 | 轮询型工具重复 → semantic_debated_occurrences > 0，但 tool_call_reduction=0 |
| T14 | `test_mixed_deterministic_and_debated` | 语义隔离 | 同时有确定性重复和轮询型重复 → 各自计数正确，hard 不包含 debated |
| T15 | `test_causal_claim_always_none` | causal_claim=NONE | 任何输入 → ABResult.causal_claim == "NONE" |
| T16 | `test_method_always_static_restatement` | 口径标注 | method == "static_restatement" |
| T17 | `test_deterministic_same_trace_twice` | 确定性 | 同一 trace 两次 build_ab_validation → 逐字段相等 |
| T18 | `test_tool004_failed_step_removed` | TOOL-004 修复 | 失败 attempt 所在 step 被标记为可删，tool004_failed_attempts=1 |
| T19 | `test_tool004_output_tokens_counted` | TOOL-004 修复 | 失败 step 的 output_tokens 计入 tool004_failed_step_output_tokens |
| T20 | `test_union_dedup_tool001_and_tool004` | 并集去重 | 同一 step 同时被 TOOL-001 和 TOOL-004 标记 → deleted_steps 不重复计数 |
| T21 | `test_additive_enable_analysis_false` | additive/金钟罩 | enable_analysis=False → ab_result is None，现有报告逐字节不变 |
| T22 | `test_ab_flag_enables_analysis` | enable_analysis 门控 | CLI --ab 自动设置 enable_analysis=True |
| T23 | `test_validation_set_sessions_exist` | 验证集 | AB_VALIDATION_SESSIONS 中的 session_id 格式合法 |
| T24 | `test_single_session_no_findings_all_zero` | 单会话全零块 | 无 TOOL-001/TOOL-004 的 trace → ABResult 全零 |
| T25 | `test_report_renders_ab_block_when_enabled` | report 渲染 | enable_analysis=True + ab_result 非 None → 报告含 AB 块 |
| T26 | `test_report_no_ab_block_when_disabled` | report 渲染 | enable_analysis=False → 报告不含 AB 块 |
| T27 | `test_fingerprint_consistency_with_detector` | 一致性 | build_ab_validation 的 fingerprint 分组与 TOOL-001 detector 一致 |
| **T28** | `test_golden_report_byte_identical_with_analysis_disabled`（评审 G） | additive/金钟罩 | enable_analysis=False → 完整报告输出与 v0.5 逐字节一致（补红线 1 证明） |
| **T29** | `test_fixed_fields_equal_original_minus_reduction`（评审 G） | fixed_* 恒等式 | fixed_* = original_* - reduction_*（steps/tool_calls/output_tokens/input_tokens/total_tokens） |
| **T30** | `test_debated_tool_calls_preserved_in_fixed`（评审 G） | 语义隔离 | debated 的 tool-call 在 fixed 中保留（fixed_tool_calls 不扣减 debated） |
| **T31** | `test_finding_counts_split_deterministic_vs_debated`（评审 G） | finding 拆分 | tool001_finding_count 与 tool001_finding_count_debated 正确拆分 |
| **T32** | `test_algorithm_matches_evidence_anchor`（评审 H） | 回归验证 | 对 fixture 化的代表会话(3 个 anchor),build_ab_validation 输出与 evidence §4 手动计算一致(deleted_steps/tool_call_reduction/output_token_reduction 精确匹配) |

### 回归测试

- 全量 pytest（当前 248 用例 + 33 新增 = 281）必须在 B1 集成后保持全绿
- 金钟罩：`test_disable_analysis_byte_identical_to_v05`（或 T28 等效）验证 enable_analysis=False 时报告逐字节不变

### 验证集 E2E

- 对 `AB_VALIDATION_SESSIONS` 中的每个 session_id，用真实 DSH 数据跑 `analyze --ab`，验证：
  - 命令成功退出（exit 0）
  - 报告含 AB 块
  - 数字下界满足 `expected_tool001_min` / `expected_tool004_min` / `expected_tool_call_reduction_min`
- 注：E2E 依赖本机数据（`@pytest.mark.dsh_data`），但**可复现性以 fixture 化测试（T32）为准**；E2E 仅作额外验证（评审 E）。

---

## Open Questions

### Q1：fixed 口径对 llm/retry 的影响——需验证

**问题**：当前设计 `llm_retry_fixed = llm_retry_original`（不变），因为 `llm/retry` 事件存储在 `trace.events[]` 中，不随 step 删除而变化。但理论上，若删除了某些 step，这些 step 的模型调用不再发生，对应的 llm/retry 也可能不再发生。

**当前立场**：实测 3 个代表会话中 llm/retry 事件数在修复前后不变，且 events 与 step 解耦（events 是独立事件层），因此设计保持 `llm_retry_change = 0`。若未来发现反例，需修正。

**实现时验证**：对验证集 5 个会话，检查 fixed trace 中是否真的有 step 删除了但其关联的 llm/retry 事件仍在 events 中。若存在，记录为已知局限。

### Q2：input token 在报告中的标注措辞

**问题**：`input_token_change` 有数值（被删 step 的 input_tokens 合计），在报告中如何标注才能既诚实又不误导？

**当前立场**：证据 D5b 模板中标注为"⚠ input token 变化是上下文变化，非可省成本"。数值仍显示，但用警告符号和说明隔离。

**备选**：完全不在报告中显示 input_token_change 数字——但用户可能想看到完整画面。当前设计折中：显示但标注。

### Q3：验证集会话如何固化——跨机可复现性（评审 E：已定，实现时导出 fixture）

**问题**：`AB_VALIDATION_SESSIONS` 中的 session_id 指向本机 `~/.dsh/sessions/` 中的真实会话。换一台机器后这些 session_id 不存在，验证集测试会失败。

**已定立场**（评审 E 强建议：fixture 固化是**实现的一部分**，非可选）：
- 实现时把 5 个验证集会话导出为 `tests/fixtures/b1_validation_sessions/{session_id}.json`（`CanonicalTrace.to_dict()` 序列化）。
- `T23` 改为检查 fixture 文件存在且可解析为 Canonical Trace（而非仅检查 id 格式合法）。
- 回归测试 `T32` 直接用 fixture，才是真正的跨机可复现。
- 本机 E2E（`@pytest.mark.dsh_data`）保留为额外验证，但 fixture 是权威可复现路径。

### Q4：`SEMANTIC_DEBATED_TOOLS` 的扩展机制

**问题**：`SEMANTIC_DEBATED_TOOLS` 是硬编码的 `frozenset`。若未来新增工具类型需要加入 debated 集合，如何扩展？

**当前立场**：`frozenset` 在模块顶层定义，可手动编辑。不做配置文件/CLI 参数（过度设计）。若需求出现，改为从 `trace.metadata` 或环境变量读取。

### Q5：粗模型口径是否在 ABResult 中保留字段

**问题**：evidence 中区分了保守模型（整 step 可删）和粗模型（含任一冗余 occurrence 即计）。当前设计只在保守模型下构造 fixed trace，粗模型仅作为报告参考。

**当前立场**：不保留粗模型字段。若未来需要，可在 ABResult 中新增 `coarse_*` 字段（如 `coarse_output_token_reduction`）。当前 YAGNI。

### Q6：`_match_param_error` 提升为公开函数——评审意见

**问题**：D7 决定将 `_match_param_error` 从私有改为公开，供 `build_ab_validation` 复用。是否违反"不改现有 detector 行为"？

**当前立场**：纯重命名（`_match_param_error` → `match_param_error`），不改函数签名、不改逻辑、不改 detector 内部调用。不违反 additive 铁律。若评审不同意，备选为内联等价实现。

---

## 附录 A：`SEMANTIC_DEBATED_TOOLS` 完整列表与理由

| 工具 | 理由 | 证据来源 |
|------|------|----------|
| `list_agents` | 长任务中反复读取 agent 列表，状态可能变化 | evidence §1.1：N=10 finding |
| `list_sessions` | 反复读取会话列表 | evidence §1.1：多个 N=5-6 finding |
| `session_status` | 反复查询会话状态 | evidence §1.1：多个 N=5-6 finding |
| `mcp__browser_use__browser_get_state` | 浏览器状态轮询，每步可能变化 | evidence §1.1：N=10×3 finding |
| `job_list` | 后台任务列表，状态可能变化 | evidence §1.1 |
| `job_output` | 轮询后台任务输出 | evidence §1.1：N=7 finding |
| `read_session` | 读取会话历史，可能合法重读 | evidence §1.1 |
| `memory_list` | 记忆列表，可能合法刷新 | evidence §1.1：N=11 finding |
| `mcp__browser_use__browser_navigate` | 浏览器导航，可能合法重定向 | evidence §1.1 |

> **边界说明（评审 D）**：`read` 对 HTTP URL 的重复读取在某些场景下可能是有意的重新校验（如检查远程内容是否更新），但由于区分 file:// 与 http(s):// 需要解析参数（过度设计），当前将 `read` 整体归为确定性重复。这可能略微高估硬可省量，但占比极小，且不影响 debated 子集的独立披露。

## 附录 B：与现有分析层数据块的同构对照

| 维度 | TokenInvariant (A1) | ContextHealth (CTX) | SessionLineage (A2) | ABResult (B1) |
|------|---------------------|---------------------|---------------------|---------------|
| 文件 | `analysis/token_invariant.py` | `analysis/context_health.py` | `analysis/session_lineage.py` | `analysis/ab_validation.py` |
| dataclass | `TokenInvariant` | `ContextHealth` | `SessionLineage` | `ABResult` |
| builder | `build_token_invariant(trace)` | `build_context_health(trace)` | `build_session_lineage(sid, map)` | `build_ab_validation(trace)` |
| 默认值 | 全零/None | 全零/None | 全零/None | 全零/None/"NONE" |
| causal_claim | NONE | NONE | NONE | NONE（硬编码） |
| 门控 | enable_analysis | enable_analysis | enable_analysis + session_map | enable_analysis |
| 进 Registry | ❌ | ❌ | ❌ | ❌ |
| 进 findings | ❌ | ❌ | ❌ | ❌ |
| 报告块 | `_render_token_invariant_block` | `_render_context_health_block` | `_render_session_lineage_block` | `_render_ab_validation_block` |

---

## 评审融合记录（deepseek-v4-pro 异模型评审,2026-08-22）

> 评审作为**阻塞闸门**执行,结论见 `openspec/changes/b1-ab-validation/review-pro.md`。8 条硬约束红线守住(无硬阻塞项);锁定 2 项**强建议**(E 固化 fixture、A 撤回 D7 改名)+ 若干建议(C/D/F/G/H),已全部融合进本设计。

| 评审项 | 结论 | 融合位置 |
|---|---|---|
| 红线 1–8 | 6 条完全守住,2 条部分存疑(A/C 已修) | — |
| **A D7 改名** | ⚠️ 弱问题:违反"源码零改动" | **撤回改名,改内联** `_is_param_error`(不动 tool_004.py),D7 已重写 |
| B fixed 口径公平性 | ✅ 保守模型正确 | 无改动(补充 tool-call 下降口径无关的说明) |
| C retry 严谨性 | ⚠️ 弱问题:llm_retry_change 欠严谨 | D4 加防御性扫描(warning)+ 报告措辞改"静态重述下不变;真实重跑可能不同" |
| D 语义隔离 | ⚠️ 弱问题:read URL 边界 | 附录 A 补 read URL 边界说明 |
| **E 验证集跨机** | 🔴 强问题:验证集形同虚设 | **fixture 固化提升为实现一部分**:导出 `tests/fixtures/b1_validation_sessions/{session_id}.json`;T23 改查 fixture;T32 直接用 fixture |
| F 指标完整性 | ⚠️ 缺 Task Success Rate | Non-Goals 显式排除(静态重述无法测量任务成功率) |
| G 测试完备性 | ⚠️ 补 4 用例 | 新增 T28(金钟罩)/T29(fixed 恒等式)/T30(debated 保留)/T31(finding 拆分) |
| H fixed 可复现 | ⚠️ 缺回归验证 | 新增 T32(算法输出与 evidence §4 锚点精确匹配) |

**结论:设计已按评审 A–H 融合定稿,可进入实现。** 交实现会话 `session-d2c507cf`。

---

## 实现对齐记录（2026-08-22,实现会话完成后的 3 处偏离——均为合理事实对齐,已确认）

| # | 偏离 | 性质 | 判定 |
|---|---|---|---|
| 1 | D6 `expected_*` 下界按语义隔离重设 | 事实对齐(design D6 数字引 evidence §4 未隔离,与 D2 冲突) | ✅ 合理:守 D2 语义隔离(核心诚实边界),下界下调 |
| 2 | T32 锚点用语义隔离值 | 同上 | ✅ 合理:evidence §4 未隔离,D2 隔离后锚点值不同 |
| 3 | fixture 序列化用本地 `_trace_to_dict` | 事实对齐(canonical_trace.py 无 to_dict 且不改它) | ✅ 合理:钢铁不改 canonical_trace,本地实现功能等价 |

**注**:第 1/2 项反映"开分析层必须守语义隔离"的设计意图,非违规。已同步 D6 说明。实现还正确落地:**spec 由主控预建**(`specs/analysis/b1-ab-validation/spec.md`),validate --strict 凭 spec 通过,无需补 `.openspec.yaml`(与 A1/A2 不同)。