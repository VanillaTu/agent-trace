# Proposal — cross-session-lineage (A2)

## Why

项目核心定位是**测 harness 架构/工具性**(token 为切入点,省 token 是副产品)。SUB-001(当前 v0.5+)只做**会话内** subagent 委托的 **flat 观测**:报 `mode/provider/label`,并在 docstring 明说"无 parent id → 不重建 parent/subagent 关系;无 cost 字段 → tokens=None"。

真实数据证据盘点(106 会话,决定性,详见 evidence.md):
- `subagent/descriptor` 事件 43 个,字段=`version/mode/provider(label/agentProvider/agentModel)`,`provider` 取值 **`spawn` / `fork`**。
- **跨会话 lineage 边可解析率 100%**:
  - `header.parentSession`(session 头)→ **权威**;57 边/16 个唯一父目标,可解析率 100%。
  - `tool-workflow/agent-start.childId` → 42/42 可解析,但 **67% 是复制伪边**(详 evidence §4),只能作佐证。
  - `user/message.source.senderSessionId` → 可解析,但 46/47 是消息路由**不是** lineage 边。

即:**DSH 日志里真实存在可解析的 parent→child 跨会话关联**,`fork` 更是明确的 lineage 传播线索。但当前 AgentTrace 只看到"某个会话里有个 subagent 委托",看不到"这个子会话是哪个、它自己又产生了多少 token/工具调用/哪些 detector 信号,以及这些是否该传播回父会话做记账"。

**这暴露一个 harness 架构不变量问题**:markup 代理会 fork/spawn 子会话去做独立工作,这些子会话的 token/工具/信号在**单会话视角**下是"看不见的"(只在子会话自己的日志里)。任何只分析父会话的消费方会**系统性漏记**这些子会话的成本与行为;而朴素把父子会话各自独立分析又会**重复归因/断裂 lineage**。这正是 harness 架构/工具性测量需要摸清的"跨会话记账"面。

## Goal

新增一个**跨会话 lineage 观测**能力,让 AgentTrace 能从单个会话(或会话集合)出发,顺着可解析的 parent→child 边,把子会话的 token 规模/工具调用/detector 信号**聚合回父会话**,并标记 lineage 形态(fork/spawn)。作为对"单会话看不到子会话成本"这一架构盲区的可验证观测。

### 分叉(spawn/fork)会话怎么计算(数据支撑)

数据证据见 `openspec/changes/cross-session-lineage/evidence.md`(106 会话实测,调研子代理产出)。核心结论(修正了多处初步判断):

**① 权威父子来源 = `header.parentSession`(session 头,非 data)**
- 106 会话 / 57 条边 / 49 根 / 16 父 / 最大深度 3,本机可解析率 100%。
- `tool-workflow/agent-start.childId` **67%(28/42)是复制伪边**(forked-session 拷贝父历史,同 runId/seq/time 在 7 会话重复)→ **不能当 lineage 主源**,只能作"父发起过子代理"的佐证(须按 runId 去重且以 header.parentSession 为准)。
- `senderSessionId` 46/47 是消息路由**不是** lineage 边,不作 lineage。

**② 必须区分两类子代(决定性)**
- `SUBAGENT`:`origin=="subagent"`(43 条)→ 进主聚合 `lineage_descendant_*`。
- `FORKED_SESSION`:`origin!=subagent` 但 `parentSession` 有值(14 个)→ 独立"会话复制"层,默认不混入 subagent 聚合(否则根父被误放成 9.1×)。
- `ROOT`:无 parentSession(49 个)。

**③ fork vs spawn(数据:fork 不继承父上下文)**
- `subagent/descriptor.provider`:`fork×6 / spawn×37`。
- **fork 不继承父上下文**:首步 input 中位 12,132 vs spawn 4,696(同量级,不放大);fork 真正差异是**任务形态**(median 87.5 step vs 9;input 占比 0.846;pwsh 67.5%;不再委托 agent_starts=0)。
- → **fork 不做 token 互斥/去重**(父子 token 基本不重叠,相加即规模相加)。

**④ 记账口径(evidence §5 可直接采用)**
- `own_tokens`(每会话自身,权威、可全局加总、不被任何祖先改写)。
- `lineage_descendant_tokens`:**仅 SUBAGENT** 后代的递归 token 合计(聚合观测值,非成本)。
- `lineage_fork_descendant_tokens`:**FORKED_SESSION** 后代的递归 token 合计(可选展示,默认不与上面混算)。
- **嵌套聚合规则**:子会话 token **只归自己**;祖辈只**看子树规模**,但**禁止沿链再加总**(否则 3 层链叶子被 ×3 重复计)。"祖先子树和"在每个节点自身报告里各存一份、只算一次。
- **只报观测,不报成本**:causal_claim=NONE;不把后代 token 并入 wasted/cost。
- **不可解析 → 悬挂节点**:引用不指向本地会话时,记 "unresolvable"、不参与聚合、不伪造父子;报告注明"本机可解析子图内成立"。
- **0-token 子代理**:steps=1/tokens=0 = "已创建未产生 model 调用",记为 `tokens=0` 而非 None 或剔除。

**⑤ 规模事实(影响报告诚实性)**
- 整个 subagent 群体(43 会话)token 仅占全部 42.2M 的 12.3%;真正吞 token 的是大型顶层/forked-session。
- 最大根父纯 subagent 子树只放大 1.5×(3.31M/2.20M),9.1× 是误把 forked-session 当 subagent 造成的。
- → 报告不得把"子树放大倍数"当作"父省子费"的因果证据;只报纯 subagent 观测值。

## Non-Goal

- **不改现有 detector 的 additive 行为**:现有 SUB-001/TOOL-001 等 finding 输出不变;默认路径逐字节不变。
- **不做因果归因**:跨会话关联只报"存在 lineage 边 + 子会话规模观测",不判"父会话因此浪费了多少",`causal_claim=NONE`。
- **不混算 Total wasted tokens**:跨会话传播只做"子会话 token 合计/工具计数"这类直接可观测聚合,不发明"可避免成本"。
- **不虚构 lineage**:引用(如 childId)若解析不到真实会话,则明确标"不可解析",不猜、不伪造父/子关系。
- **保持单一职责**:lineage 解析是分析层观测能力,不塞进现有 detector 体系(除非确需 SUB-001 增强,作为独立 change 论证)。

## Testing

`tests/test_cross_session_lineage.py`:parent→child 边解析、spawn/fork 形态判定、不可解析降级、子会话聚合(token/工具/detector 信号)、additive(现有报告逐字节不变)、确定性、无证据不报。+ 全量回归 + `check_facts`。
