# Design — complete-analysis-layer

## Context

现状(v0.5,83/83 测试全绿):pipeline 为 `Trace → Detector Registry → Finding[] → Attribution Registry → Attribution[] → Report`;Finding 契约见 `agenttrace/detectors/base.py`(已含 `confidence: float` 字段,但报告当前**不渲染** confidence);报告为五段式 + Summary(`agenttrace/report.py`);attribution 输出含 `total_tokens / confidence / kind`(`agenttrace/attribution/base.py`)。

缺口(见 proposal.md — Why):三层评判只实现规则层与统计层,**LLM 语义层未实现**;counter-evidence 设计有但未落地;报告缺会话级综合画像。

硬约束(来自 ARCHITECTURE.md / config.yaml,不可违背):

- 全部 additive,无 breaking;现有 83 测试保持全绿。
- **确定性铁律**:默认行为(不开任何开关)下,输出与现在完全一致。
- 归因边界:没有证据就不做成本归因;反证/置信度是"分析"不是"归因",不得发明 token 成本;`tokens=None` 表示 not applicable。
- correlation ≠ causation;报告禁止 "Total wasted tokens" 式跨 kind 混算。

规格来源:本设计对应 specs(`openspec/changes/complete-analysis-layer/specs/analysis/*/spec.md`)的三个 capability:counter-evidence(含置信度)、session-profile、llm-semantic-layer(仅设计说明)。

## Goals / Non-Goals

**Goals:**

- 为每个 Finding 增加反证列表 `counter_evidence`,由确定性静态规则表生成;置信度在分析层开启时按证据强度完善(高/低置信档 + 反证联动)。
- 报告 Summary 新增"综合判断"块:按"可归因成本 × 置信度"确定性排序,给出最值得调查的 2-3 条 + 一句模板化健康度概述。
- 用一个总开关(`enable_analysis`)门控全部新行为:关闭时 pipeline 与报告输出与 v0.5 逐字节一致(确定性铁律的可执行保证)。
- LLM 语义层只写设计契约与接口占位,本次不实现、不引入依赖、不进 tasks。

**Non-Goals:**

- 不改现有 detector 的检测规则、不改 attribution 引擎的归因算法、不改 pipeline 默认路径、不改报告默认输出。
- 不加第 6/7/8 个 detector;不做 v0.6 Cross-Session Lineage。
- 不实现 LLM 语义层(不写调用代码、不接 provider、不引入硬依赖)。
- 不把反证/置信度换算成任何 token 成本;不跨 kind 求和;不用 confidence 修正成本数字。

## Decisions

### D1. 单一总开关门控全部新行为(确定性铁律的执行机制)

`pipeline.diagnose(trace, ..., enable_analysis: bool = False)` 与 `report.render_report(..., enable_analysis: bool = False)` 新增开关参数,CLI 暴露对应 flag(如 `--analysis`)。开关关闭时,分析阶段整体跳过,报告渲染路径与 v0.5 完全一致(逐字节)。

- **为什么选单一开关而非按能力拆分**:三层能力(反证、置信度、画像)共享同一数据源与渲染入口,拆开关会产生 2³ 组合状态,增加测试面且无用户价值;单开关使"默认 = v0.5"成为一条不变量,可直接用现有 83 测试 + 一个逐字节对比测试守护。
- **备选**:always-on 只加字段不渲染(反证字段常驻、报告默认不显示)。否决理由:反证/置信度属于"分析结论",若常驻生成则内部 Finding 数据变化,且未来任何渲染疏漏都会污染默认输出;门控到渲染与生成两层更干净。
- **边界说明**:`Finding.counter_evidence` 字段本身带默认值常驻数据类(见 D2),但内容为空且无渲染,故不构成行为变化。

### D2. Finding 纯 additive 扩展:反证数据结构 + 置信度沿用

- 新增小 dataclass(放 `agenttrace/detectors/base.py` 或 `agenttrace/analysis/counter_evidence.py`):

  ```python
  @dataclass
  class CounterEvidence:
      direction: str      # 反证方向:"可能推翻此 finding 的证据方向"描述
      source: str = "rule"  # rule(规则层静态生成);未来语义层补充时为 "semantic"
      detail: str = ""      # 可选:支撑该反证的具体观测(如间隔步数)
  ```

