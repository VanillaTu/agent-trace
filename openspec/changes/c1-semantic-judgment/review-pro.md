# 评审报告 — c1-semantic-judgment (C1) 设计定稿

> **评审模型**: deepseek-v4-pro | **评审角色**: 外部评审专家 pro | **评审策略**: 严格性优先，只找缺陷
> **评审对象**: `design.md` (824 行) + `proposal.md` + `spec.md` + B1 现状 + 项目铁律
> **评审日期**: 2026-08-22

---

## 硬约束红线逐条核对

| # | 硬约束 | 状态 | 简要说明 |
|---|--------|------|----------|
| 1 | LLM 语义层在 agent 身上，AgentTrace 不内置 LLM 调用；不做 DSH 插件 | ✅ 守住 | Non-Goals 明确声明；D0-D4 均不包含 LLM 调用；候选清单 JSON 供 agent 消费 |
| 2 | causal_claim=NONE: verdict 是语义建议，非硬断言 | ✅ 守住 | SemanticVerdict.causal_claim 硬编码 "NONE"；报告渲染标注"语义建议，非硬断言" |
| 3 | additive: 不改现有 detector/attribution 行为；enable_analysis=False 逐字节不变 | ✅ 守住 | Non-Goals 声明；T28-T31 覆盖金钟罩；不注册 ALL_DETECTORS/ALL_ATTRIBUTION_ENGINES |
| 4 | 未回填时 verdict 保持 not_applicable，不猜测 | ✅ 守住 | T25 覆盖；D3 合并算法未匹配时保持 not_applicable |
| 5 | 候选清单/上下文构造纯确定性；agent 的 LLM 判定不作为 AgentTrace 确定性计算输入 | ✅ 守住 | D1/D2 纯函数；verdict 独立于 CounterEvidence（OQ6） |
| 6 | 不注册 ALL_DETECTORS/ALL_ATTRIBUTION_ENGINES；不进入 findings/attributions | ✅ 守住 | Non-Goals 声明；T29 覆盖；D4.1 门控逻辑 |
| 7 | 复杂系统只测局部封闭不变量；设计权衡为 hedged 建议 | ✅ 守住 | causal_claim=NONE；报告标注"语义建议"；verdict 不改变硬可省数字 |

**红线结论**: 全部 7 条硬约束均已守住，无硬红线违规。

---

## 逐项评审

### 【A】交付闭环

**【结论】: 闭环逻辑成立但存在 3 个实用性缺口，其中一个为阻塞级。**

**【理由】**:

1. **闭环路径成立**: AgentTrace 检出 → 候选 JSON → agent 读 → LLM 判定 → 回填 JSON → 报告合并。这条路径在技术上是完整的，每一步都有明确的输入/输出格式定义（D4.2 的 JSON schema、D3 的回填格式、D3 的合并算法）。

2. **候选 JSON 上下文基本足够但缺关键信息**: 候选 JSON 包含 turn_id/step_id/fingerprint/tool_name/arguments/context（gap_steps/intervening_actions/tool_result_changed/result_snippet）。agent 可以据此定位和判断。但缺少：
   - 原始 TOOL-001 finding 的 confidence 和 severity（如 "这个重复是 high severity 还是 low"——agent 需要知道优先级）
   - 规则层已有的 counter_evidence（B1 的 gap 反证等）——agent 需要知道规则层已经说了什么，避免重复劳动或矛盾
   - 候选 JSON 的 `result_snippet` 仅 500 字符，对于 `browser_get_state`（完整 DOM snapshot）或 `memory_list`（大量条目）可能严重不足。OQ5 承认此问题但未解决——"agent 可以通过 read_session 自行获取"增加了 agent 的额外步骤，破坏闭环的流畅性。

