# Design — cross-session-lineage (A2)

> 版本：定稿（基于 evidence.md 106 会话实测 + proposal.md 记账口径）
> 设计模型：deepseek-v4-pro
> 状态：待评审（阻塞闸门）

---

## Context

### 背景

AgentTrace v0.5 的 SUB-001 只在**单会话内**做 subagent 委托的 flat 观测（报 mode/provider/label），无法看到"子会话自身产生了多少 token/工具/detector 信号"。真实数据（106 会话，evidence.md）证实：

- **57 条跨会话 parent→child 边可解析率 100%**（`header.parentSession` 权威来源）
- 存在 **3 层嵌套** subagent 链（`session-eae82666 → cb171f5e → 4031b5f2 → 541f1780`）
- 最大根父 `session-5cdccd44` 子树 **33 节点**，纯 subagent 子树放大 1.5×
- 必须区分 **SUBAGENT**（origin=subagent，43 条）与 **FORKED_SESSION**（origin≠subagent 但 parentSession 有值，14 条），否则根父被误放大至 9.1×

### 分析层定位

SessionLineage 是**分析层会话级数据块**，与 `ContextHealth` / `TokenInvariant` 同构：

- 不注册 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`
- 不进入 findings/attributions
- 仅 `enable_analysis=True` 时由 pipeline Stage 3 惰性构建
- 默认关闭 → 零影响

### 硬约束（不可违背）

1. **additive**：不改现有 detector/attribution 行为；默认输出逐字节不变
2. **非 Detector/Finding**：SessionLineage 不注册 ALL_DETECTORS/ALL_ATTRIBUTION_ENGINES
3. **causal_claim=NONE**：不判 harness bug，只报观测+风险；tokens=None ≠ 0
4. **禁混算 Total wasted tokens**：跨会话只做"规模/计数观测"，不传播可避免成本
5. **子会话 token 只归自己**：祖辈只"看子树规模"，禁止沿链再加总（否则 3 层链叶子被 ×3）
6. **权威父子来源 = header.parentSession 唯一**：agent-start.childId 只作佐证（按 runId 去重，以 header.parentSession 为准）；senderSessionId 不作 lineage 边
7. **区分两类子代**：SUBAGENT（origin=subagent）进主聚合；FORKED_SESSION（origin≠subagent 但 parentSession 有值）独立"会话复制"层，默认不混算
8. **fork 不做 token 互斥/去重**：数据不支持上下文继承
9. **不可解析 → 悬挂节点**：不猜不伪造；报告注明"本机可解析子图内成立"
10. **复杂系统只测局部封闭不变量**：设计权衡为 hedged 建议

---

## Goals & Non-Goals

### Goals

1. 新增 `SessionLineage` dataclass（分析层会话级数据块），与 ContextHealth/TokenInvariant 同构
2. 从 `discover_sessions()` 出发，沿 `header.parentSession` 构建 lineage 图
3. 递归聚合子会话的 token/工具/detector 信号回父会话，区分 SUBAGENT 与 FORKED_SESSION 两类子代
4. 标 lineage 形态（fork/spawn），从 `subagent/descriptor.provider.label` 提取
5. 集成到 pipeline Stage 3（enable_analysis=True 惰性构建）与 report 渲染
6. 全量回归 + 金钟罩测试（enable_analysis=False 逐字节不变）

### Non-Goals

- 不修改 SUB-001 或任何现有 detector
- 不做因果归因（causal_claim=NONE）
- 不产"Total wasted tokens"
- 不虚构 lineage（不可解析 → 悬挂，不猜不伪造）
- 不把 forked-session 后代 token 混入 subagent 聚合
- 不实现跨机/外部会话解析（仅标"不可解析"）

---

## Decisions

### D0: Adapter 最小 additive 扩展 —— 提取 session 头 lineage 字段

**现状**：`dsh_adapter.py` 的 `session` 事件处理器（L115–118）仅提取 `id` / `cwd` / `agentPreset`，**未提取** `parentSession` / `origin` / `delegationDepth`。

**扩展**（最小 additive，不改现有字段）：

```python
# 在 session 事件处理块中追加（仅新增 3 行，不删不改现有行）
if etype == "session":
    session_id = ev.get("id", "")
    trace.metadata = {
        "cwd": data.get("cwd"),
        "agentPreset": data.get("agentPreset"),
        # --- A2 新增（additive）---
        "parentSession": ev.get("parentSession"),       # str | None
        "origin": ev.get("origin"),                     # "subagent" | None
        "delegationDepth": ev.get("delegationDepth", 0), # int
    }
    trace.session_id = session_id
