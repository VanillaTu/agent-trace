# Review — cross-session-lineage (A2) design.md 定稿评审

> 评审模型：deepseek-v4-pro（异模型、深度思考、严格性优先）
> 评审对象：`design.md`（553 行定稿）
> 评审依据：`evidence.md`（106 会话实测）、`proposal.md`、`PROJECT_STATE.md`、同构参考 `token_invariant.py` / `context_health.py` / `pipeline.py` / `cli.py` / `report.py`

---

## A. 防重复计数

**【结论】✅ 通过，算法正确，但 visited 语义有一处隐患需澄清。**

**【理由】**

D3 第 8 步的递归公式：
```
lineage_descendant_tokens += child.own_tokens + child.lineage_descendant_tokens
```
这是标准树形递归聚合。在正常树结构（每节点唯一父）中，每个后代 `own_tokens` 在祖先的 `lineage_descendant_tokens` 中出现恰好 1 次——因为递归只沿 `parentSession` 向上单向传播，不存在交叉引用。

D3"正确报告方式"的说明（第 281-284 行）也正确：报告层只展示每个会话自身的 `SessionLineage` 字段，不跨会话相加 `lineage_descendant_tokens`。这守住了"每个后代只计一次"。

**visited 防环的隐患**：设计第 239 行说"若 session_id ∈ visited → 返回 `SessionLineage()`（已访问过，不重复计数）"。在正常树中 visited 只包含祖先链，不会误杀兄弟。但若未来数据出现 DAG（多父指向同一子），visited 会让该子仅被第一个父计入，其余父得到空块。当前数据不存在 DAG（parentSession 唯一），但设计应显式声明 visited 的语义边界：**"visited 是防环安全网，在 parentSession 树（非 DAG）下等价于祖先链去重；若未来出现多父，需改用 DAG 拓扑序聚合"**。这不是阻塞项，但应写入注释。

**visited 传递方式**：第 223 行签名 `visited: set[str] | None = None`，内部递归时传递同一个 set 实例。这是正确的 Python 引用语义——所有递归层级共享同一个 visited。但需注意：如果 `build_session_lineage` 被多次调用（对多个根分别构建），每次顶层调用应传 `visited=None`（或新 set），否则上一次的 visited 会污染下一次。设计在步骤 239 以 `visited is None` 时初始化是新 set，所以多次顶层调用安全。

**【是否需改+具体改法】**

**不需要改设计正文**，但建议在 D3 注释中补充一行：
```
# visited 语义：树形防环安全网。在 parentSession 树（非 DAG）下等价于祖先链去重。
# 若未来出现多父 DAG，需改用 DAG 拓扑序聚合。
```

---

## B. 两类子代区分

**【结论】⚠️ 基本通过，但分类逻辑有一处定义模糊——origin 非 "subagent" 也非 None 的未知值会被静默归类为 FORKED_SESSION。**

**【理由】**

D1 第 4 步的分类规则：
```
- SUBAGENT：origin == "subagent"
- FORKED_SESSION：origin != "subagent" 且 parentSession 有值（非 None/空）
```

当前 106 会话实测 origin 只有 `"subagent"` 和 `None` 两种取值（evidence §1），所以 `origin != "subagent"` 等价于 `origin is None`。但设计没有防御未来可能出现的第三种 origin 值（如 `"workflow"`、`"scheduled"` 等）。

若未来出现 `origin="unknown"` 且 `parentSession` 有值，按现有逻辑会被静默归入 FORKED_SESSION，这可能不对——它既不是 subagent 委托，也不是"会话复制"。

此外，D3 第 7 步表述为"否则（origin != 'subagent' 且 parentSession 有值）→ FORKED_SESSION"，这里 `origin != "subagent"` 会匹配 `origin=None` 和任何未知值。建议收紧为显式检查 `origin is None or origin == ""`。

**【是否需改+具体改法】**

**建议修改 D1 第 4 步和 D3 第 7 步**，把分类逻辑收紧为：

```
- SUBAGENT：origin == "subagent"
- FORKED_SESSION：origin is None（或空字符串）且 parentSession 有值
- UNKNOWN_ORIGIN：origin 有值但既不是 "subagent" 也不是 None → 日志警告，暂按 FORKED_SESSION 处理（保守）
```

这不是阻塞项（当前数据无此情况），但属于防御性设计的合理要求。

---

## C. session_map 入口