3. **【阻塞】workflow 不顺畅——agent 不知道要回填**:
   - 设计描述了"agent 读取候选 JSON → 判定 → 回填"的技术路径，但**完全没有定义 agent 如何被触发执行这个流程**。
   - `--semantic` 输出候选 JSON 到文件或 stdout。然后呢？agent 需要被手动告知"请读取这个 JSON，对每个候选给出 verdict，写入 verdicts.json"。这不是自动化的闭环——是人工编排的三步流程。
   - 候选 JSON 中没有嵌入任何 "instruction" 或 "prompt" 告诉 agent 它应该做什么、输出什么格式。agent 需要外部知识（如系统提示）才知道要回填。这使得闭环在"首次使用"场景下不可用——agent 看到候选 JSON 不知道要做什么。
   - 对比项目的核心定位（"AgentTrace 作为工具被 agent 调用"），当前设计让 agent 的调用方（用户）承担了太多编排责任。

**【是否需改+具体改法】**:

**必改（阻塞）**: 在候选 JSON 顶层增加 `"instructions"` 字段，包含：
- 明确告知 agent 这是语义判断候选清单，需要它用自身 LLM 判定每个候选
- 输出格式说明（verdict JSON schema）
- 判定准则（如何利用 gap_steps / intervening_actions / tool_result_changed 判断）
- 示例（一个 true_redundant、一个 legitimate、一个 uncertain 的判定示例）

```json
{
  "session_id": "...",
  "instructions": {
    "task": "You are reviewing AgentTrace's duplicate tool-call candidates. For each candidate, judge whether the repeated call was truly redundant or a legitimate polling/state-check.",
    "output_format": "Write your verdicts to a JSON file with the schema: { \"verdicts\": [{ \"rule_id\": ..., \"fingerprint\": ..., \"turn_id\": ..., \"step_id\": ..., \"verdict\": \"true_redundant\"|\"legitimate\"|\"uncertain\", \"confidence\": 0.0-1.0, \"reason\": \"...\" }] }",
    "criteria": [
      "If tool_result_changed=false and no intervening writes → likely true_redundant",
      "If tool_result_changed=true or intervening writes exist → likely legitimate",
      "If tool_result_changed=null (truncated) → use other signals; mark uncertain if still ambiguous"
    ]
  },
  "candidates": [...]
}
```

**建议改**: 候选 JSON 中每个 candidate 增加 `"finding_confidence"` 和 `"finding_severity"` 字段（从原始 TOOL-001 finding 中提取），让 agent 知道这个候选的规则层优先级。

---

### 【B】确定性边界 — WRITE_ACTIONS 与 tool_result_changed

**【结论】: WRITE_ACTIONS 有遗漏，tool_result_changed 逻辑过于保守导致大量 None，两个问题叠加削弱 C1 信号价值。**

**【理由】**:

1. **WRITE_ACTIONS 遗漏 `todo_write`**: 这是明确的写入操作——修改 todo 列表会改变 agent 状态。当前硬编码 15 个工具中不含 `todo_write`。对比已包含的 `memory_save`/`memory_update`（状态变更），`todo_write` 同属状态变更类，遗漏是明显的 false negative。

2. **WRITE_ACTIONS 遗漏 `mcp__email__send_email`**: 发送邮件是明确的副作用操作，会改变外部状态。DSH 的 MCP 工具命名空间为 `mcp__<server>__<tool>`，按此模式 `mcp__email__send_email` 应被覆盖。

3. **WRITE_ACTIONS 遗漏 `ask_user_question`**: 向用户提问会改变对话状态（等待用户回复），属于交互式状态变更。虽然不如文件写入那么"硬"，但在判断"两次调用之间是否有状态变化"时，用户交互是重要的上下文变化。

4. **硬编码集合不可扩展**: DSH harness 支持任意 MCP 插件，新工具可能随时加入。当前硬编码集合无法自动识别新的写入工具。OQ4 承认此问题，但"加注释标注与 DSH 工具集同步维护"是弱解决方案——注释不会自动执行。

