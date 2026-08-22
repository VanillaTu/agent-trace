# AgentTrace 架构文档

> 记录 AgentTrace 的核心架构原则与边界。这是项目从"三个独立脚本"成长为"统一诊断框架"后的正式架构说明。

## 核心定位(2026-08 明确)

- **真正目标**:测 **Harness 架构/工具性**问题——不是工具调用、也不是单纯省 token。
- **Token 是切入点/仪器**:harness 的每个架构决策(记账/compaction/fork/缓存/schema)都会在 token 数字上留下可验证痕迹;用「**Token 不变量**」测架构缺陷。
- **省 Token 是副产品**:架构问题修好后自然省下,不是目的。
- **方法**:复杂系统多因子耦合 → **不归因整体行为,只测局部封闭不变量**(单变量、可验证、不依赖其他因子)。破坏不变量 = 可测的架构硬伤;设计权衡(说"改成怎样更好")是建议、需 hedge,causal_claim=NONE。
- **两层面**:(1) 已实现 = Agent 效率诊断 + Token 归因;(2) 构建中 = Harness 架构不变量检查(见 Roadmap)。

---

## 一、核心架构

```
Raw DSH
   ↓
Adapter (dsh_adapter.py)
   ↓
Canonical Trace (core/canonical_trace.py)
   ├── turns[] → steps[] (tool_calls / usage / reasoning)
   └── events[] (compaction / retry / workflow / subagent / finish ...)
   ↓
Detector Registry (detectors/__init__.py)
   ├── TOOL-001
   ├── CMP-001
   ├── THINK-001
   ├── RETRY-001
   ├── SUB-001
   └── TOOL-004
   ↓
Finding[]
   ↓
Attribution Registry (attribution/__init__.py)
   ↓
Attribution[] (kind: cost / observation / flag / reliability)
   ↓
Analysis (analysis/, Stage 3, enable_analysis=True 时挂载,默认关闭)
   ├── counter-evidence(反证)+ 置信度完善(纯规则)
   ├── session-profile(会话画像:top-3 + 健康度概述)
   └── context-health(上下文健康度观测:CTX-001,会话级数据块,非 detector)
   ↓
Report (report.py)
```

**新增 detector 只需**:写 detector 类 + attribution 类 + 在两个 registry 各注册一行。

**分析层开关**:`enable_analysis` 默认 False——关闭时 pipeline 与报告输出与 v0.5 逐字节一致(确定性铁律)。

## 二、统一 Contract

### Finding(Detector 产出)
```python
rule_id / type / severity / confidence / occurrences
evidence[] / fingerprint / details / estimated_avoidable_tokens
counter_evidence[]  # 反证列表(分析层开启时填充,默认空)
```

### Attribution(Attribution Engine 产出)
```python
finding_id / rule_id / finding_idx / kind
direct / propagated / unattributed_tokens / confidence
```

**铁律**:
- Detector 不负责算钱,Attribution Engine 不负责发现缺陷;
- `finding_idx` 用于 report 精确配对(不串 finding);
- 一个 detector 出错不阻塞其他(错误隔离)。

## 三、⭐ Attribution 的语义边界(最重要原则)

### 核心定义

> **Attribution = 把 Finding 映射回 Trace 中可验证的证据/资源,而不是强制把每个 finding 转换成 token cost。**

### 不是每个执行事件都有可归因的 token cost

| Detector | kind | 语义 | 是否有 token cost |
|---|---|---|---|
| TOOL-001 | cost | 候选可避免成本(direct + propagated) | ✅ |
| CMP-001 | observation | 观测资源量(compaction shadowed) | 观测,不声明 avoidable |
| THINK-001 | flag | 观测强度(reasoning intensity) | 观测,不声明 avoidable |
| RETRY-001 | reliability/observation | 可靠性事件 | ❌ 无(usage=0) |

### ⭐ 关键:usage=0 是 attribution boundary,不是 missing implementation

RETRY-001 的数据发现:失败的 retry attempt 的 `assistant/chunk usage = (0,0)`。

