# Evidence — cross-session-lineage (A2) 数据调研

> 数据源：本机 DSH 会话集合 `~/.dsh/sessions`，经 `discover_sessions()` 扫描得 **106 个会话**（has_zstd=True），全部解析成功（status=ok，106/106）。
> 解析：`_decompress_zstd` + `parse_dsh_jsonl`；token 用 `step.usage.total_tokens()`(input+output)。
> 性质：跨会话 lineage 的 **本机可解析子图** 内成立。本机 106 会话内**零外部引用**。

---

## 0. 结论速览（TL;DR）

| 项 | 结论 |
|---|---|
| 图规模 | 106 会话 / **57 条 parentSession 边** / **49 个根** / **16 个父** / 最大深度 **3** |
| 嵌套分叉 | 真实存在且仅 **1 条** depth-3 链：`cb171f5e`→`4031b5f2`→`541f1780`；fork 子代理 **无嵌套**（6 个全 depth=1） |
| lineage 规模 | 最大父 `session-5cdccd44` 子树 **20.1M token** = 自身 2.20M 的 **9.1×**；但纯 subagent 子树只放大 **1.5×** |
| fork vs spawn | **fork 不继承父上下文**：首步 input 中位 12,132 vs spawn 4,696；fork 是"长任务+shell 重"并非"带父上下文" |
| 权威来源 | **`parentSession`（session 头）** 唯一权威（57/57 全解析）；`agent-start.childId` 有 28/42 条是**复制伪边**；`senderSessionId` 0/47 是父子边 |
| 记账 | 子会话 token **只归自己**，不出现在任一祖辈；祖先" lineage 规模"是**聚合观测值**而非成本归因 |
| 边界 | 本机 106 会话内 3 个来源 **0 不可解析**；跨机/外部引用需降级标记 |

### 【修正】（相对"初步发现"）
1. **会话数**：初步发现 105 → 实测 **106**（多 1 个）。`origin=subagent` 42→**43**；`delegationDepth` 1 层 40→**41**。属数据漂移，非口径差异。
2. **`parentSession` 可解析数**：56/56 → 实测 **57/57**（跟会话数+1 同步）。且 **16 个唯一父目标**（不是 56 个，因为 57 条边只有 16 个不同父）。
3. **`tool-workflow/agent-start.childId` 不是干净的父子边**：43 个子代理里只有 14 个出现在 agent-start；42 次 childId 出现里 **28 次（67%）指向的"父"与 header.parentSession 不一致**。原因：**agent-start 事件被复制进 forked 会话**（相同 `runId/seq/time` 在多个会话里重复出现）。把它当 lineage 主数据源会引入**大量伪边**。
4. **根会话"子代理 12 个全都可解析"**：初步发现引用的是 `session-a79579f3`（其 agent-start 有 12 个 childId）。但实测该会话**有 parent（`session-4ee09ecf`），不是根**，且它经 header.parentSession 只有 **4 个** subagent 子代；12 是 agent-start 复制伪边计数。→ 真正最大的根父是 `session-5cdccd44`（24 个子代，其中 18 subagent + 6 forked-session）。
5. **存在"forked-session"这一类**：14 个会话 `parentSession` 有值但 `origin=None`、`delegationDepth=0`、**无 subagent/descriptor**。它们是被"复制/继续"出来的顶层会话（如 `session-4ee09ecf`、`session-a79579f3`、`session-d2c507cf`）。这与"subagent fork"是**两种不同机制**，A2 必须区分。

---

## 1. Lineage 图全貌

**指标**（图由 `header.parentSession` 权威构建）：

```
会话总数         106
边(parentSession) 57
根(无父)         49
父(有子)         16
叶子(无子)       52        # 57 有父节点；52 只有父没有子
中间层(既是父又是子) 5
最大图深度       3
深度分布(基于 parentSession 链)  {0:49, 1:47, 2:5, 3:5}
header.delegationDepth 字段分布   {0:63, 1:41, 2:1, 3:1}
```

> 两套深度值不同：`delegationDepth` 是**写入 header 的字段**，只在 subagent 上有意义（0 泛指非 subagent，含根与 forked-session）；基于 parentSession 链的图深度是**结构推导值**，会把 forked-session 链也算进去，所以 2/3 层分别有 5/5 个（而 delegationDepth 只有 1/1 个）。

