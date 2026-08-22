# Design — c1-semantic-judgment (C1)

> **状态**: 定稿 | **产出**: deepseek-v4-pro | **输入**: proposal.md + spec.md + B1 现状 + 项目铁律

---

## Context

AgentTrace 的三层评判设计为 **确定性规则 → 统计证据 → LLM 语义**，但 LLM 语义层从未实现——`detectors/base.py` 的 `CounterEvidence.source="semantic"` 标注"设计预留，本次不实现"。B1 暴露了最直接的缺口：B1 把轮询型工具（`list_agents`/`browser_get_state`/`list_sessions` 等）**一刀切全部标为 `semantic=debated`、不计入硬可省**——这是"双保险但没判断"的做法，无法区分"这次重复是真冗余"还是"合法轮询需刷新状态"。

**关键架构洞察（用户两次澄清）**：AgentTrace 是确定性 Python 工具，把 LLM 语义判断**内置进 AgentTrace 是错的方向**——它需要一个可用的 LLM endpoint（当前环境无：本地 ollama 超时、无 API key），且"规则假装 LLM"没有价值。**正确的做法是：LLM 语义层在"调用工具的 agent"身上**——DSH harness 的 agent 本身就是 LLM，让它用自身模型审视 AgentTrace 检出的候选重复。这天然解决 LLM endpoint 问题，并回归项目定位（测 harness 工具性：AgentTrace 作为工具被 agent 调用、agent 用 LLM 审视工具输出，正是"工具-智能体交互"验证）。

**现有基础设施**（可直接复用）：
- `ab_validation.py` 的 `SEMANTIC_DEBATED_TOOLS` 集合（9 个轮询型工具）——C1 复用同一集合判定 `is_debated`
- `ab_validation.py` 的 `build_ab_validation` 的 fingerprint 分组逻辑（`call_fingerprint` + 全局序号）——C1 的候选生成复用同一分组思路
- `detectors/base.py` 的 `CounterEvidence.source` 已有 `"rule"` / `"semantic"` 两档——C1 落地 `source="semantic"`
- `pipeline.py` 的 Stage 3 分析层（`enable_analysis` 门控）——C1 候选清单生成在此阶段挂载
- `report.py` 的分析层块渲染模式（A1/B1/CTX-001）——C1 的「语义判断(C1)」块按同模式追加

**交付闭环**（用户确认）：AgentTrace 检出 candidate → 输出候选清单 JSON（每候选附上下文）→ agent 调用工具读取 → 自身 LLM 判定（真冗余/合法 + 置信度 + 理由）→ 回填（输出到文件/记录）→ AgentTrace 报告合并回填后的 verdict。

---

## Goals & Non-Goals

### Goals

1. **候选清单数据结构**：定义 `SemanticCandidate` dataclass，包含完整的定位信息、工具参数、是否 debated、判断上下文
2. **上下文构造算法**：纯函数、确定性，为每个候选生成"判断上下文"——前一次调用与本次之间是否有会改变 agent 状态的动作
3. **`build_semantic_candidates(trace, findings)` 函数**：从 TOOL-001/TOOL-004 finding 生成候选清单，优先标出 debated，返回候选列表
4. **verdict 回填合并**：agent 回填（verdict + confidence + reason）→ 合并进报告；`source="semantic"`；未回填时 `not_applicable`
5. **pipeline / report / CLI 集成**：候选清单生成门控（`enable_analysis` / `--semantic`）+ 报告「语义判断(C1)」块渲染 + CLI 输出候选 JSON 供 agent 消费
6. **测试设计**：`tests/test_c1_semantic.py` 用例清单，覆盖候选生成、上下文构造、轮询型置前、verdict 回填合并、source=semantic、未回填 not_applicable、causal_claim=NONE、additive/金钟罩、确定性、enable_analysis 门控

### Non-Goals

- **不做 DSH 插件**（cordis/TS 跨语言成本高，偏离核心）
- **不内置 LLM 调用到 AgentTrace**（语义判断交给 agent）
- **不改现有 detector 行为**：候选/上下文构造 additive，不改变 TOOL-001/TOOL-004 检出
- **不改变硬可省数字**：agent 的 verdict 是语义标注，不进入硬可省量
- **不虚构语义**：无 agent 回填时，verdict 保持 `not_applicable`，不猜测
- **不注册进 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`**

---

## Decisions

### D0: 候选清单数据结构 — `SemanticCandidate` + `JudgmentContext`

**核心设计**：每个候选是一个 `SemanticCandidate` dataclass，包含定位信息、工具参数、是否 debated、以及完整的判断上下文。判断上下文独立为 `JudgmentContext` dataclass，包含前后 step 摘要、干预动作列表、工具结果变化。

```python
@dataclass
class SemanticCandidate:
    """语义判断候选：一个需要 agent 的 LLM 判定的重复调用实例。

    全部字段带默认值；空 trace 或无 TOOL-001 finding → 空列表。
    """

    # ── 定位 ──
    rule_id: str = "TOOL-001"
    """来源规则：TOOL-001（重复调用）或 TOOL-004（无效参数重试）。"""

    turn_id: int = 0
    step_id: int = 0
    fingerprint: str = ""

    # ── 工具与参数 ──
    tool_name: str = ""
    arguments: str = ""

    # ── 是否 debated（轮询型工具） ──
    is_debated: bool = False
    """True = 轮询型工具（SEMANTIC_DEBATED_TOOLS），规则层无法区分冗余/合法。"""

    # ── 出现序号 ──
    occurrence_index: int = 1
    """该 fingerprint 的第几次出现（1-based）。第 1 次是 baseline，≥2 是候选。"""

    total_occurrences: int = 1
    """该 fingerprint 的总出现次数。"""

    # ── 判断上下文 ──
    context: JudgmentContext | None = None
    """附带的判断上下文；构造失败时保持 None（保守：不虚构上下文）。"""

    # ── 回填 verdict（评审 G：合并进 SemanticCandidate，移除独立 SemanticVerdict）──
    verdict: str = "not_applicable"
    """agent 的 LLM 判定："true_redundant" | "legitimate" | "uncertain" | "not_applicable"(默认未判定)。"""
    confidence: float = 0.0
    """置信度 0.0-1.0；未判定时保持 0.0。"""
    reason: str = ""
    """判定理由(agent 的 LLM 给出的自然语言解释)。"""
    source: str = "semantic"
    """恒为 "semantic"，与规则层 source="rule" 区分。"""
    causal_claim: str = "NONE"
    """恒为 "NONE"：verdict 是语义建议，非硬断言。"""