这意味着:
- **"retry 浪费 token"在真实数据里不成立**(失败的尝试在 error 前就中断,不产生计费 token);
- **不能把 retry 次数 × 单价 算成虚构 token 成本**;
- RETRY-001 的价值是**可靠性观测**(RATE_LIMIT→配额问题,TRANSPORT→连接问题),不是 token 节省;
- 若未来某 provider 能可靠关联第二次 attempt usage,才允许 RETRY-001 产生 kind=cost(未来 capability,非当前规则)。

## 四、Attribution.kind 四种语义

```python
kind:
  "cost"          # 候选可避免成本(TOOL-001):direct+propagated
  "observation"   # 观测资源量(CMP-001 shadowed / RETRY-001 事件)
  "flag"          # 观测强度标记(THINK-001 reasoning intensity)
```

report 按 kind 语义分离汇总,**绝不把不同 kind 的 tokens 加成一个 "total wasted"**。

## 五、已实现的 Detector(数据驱动)

| Detector | 规则来源 | 关键数据 | 定位 |
|---|---|---|---|
| TOOL-001 | 重复调用确定性检测 | tool/call + tool/result callId 关联 | cost defect |
| CMP-001 | compaction/prune 硬证据 | `shadowedTokenCount` | hard observation |
| THINK-001 | 56 会话 2042 step 分布 | P95=1498 / P99=3451 | statistical flag |
| RETRY-001 | 56 会话 retry 盘点 | usage=0 → 无虚构 cost | reliability observation |
| SUB-001 | 56 会话 15 descriptor 盘点 | flat delegation,无 lifecycle | execution topology observation |
| TOOL-004 | 参数错误失败 + 同类重试成功(确定性规则) | tool/result isError 文本 + 空参代理 | avoidable-failure flag |

**核心方法论**:规则不是假设,而是由真实数据决定的(每个 detector 都先做 evidence inventory 再写规则)。

### 六类语义谱系

| Detector | Finding.kind | Attribution.kind | tokens |
|---|---|---|---|
| TOOL-001 | cost | cost | 有(候选可避免) |
| CMP-001 | observation | observation | 有(shadowed) |
| THINK-001 | flag | observation | 有(reasoning) |
| RETRY-001 | reliability | reliability | None(usage=0) |
| SUB-001 | observation | observation | None(无成本字段) |
| TOOL-004 | flag | flag | None(失败 attempt 无 usage) |

**关键边界(数据驱动,诚实声明)**:
- SUB-001 真实数据:descriptor 无 outcome/parent/cost 字段 → 只能做 delegation observation,不能重建 topology;
- RETRY-001 真实数据:失败 attempt usage=0 → 无 token 归因;
- 这些是**数据的边界**,不是实现的缺陷。

## 六、分析层(三层评判完整描述)

三层评判的落地现状(规则 → 统计 → 分析,LLM 层设计预留):

| 层 | 实现 | 说明 |
|---|---|---|
| 第一层 确定性规则 | TOOL-001 / RETRY-001 规则检测 | detector 直接出 finding |
| 第二层 统计证据 | THINK-001 分布驱动(P95/P99) | 统计阈值来自真实分布 |
| **分析层(Stage 3)** | **counter-evidence + 置信度完善 + 上下文健康度 + Token 记账不变量 + 跨会话 Lineage + 修复前后 A/B 验证 + 语义候选清单 + 会话画像** | 纯规则,`enable_analysis` 默认关闭 |
| 第三层 LLM 语义 | **候选清单已落地(C1),判定回填由 agent 完成** | LLM 语义层在调用工具的 agent 身上(C1 架构);AgentTrace 只产候选清单 JSON,agent 用自身 LLM 回填 verdict([`source="semantic"`](../../analysis/c1_semantic.py)) |