**5 个中间层节点**（既是父又是子）：

| 会话 | 类别 | parent | 子代数 |
|---|---|---|---|
| `session-a79579f3` | forked-session | `session-4ee09ecf` | 4（subagent） |
| `session-d2c507cf` | forked-session | `session-5cdccd44` | 3（forked-session） |
| `4031b5f2` | subagent | `cb171f5e` | 1（subagent） |
| `cb171f5e` | subagent | `session-eae82666` | 1（subagent） |
| `session-4ee09ecf` | forked-session | `session-5cdccd44` | 1（subagent） |

**最大的根父 = `session-5cdccd44`**（origin=None，depth=0）：
- 直接子代 **24 个**（18 subagent + 6 forked-session），子树共 **33 个节点**。
- 子树 token **20,100,339**；子树 steps 7,167；子树 tools 7,840。

**深度 1 的 subagent 链（depth-3 嵌套）** —— 真实数据里唯一的 3 层嵌套：

```
session-eae82666 (根, root)
  └─ cb171f5e   (depth=1, subagent, spawn, mode=one-shot, steps=3, tools=3)
       └─ 4031b5f2 (depth=2, subagent, spawn, mode=one-shot, steps=18, tools=29)
            └─ 541f1780 (depth=3, subagent, spawn, mode=continuable, steps=5, tools=5)
```

这一链 token：cb171f5e=6,815 → 4031b5f2=59,846 → 541f1780=75,421（见 §2）。

**对 A2 的含义**：
- A2 的聚合必须**递归**（从任意父沿 parentSession 聚合全部后代），且要区分**两类父子关系**：
  - **subagent 边**（43 条，child.origin=subagent）：真正的"子代理委托"。
  - **forked-session 边**（14 条，child.origin=None 但 parentSession 有值）：会话被"复制/继续"出来的顶层会话，它们自己有大量 token/工具，不应与 subagent 混为一谈。
- `delegationDepth` 只能用来判断 subagent 的嵌套层级，不能当图深度；图深度改从 parentSession 链推导。

---

## 2. 嵌套分叉的记账

**三类自身的 token 规模**（每会话自身，不是子树）：

```
forked-session  n=14  tokens=20,807,894  steps=6,911  tools=7,161   input=16,089,701  out=4,718,193
root            n=49  tokens=16,182,639  steps=3,756  tools=4,376   input=13,661,372  out=2,521,267
subagent        n=43  tokens= 5,210,838  steps=1,066  tools=1,501   input= 4,228,375  out=  982,463
──
TOTAL           n=106 tokens=42,201,371
```

**一个父会话的完整的"lineage 规模"（包含全部后代 token）有多少？** 逐父（按 header.parentSession 递归聚合）：

| 父会话 | 类别 | 自身 token | 全部后代子树 token | 子树/自身 |
|---|---|---|---|---|
| `session-5cdccd44` | root | 2,198,404 | **20,100,339** | **9.1×** |
| `session-d2c507cf` | forked-session | 1,756,425 | 5,023,627 | 2.9× |
| `session-4ee09ecf` | forked-session | 2,242,118 | 5,019,091 | 2.2× |
| `session-65460504` | root | 1,491,206 | 3,956,835 | 2.7× |
| `session-3ab9b08e` | root | 1,723,890 | 3,424,886 | 2.0× |
| `session-a79579f3` | forked-session | 2,574,743 | 2,776,973 | 1.1× |
| `session-c2253ae3` | root | 1,127,781 | 2,514,560 | 2.2× |
| `session-710049f7` | root | 680,718 | 1,849,034 | 2.7× |
| `session-3343d283` | root | 356,086 | 1,259,086 | 3.5× |

**关键观察**：`session-5cdccd44` 的 9.1× 是被 **6 个 forked-session 子代**（它们自身就是大型会话）撑起来的。若只看 **subagent 子代**（A2 核心关注），同一父的放大倍数急剧下降：