```

**依据**：evidence.md §4——`parentSession` / `origin` / `delegationDepth` 在 session 事件的**顶层**（非 `data` 内），106 会话全部可解析。

**佐证信号（本次移除——评审 E/evidence §4:parentSession 100% 可靠,agent-start 67% 伪边,无增量信息,不纳入）**:

- ~~`tool-workflow/agent-start` 佐证~~ —— **不采纳**。evidence §4 证明 `agent-start.childId` 有 28/42 复制伪边(forked-session 拷贝父历史),且 parentSession 已 100% 可靠。砍掉后 D1 简化,不需要 runId 去重、不需要佐证落盘字段,避免过度设计。
- `subagent/descriptor`:`data.provider.label` 仍用于给 SUBAGENT 边标形态(`"fork"`/`"spawn"`),来自已有 `trace.events[]`,不改 adapter。

**不改动**：`STANDALONE_EVENT_TYPES` 集合不变、事件追加逻辑不变。

---

### D1: 图构建 —— 权威序列

**唯一权威来源 = `header.parentSession`**（evidence.md §4 结论）。构建序列：

```
1. discover_sessions() → 所有会话的 session_id 列表
2. 对每个会话，读取 trace.metadata["parentSession"] / ["origin"] / ["delegationDepth"]
3. 建边：child → parent（parentSession 指向的会话）
4. 标子代类别（评审 B：收紧，防御未知 origin）：
   - SUBAGENT：origin == "subagent"
   - FORKED_SESSION：origin is None（或空串）且 parentSession 有值（非 None/空）
   - ROOT：parentSession 为 None/空
   - UNKNOWN_ORIGIN：origin 有值但既 ≠"subagent" 也不为空 → 保守按 FORKED_SESSION 处理 + 日志警告
5. 标 SUBAGENT 形态：从 trace.events[] 中取 type=="subagent/descriptor" 的事件，读 data.provider.label（"fork"|"spawn"）
6. senderSessionId 不作 lineage 边（evidence §4 结论：46/47 是消息路由）
```

**图深度**：从 parentSession 链**结构化推导**（从根到叶的最长路径），不用 `delegationDepth`（后者只在 subagent 上有意义，且 forked-session 默认 delegationDepth=0）。

**不可解析**：parentSession 指向的会话不在本地会话集合中 → 标 `unresolvable`，不参与聚合，不伪造父子关系。

---

### D2: SessionLineage dataclass 字段定义

```python
@dataclass
class SessionLineage:
    """跨会话 lineage 观测（分析层会话级数据块，非 Finding）。

    全部字段带默认值：空会话 / 单会话 / 无子代 → 全零/None，不虚构数值。
    None 语义 = not applicable（如无 subagent/descriptor 事件时 provider 为 None）。
    """

    # ── 自身指标（权威，可全局加总，不被任何祖先改写）──
    own_tokens: int = 0
    """该会话自身 token 合计（input+output，按 Step.usage.total_tokens()）。"""

    own_steps: int = 0
    """该会话自身 step 数。"""

    own_tools: int = 0
    """该会话自身工具调用次数。"""

    # ── 子代聚合（SUBAGENT 后代递归，聚合观测值，非成本）──
    lineage_descendant_tokens: int = 0
    """SUBAGENT 后代的递归 token 合计（仅 origin=subagent 后代）。
    聚合观测值，非成本；不参与 wasted/cost 归因。"""

    lineage_descendant_steps: int = 0
    """SUBAGENT 后代的递归 step 合计。"""

    lineage_descendant_tools: int = 0
    """SUBAGENT 后代的递归工具调用合计。"""

    # ── FORKED_SESSION 子代聚合（独立层，默认不混入上面）──
    lineage_fork_descendant_tokens: int = 0
    """FORKED_SESSION 后代的递归 token 合计（可选展示，默认不与 subagent 混算）。"""

    lineage_fork_descendant_steps: int = 0

    lineage_fork_descendant_tools: int = 0

    # ── 图拓扑 ──
    lineage_depth: int = 0
    """从本机根的图深度（结构化推导，沿 parentSession 链的最长路径）。"""

    parent_session_id: Optional[str] = None
    """该会话的父会话 id（来自 header.parentSession）；None = 根。"""

    parent_category: str = "none"
    """父边类别："subagent" | "forked_session" | "none"（无父）。"""

    # ── 子代计数 ──
    child_count: int = 0
    """直接子代总数（SUBAGENT + FORKED_SESSION）。"""

    subagent_child_count: int = 0
    """直接 SUBAGENT 子代数。"""

    fork_session_child_count: int = 0
    """直接 FORKED_SESSION 子代数。"""

    root_child_count: int = 0
    """子树中 ROOT 节点数（深度 0 节点，含自身若是根）。"""

    # ── 形态标记（仅 SUBAGENT 有意义）──
    provider: Optional[str] = None
    """subagent/descriptor.provider.label："fork" | "spawn" | None（非 subagent 或无 descriptor 事件）。"""

    mode: Optional[str] = None
    """subagent/descriptor.mode："one-shot" | "continuable" | None。"""

    # ── 边界标记 ──
    unresolvable_edges: int = 0
    """不可解析的 parent→child 边数（父指向本机不存在的会话）。"""

    is_resolvable_subgraph: bool = True
    """本机可解析子图内成立；False 表示存在不可解析边。"""

    # ── 确定性元数据 ──
    total_sessions_in_graph: int = 0
    """参与图构建的会话总数（传入 session_map 的大小），供报告注明覆盖范围。"""