5. **【较严重】tool_result_changed 逻辑过于保守**:
   - 当前设计: "若任一被截断 → tool_result_changed = None（无法判断）"
   - 这意味着: 只要有一次调用的结果 ≥ 500 字符（极其常见——`list_sessions`、`memory_list`、`browser_get_state`、`read` 等），tool_result_changed 就永远是 None。
   - **正确逻辑应为**: 若两者都截断且 500 字符前缀相同 → None（无法判断）；若 500 字符前缀不同 → True（已变化，确定性结论）；若仅一个截断但前缀不同 → True。
   - 当前设计把"截断"等同于"不可比"，损失了大量可确定的信息。例如 `list_sessions` 返回 10 个会话（800 字符），第二次返回 5 个会话（600 字符），前 500 字符大概率不同 → 可以确定 tool_result_changed=True，但当前设计会返回 None。
   - 实际影响: 对于 `SEMANTIC_DEBATED_TOOLS` 中的 9 个工具，大部分返回结果会超过 500 字符（`browser_get_state`、`list_sessions`、`memory_list`、`read_session`、`job_output`），导致 tool_result_changed 大面积 None，C1 最核心的信号失效。

6. **500 字符阈值的选择缺少实证依据**: 设计未说明为什么是 500 而非 200 或 1000。对于 JSON 结果，500 字符可能截断在 JSON 结构的中间，破坏可读性。建议至少基于真实数据中 debated 工具的典型结果长度做一次实证校准。

**【是否需改+具体改法】**:

**必改**: 修正 tool_result_changed 判断逻辑：

```python
# 修正后的逻辑（伪代码）
if prev_result is None or curr_result is None:
    tool_result_changed = None  # 无法比较
elif len(prev_result) < 500 and len(curr_result) < 500:
    tool_result_changed = (prev_result != curr_result)  # 精确比较
elif prev_result[:500] != curr_result[:500]:
    tool_result_changed = True  # 前缀已不同，确定变化
else:
    tool_result_changed = None  # 前缀相同但可能截断点之后不同
```

**必改**: WRITE_ACTIONS 补充遗漏：
```python
WRITE_ACTIONS: frozenset[str] = frozenset({
    # ... 现有 15 个 ...
    "todo_write",             # 修改 todo 列表（状态变更）
    "ask_user_question",      # 向用户提问（交互状态变更）
    # MCP 插件写入工具（按 DSH 命名空间 pattern 匹配）
    # 注意：mcp__*__send_* / mcp__*__create_* / mcp__*__delete_* / mcp__*__update_*
    # 无法穷举，保留注释供扩展
})
```

**建议改**: 对 500 字符阈值做实证校准——从 B1 验证集的 5 个真实会话中取样 debated 工具的结果长度分布，选择一个能覆盖 80%+ 结果不截断的阈值（可能 1000–2000）。

---

### 【C】判定信号有效性 — C1 的增量价值

**【结论】: 判定信号（gap_steps / intervening_actions / tool_result_changed）对规则层已有部分覆盖，但 C1 的增量价值在于 per-occurrence 粒度 + 结构化上下文 + 工具结果对比。增量价值真实但有限，不构成阻塞。**

**【理由】**:

1. **gap_steps 已被 B1 规则层使用**: `counter_evidence.py` 的 `_tool_001` 函数已经基于 gap_steps 做置信度调整和反证生成。C1 的 gap_steps 不是新信号——但 C1 提供的是 per-occurrence 的精确 gap（而非 B1 的 per-finding 最大间隔），粒度更细。

2. **intervening_actions 是新信号且规则层可以做但未做**: B1 的 counter_evidence 只看 gap 大小，不看间隔中具体发生了什么。C1 给出结构化的干预动作列表（含 is_write 标记），这是真正的增量信息。但——如果"no writes + no result change → redundant"这个启发式足够强，它本质上仍是确定性规则，不需要 LLM。C1 的价值在于: 当这个启发式不够时（如 tool_result_changed=None、intervening_actions 含 reads 但可能有间接影响），LLM 能综合判断。

3. **tool_result_changed 是 C1 最有价值的增量信号**: B1 完全不比较工具结果。如果 tool_result_changed 能可靠地判定（而非大面积 None），它本身就是最强的区分信号——结果没变 → 大概率冗余，结果变了 → 大概率合法。这个信号的价值高度依赖 B 项的修正（tool_result_changed 逻辑修正）。