```
parent=session-5cdccd44   pure-subagent 子树 3,308,120 / 自身 2,198,404 = 1.5×（非 9.1×）
parent=session-a79579f3   pure-subagent 子树 2,776,973 / 自身 2,574,743 = 1.1×
parent=session-3343d283   pure-subagent 子树 1,259,086 / 自身   356,086 = 3.5×
parent=session-710049f7   pure-subagent 子树 1,849,034 / 自身   680,718 = 2.7×
```

**整个 subagent 群体（43 会话）合计**：token 5,210,838 / steps 1,066 / tools 1,501，只占全部 42.2M token 的 **12.3%**。

**对 A2 的含义**：
- 父的子代 token 在**直接子代层面**通常只占父自身的 0.1×~2.5×；**远远不是"大部分成本来自子代理"**。真正吞 token 的是大型顶层/复制的会话（forked-session），不是一层 subagent。
- 所以"聚合子代 token 到父"若把 forked-session 也算进去，会得到 **9×** 这种会误导的数字（仿佛父很省、子孙很费）。A2 应**默认只聚合 subagent 子代**，把 forked-session 链作为可选的"会话复制"展示层，二者分开。

---

## 3. fork vs spawn 规模差异

**provider 分布**：`subagent/descriptor` 43 个 → **fork ×6 / spawn ×37**（全部 `mode`: fork 全是 `one-shot`；spawn 有 `one-shot`×26 / `continuable`×11）。

**token / 工具规模对比**（6 个 fork 会话 vs 37 个 spawn 会话）：

```
provider=spawn (n=37)
   首步 input: 中位 4,696   min 0   max 12,437   mean 5,793
   total_in:   中位 36,493  mean 34,371
   total_out:  中位 10,505  mean 13,529
   n_steps:    中位 9      mean 12.2
   input 占比 (in/(in+out)) 中位 0.691  min 0.375  max 0.978

provider=fork (n=6)
   首步 input: 中位 12,132  min 6,285  max 12,885  mean 10,434
   total_in:   中位 489,776 mean 492,772
   total_out:  中位 86,719  mean 80,315
   n_steps:    中位 87.5    mean 107
   input 占比 中位 0.846  min 0.760  max 0.922
```

**"fork 是否继承父上下文？" —— 数据结论：否。**

`fork` 子代理首步 input 只有 6,285~12,885（中位 12,132）。如果真"继承父会话上下文"，首步 input 应接近父会话规模（父通常几十万 token）。实测 **fork 首步 input 与 spawn 同量级**，不放大。逐个 fork 会话看：

```
05fc2d6b: 首8步 input=[6285,250,1114,147,7589,11321,944,1424]  # 步进小，无大上下文注入
ce78f433: 首8步 input=[6285,250,1114,147,7589,11321,944,1424]  # 与上完全一致（同模板注入）
9fe0a253: 首8步 input=[12132,16363,5403,1415,561,9426,1456,1933]
```

`fork` 真正大的不是"继承上下文"，而是**任务本身长**（median 87.5 step vs spawn 9）+ **input 占比高**（0.846 vs 0.691）。峰值 input 出现在**中期少数字步**（如 `ce78f433` 第 121 步 input=179,935、第 169 步=247,019），这是**工具结果累积**（`list_sessions` 等），不是首步注入。

**工具画像差异**也佐证"fork 是另一类任务"：

```
fork  (n=736 次工具调用):  pwsh 497(67.5%) + filesystem 144(19.6%) + 其它;  无 subagent/无 send_message/无 skill
spawn (n=820 次工具调用):  filesystem 436(53.2%) + pwsh 168(20.5%) + read 362 + edit/write/glob/search 混杂
```

fork 全是 6 个 `Mnemon idle checkpoint review` 任务，**工具高度集中在 pwsh（67.5%）**，几乎不做"再委托"（fork 子代理**全部没有再调度子 agent**，agent_starts=0），也不做知识库写入。说明 `fork` 在 DSH 里是"**借父会话某个状态做一次独立、长程、shell 重的巡检**"，而不是"把父的上下文灌进来再继续"。