@dataclass
class JudgmentContext:
    """判断上下文：为 agent 判定"真冗余/合法"提供确定性信息。

    纯确定性构造；不包含任何 LLM 推断或猜测。
    """

    # ── 前一次出现 ──
    previous_turn_id: int = 0
    previous_step_id: int = 0
    previous_global_step: int = 0
    previous_result_snippet: str = ""
    """前一次调用结果的截断摘要（前 500 字符）。"""

    # ── 本次出现 ──
    current_turn_id: int = 0
    current_step_id: int = 0
    current_global_step: int = 0
    current_result_snippet: str = ""
    """本次调用结果的截断摘要（前 500 字符）。"""

    # ── 间隔 ──
    gap_steps: int = 0
    """两次出现之间的 step 数（全局序号差 - 1）。"""

    # ── 干预动作 ──
    intervening_actions: list[InterveningAction] = field(default_factory=list)
    """两次调用之间发生的、可能改变 agent 状态的动作列表。"""

    # ── 工具结果变化 ──
    tool_result_changed: bool | None = None
    """两次调用的工具结果是否发生了变化。
    True = 结果不同（变化了），False = 结果相同（未变化），None = 无法判断（结果截断/不可比）。
    """


@dataclass
class InterveningAction:
    """两次相同调用之间的一个干预动作。"""

    turn_id: int = 0
    step_id: int = 0
    tool_name: str = ""
    description: str = ""
    is_write: bool = False
    """是否为写入/状态变更操作（send_message / write / create / delete / kill 等）。"""
```

> **评审 G 修改**：原独立的 `SemanticVerdict` dataclass 已**移除**——verdict 字段直接并入 `SemanticCandidate`（见 D0），减少一个类 + 简化 merge 逻辑。`merge_semantic_verdicts` 直接按 fingerprint/turn_id/step_id 原地填充 candidate 的 verdict 字段。

**设计理由**：
- 数据结构与 `ABResult` 对齐风格（全部字段带默认值，空 trace → 全零/空块，不虚构数值）
- `JudgmentContext` 独立 dataclass，便于序列化为 JSON 供 agent 消费
- `InterveningAction` 提供结构化干预动作列表，agent 无需遍历原始 trace
- `tool_result_changed` 是关键的确定性信号：结果相同 → 更可能是冗余；结果不同 → 更可能是合法轮询
- `SemanticVerdict` 独立于 `SemanticCandidate`，支持回填后合并，不修改原始候选

---

### D1: 上下文构造算法 — `build_judgment_context`

**核心设计**：纯函数、确定性。给定 trace、前一次出现位置、本次出现位置，构造 `JudgmentContext`。

**算法**：

```
build_judgment_context(trace, prev_loc, curr_loc) → JudgmentContext:

1. 构建全局 step 序号映射（复用 ab_validation 的 _build_step_order 模式）
   step_order: (turn_id, step_id) → global_index

2. 定位前一次出现 step 和本次出现 step
   prev_step = trace.get_step(prev_loc)
   curr_step = trace.get_step(curr_loc)

3. 提取结果摘要（截断 500 字符）
   prev_result = truncate(prev_step.tool_calls[matching].result, 500)
   curr_result = truncate(curr_step.tool_calls[matching].result, 500)

4. 判断结果是否变化（评审 B 修复：用前缀比较,避免"任一截断→None"导致信号大面积失效）
   - 若 prev_result 或 curr_result 为 None → tool_result_changed = None（无法比较）
   - 若两者长度都 <500 → 精确比较字符串: tool_result_changed = (prev_result != curr_result)
   - 若两者前 500 字符不同 → tool_result_changed = True（前缀已不同,确定性结论）
   - 否则（前缀相同但可能截断点之后不同）→ tool_result_changed = None（不确定）

5. 收集干预动作（step_order[prev] 到 step_order[curr] 之间的所有 step）
   for each intervening step:
       检查每个 tool_call:
         - 若 tool_name 在 WRITE_ACTIONS 集合中 → is_write = True
         - 否则 is_write = False
         - 生成 InterveningAction(turn_id, step_id, tool_name, description, is_write)

6. 计算 gap_steps = step_order[curr] - step_order[prev] - 1