4. **C1 的增量价值总结**:
   - per-occurrence 粒度: B1 是 per-finding（一组重复调用整体），C1 是 per-occurrence（每次重复单独判断）。这是真实增量——同一 finding 内，第 2 次和第 5 次重复可能有不同的合法性。
   - 结构化上下文: intervening_actions 列表 + tool_result_changed 是 B1 没有的。
   - debated 优先排序: 让 agent 聚焦最需要判断的候选，而非遍历所有重复。

5. **"规则层不能做吗"的边界**: 如果 tool_result_changed 逻辑修正后，规则"no intervening writes + tool_result_changed=False → true_redundant"可以覆盖大部分场景。但以下场景仍需 LLM:
   - tool_result_changed=None（截断且前缀相同）
   - intervening_actions 含 reads 但 reads 可能间接改变状态（如 `read_session` 读了子会话的更新）
   - 混合信号（有 write 但 tool_result_changed=False → 写了但结果没变？可能是幂等写入）
   - 低置信度边缘情况

**【是否需改+具体改法】**:

**不需要改设计**。C1 的增量价值真实存在——per-occurrence 粒度 + 结构化上下文 + 工具结果对比。价值的大小取决于 B 项的修正（tool_result_changed 逻辑修正后信号会更强）。建议在 design 的 D1 中增加一段"为什么规则层不能替代 LLM"的论述，明确规则/LLM 的分界线。

---

### 【D】verdict 不改变硬可省 — "只展示不决策"

**【结论】: 设计选择正确（保持确定性与语义层分离），但价值主张需更明确——当前设计让 C1 变成"昂贵的 display-only 层"，需在报告中增强 verdict 的可操作性。**

**【理由】**:

1. **OQ6 的决策是正确的**: 不将 verdict 混入 CounterEvidence 或硬可省数字。理由充分——语义判定是"agent 的判断"而非"trace 证据"，混入会破坏确定性边界。

2. **但"只展示不决策"确实削弱了 C1 的价值**: 用户看到 agent 判了 `true_redundant`，但报告中的硬可省数字没有变化。这会产生认知 dissonance——"既然 agent 都说这是真冗余，为什么不算？"

3. **verdict 的实际用途**:
   - 正向: 供人类开发者做代码改进决策（"这个重复调用确实该删"）
   - 正向: 审计追踪（记录 agent 的判断过程）
   - 负向: 如果用户期望 verdict 改变数字而它不改变，会降低对 C1 的信任

4. **设计对此的回应不够充分**: design 只在 OQ6 中讨论了"不追加到 finding"，但没有正面回答"那 verdict 到底有什么用"。报告渲染中 verdict 标注"语义建议，非硬断言"是好的防御性标注，但缺少正面的价值说明。

**【是否需改+具体改法】**:

**建议改（非阻塞）**: 在 B1 的 ABResult 报告中，`semantic_debated_occurrences` 旁边增加一个子项:

```
- 语义存疑(debated): 15
  - 其中 agent 判为真冗余: 8 (未计入硬可省，供人工审查)
  - 其中 agent 判为合法: 5
  - 其中 agent 判定不确定: 2
```

这样 verdict 虽然不改变数字，但为 debated 数字提供了"下钻"信息，增强了可操作性。同时保持 causal_claim=NONE 和"语义建议"标注。

**建议改**: 在 design 的 Goals 中增加一条: "为 B1 的 debated 统计提供语义下钻（agent 判定明细），帮助用户聚焦高价值改进点"。

---

### 【E】与 B1 的关系 — 语义矛盾

**【结论】: 存在语义张力（agent 判"真冗余"但 B1 仍不计入硬可省），但设计有清晰的哲学一致性。需在报告中明确标注以避免用户困惑。非阻塞但强烈建议处理。**

**【理由】**:

1. **矛盾场景**: agent 对某个 `list_sessions` 重复判 `true_redundant`（置信度 0.90），但 B1 的 `semantic_debated_occurrences` 仍然计入它，`tool_call_reduction` 不包含它。用户看到报告: "为什么 agent 说真冗余却不省？"

2. **设计的哲学一致性**: OQ6 的立场——"语义判定是 agent 的判断而非 trace 证据"——在哲学上是自洽的。确定性数字（硬可省）应只基于 trace 中的确定性证据；LLM 判定是概率性的外部输入。