**对 A2 的含义**：
- 不能把 `provider=fork` 建模成"继承父上下文 ⇒ token 会在父子间重复"。数据不支持。
- fork 与 spawn 的系统性差异是**任务形态**（fork=长程+shell 重+不再委托；spawn=短程+读写文件+可能再委托），不是上下文继承。
- 因此 A2 标 lineage 形态时：`provider` 记为 fork/spawn 是对的，但**不要据此做 token 去重或成本互斥**——fork 子代理的 token 与父会话 token 基本不重叠。

---

## 4. 如何建 lineage 图最可靠

**三个来源的完整性/一致性实测**：

```
来源 A  header.parentSession（session 头，child→parent）
       边=57, 唯一父目标=16, 不可解析=0, 每个 child 恰好 1 个父
       → 权威、完整、无歧义

来源 B  tool-workflow/agent-start.childId（parent→child 候选）
       出现=42, 唯一子代=14, 全部可解析到本地会话
       ⚠️ 与 header.parentSession 一致性仅 14/42 (33%)
       ⚠️ 43 个 subagent 子代里 29 个(67%) 未被 agent-start 捕获
       ⚠️ 28/42 条边指向的"父"≠ header 里的真父

来源 C  user/message + agent/inbox/spliced 的 source.senderSessionId
       (sender→receiver) 消息对=47 唯一, 全部可解析
       与 header 父子边一致=1/47；46/47 是**兄弟/无关会话互发消息**
```

**为什么 source B 不可靠——复制伪边（决定性证据）**：

同一个 `childId=7806edcf` 在 **7 个不同会话**里都出现 agent-start，而它们的 `runId/seq/time` **完全一致**（`runId=013a2f12`, seq=259111, time=1787327644451）：

```
session-3c152886 / session-4ee09ecf / session-4fbc44a5 / session-5cdccd44 /
session-a79579f3 / session-e4994f53 / session-f517be08    ← 7 个会话，同一 runId+seq+time
```

而 `7806edcf` 的 **header.parentSession 只有 `session-5cdccd44`**。其余 6 个会话是`session-5cdccd44` 的 **forked-session 复制体**——它们把父会话历史里的 `tool-workflow/agent-start` 事件**原样拷贝**了进来。所以 agent-start 会为同一个子代造出多个"伪父"。→ **必须去重（按 runId+seq 或按 child 唯一）并把目标锁定到真父，不能直接当边。**

**source C 的本质**：`senderSessionId` 是**消息路由/投递记录**，表示的是一条 `user/message` 或 `agent/inbox/spliced` 消息**从哪个会话发来、被投递到哪个会话**。它反映的是"会话间消息流通"（尤其 `agent/inbox/spliced` 是跨会话消息拼接，兄弟/父发来皆可），**不是 lineage 关系**。46/47 与父子边不一致佐证：它不能当 lineage 用，只能当"消息流"副信号。

**主数据源建议**：
- **主数据源 = `header.parentSession`**（child 声明自己的父），唯一权威、可解析率 100%、无冲突。
- **辅信号**：
  - `origin=subagent` + `delegationDepth` → 判定该边是不是 subagent 委托、及嵌套层级。
  - `subagent/descriptor.provider=fork|spawn` → 给该边标形态（需用 `session_id` 交叉关联到子会话的 descriptor）。
  - `agent-start.childId` → **仅用于"该父确实发起过 workflow 子代理"的佐证**，必须按 `runId` 去重，且最终父以 header.parentSession 为准。
  - `senderSessionId` → **不作为 lineage 边**，如需要可作"会话间消息流"层。

---

## 5. 记账口径建议（基于数据）

**核心问题**：嵌套聚合时，子会话 token 该不该让每个祖先都看到整个子树？

**数据理由**：
1. 不重复性有据可信：`fork` 子代理**没有继承父上下文**（§3，首步 input 不放大）。因此**唯一真实的"重复计算"源是同一会话被多个祖先各自聚合**——即**记账层面的重复，而非 token 本身重复**。
2. 结构放大是**严重的**：若每个祖先都累加整个子树，链路 `session-5cdccd44 → session-4ee09ecf → session-a79579f3 → f350a9ec` 会让 `f350a9ec` 的 token **被 3 次重复计入**（一次在 a79579f3 子树、一次在 4ee09ecf 子树、一次在 5cdccd44 子树）。数据里这种 2~3 层的祖先共享后代在 forked-session 链中真实存在。
3. subagent 本身占比小：全部 subagent token 仅占 12.3%（5.21M/42.2M），**即使全数聚合到根，也只是"规模观测"，不会左右成本结论**；但反过来**误把子树 sum 传给祖辈会造成"父看起来省、实则子孙贵"的错误叙事**（9.1× 就是这么来的）。