7. 返回 JudgmentContext(...)
```

**`WRITE_ACTIONS` 集合**（确定性硬编码，用于判断是否为"会改变 agent 状态的动作"）：

```python
WRITE_ACTIONS: frozenset[str] = frozenset({
    "send_message",          # 发送消息到子 agent
    "send_session_message",  # 跨会话发送消息
    "write",                 # 写入文件
    "edit",                  # 编辑文件
    "create_goal",           # 创建目标
    "update_goal",           # 更新目标
    "memory_save",           # 保存记忆
    "memory_update",         # 更新记忆
    "memory_delete",         # 删除记忆
    "memory_forget",         # 遗忘记忆
    "job_kill",              # 终止 job
    "interrupt_agent",       # 中断 agent
    "session_recover",       # 恢复会话
    "subagent",              # 启动子代理
    "subagent_fork",         # fork 子代理
    "todo_write",            # 评审 B 修复:修改 todo 列表(状态变更)
    "ask_user_question",     # 评审 B 修复:向用户提问(交互状态变更)
    # MCP 插件写入工具(按 DSH 命名空间 pattern:mcp__*__send_*/create_*/delete_*/update_*)
    # 无法穷举,保留注释供扩展
})
```

**设计理由**：
- 纯确定性：不依赖任何 LLM 或外部调用，同一 trace 两次运行结果一致
- 结果变化检测是 agent 判定"真冗余/合法"的核心信号：结果相同 → 更可能是冗余；结果不同 → 更可能是合法轮询
- 干预动作列表直接告诉 agent"两次调用之间发生了什么"，agent 无需遍历原始 trace
- 截断处理：结果超过 500 字符时采用前缀比较（评审 B 修复），非简单置 None；仅前缀相同且可能截断点后不同才 None

**为什么规则层不能替代 LLM（评审 C，C1 增量价值边界）**：
- **per-occurrence 粒度**：B1 是 per-finding（一组重复整体判定），C1 是 per-occurrence——同一 finding 内第 2 次与第 5 次重复可能有不同合法性。这是真实增量。
- **结构化上下文**：intervening_actions（含 is_write）+ tool_result_changed 是 B1 没有的（B1 只看 gap 大小）。
- **需 LLM 的场景**（规则层不足）：tool_result_changed=None（截断且前缀相同）；intervening_actions 含 reads 但可能间接改变状态；混合信号（有 write 但结果没变 → 可能幂等写入）；低置信度边缘。
- **规则层可覆盖的部分**（无需 LLM）：无前后写 + 结果未变 → 冗余。这部分可以确定性判定，但 C1 的 per-occurrence + 上下文仍比 B1 的"一刀切 debated 不计入"更细。

---

### D2: 候选清单生成算法 — `build_semantic_candidates`

**核心设计**：纯函数、确定性。从 trace 和 TOOL-001/TOOL-004 finding 中生成 `SemanticCandidate` 列表。优先标出 debated 候选。

**算法**：

```
build_semantic_candidates(trace, findings) → list[SemanticCandidate]:

1. 过滤 findings：仅保留 rule_id ∈ {"TOOL-001", "TOOL-004"} 的 finding

2. 构建全局 step 序号映射
   step_order: (turn_id, step_id) → global_index

3. 构建所有 step 的快速查找表
   steps_by_key: (turn_id, step_id) → Step

4. 对每个 TOOL-001 finding：
   a. 提取 occurrence_indexes（(turn_id, step_id) 列表）
   b. 提取 tool_name / fingerprint
   c. 判定 is_debated = (tool_name in SEMANTIC_DEBATED_TOOLS)
   d. 对第 2..N 个 occurrence（索引 1..N-1）：
      - 创建 SemanticCandidate
      - 定位前一次 occurrence（索引 i-1）
      - 调用 build_judgment_context(trace, prev_loc, curr_loc)
      - 填充 context

5. 对每个 TOOL-004 finding：
   a. 提取 tool_name / fingerprint / error_pattern
   b. 判定 is_debated = False（参数错误通常不是轮询语义问题）
   c. 对每个失败 attempt：
      - 创建 SemanticCandidate（rule_id="TOOL-004"）
      - 若有成功重试：context 含成功调用的结果对比
      - 若无成功重试：context 仅含失败信息

6. 排序（优先输出 debated）：
   - 第一排序键：is_debated（True 在前）
   - 第二排序键：total_occurrences（DESC，高倍率在前）
   - 第三排序键：(turn_id, step_id)（稳定排序）

7. 返回候选列表
```

**设计理由**：
- 复用 B1 的 `SEMANTIC_DEBATED_TOOLS` 集合（单一真相源）
- 复用 B1 的 fingerprint 分组与全局序号映射逻辑
- 排序确保 agent 最先看到最需要语义判断的候选（debated + 高倍率）
- TOOL-004 候选也包含在内：agent 可以判断"参数错误是否可避免"
- 纯函数确定性：同一 trace + 同一 findings → 同一候选列表

---

### D3: verdict 回填合并 — `merge_semantic_verdicts`

**核心设计**：agent 回填的 verdict JSON 文件被读入，与候选清单合并，生成带 verdict 的候选列表。`source="semantic"` 标注。

**回填 JSON 格式**（agent 产出的文件）：

```json
{
  "session_id": "session-abc123",
  "generated_by": "agent (deepseek-v4-pro)",
  "generated_at": "2026-08-22T12:00:00Z",
  "verdicts": [
    {
      "rule_id": "TOOL-001",
      "fingerprint": "abc123def456...",
      "turn_id": 3,
      "step_id": 2,
      "verdict": "true_redundant",
      "confidence": 0.85,
      "reason": "两次 list_sessions 调用之间无任何写入操作，且结果完全一致，确认是冗余调用"
    },
    {
      "rule_id": "TOOL-001",
      "fingerprint": "def789ghi012...",
      "turn_id": 5,
      "step_id": 1,
      "verdict": "legitimate",
      "confidence": 0.90,
      "reason": "两次 job_output 调用之间发生了 job_kill 操作，job 状态已改变，需重新读取"
    },
    {
      "rule_id": "TOOL-001",
      "fingerprint": "ghi345jkl678...",
      "turn_id": 7,
      "step_id": 3,
      "verdict": "uncertain",
      "confidence": 0.50,
      "reason": "两次 memory_list 调用之间无写入，但结果被截断无法比较；无法确定是否冗余"
    }
  ]
}
```

**合并算法**：

```
merge_semantic_verdicts(candidates, verdicts_path) → list[SemanticCandidate]:

1. 读取 verdicts JSON 文件
2. 构建索引：key = (rule_id, fingerprint, turn_id, step_id) → verdict
3. 对每个 candidate：
   a. 查找匹配的 verdict
   b. 若找到 → 在 candidate 上设置 verdict 字段（或创建独立的 verdict 属性）
   c. 若未找到 → verdict 保持 "not_applicable"
4. 返回更新后的候选列表
```

**合并数据结构**（扩展 `DiagnosisResult`）：

```python
@dataclass
class DiagnosisResult:
    # ... 现有字段 ...
    semantic_candidates: list[SemanticCandidate] | None = None
    """C1 语义判断候选清单；enable_analysis=True 且存在 TOOL-001/TOOL-004 finding 时生成。"""