3. **但实用层面有问题**: 用户不关心哲学一致性，关心"这个报告告诉我什么可以做"。如果 agent 高置信度判了真冗余，用户期望能据此行动。当前设计让用户需要在脑中做"B1 数字 + C1 verdict"的自行合并，增加了认知负担。

4. **B1 的一刀切 vs C1 的精细化**: B1 对 SEMANTIC_DEBATED_TOOLS 中的工具一刀切全部不计入硬可省。C1 的 agent 可以逐个判断。这两个层次的信息存在自然的"汇总"关系——C1 的 verdict 是对 B1 的 debated 统计的细化。但当前设计没有利用这个关系。

**【是否需改+具体改法】**:

**建议改（非阻塞）**: 在 B1 的 ABResult 报告中增加 `semantic_confirmed_redundant` 和 `semantic_confirmed_legitimate` 两个字段（从 C1 verdict 回填后计算），标注为"语义确认，非硬可省，供参考"。这样 B1 和 C1 的信息在报告中形成"汇总→下钻"的关系，而非"矛盾"。

**建议改**: 在 design 的 OQ6 中增加一段，明确讨论 B1 debated 统计与 C1 verdict 的展示关系，以及为什么不合并的原因。

---

### 【F】测试完备性

**【结论】: T1-T42 覆盖了核心路径，但存在 6 个可测试的缺口，其中 2 个可能导致实现时遗漏边界 bug。**

**【理由】**:

覆盖情况矩阵:

| 覆盖域 | 测试 | 状态 |
|--------|------|------|
| 候选生成（空/单/多/debated/TOOL-004） | T4-T10 | ✅ |
| 排序（debated 优先/高倍率优先） | T11-T12 | ✅ |
| 上下文构造（gap/干预/结果变化/截断） | T13-T19 | ✅ |
| 回填合并（全量/部分/无匹配） | T20-T22 | ✅ |
| source=semantic / causal_claim=NONE | T23-T24 | ✅ |
| 未回填 not_applicable | T25 | ✅ |
| 确定性（候选清单/上下文两次一致） | T26-T27 | ✅ |
| additive/金钟罩（关闭/检测器不变/finding 不变/逐字节） | T28-T31 | ✅ |
| 报告渲染（语义块存在/关闭/无 wasted/待判定/已回填） | T32-T36 | ✅ |
| CLI 输出（合法 JSON/结构/门控/写出） | T37-T40 | ✅ |
| 边界（空候选/缺失 step） | T41-T42 | ✅ |

**缺口**:

1. **【缺口】tool_result_changed 边界值**: 没有测试精确 500 字符截断边界（499/500/501 字符结果）。这是 tool_result_changed 逻辑的核心边界，当前 T18 只测了"超过 500 字符被截断"但未测边界值。如果 B 项修正后被采纳（前缀比较逻辑），需要更多边界测试。

2. **【缺口】WRITE_ACTIONS 误判**: 没有测试验证 WRITE_ACTIONS 集合中的工具确实被标记为 is_write=True，也不在集合中的工具被标记为 is_write=False。也没有测试新增工具（如 `todo_write`）是否被正确识别。当前 T14 只测了 send_message 一个案例。

3. **【缺口】verdict JSON 格式错误处理**: `merge_semantic_verdicts` 读取 JSON 文件——如果文件格式错误（非 JSON、缺少必需字段、verdict 值非法枚举），函数行为未定义也无测试。应至少测试 malformed JSON → 明确错误/空合并。

4. **【缺口】context=None 时的报告渲染**: T42 测试了缺失 step 时 context 为全默认值，但 `_render_single_candidate` 中对 `c.context` 有条件判断 `if c.context:`——没有测试 context=None 时报告是否正常渲染（不崩溃）。

5. **【缺口】TOOL-004 含成功重试的上下文对比**: T10 测试了基本 TOOL-004 候选，但 design 提到"若有成功重试：context 含成功调用的结果对比"——这个场景没有独立测试。TOOL-004 的失败→成功重试对比是 C1 对 TOOL-004 的核心价值。