```

**默认值语义**：

| 场景 | 返回值 |
|---|---|
| 空会话（无 step） | `SessionLineage()` 全零块，`parent_category="none"`，`is_resolvable_subgraph=True` |
| 单会话（无父无子） | `own_*` 有值，`lineage_descendant_*` 全零，`child_count=0` |
| 无 subagent/descriptor 事件 | `provider=None`，`mode=None`（not applicable，非 "unknown"） |
| 不可解析边存在 | `unresolvable_edges>0`，`is_resolvable_subgraph=False` |

---

### D3: build_session_lineage(...) 算法

**函数签名**：

```python
def build_session_lineage(
    session_id: str,
    session_map: dict[str, Trace],
    visited: set[str] | None = None,
) -> SessionLineage:
```

**参数**：

- `session_id`：当前会话的 id
- `session_map`：`{session_id: Trace}` 全量映射（由调用方从 `discover_sessions()` + `load_dsh_session()` 构建）
- `visited`：递归防环用（内部传参，调用方不传）

**算法（纯函数、确定性）**：

```
1. 从 session_map 取当前 trace；若不在 map 中 → 返回 SessionLineage(is_resolvable_subgraph=False)
2. 防环：若 session_id ∈ visited → 返回 SessionLineage()（已访问过，不重复计数）
   # visited 语义（评审 A）：树形防环安全网。在 parentSession 树（非 DAG）下等价于祖先链去重。
   # 若未来出现多父 DAG，需改用 DAG 拓扑序聚合。visited 须在顶层调用传 None（每次新 set），避免跨顶层污染。
3. visited.add(session_id)
4. 计算 own_*：
   - own_tokens = sum(s.usage.total_tokens() for s in trace.all_steps())
   - own_steps  = len(trace.all_steps())
   - own_tools  = len(trace.all_tool_calls())
5. 读取 metadata：
   - parentSession = trace.metadata.get("parentSession")
   - origin         = trace.metadata.get("origin")
   - delegationDepth = trace.metadata.get("delegationDepth", 0)
6. 查找直接子代（在 session_map 中搜索 metadata["parentSession"] == session_id 的会话）
7. 对每个直接子代，分类（评审 B：收紧，显式检查 origin is None）：
   - 若 child.metadata["origin"] == "subagent" → SUBAGENT
   - 若 child.metadata["origin"] is None（或空串）且 parentSession 有值 → FORKED_SESSION
   - 否则 → UNKNOWN_ORIGIN：保守按 FORKED_SESSION 处理 + 记 unresolvable/警告（防御未来未知 origin）