**【结论】🔴 这是一个需要正视的设计不对称问题。session_map 作为 diagnose() 新参数，打破了 ContextHealth/TokenInvariant 的"只吃 Trace"同构约定。单会话 CLI 路径下 session_lineage 永远为 None，A2 在默认路径下不可用。**

**【理由】**

设计附录的同构表（第 544-553 行）明确标注了差异：
| 维度 | ContextHealth | TokenInvariant | SessionLineage |
|---|---|---|---|
| 输入 | `Trace` | `Trace` | `(session_id, session_map)` |

这是**架构层面的根本不对称**：ContextHealth 和 TokenInvariant 是**会话内**分析（从单个 Trace 即可计算），而 SessionLineage 是**跨会话**分析（需要整个会话池）。两者性质不同，放在同一"分析层"但依赖模型不同，这会导致：

1. **默认路径不可用**：`analyze <session> --analysis` 会渲染 ContextHealth + TokenInvariant，但 session_lineage 始终为 None。用户看到"开启分析层"却得不到 lineage 信息，体验割裂。
2. **构建责任不明确**：design 说 session_map 由"调用方从 `discover_sessions()` + `load_dsh_session()` 构建"，但 pipeline.py 和 cli.py 均未实现。Q4 说"后续批量分析模式再传 session_map"，但没有给出批量模式的任何设计轮廓。
3. **惰性构建的缺失**：ContextHealth/TokenInvariant 在 `render_report` 中有惰性构建 fallback（`if token_invariant is None: build_token_invariant(trace)`），但 SessionLineage 不可能有——因为它需要 session_map，而 render_report 不持有 session_map。

**对比 A1 的实现路径**：A1（TokenInvariant）的 adapter 扩展、pipeline 集成、CLI 参数是一气呵成的——`analyze <session> --analysis` 就能看到结果。A2 却把 CLI 集成推迟到"后续"，这导致 A2 的"可观测"承诺在当前版本无法兑现——用户只能通过编程 API 调用 `build_session_lineage(session_id, session_map)` 才能看到结果。

**【是否需改+具体改法】**

**建议（二选一，推荐方案 1）**：

**方案 1（推荐）**：在本次 change 中同步实现 CLI 批量入口 `analyze --all`，最小可行版：
- CLI 新增 `--all` flag，触发 `discover_sessions()` 扫描全部会话
- 对每个会话构建 Trace，组装 session_map
- 对每个会话调用 `diagnose(trace, session_map=session_map, enable_analysis=True)`
- 这同时解决了 session_map 构建责任归属问题

**方案 2（最小改动）**：即使不实现 `--all`，也应在 `cmd_analyze` 中**对单会话做局部 session_map**：
- 从 `discover_sessions()` 获取所有 session_id
- 仅加载当前会话的 Trace（不变）
- 但将当前会话的 (session_id, trace) 作为单元素 session_map 传入
- 这样单会话也能得到 `SessionLineage`（own_* 有值，lineage_* 全零，parent_session_id 可能有值）
- 代价极低（只多一次 `discover_sessions()` 调用），让 A2 在默认路径下至少"部分可观测"

**方案 3（记录为技术债）**：在当前 design 中明确标注"CLI 集成推迟到 v0.7，当前仅 API 可用"，并在 D4 处加醒目标注。这不算阻塞，但必须在 design 和 proposal 中诚实声明。

**评审立场**：这不算硬红线阻塞（design 已诚实标注 Q4），但**强烈建议**采用方案 2——代价极低，收益是让 A2 在默认路径下可用。

---

## D. forked-session 展示

**【结论】⚠️ 默认展示可以接受，但"会话复制链"措辞不准确，建议改为"会话延续链"或"关联会话链"。**

**【理由】**

Q3 开放问题："forked-session 块 report 是否默认展示？"

设计选择：默认展示，独立"会话复制链"块，标注"可选,非 subagent"。

**"会话复制链"措辞问题**：evidence §3 明确说 fork 子代理**不继承父上下文**（首步 input 中位 12,132 vs spawn 4,696，同量级）。forked-session 是"被复制/继续出来的顶层会话"（evidence 修正 5），但"复制"暗示内容拷贝，实际它们是独立运行的大型会话。DSH 的 fork 机制是"借父会话状态启动新会话"，不是"复制会话内容"。**"会话复制链"措辞可能误导用户认为 token 在父子间重复**，这与 evidence 结论矛盾。

建议措辞：
- "会话延续链"（更准确：forked-session 是父会话的延续/分支）
- 或 "关联会话链"（中性，不暗示复制/继承）