- `Finding` 新增字段:`counter_evidence: list[CounterEvidence] = field(default_factory=list)`(纯 additive,默认空列表 → 现有构造/测试不受影响)。
- 置信度:**不新增字段**,沿用现有 `Finding.confidence: float`。当前 detector 已产 confidence(如 TOOL-001:有状态 0.98 / 无状态 0.55),报告本就不渲染它 → 分析层开启时的"完善"只影响内部值与新渲染块,不影响默认输出。
- 置信度语义(写入文档与报告标注):confidence 表示"该 finding 成立的证据强度",**不是**成本金额的可信度;禁止用它修正 attribution 数字。

### D3. 静态反证规则表 + 置信度完善规则(纯函数,确定性)

新建 `agenttrace/analysis/counter_evidence.py`,核心是一张 `rule_id → 纯函数` 表;输入 `(finding, trace)` → 输出 `(list[CounterEvidence], refined_confidence)`。纯函数保证确定性(无随机、无时间、无外部调用),同一 trace 两次运行结果逐条相同(对应 spec 场景)。

规则表(第一版,全部基于 finding 已有 details/evidence 与 trace 可验证观测):

| rule_id | 静态反证(触发条件) | 置信度处理 |
|---|---|---|
| TOOL-001 | (a) 相邻 occurrence 间隔步数 > N(默认 5)→ 反证"间隔大,中间可能有状态变化 → 重复可能是有意操作";(b) 工具在 `STATELESS_TOOLS`(如 web_search/get_time)→ 反证"无状态工具,结果可能随时间变化,重复不必然浪费" | 间隔 ≤ N 且参数完全一致 → 高置信档(≥ 0.9,保持 0.98)且无反证;间隔 > N → 降至低置信档(< 0.6)并附反证(a);无状态工具 → 保持 detector 低置信(0.55)并附反证(b);中间档(间隔 ≤ N 但参数不完全一致)→ 保持 detector 原值,无额外反证 |
| CMP-001 | 反证"压缩可能是长上下文下的必要上下文管理,不构成浪费声明" | 保持 detector 原值 |
| THINK-001 | 反证"推理强度高不证明其不必要(统计标记,非缺陷)" | 保持 detector 原值 |
| RETRY-001 | 反证"重试可能是正确的容错行为,且失败尝试无 token 成本(usage=0)" | 保持 detector 原值 |
| SUB-001 | 反证"委托可能是合理的并行/分工策略" | 保持 detector 原值 |
| (其他/无表项) | 空反证列表 | 保持原值,不改写 |

- 间隔计算基于 `details["occurrence_indexes"]` 的 (turn_id, step_id) 复合定位(turn 内步数 + 跨 turn 位移),沿用 v0.5 已修复的复合 key 约定,不重新发明定位。
- N 为可配置参数(默认 5),实现时用真实 trace inventory 校准(见 Open Questions)。

### D4. 会话画像:排序键与健康度概述(确定性)

新建 `agenttrace/analysis/profile.py`:

- **输入**:分析阶段后的 findings + attributions(attribution 已含 `total_tokens / kind / confidence`)。
- **排序键**(spec: 可归因成本 × 置信度,确定性):
  `score = attributable_cost × confidence`,其中 `attributable_cost = attribution.total_tokens`(仅 `kind == "cost"`),其余 kind 成本维度按 0 计 → 排在有成本 finding 之后。
  tie-break 依次为 `confidence` 降序 → `rule_id` 升序 → `finding_idx` 升序 → 完全确定性。
- **输出**:top ≤ 3 条(不足则按实际条数,0 条标注"无可调查项"),每条含 `rule_id / finding_idx / 可归因成本 / 置信度 / 一句话理由(取自规则表 interpretation + 反证数)`。
- **健康度概述**:确定性模板一句话,例如:
  `"5 类 detector 信号分布(4 类 kind):cost 缺陷 {n_cost} 处(候选可避免 ~{t} tokens)、观测 {n_obs} 处、统计标记 {n_flag} 处、可靠性 {n_rel} 处;反证 {n_ce} 条;建议优先核查 {top_rule}。"`
  成本数字仅取 attribution 的 cost kind 合计(有正成本者排前),标注"候选可避免";措辞禁止"浪费/必然损失"式因果断言。