8. 对每个 SUBAGENT 子代，递归调用 build_session_lineage(child_id, session_map, visited)，
   累加：
   - lineage_descendant_tokens += child.own_tokens + child.lineage_descendant_tokens
   - lineage_descendant_steps  += child.own_steps  + child.lineage_descendant_steps
   - lineage_descendant_tools  += child.own_tools  + child.lineage_descendant_tools
9. 对每个 FORKED_SESSION 子代，同理递归累加到 lineage_fork_descendant_*
10. 从 trace.events[] 提取 subagent/descriptor → provider / mode
11. 计算 lineage_depth：从根沿 parentSession 链的最长路径（BFS/DFS）
12. 检测不可解析边：parentSession 非空但不在 session_map 中 → unresolvable_edges++
13. 计算 child_count / subagent_child_count / fork_session_child_count（直接子代计数）
    # root_child_count（评审 G3）：parentSession 是树、根唯一，故该字段恒为 1（当自己是根）或 0（非根）。
    # 语义冗余，为清晰保留：值 = 1 if parent_category == "none" else 0（即"子树里有没有根"）。
14. 返回 SessionLineage(...)
```

**防重复计数规则（核心）**：

```
每个节点自身报告各存一份子树和，禁止沿链再加总。

正确：
  session-5cdccd44.lineage_descendant_tokens
    = sum(child.own_tokens + child.lineage_descendant_tokens for child in subagent_children)
  → 每个后代的 own_tokens 在祖先的 lineage_descendant_tokens 中恰好出现 1 次

错误（会导致 3 层链叶子 ×3）：
  grandparent.lineage_descendant_tokens
    = parent.own_tokens + parent.lineage_descendant_tokens  ← 正确，parent.lineage_descendant_tokens 已含后代
  report: "total lineage cost = grandparent.lineage_descendant_tokens + parent.lineage_descendant_tokens"
    ← 错误！后代被重复计数

正确报告方式：
  - 只展示每个会话自身的 SessionLineage 字段
  - 不跨会话相加 lineage_descendant_tokens
```

**确定性保证**：

- 输入相同（session_id + session_map）→ 输出逐字段一致
- 子代遍历按 session_id 排序（确定性顺序）
- 无随机/采样/LLM 调用

---

### D4: Pipeline 集成

**DiagnosisResult 扩展**（additive，仅新增 1 字段）：

```python
@dataclass
class DiagnosisResult:
    # ... 现有字段不变 ...
    # A2 新增（分析层会话级数据块）
    session_lineage: Optional[object] = None  # SessionLineage 实例；enable_analysis=True 时构建
```

**Stage 3 惰性构建**（镜像 context_health / token_invariant）：

```python
# pipeline.py 的 diagnose() 中，Stage 3 块追加：
if enable_analysis:
    from .analysis.context_health import build_context_health
    from .analysis.counter_evidence import refine_findings
    from .analysis.profile import build_profile
    from .analysis.token_invariant import build_token_invariant
    from .analysis.session_lineage import build_session_lineage  # A2 新增

    refine_findings(result.findings, trace)
    result.context_health = build_context_health(trace)
    result.profile = build_profile(result.findings, result.attributions)
    result.token_invariant = build_token_invariant(trace)
    # A2：session_lineage 需要 session_map；调用方通过 diagnose() 的新参数传入。
    # session_map=None 时 session_lineage 保持 None（跨会话数据不可得）；否则构建（含全零块）。
    if session_map is not None:
        result.session_lineage = build_session_lineage(trace.session_id, session_map)