**记账规则（建议 A2 采用）**：

1. **每会话 token 只归自己**。`usage.total_tokens()` 是 per-step 计量，属该会话自己的工作量。子代理 token **绝不**并回父会话当"成本"或"wasted"。
2. **对每个会话提供两个明确分离的数字**：
   - `own_tokens`：该会话自身 token 合计（唯一权威、可加总、全局不重复）。
   - `lineage_descendant_tokens`：其(递归)全部后代的 token 合计（**聚合观测值**，不是成本，不参与 wasted/cost 归因）。
3. **祖先看到整个子树？→ 可以，但仅作为只读的"聚合观测"，且每个会话的该值在其**自身报告里只算一次**（沿链每个节点各存一份自己的子树和）。**绝不**把"祖先的子树和"沿链再加起来（否则后代被多次计数）。
   - 反例：报告"会话 X 的 lineage 规模 = X.own + Σ后代 own"，只对**单个** X 成立；把它套到 X 的父、祖父上并相加，就会重复计后代。
4. **区分两类子代**（决定性，来自 §1）：
   - `subagent` 子代（origin=subagent）→ 计入 `lineage_descendant_tokens`。
   - `forked-session` 子代（origin=None 但 parentSession 有值）→ 单独归到 `session_fork_descendants`（可选展示层），**默认不混入 subagent 聚合**，避免 9.1× 之类误导放大。
5. **fork 不做 token 互斥/去重**：§3 显示 fork 不继承父上下文，父子 token 基本不重叠；把它们相加是"规模相加"，无重复。真正的防重复只在于**祖辈对共有后代的一次性计数**（规则 3）。
6. **causal_claim=NONE**：跨会话只报"存在边 + 子代规模观测"，不判"父因此浪费/可控"。

**要防的坑（Pitfalls）**：
- ⚠️ **不在成本/wasted 口径上合并**后代 token（违反铁律"报告禁止混算 Total wasted"）。
- ⚠️ **不在祖辈链上累加子树和**（后代重复计数，尤其 3 层链会让叶子 ×3）。
- ⚠️ **不把 forked-session 当 subagent**（否则根父会从 1.5× 变成 9.1×，结论被污染）。
- ⚠️ **不以 provider=fork 触发去重/成本互斥**（数据不支持上下文继承）。
- ⚠️ **不可把带 0-token 的 subagent 当作"无成本"**：`session-76efbe7b/8ce6bce8/938aba3a/f4686508` 都是 steps=1/tokens=0，属"会话已创建但未产生 model 调用"，应记为 `tokens=0` 而非 `tokens=None` 或剔除。

---

## 6. 边界（不可解析/外部引用）

**本机 106 会话内，三个来源全部可解析，零外部引用**：

```
parentSession   唯一目标 16, 不可解析 0
agent-start childId 唯一 14, 不可访问 0
senderSessionId 唯一 11, 不可解析 0
```

**说明**：
- 本机 `~/.dsh/sessions` 是一个**单机、自治的 session 池**。所有 `parentSession`、`childId`、`senderSessionId` 都指向本机已存在会话，**当前样本没有捕获到跨机/外部子会话**。
- 因此"跨机外部子会话"在**本样本里占比 = 0%**（非"不知道"，是"未出现"）。这来自单机部署：子代理在本机同运行，不会指向外机。

**潜在断开场景与降级建议**（针对未来/其它部署）：
- **子机/外部会话**：`childId`/`parentSession` 指向不存在的会话时，`lineage` 图在该处断开。此时应：
  1. **标 `unresolvable`**，不猜、不伪造父子关系（符合"不虚构 lineage"铁律）。
  2. **降级为"悬挂节点"**：仍记录"存在指向 <external-ref> 的边"，但该引用不参与聚合、不与任何本地会话相连。
  3. 报告口径改为"**本机可解析子图内成立**"，给出"该图覆盖 N/总数 条边、断开 M 条"的诚实说明。