```

**设计理由**：
- 回填 JSON 由 agent 产出，AgentTrace 只负责读取和合并，不内置 LLM 调用
- 匹配 key 为复合键 `(rule_id, fingerprint, turn_id, step_id)`，精确匹配不歧义
- 未回填的候选保持 `verdict="not_applicable"`，不猜测
- `source="semantic"` 标注确保报告能区分规则层和语义层的判定

#### D3 补充：verdict 语义下钻与 B1 展示关系（评审 D/E 建议）

- **verdict 不改变硬可省数字**（causal_claim=NONE）：verdict 是语义建议，B1 的硬可省/`semantic_debated_occurrences` 不因 agent 判定而改变。这避免确定性数字依赖 LLM 概率判断。
- **但为 B1 的 debated 提供语义下钻**：在报告 ABResult 块中，`semantic_debated_occurrences` 旁增加子项：
  ```
  - 语义存疑(debated): 15
    - 其中 agent 判为真冗余: 8 (未计入硬可省,供人工审查)
    - 其中 agent 判为合法: 5
    - 其中 agent 判定不确定: 2
  ```
  这样 verdict 虽不改数字,但为 debated 统计提供下钻信息,增强可操作性。展示关系(汇总→下钻)而非矛盾。
- **语义张力说明**:agent 判"真冗余"但 B1 仍不计入硬可省——这是设计哲学一致性(确定性数字仅基于 trace 确定性证据,LLM 判定是概率性外部输入)。报告明确标注,避免"agent 说真冗余却不省"的认知 dissonance。

### D4: pipeline / report / CLI 集成

#### D4.1: Pipeline 集成（Stage 3）

在 `diagnose()` 的 Stage 3（`enable_analysis=True` 时）追加 C1 候选清单生成：

```python
# pipeline.py, Stage 3 (enable_analysis=True 时)
if enable_analysis:
    # ... 现有分析层调用 ...
    from .analysis.c1_semantic import build_semantic_candidates
    result.semantic_candidates = build_semantic_candidates(trace, result.findings)
```

**门控**：
- `enable_analysis=False`（默认）→ `semantic_candidates` 保持 `None`，不生成，不输出
- `enable_analysis=True` → 自动生成候选清单（若存在 TOOL-001/TOOL-004 finding）
- 不注册进 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`

#### D4.2: CLI 集成

新增 `--semantic` 标志和 `--semantic-verdicts` 选项：

```python
# analyze / diagnose 子命令新增参数
p_analyze.add_argument(
    "--semantic",
    action="store_true",
    help="输出语义判断候选清单 JSON（供 agent 消费）；自动启用 --analysis",
)
p_analyze.add_argument(
    "--semantic-verdicts",
    default=None,
    help="agent 回填的 verdict JSON 文件路径（合并进报告显示）",
)
# 评审 G 修改：移除 --semantic-out（候选 JSON 输出到 stdout，用 shell 重定向 > 即可，减少 CLI 表面积）。
```

**`--semantic` 行为**：
1. 自动启用 `--analysis`（与 `--ab` 类似）
2. 运行 pipeline 生成候选清单
3. 将候选清单序列化为 JSON 输出到 stdout（供 agent 消费/重定向）
4. 若同时指定 `--semantic-verdicts`，则合并回填并渲染报告

**候选清单 JSON 输出格式**（供 agent 消费，含 `instructions`——评审 A 阻塞修复，告知 agent 任务/输出格式/判定准则）：

```json
{
  "session_id": "session-abc123",
  "model": "deepseek-v4-pro",
  "generated_at": "2026-08-22T12:00:00Z",
  "total_candidates": 15,
  "debated_count": 8,
  "deterministic_count": 7,
  "instructions": {
    "task": "You are reviewing AgentTrace's duplicate tool-call / invalid-param-retry candidates. For each candidate, judge whether the repeated call was truly redundant (true_redundant) or a legitimate polling/state-check (legitimate), or uncertain.",
    "output_format": "Write your verdicts to a JSON file with schema: { \"verdicts\": [{ \"rule_id\": ..., \"fingerprint\": ..., \"turn_id\": ..., \"step_id\": ..., \"verdict\": \"true_redundant\"|\"legitimate\"|\"uncertain\", \"confidence\": 0.0-1.0, \"reason\": \"...\" }] }. Write to the --semantic-verdicts path.",
    "criteria": [
      "If tool_result_changed=false and no intervening writes → likely true_redundant",
      "If tool_result_changed=true or intervening writes exist → likely legitimate",
      "If tool_result_changed=null (truncated/indeterminate) → use other signals; mark uncertain if ambiguous",
      "verdict is a semantic suggestion, NOT a hard assertion; causal_claim=NONE; do not change any savings numbers"
    ],
    "example_verdict": { "verdict": "true_redundant", "confidence": 0.85, "reason": "两次 list_sessions 调用间无写入且结果相同，确为冗余" }
  },
  "candidates": [
    {
      "rule_id": "TOOL-001",
      "turn_id": 3,
      "step_id": 2,
      "fingerprint": "abc123...",
      "tool_name": "list_sessions",
      "arguments": "{}",
      "is_debated": true,
      "occurrence_index": 2,
      "total_occurrences": 5,
      "context": {
        "previous_turn_id": 3,
        "previous_step_id": 1,
        "previous_global_step": 12,
        "previous_result_snippet": "[{\"id\": \"s1\", ...}]",
        "current_turn_id": 3,
        "current_step_id": 2,
        "current_global_step": 14,
        "current_result_snippet": "[{\"id\": \"s1\", ...}]",
        "gap_steps": 1,
        "tool_result_changed": false,
        "intervening_actions": [
          {
            "turn_id": 3,
            "step_id": 1,
            "tool_name": "send_session_message",
            "description": "发送消息到子会话",
            "is_write": true
          }
        ]
      }
    }
  ]
}
```