```

**diagnose() 签名扩展**（additive，向后兼容）：

```python
def diagnose(
    trace: Trace,
    detector_names: list[str] | None = None,
    enable_analysis: bool = False,
    session_map: dict[str, Trace] | None = None,  # A2 新增，默认 None
) -> DiagnosisResult:
```

- `session_map=None` 时：`session_lineage` 设为 `None`（单会话模式，无跨会话数据）
- `session_map` 传入时：构建 `SessionLineage`，即使该会话无父无子也返回全零块（不返回 None）

**session_map 构建责任与可用性（评审 C，方案 2）**：

- **CLI `cmd_analyze` 必须传 session_map**（评审 C 强建议方案 2：单会话局部 session_map，代价极低，让 A2 在默认 `analyze <session> --analysis` 路径下至少部分可观测）。具体：
  1. 调 `discover_sessions()` 获取全量 `session_id` 列表（不加载全部 Trace，仅拿到 id 集合）。
  2. 解析目标会话得到 `trace`，构建 `session_map = {trace.session_id: trace}`（单元素）。
  3. 传给 `diagnose(trace, session_map=session_map, enable_analysis=True)`。
  - 这样单会话也能得到 `own_*` 有值、`parent_session_id` 可能指向父、`lineage_*` 聚合（父会话若也在本机会话池中，可见其直接子代——若仅当前会话在 map 中，则 lineage_* 多为 0，但 `parent_category`/`parent_session_id`/`lineage_depth` 仍据 header 可观测）。
  - 注意：单元素 session_map 不会含子会话的 Trace，故 `lineage_descendant_*` 无法递归（子会话不在 map 中即视为"不可解析"）。这是"部分可观测"的诚实边界，report 会按 boundary 标注。
  - **替代/后续**：`analyze --all`（全量 session_map）可让递归聚合真正生效，作为后续增强（非本次阻塞项，design 诚实标注为技术债）。

**分析层 `__init__.py` 导出**：

```python
from .session_lineage import SessionLineage, build_session_lineage
```

---

### D5: Report 渲染

**render_report() 签名扩展**（additive，向后兼容）：

```python
def render_report(
    trace, findings, attributions,
    enable_analysis=False,
    profile=None, context_health=None, token_invariant=None,
    session_lineage=None,  # A2 新增
) -> str:
```

**渲染块 `_render_session_lineage_block(sl)`**：

仅当 `enable_analysis=True` 且 `session_lineage is not None` 时渲染。措辞严守铁律：

```
### 跨会话 Lineage (A2)

- 自身: <own_tokens> tokens / <own_steps> steps / <own_tools> 工具调用
- 父会话: <parent_session_id or "无(根会话)">
- 父边类别: <parent_category>
- 图深度: <lineage_depth> (从本机根)
- 子代理委托(token 规模观测,非成本):
  - subagent 直接子代: <subagent_child_count> 个
  - subagent 后代 token 合计: <lineage_descendant_tokens> tokens / <lineage_descendant_steps> steps / <lineage_descendant_tools> 工具
- 会话复制链(可选,非 subagent):
  - forked-session 直接子代: <fork_session_child_count> 个
  - forked-session 后代 token 合计: <lineage_fork_descendant_tokens> tokens