6. **【缺口】同一步多个 tool_call 时 fingerprint 定位**: 一个 step 可能有多个 tool_call（如实测中常见 `read` + `grep` 同 step）。`build_judgment_context` 通过 fingerprint 定位具体 tool_call——如果同一步内有两个同 fingerprint 的调用（罕见但可能），当前逻辑取第一个匹配。没有测试这个边界。

**【是否需改+具体改法】**:

**建议补充测试**（非阻塞但强烈建议，防止实现时遗漏）:

| # | 新增用例 | 说明 |
|---|---------|------|
| T43 | `test_tool_result_changed_boundary_500` | 结果 499/500/501 字符的截断判断 |
| T44 | `test_write_actions_coverage` | 验证已知写入工具 is_write=True，已知只读工具 is_write=False |
| T45 | `test_merge_verdicts_malformed_json` | 格式错误的 verdict JSON → 明确错误处理 |
| T46 | `test_report_context_none_renders` | context=None 的候选正常渲染不崩溃 |
| T47 | `test_tool004_with_successful_retry` | TOOL-004 失败 attempt + 成功重试的上下文对比 |
| T48 | `test_same_step_multiple_tool_calls_fingerprint_match` | 同一步多 tool_call 时 fingerprint 定位正确 |

---

### 【G】过度设计

**【结论】: 存在中等程度的过度设计风险，但不是阻塞级。4 个 dataclass 结构合理，但 SemanticVerdict 可合并、报告渲染代码量偏大、CLI 3 个新 flag 可精简。**

**【理由】**:

1. **4 个 dataclass 的结构分析**:
   - `SemanticCandidate`: 必需。候选清单的核心数据结构。
   - `JudgmentContext`: 必需。上下文信息需要结构化序列化。
   - `InterveningAction`: 必需。JudgmentContext 的子结构。
   - `SemanticVerdict`: **可合并**。当前设计将其独立于 SemanticCandidate 以"支持回填后合并，不修改原始候选"。但实际使用中，verdict 就是 candidate 的一个属性——可以直接在 SemanticCandidate 上增加 `verdict`/`confidence`/`reason`/`source`/`causal_claim` 字段（默认值 `not_applicable`/`0.0`/`""`/`"semantic"`/`"NONE"`）。独立 SemanticVerdict 增加了匹配逻辑的复杂度（需要复合 key 匹配），而合并到 SemanticCandidate 后 merge 函数直接原地修改 candidate 即可。

2. **报告渲染代码量**: `_render_semantic_block`（~50 行）+ `_render_single_candidate`（~30 行）= ~80 行报告代码。对于一个"display-only"的层，这个量偏大。但考虑到需要区分 debated/deterministic、已回填/未回填、verdict 三色标签，这个量可以接受。

3. **CLI 3 个新 flag 偏多**: `--semantic`、`--semantic-verdicts`、`--semantic-out`。`--semantic-out` 和 `--semantic-verdicts` 可以合并——`--semantic` 输出候选 JSON 到 stdout 或文件，`--semantic-verdicts` 读取回填。但 `--semantic-out` 只是一个输出重定向（`>` 就能做到），增加了 CLI 表面积。

4. **42 个测试**: 对于 4 个 dataclass + 4 个函数，42 个测试偏多但不是浪费。核心路径都有覆盖，但部分测试（如 T1-T3 的默认值测试）可以合并为参数化测试。42 个测试不构成过度设计，但实现时需注意维护成本。

5. **与"更轻量方案"的对比**:
   - 更轻方案: 在 B1 的 `build_ab_validation` 中增加一个可选参数 `output_candidates=True`，对 debated 工具额外输出候选 JSON（含 context），不引入新的 dataclass。
   - 当前设计的优势: 关注点分离清晰，C1 独立演进不耦合 B1。如果未来 B1 重构，C1 不受影响。
   - 当前设计的劣势: 新增文件/类/测试/CLI flag 的维护成本。

6. **整体判断**: 设计在"完整性"和"轻量性"之间偏向了完整性。对于"语义判断层"这个定位，完整的数据结构是合理的。但 SemanticVerdict 独立 dataclass 和 3 个 CLI flag 有精简空间。