#### D4.3: Report 集成

报告新增「语义判断(C1)」块，按现有分析层块渲染模式追加：

```python
def _render_semantic_block(candidates, verdicts_map) -> list[str]:
    """渲染语义判断(C1)块（分析层观测）。

    纯函数、确定性。语义边界：
    - causal_claim=NONE；
    - verdict 标注"语义建议，非硬断言"；
    - 未回填候选标注"待 agent 语义判断"；
    - 不出现 "wasted" / 因果断言。
    """
    lines = ["", "### 语义判断(C1)"]
    lines.append("")
    lines.append(
        f"> **causal_claim=NONE**：以下判定是 agent 的 LLM 语义建议，非硬断言。"
    )
    lines.append("")

    if not candidates:
        lines.append("无可语义判断的候选重复。")
        return lines

    debated = [c for c in candidates if c.is_debated]
    deterministic = [c for c in candidates if not c.is_debated]

    lines.append(f"- 候选总数：{len(candidates)}")
    lines.append(f"  - 轮询型(debated)：{len(debated)}")
    lines.append(f"  - 确定性重复：{len(deterministic)}")

    # 回填统计
    backfilled = sum(1 for c in candidates if _get_verdict(c, verdicts_map) != "not_applicable")
    lines.append(f"- 已回填：{backfilled}/{len(candidates)}")
    lines.append("")

    # 优先展示 debated 候选
    if debated:
        lines.append("#### 轮询型候选（优先审视）")
        lines.append("")
        for c in debated:
            lines.extend(_render_single_candidate(c, verdicts_map))
        lines.append("")

    if deterministic:
        lines.append("#### 确定性重复候选")
        lines.append("")
        for c in deterministic:
            lines.extend(_render_single_candidate(c, verdicts_map))
        lines.append("")

    return lines


def _render_single_candidate(c, verdicts_map) -> list[str]:
    """渲染单个候选。"""
    v = verdicts_map.get((c.rule_id, c.fingerprint, c.turn_id, c.step_id))
    lines = []
    lines.append(f"- **{c.tool_name}** (turn {c.turn_id}, step {c.step_id})")
    lines.append(f"  - fingerprint: `{c.fingerprint[:16]}...`")
    lines.append(f"  - 出现: {c.occurrence_index}/{c.total_occurrences}")
    if c.context:
        lines.append(f"  - 间隔: {c.context.gap_steps} step(s)")
        if c.context.intervening_actions:
            writes = [a for a in c.context.intervening_actions if a.is_write]
            if writes:
                lines.append(f"  - ⚠ 干预动作({len(writes)} 个写入): {', '.join(a.tool_name for a in writes)}")
            else:
                lines.append(f"  - 干预动作: 仅读取操作（无写入）")
        if c.context.tool_result_changed is True:
            lines.append(f"  - 工具结果: **已变化**（可能是合法轮询）")
        elif c.context.tool_result_changed is False:
            lines.append(f"  - 工具结果: 未变化（更可能是冗余）")
        else:
            lines.append(f"  - 工具结果: 无法判断（结果截断）")
    if v and v != "not_applicable":
        verdict_label = {"true_redundant": "🔴 真冗余", "legitimate": "🟢 合法", "uncertain": "🟡 不确定"}.get(v, v)
        lines.append(f"  - **语义判定**: {verdict_label} [source=semantic, 语义建议，非硬断言]")
        lines.append(f"  - 置信度: {_get_confidence(c, verdicts_map):.2f}")
        lines.append(f"  - 理由: {_get_reason(c, verdicts_map)}")
    else:
        lines.append(f"  - **语义判定**: 待 agent 语义判断")
    return lines
```

**与 `render_report` 的集成**：

在 `render_report` 中，`enable_analysis=True` 且 `semantic_candidates` 非空时，在 Summary 块之后、kind 分组之前渲染 C1 块：

```python
# render_report 中，在 Summary 块之后：
if enable_analysis:
    lines.extend(_render_profile_block(profile))
    if ab_result is not None:
        lines.extend(_render_ab_validation_block(ab_result))
    # C1: 语义判断块（在 A/B 验证和上下文健康度之间）
    if semantic_candidates is not None:
        lines.extend(_render_semantic_block(semantic_candidates, semantic_verdicts_map))
    lines.extend(_render_context_health_block(context_health))
    lines.extend(_render_token_invariant_block(token_invariant))
    if session_lineage is not None:
        lines.extend(_render_session_lineage_block(session_lineage))
```

**`render_report` 签名扩展**：

```python
def render_report(
    trace: Trace,
    findings: list[Finding],
    attributions,
    enable_analysis: bool = False,
    profile=None,
    context_health=None,
    token_invariant=None,
    session_lineage=None,
    ab_result=None,
    semantic_candidates: list | None = None,   # C1 新增
    semantic_verdicts_map: dict | None = None,  # C1 新增
) -> str:
```

---

### D5: 文件组织

新增文件：

```
agenttrace/analysis/c1_semantic.py    # SemanticCandidate / JudgmentContext / InterveningAction + 核心函数(verdict 并入 SemanticCandidate)
                                       # + build_semantic_candidates / build_judgment_context / merge_semantic_verdicts
tests/test_c1_semantic.py             # C1 测试
```

修改文件：

```
agenttrace/pipeline.py                # DiagnosisResult 新增 semantic_candidates 字段
                                       # Stage 3 追加 build_semantic_candidates 调用
agenttrace/report.py                  # render_report 新增 semantic_candidates / semantic_verdicts_map 参数
                                       # 新增 _render_semantic_block / _render_single_candidate
agenttrace/cli.py                     # 新增 --semantic / --semantic-verdicts / --semantic-out 参数
                                       # cmd_analyze 新增候选清单 JSON 输出逻辑 + 回填合并
```

---

## Schema & API

### `build_semantic_candidates`