- 形态: provider=<provider> mode=<mode>  (仅 subagent 有值)
- 边界: 本机可解析子图内成立 (覆盖 <total_sessions_in_graph> 会话, <unresolvable_edges> 条不可解析边)
```

**关键措辞规则**：

- 后代 token 标注"token 规模观测,非成本"——不出现 "wasted" / "cost" / "可避免"
- 不出现 "Total wasted tokens"
- 不出现 "父因此浪费" / "父省子费" 等因果断言
- 不出现 "9.1×" 等放大倍数（那是 forked-session 混算的误导数字）
- 边界标注"本机可解析子图内成立"——若有不可解析边，显式报告数量
- `provider=None` 时该项不渲染（非 subagent 无形态）

**Summary 头部集成**：

在 Summary 块末尾（`_render_token_invariant_block` 之后）追加 lineage 块。不修改 Summary 的现有行。

---

### D6: 测试设计

**测试文件**：`tests/test_cross_session_lineage.py`

**用例清单**（按设计文档测试表组织）：

| # | 用例 | 覆盖 |
|---|---|---|
| **T1** | `test_session_lineage_defaults_all_zero` | 默认构造 → 全零/None，不虚构 |
| **T2** | `test_empty_session_zero_block` | 空会话（无 step）→ `SessionLineage()` 全零 |
| **T3** | `test_single_session_no_parent_no_child` | 单会话，无父无子 → own_* 有值，lineage_descendant_* 全零，child_count=0 |
| **T4** | `test_parent_session_edge_parsed` | adapter 解析 session 头 → `trace.metadata["parentSession"]` / `["origin"]` / `["delegationDepth"]` 正确 |
| **T5** | `test_subagent_descriptor_provider` | 从 `trace.events[]` 提取 `subagent/descriptor` → `provider="fork"` / `"spawn"` |
| **T6** | `test_subagent_child_classification` | origin=subagent → 分类为 SUBAGENT，进 `lineage_descendant_*` |
| **T7** | `test_forked_session_child_classification` | origin≠subagent 但 parentSession 有值 → 分类为 FORKED_SESSION，进 `lineage_fork_descendant_*` |
| **T8** | `test_root_classification` | parentSession=None → 分类为 ROOT |
| **T9** | `test_simple_parent_child_aggregation` | 父→1 个 SUBAGENT 子：父的 `lineage_descendant_tokens` = 子.own_tokens |
| **T10** | `test_nested_three_level_aggregation` | 3 层链：根→子→孙。根的 `lineage_descendant_tokens` = 子.own + 孙.own（恰好 1 次） |
| **T11** | `test_anti_double_count_three_level` | 3 层链：验证孙的 own_tokens 在根的 lineage_descendant_tokens 中仅出现 1 次 |
| **T12** | `test_fork_not_mixed_into_subagent_aggregation` | 父同时有 SUBAGENT 子 + FORKED_SESSION 子：fork 的 token 不进 `lineage_descendant_tokens` |
| **T13** | `test_fork_no_token_dedup` | fork 子代 token 与父独立（不做互斥/去重），各自 own_tokens 独立 |
| **T14** | `test_unresolvable_parent_graceful` | parentSession 指向不存在会话 → `unresolvable_edges>0`，`is_resolvable_subgraph=False`，不抛异常 |
| **T15** | `test_unresolvable_child_omitted` | 子会话不在 session_map → 不参与聚合，不计入 child_count |
| **T16** | `test_agent_start_not_lineage_edge`（评审 E） | agent-start.childId **不**被用作 lineage 边（仅 parentSession 权威）；验证 agent-start 伪边不会产生父子关系 |
| **T17** | `test_sender_session_id_not_lineage_edge` | senderSessionId 不产生 lineage 边 |
| **T18** | `test_additive_golden_shield` | enable_analysis=False → 现有报告逐字节不变（金钟罩） |
| **T19** | `test_not_in_all_detectors` | SessionLineage 不在 ALL_DETECTORS / ALL_ATTRIBUTION_ENGINES 中 |
| **T20** | `test_determinism` | 相同输入跑两次 → 输出逐字段一致 |
| **T21** | `test_cycle_detection` | 人工构造 A→B→A 环 → visited 防环，不无限递归 |
| **T22** | `test_zero_token_subagent` | steps=1/tokens=0 的子代理 → `own_tokens=0`（不是 None），正常参与聚合 |
| **T23** | `test_lineage_depth_structural` | lineage_depth 从 parentSession 链推导，不用 delegationDepth |
| **T24** | `test_enable_analysis_gate` | enable_analysis=False → `session_lineage=None`，report 不渲染 lineage 块 |
| **T25** | `test_enable_analysis_without_session_map` | enable_analysis=True 但 session_map=None → `session_lineage=None`，不报错 |
| **T26** | `test_provider_none_for_non_subagent` | 非 subagent 会话 → `provider=None`，`mode=None` |
| **T27** | `test_report_lineage_block_wording` | report 渲染不出现 "wasted" / "cost" / "Total wasted" / "causal" |
| **T28** | `test_report_lineage_boundary_note` | report 渲染包含"本机可解析子图内成立"边界标注 |
| **T29** | `test_parent_category_values`（评审 G1） | subagent 子→`parent_category="subagent"`；forked-session 子→`"forked_session"`；根→`"none"` |
| **T30** | `test_multiple_children_counts`（评审 G2） | 父有 3 SUBAGENT 子 + 2 FORKED_SESSION 子 → `child_count=5`、`subagent_child_count=3`、`fork_session_child_count=2` |
| **T31** | `test_root_child_count_semantics`（评审 G3） | `root_child_count`：自己是根→1，非根→0（冗余字段，验证语义清晰） |
| **T32** | `test_single_session_map_partial_observable`（评审 C） | 单元素 session_map（仅当前会话）→ `lineage_descendant_*` 为 0（子不可解析），但 `parent_category`/`parent_session_id` 据 header 可观测，不报错 |

**全量回归**：`python -m pytest tests -q`（当前 216 用例 + 32 新增 = 248 预期全绿）

---

### D7: report 渲染措辞（完整模板）

以下为 `_render_session_lineage_block` 的完整渲染模板：

```python
def _render_session_lineage_block(sl) -> list[str]:
    """渲染跨会话 Lineage 块（A2，分析层观测）。

    纯函数、确定性。语义边界：
    - 后代 token 标注"token 规模观测,非成本"；
    - 不出现 "wasted" / "Total wasted" / "harness bug" / "父浪费" / "父省子费"；
    - causal_claim=NONE；
    - 边界标注"本机可解析子图内成立"；
    - provider=None 时该项不渲染。
    """
    lines = ["", "### 跨会话 Lineage (A2)"]
    lines.append("")
    lines.append(f"- **自身**: {sl.own_tokens} tokens / {sl.own_steps} steps / {sl.own_tools} 工具调用")

    parent_str = sl.parent_session_id or "无(根会话)"
    lines.append(f"- **父会话**: {parent_str}")
    lines.append(f"- **父边类别**: {sl.parent_category}")
    lines.append(f"- **图深度**: {sl.lineage_depth} (从本机根)")

    lines.append(f"- **子代理委托(token 规模观测,非成本)**:")
    lines.append(f"  - subagent 直接子代: {sl.subagent_child_count} 个")
    lines.append(f"  - subagent 后代 token 合计: {sl.lineage_descendant_tokens} tokens / {sl.lineage_descendant_steps} steps / {sl.lineage_descendant_tools} 工具")

    if sl.fork_session_child_count > 0:  # 评审 D：count>0 才渲染（避免噪音）
        lines.append(f"- **会话延续链(关联会话,非 subagent 委托)**:")
        lines.append(f"  - forked-session 直接子代: {sl.fork_session_child_count} 个")
        lines.append(f"  - forked-session 后代 token 合计: {sl.lineage_fork_descendant_tokens} tokens")

    if sl.provider is not None:
        lines.append(f"- **形态**: provider={sl.provider} mode={sl.mode}")

    boundary = (
        f"本机可解析子图内成立 (覆盖 {sl.total_sessions_in_graph} 会话"
        + (f", {sl.unresolvable_edges} 条不可解析边" if sl.unresolvable_edges > 0 else "")
        + ")"
    )
    lines.append(f"- **边界**: {boundary}")

    return lines