**默认展示 vs 隐藏**：当前设计默认展示，标注"可选,非 subagent"。我倾向于**保持默认展示**——因为 forked-session 是真实存在的结构，隐藏会丢失信息。但 D7 渲染模板中 forked-session 块总是渲染（即使 `fork_session_child_count=0`），这会产生噪音。建议：**仅当 `fork_session_child_count > 0` 时才渲染 forked-session 块**。

**【是否需改+具体改法】**

1. 将"会话复制链"改为"会话延续链(关联会话,非 subagent 委托)"
2. D7 渲染模板加门控：`if sl.fork_session_child_count > 0:` 才渲染 forked-session 块
3. 非阻塞，但建议修改。

---

## E. 佐证逻辑

**【结论】🔴 这是一个设计未完项。agent-start 佐证逻辑在 Q6 中仍为开放问题，SessionLineage 没有存储佐证结果的字段，佐证规则（按 runId 去重）虽正确但无落盘目标。**

**【理由】**

Q6 原文："实现时决定：佐证仅作 debug 日志输出，还是存入 SessionLineage 的 `corroborated_edges` 字段？"

逐项分析：

1. **SessionLineage 没有 `corroborated_edges` 字段**：D2 的 dataclass 定义中不存在此字段。如果设计最终决定"存入 SessionLineage"，则 D2 需补充字段；如果决定"仅 debug 日志"，则 D1 第 6 步的佐证规则在实现层没有产出物。

2. **evidence 说 agent-start 67% 伪边，为何还要它？** 设计在 evidence §4 中已证明 agent-start 不可靠，但仍保留佐证逻辑。理由是"父确实发起过 workflow 子代理的佐证"。但若佐证结果不落盘、不展示、仅 debug 日志，其实际价值存疑——用户不会看 debug 日志。若佐证结果存入 SessionLineage 并展示，则可能引入噪音（67% 伪边即使去重后仍可能有残留不一致）。

3. **过度设计风险**：D1 第 6 步定义了完整的佐证规则（按 runId 去重 + 以 header.parentSession 为准），但无落盘目标。这属于"设计了一个机制但没决定它产出什么"。建议要么**砍掉 agent-start 佐证**（因为 evidence 已证明它不可靠，parentSession 100% 可靠），要么**明确产出物**。

**【是否需改+具体改法】**

**建议方案**：**砍掉 agent-start 佐证逻辑**。理由：
- evidence 已证明 parentSession 100% 可解析、0 冲突，是充足且权威的 lineage 来源
- agent-start 67% 伪边，即使按 runId 去重后仍可能引入不一致
- 佐证不增加任何 parentSession 已提供的信息（parentSession 已告诉我们父子关系）
- 砍掉后 D1 步骤从 7 步简化为 5 步，D0 的"佐证信号"段落可删除，测试 T16 改为"验证 agent-start 不被用作 lineage 边"

**如果坚持保留**，则必须：
1. 在 D2 中补充字段（如 `corroborated_edges: int = 0` 和 `corroboration_mismatches: int = 0`）
2. 在 D7 渲染模板中展示佐证结果
3. 在 Q6 中给出明确决定

**评审立场**：这是**阻塞项**——Q6 不能在实现时再决定，设计必须给出明确答案。

---

## F. 图深度

**【结论】✅ 通过。lineage_depth 从 parentSession 链推导是正确的，不用 delegationDepth。**

**【理由】**

evidence §1 明确结论："delegationDepth 只能用来判断 subagent 的嵌套层级，不能当图深度；图深度改从 parentSession 链推导"。原因：
- `delegationDepth` 只在 subagent 上有意义（43 个有值），forked-session 的 delegationDepth=0（14 个），root 也是 0（49 个）
- 基于 parentSession 链的图深度会正确计入 forked-session 链的深度
- 数据证实：parentSession 链深度分布 {0:49, 1:47, 2:5, 3:5} vs delegationDepth {0:63, 1:41, 2:1, 3:1}

**深度定义**：D2 字段说明"从本机根的图深度（结构化推导，沿 parentSession 链的最长路径）"。定义明确：
- 根（无 parentSession）→ depth=0
- 根的直接子代 → depth=1
- 中间层（既是父又是子）→ depth=其沿 parentSession 链到根的距离