```python
def build_semantic_candidates(
    trace: Trace,
    findings: list[Finding],
) -> list[SemanticCandidate]:
    """从 TOOL-001/TOOL-004 finding 生成语义判断候选清单（纯函数，确定性）。

    Args:
        trace: 规范化 trace
        findings: pipeline Stage 1 产出的 finding 列表

    Returns:
        SemanticCandidate 列表，按 debated 优先 + 高倍率降序排列。
        无 TOOL-001/TOOL-004 finding 时返回空列表。
    """
```

### `build_judgment_context`

```python
def build_judgment_context(
    trace: Trace,
    prev_turn_id: int,
    prev_step_id: int,
    curr_turn_id: int,
    curr_step_id: int,
    fingerprint: str,
) -> JudgmentContext:
    """为一次重复调用构造判断上下文（纯函数，确定性）。

    Args:
        trace: 规范化 trace
        prev_turn_id / prev_step_id: 前一次出现位置
        curr_turn_id / curr_step_id: 本次出现位置
        fingerprint: 调用的 fingerprint（用于在同 step 内定位具体 tool_call）

    Returns:
        JudgmentContext；构造失败时返回全默认值（保守：不虚构上下文）。
    """
```

### `merge_semantic_verdicts`

```python
def merge_semantic_verdicts(
    candidates: list[SemanticCandidate],
    verdicts_path: str | Path,
) -> tuple[list[SemanticCandidate], dict]:
    """合并 agent 回填的 verdict 到候选清单（纯函数）。

    Args:
        candidates: build_semantic_candidates 的产出
        verdicts_path: agent 回填的 verdict JSON 文件路径

    Returns:
        (更新后的候选列表, verdicts_map)：
        - 候选列表中的 verdict 字段被填充（若有匹配）
        - verdicts_map 为 {(rule_id, fingerprint, turn_id, step_id): verdict_str} 供报告渲染
    """
```

### `serialize_candidates_to_json`

```python
def serialize_candidates_to_json(
    candidates: list[SemanticCandidate],
    session_id: str,
    model: str,
) -> str:
    """将候选清单序列化为 JSON 字符串（供 agent 消费）。

    Returns:
        格式化的 JSON 字符串，含 session_id / model / generated_at / total_candidates /
        debated_count / deterministic_count / candidates 数组。
    """
```

---

## Testing

### 测试文件：`tests/test_c1_semantic.py`