**【是否需改+具体改法】**:

**建议改（非阻塞）**: 合并 SemanticVerdict 到 SemanticCandidate:

```python
@dataclass
class SemanticCandidate:
    # ... 现有字段 ...
    
    # ── 回填 verdict（agent 的 LLM 判定）──
    verdict: str = "not_applicable"
    confidence: float = 0.0
    reason: str = ""
    source: str = "semantic"
    causal_claim: str = "NONE"
```

这样 `merge_semantic_verdicts` 直接修改 candidate 的 verdict 字段，无需复合 key 匹配逻辑（直接在 candidate 列表中查找匹配的 fingerprint + turn_id + step_id）。同时减少一个 dataclass 和对应的测试。

**建议改（非阻塞）**: 去掉 `--semantic-out` flag。`--semantic` 输出候选 JSON 到 stdout，用户用 shell 重定向 `>` 即可。减少 CLI 表面积。

---

## 总评

### 设计整体评价

C1 设计在**架构一致性**上表现优秀：严格守住了 7 条硬约束红线，LLM 层在 agent 身上的架构决策贯彻到底，候选清单/上下文纯确定性构造，verdict 独立于硬可省数字。数据结构设计清晰（JudgmentContext 的分层设计尤为合理），与 B1 的复用关系明确（SEMANTIC_DEBATED_TOOLS 单一真相源）。

但在**实用性**上有 3 个缺口：交付闭环缺少 agent 指令（agent 不知道要回填）、tool_result_changed 逻辑过于保守导致核心信号大面积失效、WRITE_ACTIONS 遗漏关键工具。这 3 个问题叠加会导致 C1 在真实使用中"候选清单能生成，但 agent 不知道怎么判、判了也缺乏可靠信号"。

### 必改项（阻塞实现）

| 编号 | 项 | 严重性 | 说明 |
|------|-----|--------|------|
| **A-阻塞** | 候选 JSON 缺少 agent 指令 | 🔴 阻塞 | agent 读取候选 JSON 后不知道要做什么、输出什么格式。必须在候选 JSON 中嵌入 `instructions` 字段。 |
| **B-必改** | tool_result_changed 逻辑过于保守 | 🔴 阻塞 | "任一截断→None"导致 debated 工具大面积不可判断。修正为前缀比较逻辑。 |
| **B-必改** | WRITE_ACTIONS 遗漏 `todo_write` | 🟡 强建议 | 明确的写入操作被遗漏，影响 intervening_actions 的 is_write 标记准确性。 |

### 建议改项（非阻塞，但强烈建议实现前修正）

| 编号 | 项 | 说明 |
|------|-----|------|
| **D-建议** | B1 报告中增加语义下钻 | 在 debated 统计旁展示 agent 判定明细，增强 verdict 可操作性 |
| **E-建议** | 明确 B1 debated 与 C1 verdict 的展示关系 | 避免"agent 说真冗余但 B1 不算"的认知 dissonance |
| **F-建议** | 补充 6 个测试用例（T43-T48） | 覆盖截断边界、WRITE_ACTIONS 覆盖、malformed JSON、context=None 渲染、TOOL-004 成功重试、同步骤多 tool_call |
| **G-建议** | 合并 SemanticVerdict 到 SemanticCandidate | 减少一个 dataclass，简化 merge 逻辑 |
| **G-建议** | 去掉 `--semantic-out` flag | 用 shell 重定向替代，减少 CLI 表面积 |
| **C-建议** | D1 增加"为什么规则层不能替代 LLM"论述 | 明确 C1 增量价值边界 |

### 最终判定

**设计可进入实现，但必须先修复 A-阻塞（候选 JSON 增加 agent 指令）和 B-必改（tool_result_changed 逻辑修正 + WRITE_ACTIONS 补充）两项。** 这 3 个修改集中在 design.md 的 D1（上下文构造算法）和 D4.2（候选清单 JSON 格式），修改范围可控，不涉及架构调整。建议修改完成后复审 D1/D4.2 两节，再交实现。

---

*评审人: deepseek-v4-pro (外部评审专家 pro) | 评审完成时间: 2026-08-22*