**与 delegationDepth 的差异处理**：Q5 标注"delegationDepth 仅作 metadata 参考（不参与 lineage_depth 计算）"。这正确。但 D2 字段中没有存储 delegationDepth 的位置——它在 metadata 中但不在 SessionLineage 中。如果需要对比验证，可考虑在 SessionLineage 中加一个 `delegation_depth_from_header: Optional[int]` 字段，但这不是必须的。

**【是否需改+具体改法】**

不需要修改。lineage_depth 的推导逻辑正确，定义清晰。

---

## G. 测试完备性

**【结论】⚠️ 基本覆盖，但有以下遗漏：**

**【理由与遗漏清单】**

**已覆盖（良好）**：
- T1-T3：默认值、空会话、单会话 ✅
- T4-T5：Adapter 解析、descriptor 提取 ✅
- T6-T8：三类分类 ✅
- T9-T11：聚合（简单、嵌套、防重复）✅
- T12-T13：Fork 分离、不去重 ✅
- T14-T15：不可解析 ✅
- T16-T17：佐证、senderSessionId ✅
- T18-T19：金钟罩、不进 registry ✅
- T20-T21：确定性、防环 ✅
- T22：0-token ✅
- T23-T25：深度、门控、无 session_map ✅
- T26-T28：provider None、措辞禁词、边界标注 ✅

**遗漏（按严重程度排序）**：

1. **G1：parent_category 取值未测**（中）。D2 定义 `parent_category` 取值 `"subagent" | "forked_session" | "none"`。应有测试验证：
   - subagent 子 → `parent_category="subagent"`
   - forked-session 子 → `parent_category="forked_session"`
   - 根 → `parent_category="none"`

2. **G2：多子代计数未测**（中）。T9 只测 1 个子代，未测多个子代的计数准确性。应有测试验证一个父有 3 个 SUBAGENT 子 + 2 个 FORKED_SESSION 子时，`child_count=5`、`subagent_child_count=3`、`fork_session_child_count=2`。

3. **G3：root_child_count 计算逻辑未测**（中）。D2 定义 `root_child_count` 为"子树中 ROOT 节点数（含自身若是根）"。这个字段的计算逻辑在 D3 算法中未显式描述（步骤中没有"计算 root_child_count"），测试表也未覆盖。需要明确：这个值是在递归中计算还是后处理？若会话自己是根，算 1；若子树中还有别的根（不可能，因为 parentSession 是树），那这个字段恒为 1（当自己是根）或 0（当自己不是根）。但若设计意图是"子树中所有 depth=0 的节点数"，在树结构中这个值恒为 1（树的根唯一）。**这个字段可能是冗余的**，建议澄清或删除。

4. **G4：空白/缺失 metadata 未测**（低）。如果 trace.metadata 为 None 或 {}，`trace.metadata.get("parentSession")` 返回 None，逻辑应正确。但应有防御性测试。

5. **G5：total_sessions_in_graph 计算未测**（低）。D2 定义此字段为"传入 session_map 的大小"，应在测试中验证。

6. **G6：中间层节点（既是父又是子）的 lineage_depth 未测**（低）。evidence 中有 5 个中间层节点，应验证其 depth 计算正确。

7. **G7：`subagent/descriptor` 事件缺失时的 provider/mode 行为**（低）。T26 覆盖了非 subagent 场景，但未覆盖"是 subagent 但没有 descriptor 事件"的边界（理论上不应出现，但应防御）。

**【是否需改+具体改法】**

**必须补充**：
- G1：新增 `test_parent_category_values`（T29）
- G2：新增 `test_multiple_children_counts`（T30）

**建议补充**：
- G3：要么补充测试，要么在 D2 中明确 `root_child_count` 的计算逻辑（或删除该字段如果它恒为 1/0）
- G4-G7：可在实现阶段补充，不阻塞设计评审

---

## H. additive 风险

**【结论】✅ 通过。D0/D4/D5 的扩展均为 additive，不破坏金钟罩。**

**【理由】**

逐项审查：

**D0 adapter 扩展**：在第 117 行 `trace.metadata = {...}` 中追加 3 个 key。现有代码对 metadata 的使用模式：
- `context_health.py`：`trace.metadata.get("context_window")` —— 按 key 取值，新 key 不影响
- `pipeline.py`：不直接访问 metadata
- `report.py`：不直接访问 metadata
- 各 detector：不访问 metadata
- 测试：金钟罩测试 `test_disable_analysis_byte_identical_to_v05` 会验证