| # | 用例 | 覆盖 | 类别 |
|---|------|------|------|
| T1 | `test_semantic_candidate_defaults` | `SemanticCandidate` 默认值全零/空字符串，`is_debated=False`，`context=None` | 数据结构 |
| T2 | `test_judgment_context_defaults` | `JudgmentContext` 默认值全零/空，`tool_result_changed=None`，`intervening_actions=[]` | 数据结构 |
| T3 | `test_semantic_verdict_defaults` | `SemanticCandidate` 的 verdict 字段默认 `verdict="not_applicable"`，`confidence=0.0`，`reason=""`，`source="semantic"`，`causal_claim="NONE"`（合并后） | 数据结构 |
| T4 | `test_build_candidates_empty_trace` | 空 trace → 空候选列表 | 候选生成 |
| T5 | `test_build_candidates_no_findings` | 有 trace 但无 TOOL-001/TOOL-004 finding → 空列表 | 候选生成 |
| T6 | `test_build_candidates_single_duplicate` | 2 次 read_file → 1 个候选（第 2 次），`is_debated=False` | 候选生成 |
| T7 | `test_build_candidates_debated_tool` | 2 次 list_sessions → 1 个候选，`is_debated=True` | 候选生成 |
| T8 | `test_build_candidates_multiple_fingerprints` | 多组不同 fingerprint → 每个冗余 occurrence 一个候选 | 候选生成 |
| T9 | `test_build_candidates_high_occurrence_count` | N=5 的重复组 → 4 个候选（第 2-5 次），`total_occurrences=5` | 候选生成 |
| T10 | `test_build_candidates_tool004` | TOOL-004 失败 attempt → 候选，`rule_id="TOOL-004"`，含错误信息 | 候选生成 |
| T11 | `test_candidates_sorted_debated_first` | 混合 debated + 确定性 → debated 候选排在前面 | 排序 |
| T12 | `test_candidates_sorted_by_occurrence_count` | debated 内按 `total_occurrences` 降序 | 排序 |
| T13 | `test_judgment_context_gap_steps` | 两次调用间隔 3 step → `gap_steps=3` | 上下文构造 |
| T14 | `test_judgment_context_intervening_write` | 间隔中有 send_message → `intervening_actions` 含 `is_write=True` 的条目 | 上下文构造 |
| T15 | `test_judgment_context_no_intervening` | 相邻 step 无间隔 → `gap_steps=0`，`intervening_actions=[]` | 上下文构造 |
| T16 | `test_judgment_context_result_changed` | 两次调用结果不同 → `tool_result_changed=True` | 上下文构造 |
| T17 | `test_judgment_context_result_unchanged` | 两次调用结果相同 → `tool_result_changed=False` | 上下文构造 |
| T18 | `test_judgment_context_result_truncated` | 结果超过 500 字符被截断 → `tool_result_changed=None` | 上下文构造 |
| T19 | `test_judgment_context_result_snippet_length` | `result_snippet` 不超过 500 字符 | 上下文构造 |
| T20 | `test_merge_verdicts_backfill` | 回填 verdict JSON → `merge_semantic_verdicts` 正确填充 verdict | 回填合并 |
| T21 | `test_merge_verdicts_partial_backfill` | 仅回填部分候选 → 其余保持 `not_applicable` | 回填合并 |
| T22 | `test_merge_verdicts_no_match` | 回填 JSON 的 fingerprint 不匹配 → 所有候选保持 `not_applicable` | 回填合并 |
| T23 | `test_verdict_source_semantic` | 回填的 verdict 标注 `source="semantic"` | 回填合并 |
| T24 | `test_verdict_causal_claim_none` | 回填的 verdict 标注 `causal_claim="NONE"` | 回填合并 |
| T25 | `test_not_backfilled_is_not_applicable` | 未回填的候选 `verdict="not_applicable"`，`confidence=0.0` | 未回填保守 |
| T26 | `test_deterministic_same_trace_twice` | 同一 trace 两次生成候选清单 → 逐字段一致 | 确定性 |
| T27 | `test_deterministic_context_same_twice` | 同一候选两次构造上下文 → 逐字段一致 | 确定性 |
| T28 | `test_additive_enable_analysis_false` | `enable_analysis=False` → `semantic_candidates=None` | 金钟罩 |
| T29 | `test_additive_detectors_unchanged` | 开启语义层 → `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES` 数量不变 | 金钟罩 |
| T30 | `test_additive_findings_unchanged` | 开启语义层 → 现有 finding 不变 | 金钟罩 |
| T31 | `test_golden_report_byte_identical` | `enable_analysis=False` → 报告与 v0.6 逐字节一致 | 金钟罩 |
| T32 | `test_report_renders_semantic_block` | 开启分析层且存在候选 → 报告含「语义判断(C1)」块 | 报告渲染 |
| T33 | `test_report_no_semantic_block_when_disabled` | 关闭分析层 → 报告不含「语义判断(C1)」块 | 报告渲染 |
| T34 | `test_report_semantic_block_no_wasted` | 语义块不含 "wasted" / 因果断言 | 报告渲染 |
| T35 | `test_report_not_backfilled_shows_pending` | 未回填候选显示"待 agent 语义判断" | 报告渲染 |
| T36 | `test_report_backfilled_shows_verdict` | 已回填候选显示 verdict + confidence + reason | 报告渲染 |
| T37 | `test_serialize_candidates_json_valid` | `serialize_candidates_to_json` 产出合法 JSON | CLI 输出 |
| T38 | `test_serialize_candidates_json_structure` | JSON 含 session_id / model / generated_at / total_candidates / debated_count / deterministic_count / candidates | CLI 输出 |
| T39 | `test_cli_semantic_flag_enables_analysis` | `--semantic` 自动启用 `--analysis` | CLI 门控 |
| T40 | `test_cli_semantic_out_writes_json` | `--semantic --semantic-out <path>` 写出候选 JSON | CLI 输出 |
| T41 | `test_empty_candidates_serializes_empty_array` | 无候选 → JSON 的 candidates 数组为空 | 边界 |
| T42 | `test_judgment_context_handles_missing_previous_step` | 前一次 occurrence 无法定位 → context 为全默认值（不抛异常） | 鲁棒性 |
| T43 | `test_tool_result_changed_boundary_500`（评审 F） | 结果 499/500/501 字符的截断判断（前缀比较逻辑边界） | 上下文构造 |
| T44 | `test_write_actions_coverage`（评审 F） | 验证已知写入工具 `todo_write`/`ask_user_question` 被标 is_write=True，已知只读工具标 is_write=False | 上下文构造 |
| T45 | `test_merge_verdicts_malformed_json`（评审 F） | malformed verdict JSON → 明确错误处理（不崩溃、有空合并） | 回填合并 |
| T46 | `test_report_context_none_renders`（评审 F） | context=None 的候选正常渲染不崩溃 | 报告渲染 |
| T47 | `test_tool004_with_successful_retry`（评审 F） | TOOL-004 失败 attempt + 成功重试的上下文对比（含成功调用结果） | 候选生成 |
| T48 | `test_same_step_multiple_tool_calls_fingerprint_match`（评审 F） | 同一步多 tool_call 时 fingerprint 定位正确 | 上下文构造 |

---

## Open Questions

### OQ1: agent 如何回填 verdict？（文件 vs 记录）

**当前设计**：agent 将 verdicts 写入 JSON 文件，AgentTrace 通过 `--semantic-verdicts <path>` 读取。

**未定点**：是否有更好的回填方式？例如：
- agent 直接编辑候选 JSON 文件原地追加 verdict 字段
- agent 通过 stdin/stdout 管道与 AgentTrace 交互

**倾向**：JSON 文件方案（当前设计）——最简单、最可调试、agent 无需了解 AgentTrace 内部格式。实现时验证 agent 工作流是否顺畅。

### OQ2: verdict 数据结构的三方一致性

**当前设计**：`SemanticCandidate.verdict` 字段（AgentTrace 侧，评审 G 合并后）、回填 JSON（agent 产出的 verdicts 数组）、报告渲染（用户看到）——三方需要保持一致。

**未定点**：verdict 枚举值（`true_redundant` / `legitimate` / `uncertain`）的命名是否足够表达 agent 的判定语义？是否需要更多分级（如 `likely_redundant` / `likely_legitimate`）？

**倾向**：保持三档（`true_redundant` / `legitimate` / `uncertain`），配合 `confidence` 连续值（0.0–1.0）提供细粒度。`uncertain` + 低 confidence 足以表达不确定。实现时验证 agent 实际使用是否够用。

### OQ3: 是否复用 B1 的 `SEMANTIC_DEBATED_TOOLS`？

**当前设计**：C1 直接从 `ab_validation.py` 导入 `SEMANTIC_DEBATED_TOOLS`。

**未定点**：C1 是否需要独立的 debated 工具集合？B1 的集合是基于"轮询/状态读取"语义，C1 对 debated 的定义是否完全一致？

**倾向**：复用 B1 的集合（单一真相源）。C1 和 B1 对 debated 的定义一致——都是"规则层无法区分冗余/合法的轮询型工具"。如果未来需要独立维护，可以在 C1 模块中定义别名并注明来源。

### OQ4: 上下文构造的 `WRITE_ACTIONS` 集合是否完备？

**当前设计**：硬编码 17 个写入/状态变更工具名（含评审 B 补充的 `todo_write`/`ask_user_question`）。

**未定点**：是否有遗漏的写入工具？随着 DSH harness 工具集扩展，是否需要动态识别？