- **父不在本机**：`origin=subagent` 但其 parentSession 指向本机没有的会话 → 该 subagent 成为本机图的**根**（孤儿），聚合时从它自身开始，且注明"真实父不可解析"。
- **校验**：建议对每一条边做一次 `parent ∈ local_sessions_set` 检查，把不可解析边单独成清单输出，而不是静默丢弃。

---

## Lineage 记账口径建议（给 A2 设计者，可直接采用）

> 目标：让 A2 从任意父会话出发，沿可解析 parent→child 边，把子会话的 token/工具/detector 信号**聚合回父会话**，同时**不打破"不混算成本"与"不重复记账"两条铁律**。

### 图构建（权威序列）
1. 扫描 `discover_sessions()`，对每会话读 `session` 头事件的 `parentSession` / `origin` / `delegationDepth`。
2. 建边：`child.parentSession → parent`。**唯一权威来源是 header.parentSession**。
3. 标子代类别：
   - `SUBAGENT`：`origin=="subagent"`（→ 进入主聚合）。
   - `FORKED_SESSION`：`origin!=subagent` 且 `parentSession` 有值（→ 独立"会话复制"层）。
   - `ROOT`：无 parentSession。
4. 用 `subagent/descriptor.provider`（`fork|spawn`）+ 同序列的 `label/mode` 给 SUBAGENT 边标形态。
5. `agent-start.childId` **只作佐证**：按 `runId` 去重后，仅当 `child.header.parentSession == 该父` 才计入；否则丢弃（复制伪边）。

### 指标（每个会话一份）
- `own_tokens / own_steps / own_tools`：该会话自身（权威，可全局加总，**不**被任何祖先改写）。
- `lineage_descendant_tokens`：**SUBAGENT** 后代的递归 token 合计（聚合观测，仅含 origin=subagent 后代）。
- `lineage_fork_descendant_tokens`：**FORKED_SESSION** 后代的递归 token 合计（可选展示，默认不与上面混算）。
- `lineage_depth`：从本机根的图深度（结构化推导，不用 delegationDepth）。
- `child_count / subagent_child_count / fork_session_child_count`：直接子代计数。

### 记账边界
- **token 只归自己**：任何聚合都不改变每个会话自身的 token 计数。
- **祖辈不"占有"后代**：后代 token 只出现在`其自身`、以及`每个祖先各一份的自有子树和(descendant)`里；**禁止**把"祖先的 descendant 和"沿链再加总。
- **fork 不做互斥/去重**：fork 子代 token 与父基本不重叠，相加即规模相加。
- **只报观测，不报成本**：causal_claim=NONE；不把后代 token 并入"wasted/cost"。
- **不可解析 → 悬挂**：引用不指向本地会话时，记为 `unresolvable` 悬挂节点，不参与聚合、不伪造关系；报告注明"在本机可解析子图内成立"。
- **0-token 子代理视为"已创建未产生调用"**：`tokens=0`（不是 None），不剔除、不虚构。

### 输出示例（父会话）
```
session-5cdccd44 (ROOT)
  own_tokens=2,198,404  steps=821  tools=891
  lineage_descendant_tokens=3,308,120   (subagent 后代)
  lineage_fork_descendant_tokens=16,792,219  (forked-session 后代, 不混算)
  subagent_children=18  fork_session_children=6  depth=0
```

---

## 附：数据核验脚本（可复现）

落盘于 `research/lineage_probe/`（仅调研产物，不进入产品代码）：
- `discover.py` → 发现 + header/origin/depth
- `idconvention.py` → id 命名约定 + parentSession 可解析性
- `inventory.py` → 全量 inventory（descriptor/agent-start/sender/token/tool）`inventory.json`
- `graph.py` / `final_q1.py` / `final_q456.py` → 图拓扑与三源一致性
- `forkinput.py` / `forkramp.py` / `forkmode.py` / `toolcls.py` → fork vs spawn
- `copycheck.py` → agent-start 复制伪边证据

关键数据文件：`research/lineage_probe/inventory.json`（106 会话可复算）。