**风险点**：`trace.metadata` 从 `{"cwd", "agentPreset"}` 变为 `{"cwd", "agentPreset", "parentSession", "origin", "delegationDepth"}`。如果任何代码做 `len(trace.metadata)` 或 `for k in trace.metadata` 的遍历，会受影响。但经源码审查，无此用法。金钟罩测试会兜底。

**D4 DiagnosisResult 扩展**：新增 `session_lineage: Optional[object] = None` 字段。现有代码：
- `pipeline.py` 的 `DiagnosisResult` 构造：不传 `session_lineage` → 默认 None → 不影响
- `cli.py` 的 `cmd_analyze`：不访问 `result.session_lineage` → 不影响
- `report.py`：不访问 `result.session_lineage` → 不影响

**D4 diagnose() 签名扩展**：新增 `session_map: dict[str, Trace] | None = None` 参数。现有调用方 `cmd_analyze` 不传此参数 → 默认 None → 向后兼容。

**D5 render_report() 签名扩展**：新增 `session_lineage=None` 参数。现有调用方 `cmd_analyze` 不传此参数 → 默认 None → 向后兼容。D7 渲染模板有门控 `if enable_analysis and session_lineage is not None` → 默认路径不渲染。

**D5 report 渲染路径**：在 `render_report` 中，`enable_analysis=False` 时不进入任何分析层渲染路径。现有 Summary 块末尾追加 lineage 块仅在 `enable_analysis=True` 时触发。金钟罩测试验证 `enable_analysis=False` → 逐字节不变。

**metadata 字段影响现有逻辑的深度审查**：
- `context_health.py` 的 `_resolve_window` 只读 `trace.metadata.get("context_window")`，仅当 key 存在且为正整数时启用。新 key 不影响。
- `token_invariant.py` 的 `build_token_invariant` 只读 `trace.events`，不读 metadata。
- 无其他代码访问 metadata。

**【是否需改+具体改法】**

**不需要修改**。additive 属性成立。金钟罩测试（T18）会在实现阶段验证。

---

## 总评

### 硬红线逐条核对

| # | 红线 | 状态 |
|---|---|---|
| 1 | additive：不改现有 detector/attribution 行为 | ✅ 通过 |
| 2 | SessionLineage 非 Detector/Finding，不注册 ALL_DETECTORS | ✅ 通过 |
| 3 | causal_claim=NONE；tokens=None≠0 | ✅ 通过 |
| 4 | 禁混算 Total wasted tokens | ✅ 通过 |
| 5 | 子会话 token 只归自己；禁沿链再加总 | ✅ 通过（D3 算法正确） |
| 6 | 权威父子来源=header.parentSession 唯一 | ✅ 通过 |
| 7 | 区分 SUBAGENT vs FORKED_SESSION | ⚠️ 通过但分类逻辑可收紧（见 B） |
| 8 | fork 不做 token 互斥/去重 | ✅ 通过 |
| 9 | 不可解析→悬挂节点 | ✅ 通过 |
| 10 | 复杂系统只测局部封闭不变量 | ✅ 通过 |

**无硬红线违反。设计可以进入实现。**

### 阻塞项（必须在实现前解决）

| # | 项 | 严重程度 |
|---|---|---|
| **E** | **agent-start 佐证逻辑未定稿**：Q6 仍为开放问题，SessionLineage 无存储佐证结果的字段。必须决定：砍掉佐证，或补充字段+渲染。**推荐砍掉**（evidence 已证明 parentSession 100% 可靠，佐证无增量信息）。 | 🔴 阻塞 |
| **C** | **session_map 构建责任不明**：单会话 CLI 路径下 session_lineage 永远为 None，A2 默认不可用。**推荐采用方案 2**（单会话局部 session_map，代价极低），让 A2 在默认路径下至少部分可观测。 | 🟡 强建议 |

### 建议修改项（非阻塞，但应融合进 design）

| # | 项 |
|---|---|
| **B** | 收紧分类逻辑：`origin is None` 显式检查，防御未知 origin 值 |
| **D** | 措辞："会话复制链"→"会话延续链"；forked-session 块仅 count>0 时渲染 |
| **G** | 补充测试：G1（parent_category 取值）、G2（多子代计数）、G3（root_child_count 澄清或删除） |
| **A** | D3 注释补充 visited 语义边界说明 |

### 最终判断

**设计可进入实现，但必须先解决阻塞项 E（agent-start 佐证去留）。建议同步处理 C（session_map 构建），否则 A2 在 CLI 默认路径下不可用，违背"可观测"初衷。**

评审已写入 review-pro.md。