**倾向**：硬编码是当前最安全的选择——确定性、可审查、不会误判。如果 DSH 工具集扩展，通过更新此集合适配。实现时加注释标注"与 DSH 工具集同步维护"。

### OQ5: 候选清单 JSON 是否应该包含完整的原始结果（而非截断摘要）？

**当前设计**：`result_snippet` 截断为 500 字符。

**未定点**：agent 的 LLM 可能需要完整结果才能准确判断。但完整结果可能非常大（如 `browser_get_state` 的完整 DOM snapshot）。

**倾向**：保持 500 字符截断。agent 如果需要完整结果，可以通过 `read_session` 自行获取。候选清单 JSON 提供定位信息（turn_id/step_id），agent 可以精确定位。实现时验证 500 字符阈值是否合理。

### OQ6: verdict 回填后是否应该更新 `CounterEvidence`？

**当前设计**：回填的 verdict 独立于 `CounterEvidence`，在报告 C1 块中渲染。

**未定点**：是否应该将回填的 verdict 作为 `CounterEvidence(source="semantic")` 追加到对应 finding 上？这样 B1 的 render 也能看到语义判定。

**倾向**：**不追加到 finding**。保持 C1 语义块独立渲染，不修改现有 finding 的 `counter_evidence` 列表。理由：
- finding 的 `counter_evidence` 是规则层确定性反证，追加语义层内容会改变其确定性语义
- 语义判定是"agent 的判断"而非"trace 证据"，混入 finding 反证不合适
- 独立渲染的 C1 块更清晰地区分"规则层反证"和"语义层判定"

### OQ7: 是否支持 `--semantic` 独立于 `--analysis`？

**当前设计**：`--semantic` 自动启用 `--analysis`（与 `--ab` 一致）。

**未定点**：是否需要独立门控——即只输出候选清单 JSON 而不渲染完整报告？

**倾向**：保持当前设计（`--semantic` 自动启用 `--analysis`）。候选清单 JSON 输出到 `--semantic-out` 时，报告仍然正常渲染。如果用户只需要候选清单而不需要报告，可以用 `--semantic-out` + 丢弃 stdout。实现时验证此工作流是否合理。

---

## 附录：与现有代码的集成点清单

| 集成点 | 文件 | 改动类型 | 说明 |
|--------|------|----------|------|
| 数据结构 | `agenttrace/analysis/c1_semantic.py` | **新增** | `SemanticCandidate` / `JudgmentContext` / `InterveningAction`(verdict 并入 SemanticCandidate) + 核心函数 |
| Pipeline | `agenttrace/pipeline.py` | 修改 | `DiagnosisResult` 新增 `semantic_candidates` 字段；Stage 3 追加 `build_semantic_candidates` 调用 |
| Report | `agenttrace/report.py` | 修改 | `render_report` 签名扩展；新增 `_render_semantic_block` / `_render_single_candidate` |
| CLI | `agenttrace/cli.py` | 修改 | 新增 `--semantic` / `--semantic-verdicts` / `--semantic-out` 参数；`cmd_analyze` 新增输出逻辑 |
| 测试 | `tests/test_c1_semantic.py` | **新增** | 48 个测试用例（T1–T48） |
| 复用 | `agenttrace/analysis/ab_validation.py` | 仅导入 | 导入 `SEMANTIC_DEBATED_TOOLS`（单一真相源） |
| 复用 | `agenttrace/core/normalize.py` | 仅导入 | 导入 `call_fingerprint` |

---

## 变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-22 | v1.0 | 初稿定稿（deepseek-v4-pro），基于 proposal.md + spec.md + B1 现状 + 项目铁律 |

---

## 评审融合记录（deepseek-v4-pro 异模型评审,2026-08-22）

> 评审作为**阻塞闸门**执行,结论见 `openspec/changes/c1-semantic-judgment/review-pro.md`。7 条硬约束红线全部守住(无硬红线违规);锁定 **2 项必改(A 候选 JSON 缺 agent 指令、B tool_result_changed 太保守 + WRITE_ACTIONS 遗漏)** + 若干建议,已全部融合进本设计。

| 评审项 | 结论 | 融合位置 |
|---|---|---|
| 红线 1–7 | 全部守住 | — |
| **A 交付闭环** | 🔴 **阻塞**:候选 JSON 缺 agent 指令,agent 不知道要做什么 | **候选 JSON 顶层加 `instructions` 字段**(task/output_format/criteria/example),D4.2 |
| **B tool_result_changed** | 🔴 **阻塞**:"任一截断→None"导致信号大面积失效 | D1 改**前缀比较逻辑**(前缀不同→True,只有前缀相同且可能截断点后不同才 None) |
| **B WRITE_ACTIONS** | 🟡 必改:遗漏 todo_write/ask_user_question | D1 补充 `todo_write`/`ask_user_question`(现 17 个) |
| C 判定信号有效性 | 🟢 增量价值真实(per-occurrence+结构化上下文) | D1 补"为什么规则层不能替代 LLM"论述 |
| D verdict 不改变硬可省 | 🟡 建议:增强可操作性 | D3 补语义下钻(debated 统计旁展示 agent 判定明细) |
| E 与 B1 关系 | 🟡 建议:明确展示关系 | D3 补"汇总→下钻"关系说明+语义张力标注 |
| F 测试完备性 | 🟡 补 6 用例 | 新增 T43–T48(截断边界/WRITE_ACTIONS 覆盖/malformed JSON/context=None 渲染/TOOL-004 成功重试/同步多 tool_call) |
| G 过度设计 | 🟡 合并 SemanticVerdict + 去掉 --semantic-out | **移除独立 SemanticVerdict**(verdict 并入 SemanticCandidate);**移除 `--semantic-out`**(候选 JSON 输出到 stdout,shell 重定向) |

**结论:设计已按评审 A–G 融合定稿,可进入实现。** 交实现会话 `session-d2c507cf`。