```

---

## Schema & API

### 新增文件

| 文件 | 说明 |
|---|---|
| `agenttrace/analysis/session_lineage.py` | SessionLineage dataclass + build_session_lineage() |
| `tests/test_cross_session_lineage.py` | 28 用例（T1–T28） |

### 修改文件（均为 additive）

| 文件 | 修改内容 |
|---|---|
| `agenttrace/adapters/dsh_adapter.py` | session 事件处理追加 3 行（parentSession/origin/delegationDepth → metadata） |
| `agenttrace/analysis/__init__.py` | 导出 SessionLineage + build_session_lineage |
| `agenttrace/pipeline.py` | DiagnosisResult 加 session_lineage 字段；diagnose() 加 session_map 参数；Stage 3 加 build_session_lineage 调用 |
| `agenttrace/report.py` | render_report() 加 session_lineage 参数；新增 _render_session_lineage_block()；Summary 尾部追加调用 |

### 不变更的文件（零影响）

- `agenttrace/detectors/` —— 全部不变
- `agenttrace/attribution/` —— 全部不变
- `agenttrace/core/` —— 全部不变
- `agenttrace/cli.py` —— 本次不改（单会话 CLI 不传 session_map，session_lineage=None；后续批量分析模式再扩展）

---

## Testing

### 测试策略

1. **单元测试**：T1–T17（数据块、边解析、分类、聚合、防重复、降级）
2. **集成测试**：T18（金钟罩）、T19（不进 registry）、T24（门控）
3. **确定性测试**：T20（相同输入 → 相同输出）
4. **边界测试**：T21（防环）、T22（0-token）、T25（无 session_map）
5. **报告措辞测试**：T27（禁词检查）、T28（边界标注）

### 回归要求

- 全量 `pytest` 248 用例全绿
- `enable_analysis=False` 时报告与 v0.5 逐字节一致（T18 金钟罩）
- `check_facts` exit 0

---

## Open Questions（评审后已定稿大部分）

| # | 问题 | 结论（评审后） | 待实现时验证 |
|---|---|---|---|
| Q1 | adapter 是否已解析 `parentSession`/`origin`/`delegationDepth`？ | **未解析**（L115–118 仅提取 id/cwd/agentPreset）。D0 已给最小 additive 扩展方案。 | 真实会话验证 3 字段可解析（106/106） |
| Q2 | `subagent/descriptor.provider.label` 取值？ | evidence 确认 43 个全为 fork/spawn。未知值 → `provider="unknown"`。 | 实现时验证 43 个 descriptor 取值 |
| Q3 | forked-session 在 report 是否默认展示？ | **已定**：默认展示，但**仅 `fork_session_child_count>0` 时渲染**（评审 D）；措辞改「会话延续链(关联会话,非 subagent 委托)」。 | 无 |
| Q4 | 单会话 CLI 是否需要 lineage？ | **已定**（评审 C 方案2）：`cmd_analyze` 传**单元素 session_map**，让 A2 默认路径至少部分可观测。`analyze --all` 全量递归聚合作为后续技术债。 | — |
| Q5 | `delegationDepth` 与图深度交互？ | 图深度从 parentSession 链推导，delegationDepth 仅 metadata 参考。 | 确认二者差异 |
| Q6 | `agent-start.childId` 佐证？ | **已定（评审 E）**：**砍掉**。evidence §4 证明 parentSession 100% 可靠、agent-start 67% 伪边、无增量信息 → 不作为 lineage 来源。 | 无 |
| Q7 | 跨机/外部会话引用？ | 标 `unresolvable`，不参与聚合。本机样本 0 不可解析。 | 跨机部署时补充 |

---

## 附录：与现有分析层数据块的同构关系

| 维度 | ContextHealth | TokenInvariant | **SessionLineage (A2)** |
|---|---|---|---|
| 输入 | `Trace` | `Trace` | `(session_id, session_map)` |
| 输出 | `ContextHealth` | `TokenInvariant` | `SessionLineage` |
| 注册 | 不注册 | 不注册 | 不注册 |
| 门控 | `enable_analysis=True` | `enable_analysis=True` | `enable_analysis=True` |
| 默认值 | 全 not-applicable | 全零块 | 全零/None |
| 因果 | NONE | NONE | NONE |
| 渲染 | `_render_context_health_block` | `_render_token_invariant_block` | `_render_session_lineage_block` |
| 位置 | Summary 尾部 | Summary 尾部 | Summary 尾部（最后） |

---

## 评审融合记录（deepseek-v4-pro 异模型评审，2026-08-22）

> 评审作为**阻塞闸门**执行，结论见 `openspec/changes/cross-session-lineage/review-pro.md`。10 条硬约束红线全部守住（A–H 架构判断正确）；锁定 **1 个阻塞项（E）+ 1 个强建议（C）**，全部融合进本设计。

| 评审项 | 结论 | 融合位置 |
|---|---|---|
| 红线 1–10 | 全部通过（B 分类逻辑收紧建议已采纳） | — |
| A 防重复计数 | 算法正确 | D3 加 visited 语义边界注释（树形防环；未来 DAG 改拓扑序） |
| B 两类子代 | 基本通过，防御未知 origin | D1/D3 分类收紧：`origin is None` 显式检查 + UNKNOWN_ORIGIN 防御 |
| **C session_map 入口** | 🟡 强建议：单会话 CLI 下不可用 | D4 采纳方案2：`cmd_analyze` 传单元素 session_map，A2 默认路径至少部分可观测；`--all` 列为技术债 |
| D forked-session 展示 | 措辞不准确 | D7 措辞「会话延续链(关联会话,非 subagent 委托)」；`fork_session_child_count>0` 才渲染 |
| **E agent-start 佐证** | 🔴 **阻塞**（无增量信息） | **砍掉**：D0/D1 删除 agent-start 佐证逻辑；T16 改为「验证 agent-start 不作 lineage 边」；Q6 定稿=砍掉 |
| F 图深度 | 通过 | 无改动 |
| G 测试完备性 | 补测试 | 新增 T29–T32（parent_category/多子代计数/root_child_count 语义/单 session_map 部分可观测） |
| H additive 风险 | 通过 | 无改动；金钟罩 T18 兜底 |

**结论:设计已按评审 A–H 融合定稿,可进入实现。** 交实现会话 `session-d2c507cf`。