**分析层八件事(纯规则,无 LLM)**:
1. **反证(counter-evidence)**:每个 finding 附带"可能推翻此发现"的证据方向(TOOL-001 间隔大/无状态工具;CMP/THINK/RETRY/SUB 观测性反证;TOOL-004 adjacent_step 可能是独立调用)。
2. **置信度完善**:沿用 `Finding.confidence`,基于证据强度精化(TOOL-001 间隔≤N 且参数一致→高置信;间隔>N→降置信+反证;无状态→保持 0.55+反证)。
3. **会话画像**:Summary 新增"综合判断"块,按"可归因成本 × 置信度"确定性排序 top-3 + 一句话健康度概述。
4. **上下文健康度(CTX-001)**:Summary 新增"上下文健康度"块(会话级观测数据块,非 detector/finding)——当前/峰值上下文 tokens(含 cache_read,M1 口径)、turn 数、重复工具调用操作率;量化"上下文压力"仅在 `metadata["context_window"]` 真实已知时给出,否则显示 not applicable(不虚构窗口、不产虚假压力结论);不判因果、不做成本归因。
5. **Token 记账不变量(A1)**:Summary 新增"架构不变量检查"块(会话级观测数据块,非 detector/finding)——adapter 解析时按 (turn,step) 收集 chunk/message 两份 usage,all-pairs 一致则发 `token/usage-duplicate`、不一致则发 `token/usage-inconsistent`;数据块统计双写步数、"非去重消费方的假设性溢出上界"(`naive_double_count_tokens`)、双写子集内乘数(恒 2.0)与全局稀释因子;`causal_claim=NONE`,不判 harness bug、不混算 wasted。
6. **跨会话 Lineage(A2)**:Summary 新增"跨会话 Lineage"块(会话级观测数据块,非 detector/finding)——adapter 提取 session 头 `parentSession`/`origin`/`delegationDepth`;沿 `header.parentSession` 权威边构建血缘图,递归聚合 SUBAGENT(`lineage_descendant_*`,仅 origin=subagent)与 FORKED_SESSION(`lineage_fork_descendant_*`,独立层)子代;子会话 token 只归自己、禁止沿链再加总;`causal_claim=NONE`,不判因果、不做成本归因;不可解析 → 悬挂节点,报告注明"本机可解析子图内成立"。
7. **修复前后 A/B 验证(B1)**:Summary 新增"A/B 验证"块(会话级观测数据块,非 detector/finding)——对单个会话构建 original(全量)与 fixed(去掉 TOOL-001 重复调用 / TOOL-004 失败尝试)两种**静态反事实重述**,量化 tool-call 下降、删 step 数、output token 下降;复用 TOOL-001/TOOL-004 检出逻辑(不改其行为),语义隔离(轮询型 `SEMANTIC_DEBATED_TOOLS` 不计入硬可省)、retry 严格分开(工具级 vs llm/retry)、`causal_claim=NONE`;不把 input token 当省(仅 output 是可信子指标);配套固定验证集(5 会话)与 fixture 化回归。
8. **语义候选清单(C1)**:Summary 新增"语义判断"块(候选清单层,非 detector/finding)——LLM 语义层**在调用工具的 agent 身上**(用户两次澄清):AgentTrace 是确定性工具,从 TOOL-001/TOOL-004 finding 生成候选清单 JSON(每候选附判断上下文:前后 step / 干预动作 / 工具结果前缀比较 / 轮询型 debated 置前),agent 用自身 LLM 回填 verdict(真冗余/合法/不确定 + 置信度 + 理由)。`causal_claim=NONE`,verdict 是语义建议非硬断言、不改变硬可省数字;未回填时 verdict=not_applicable 不猜;AgentTrace 不内置 LLM 调用、不做 DSH 插件。

**分析层边界铁律**:
- 反证/置信度是"分析"不是"归因",不参与成本归因、不发明 token 成本;
- 排序键用 `Finding.confidence`(精化后),不用 attribution 拷贝值;
- 阈值 N=5 由真实分布校准(76 会话 131 条 TOOL-001,gap 中位数 13,仅 24% ≤5 → 反证机制实际在起作用)。

## 七、测试