- 画像模块**不产生任何新 token 数字**,只聚合已有 attribution 输出 → 归因边界不被突破。

### D5. 报告扩展(仅开关开启时渲染)

`report.render_report` 增加 `enable_analysis` 参数:

- five-段式每条 finding 后追加(开关开):`**Confidence:** 0.98 | **Counter-evidence:** 反证方向列表(无则"无反证")`,反证明确标注"反证",与 Evidence 段分离。
- Summary 区在开关开启时追加 `**综合判断**` 块(D4 的 top-3 + 健康度概述)。
- 所有新渲染都在 `if enable_analysis:` 分支内 → 默认输出逐字节不变。

### D6. LLM 语义层:仅接口预留,本次不实现

见 specs `analysis/llm-semantic-layer`。设计契约(供未来实现,本次不写实现代码):

- **接口占位**(仅定义,不注册进 pipeline):`SemanticJudge` protocol,`judge(finding, evidence, counter_evidence) -> SemanticVerdict`;`SemanticVerdict = (judgment, confidence: float, cross_check: CrossCheckResult, source="semantic")`。
- **触发条件**(双重 AND):用户显式开启(`enable_semantic`,默认 False)+ "规则层与统计层均无结论"的确定性判定(如:无任何 finding、或所有 finding 置信度 < 阈值 0.6、或反证与证据冲突无法判定)。
- **确定性守护**:语义输出只**附加**、不覆盖任何确定性 finding/attribution;报告单列"语义层"分区并标注来源;confidence 与规则层置信度不同量纲、禁止混用。
- **cross-check**:独立二次评判(或等价机制)验证一致性;不一致 → 降低 confidence / 标注存疑,不作为确定性结论呈现。
- **本次不实现**:不创建实现代码、不引入 OpenAI 兼容依赖(proposal 中"依赖:语义层开启时需要 LLM 调用通道"仅作未来说明)、不进 tasks。

## Risks / Trade-offs

- [反证阈值 N(默认 5 步)可能不适配真实数据分布] → N 参数化 + 独立测试;实现时用现有 evidence inventory(TOOL-001 重复调用间隔分布)校准,不改 spec/结构。
- ["成本 × 置信度"排序键对非 cost finding 退化为 0 分,可能被误读为"不重要"] → 报告明确标注排序键语义;非 cost finding 在无 cost 时按置信度列出;tie-break 全确定性。
- [Finding 新增字段带来序列化/构造兼容风险] → 全部默认值 additive;验收标准 = 83 测试全绿 + 默认路径逐字节对比测试。
- [对外叙事上"分析层"可能被误读为已实现 LLM] → specs/design/README 统一标注"LLM 语义层:设计预留,本次不实现";tasks 不含该层。
- [置信度被误用为成本可信度] → 文档与报告标注语义;confidence 禁止参与 attribution 计算(测试断言隔离)。

## Migration Plan

- 无 breaking、无数据迁移:所有字段 additive,新行为默认关闭。
- 实施顺序(供 tasks 参考,本次不写 tasks):(1) 加字段 + `enable_analysis` 开关(默认 off,83 测试 + 逐字节对比回归);(2) 静态反证表 + 置信度完善;(3) 画像模块;(4) 报告渲染扩展;(5) 文档同步(ARCHITECTURE.md 三层评判、目标对照文档分析层状态)。
- 回滚策略:开关即总闸——关闭 `enable_analysis` 即回到 v0.5 行为;无 schema/数据变更需要回滚。

## Open Questions

- 反证阈值 N 的最终默认值:5 步是初值,实现时用真实 trace 间隔分布校准(不改变 spec 与结构,可延后)。
- 健康度概述模板是否需要中英双语/可配置措辞(展示维度,可延后)。
- CLI 开关命名(`--analysis` vs `--profile`)与是否同时暴露 `enable_semantic` 预留参数(实现细节,可延后)。