- 328 个 pytest 全过:TOOL-001×26 / CMP-001×7 / THINK-001×9 / RETRY-001×9 / SUB-001×8 / TOOL-004×35 / 分析层×31 / 归因×8 / registry 快照×10 / v0.3 checkpoint×6 / adapter×5 / CLI×8 / pipeline×6 / recommendation×7 / CTX-001×21 / token-invariant×20 / session-lineage×32 / ab-validation×32 / c1-semantic×48
- 覆盖:Golden Trace / Precision/Recall 基线 / lifecycle / outcome / zero-usage / contract 兼容 / 错误隔离 / 缺失字段 / 反证规则 / 置信度完善 / 画像排序 / 上下文健康度观测 / Token 记账不变量 / 跨会话 Lineage / A/B 验证(修复前后对比)/ 语义候选清单(候选生成/上下文/回填合并)/ 开关门控 / 默认路径逐字节对比

## 八、Roadmap

```
✅ TOOL-001      cost defect
✅ CMP-001       hard observation
✅ THINK-001     statistical flag
✅ RETRY-001     reliability event + no cost
✅ SUB-001       execution topology
✅ TOOL-004      invalid-param retry flag(可避免失败尝试标记)
✅ v0.3          semantic/architecture checkpoint
✅ 分析层         counter-evidence + 置信度 + 上下文健康度 + 会话画像(纯规则)
✅ 建议维         Recommendation(分析层,补全四元组"建议";守归因边界,默认关闭)
✅ CTX-001       上下文健康度数据块(会话级观测,非 detector;窗口真实已知才判压力)
✅ CLI 会话发现    list-sessions + analyze --session-id(免手输目录)
⏳ LLM 语义层     设计预留,未实现(默认关闭保确定性)
⏳ v0.6          Cross-Session Lineage
⏳ 架构评估层      Token 不变量检查(双写 / fork 溢出 / compaction 漏计 / 缓存口径 / schema)——测 Harness 架构/工具性
⏳ CDSA           (paper arXiv:2511.10650)循环/无进展检测——FLOW 候选,默认关语义聚类,未实现
```

## 九、参考论文技法(候选,未实现)

> 从论文/竞品提炼、暂未落地为实现的技法,先固化到文档防止遗忘、也防止"参考过却没实现"的失真。
> 统一原则:守确定性(默认关 / 确定性代理)、`causal_claim=NONE`、不归因整体行为。

### CDSA — 循环/无进展检测(FLOW 候选)

**来源**:IBM Research, arXiv:2511.10650《Unsupervised Cycle Detection in Agentic Applications》(ACM/SPEC ICPE 2026 WIP)。

**方法**(轨迹 = span 元组 ⟨trace_id, span_id, parent_span_id, op, input, output⟩):
- **CDDAG(结构)**:父-子 DAG,边权重 = 该父子对出现频次,`> μ+m·σ` 判环。
- **CDCS(结构/调用栈)**:时间序列排序 + 滑窗找连续子序列,频次 `> μ+k·σ` 判环。
- **CDSA(语义)**:span output 向量余弦相似度,只比兄弟节点(降复杂度),`> 阈值 s` 判冗余。
- **Hybrid**:结构 + 语义融合,抓"单独任一维都抓不全"的环。

**结果**(1575 条 LangGraph 股票应用轨迹):Hybrid **F1=0.72**(P=0.62, R=0.86);结构 only F1=0.08,语义 only F1=0.28 → **多信号融合远超单信号**。⚠️ 注意 **P=0.62(38% 误报)**,作者自标 WIP、未达生产级。

**与 AgentTrace 的关系**:
- 我们 **SUB-001 是 flat、无 parent_span_id 层级** → 缺论文的 span 层级建模。
- 我们三层评判的**语义层默认关**;L2 是统计(THINK 分位),**未做语义聚类** → 这是 05 文档"你的 L2 应引入语义相似度聚类"的来源,但未落地。
- 我们守 `correlation≠causation`、`causal_claim=NONE`,比它**保守**(它 P=0.62 会误报;我们宁可标 observation/flag,不给因果)。

**落点(候选)**:作为 **FLOW 类别**候选信号,**默认关闭**的分析标记。守确定性 → 用**确定性相似度代理**(minhash / token-set Jaccard)而非 LLM 嵌入;classification=flag/observation,`causal_claim=NONE`。**未实现。**
