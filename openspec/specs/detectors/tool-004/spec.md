# detectors/tool-004 Specification

## Purpose
新增 detector `TOOL-004 invalid-param-retry`,填补盲区 BL-001:工具调用因**参数错误**(invalid arguments / missing required / invalid_request,或工具调用缺必需参数)失败后,Agent 以**同类调用**重试并成功。这类"无效参数重试"是 Agent 可避免的失败尝试,但现有 5 个 detector 全部漏检——TOOL-001 因两次调用参数不完全一致(缺参)走中间档不判重复,RETRY-001 只认模型层 `llm/retry`,不覆盖工具调用失败后的重试。

本 detector 定位为**候选缺陷模式标记**(非成本缺陷声明):标记"可避免的失败尝试",用证据链定位失败 attempt 与成功重试;因失败 attempt 无 usage,**不估算成本**(tokens=not applicable)。全部由确定性规则生成,默认随 pipeline 运行但不改变现有 detector 行为与默认报告输出。

## Requirements

### Requirement: 识别"参数错误"失败 attempt

detector SHALL 扫描每个 step 的每个 tool_call,判定该次调用是否为"参数错误失败 attempt"。一个 tool_call 命中下列任一条件即 SHALL 被判定为参数错误失败 attempt:

- 该调用 `is_error` 为真,且其 result 文本(小写)包含参数错误关键词之一;或
- 该调用 `is_error` 为真,且其 arguments 为空(空串 / `"{}"` / `"null"`),即"缺必需参数"的确定性代理信号。

- 参数错误关键词 SHALL 由确定性静态集合定义(规则层,非 LLM),至少覆盖 proposal 的三类:invalid arguments、missing required、invalid_request;实现可扩展同族参数错误词(invalid parameter / required parameter / missing argument 等),但 SHALL 保持纯确定性、大小写不敏感的子串匹配。
- 未命中上述条件的调用 SHALL 不被判定为参数错误失败 attempt(非参数类错误、成功调用、无错误调用均不触发)。
- 判定只依赖 Canonical Trace 已建模字段(`ToolCall.is_error` / `ToolCall.result` / `ToolCall.arguments`),SHALL NOT 引入外部 schema 或服务。

#### Scenario: result 命中参数错误关键词

- **WHEN** 某 tool_call 的 `is_error=True`,且其 result 文本包含 "missing required argument"
- **THEN** 该调用被判定为参数错误失败 attempt,记录 error_pattern 为命中的关键词类

#### Scenario: 缺必需参数(空参数 + 错误)

- **WHEN** 某 tool_call 的 `is_error=True`,且其 arguments 为 `"{}"`
- **THEN** 该调用被判定为参数错误失败 attempt,error_pattern 记为 empty_args(缺参代理信号)

#### Scenario: 非参数错误不触发

- **WHEN** 某 tool_call 的 `is_error=True`,但 result 文本为连接超时等非参数错误(不含任何参数错误关键词),且 arguments 非空
- **THEN** 该调用不被判定为参数错误失败 attempt

### Requirement: 识别同类重试成功并标记"可避免的失败尝试"

对每个参数错误失败 attempt,detector SHALL 判定是否存在"同类重试成功",存在时 SHALL 输出一条 finding,标记该失败 attempt 为"可避免的失败尝试"。

- 同类重试成功 SHALL 满足:存在一个**后续**的成功调用(`is_error=False`),且满足其一:(a) 与失败 attempt **同一 callId**(call_id 相等,调用身份同一,重试证据类型 call_id;⚠️ 当前 adapter 每个 call_id 只落一条 `tool/result`,该分支在真实数据下**不可达**,仅合成测试覆盖);或(b) 与失败 attempt **同 tool_name**(同类)且**同一 turn**,并位于失败 attempt 之后、紧随其后的 step(step 位置差 = 1 步,重试证据类型 adjacent_step)。**不配对同 step 内"靠后调用"**(同一 assistant 消息内的多个 tool-call 是并行,不构成"失败后重试",评审 M2);**跨 turn 不配对**(turn 末失败与下一 turn 首成功中间隔着用户新消息,非模型自主重试,评审 M5)。
- 配对 SHALL 只按 tool_name / call_id,**不要求参数一致**(失败 attempt 缺参、重试已补参,参数本就不一致——这正是 TOOL-001 漏检的原因)。
- 每个参数错误失败 attempt SHALL 至多输出一条 finding(occurrences=1);多个失败 attempt 各自成 finding。
- 成功重试的配对 SHALL 取距离失败 attempt 最近的一个;无成功重试的参数错误失败 attempt SHALL NOT 输出 finding。

#### Scenario: 相邻 step 同类成功 → 检出

- **WHEN** step 1 的 tool_call `read_file`(缺参)报参数错误,step 2 的 tool_call `read_file`(补全参数)成功
- **THEN** 输出一条 TOOL-004 finding,retry_evidence=adjacent_step,evidence 定位到 step 1(失败)与 step 2(成功)

#### Scenario: 同一 callId 重试 → 检出

- **WHEN** 某失败 attempt 与一个后续成功调用共享同一 call_id
- **THEN** 输出一条 TOOL-004 finding,retry_evidence=call_id

#### Scenario: 参数不一致不影响配对

- **WHEN** 失败 attempt 参数为 `"{}"`,成功重试参数为 `'{"path":"a.py"}'`(参数不同)
- **THEN** 仍按 tool_name 配对检出 TOOL-004(不要求参数一致)

#### Scenario: 无成功重试不触发

- **WHEN** 某参数错误失败 attempt 之后没有同 tool_name 的成功调用(或最终也失败)
- **THEN** 不为该失败 attempt 输出 TOOL-004 finding

### Requirement: 归因边界——失败 attempt 不估算成本

TOOL-004 的归因 SHALL NOT 估算或虚构任何 token 成本;失败 attempt 的 usage 不存在,tokens SHALL 记为 not applicable(None),不等于 0。

- Attribution 的 direct.tokens / propagated.tokens / unattributed_tokens SHALL 均为 None(not applicable)。
- 报告 SHALL 明确标注"无 token 归因(失败 attempt 无 usage)"或等价表述,SHALL NOT 出现失败 attempt 的 token 数字。
- SHALL NOT 把成功重试的 usage 当作失败 attempt 的成本(重试是必要修正,其 usage 不是"浪费")。
- SHALL NOT 跨 kind 混合计算;报告禁止出现 "Total wasted tokens" 式汇总。

#### Scenario: tokens=not applicable

- **WHEN** 对 TOOL-004 finding 生成 attribution
- **THEN** direct.tokens、propagated.tokens、unattributed_tokens 均为 None(非 0)

#### Scenario: 报告不出现虚构 token

- **WHEN** 渲染 TOOL-004 finding 的 Attribution 段
- **THEN** 输出"无 token 归因(失败 attempt 无 usage)"式表述,不出现任何 token 数值

### Requirement: kind 语义与证据链

TOOL-004 的 Finding 与 Attribution SHALL 遵循现有 kind 契约与证据链抽象,SHALL NOT 新增 kind 枚举值。

- Finding.kind SHALL 为 `flag`:诊断语义 = "候选缺陷模式标记"(可避免的失败尝试),非 cost、非 observation 资源量、非 reliability 事件。
- Attribution.kind SHALL 为 `flag`:证据归因 = 标记"可避免的失败尝试",tokens=not applicable;二者一致不违反"Finding.kind 与 Attribution.kind 解耦"铁律(解耦是"不强制一一对应",允许语义正确时一致)。
- finding 的 evidence SHALL 为公共 EvidenceChain,SHALL 至少含两个 link:失败 attempt(turn/step/error_pattern)与成功重试(turn/step/retry_evidence)。
- severity SHALL 为 low(单个失败 attempt,无成本声明);confidence 由证据强度确定性推导(见下)。

#### Scenario: Finding.kind=flag

- **WHEN** 输出 TOOL-004 finding
- **THEN** `finding.kind == "flag"`,type 为 invalid_param_retry,severity 为 low

#### Scenario: 证据链含失败与成功两个 link

- **WHEN** 输出 TOOL-004 finding
- **THEN** `evidence` 至少含两个 link,分别指向失败 attempt 与成功重试,且 `details["evidence_chain"]` 为同一 EvidenceChain 实例

### Requirement: 确定性置信度与反证(分析层开启时)

TOOL-004 的 confidence SHALL 由证据强度确定性推导,不依赖随机/时间/外部服务。

- retry_evidence=call_id(call 身份同一)→ confidence = 0.95,无反证。
- retry_evidence=adjacent_step 且命中显式参数错误关键词 → confidence = 0.85。
- retry_evidence=adjacent_step 且仅命中 empty_args 代理 → confidence = 0.70。
- 分析层开启时,adjacent_step 证据 SHALL 附带一条反证"相邻同类成功可能是新的独立调用而非重试(无 callId 关联,参数已修正)",置信度保持 detector 原值;call_id 证据 SHALL 无反证。
- 分析层关闭时,counter_evidence SHALL 为空,confidence SHALL 保持 detector 原值。

#### Scenario: 相邻 step 证据附反证

- **WHEN** 分析层开启,且 TOOL-004 finding 的 retry_evidence=adjacent_step
- **THEN** 反证列表含一条"可能是新的独立调用"方向的反证,置信度保持 detector 原值

#### Scenario: call_id 证据无反证

- **WHEN** 分析层开启,且 TOOL-004 finding 的 retry_evidence=call_id
- **THEN** 反证列表为空,置信度为 0.95

#### Scenario: 默认关闭不产生反证

- **WHEN** 未开启分析层运行完整 pipeline
- **THEN** TOOL-004 finding 的 counter_evidence 为空,报告不出现反证内容

### Requirement: 注册、报告集成与 additive 保证

TOOL-004 SHALL 注册进 Detector Registry 与 Attribution Registry,并在报告中呈现;SHALL 为纯新增,不改变现有 5 个 detector 的行为与默认报告输出。

- SHALL 注册进 `agenttrace/detectors/__init__.py::ALL_DETECTORS` 与 `agenttrace/attribution/__init__.py::ALL_ATTRIBUTION_ENGINES`(key "TOOL-004")。
- 报告 SHALL 为 TOOL-004 提供五段式元数据(RULE_META 的 signal/interpretation)与 Observed/Attribution 渲染分支。
- 在**不含任何参数错误调用**的 trace 上(含现有 golden 基线 trace),TOOL-004 SHALL 输出 0 条 finding,默认报告输出与现状逐字节一致。
- 现有 detector 的检测规则、归因算法、默认报告输出 SHALL NOT 被修改。

#### Scenario: Registry 注册

- **WHEN** 遍历 ALL_DETECTORS 与 ALL_ATTRIBUTION_ENGINES
- **THEN** TOOL-004 出现在二者中,且 TOOL-004 能独立跑出 finding 与 attribution

#### Scenario: 报告五段式渲染

- **WHEN** 某 trace 检出 TOOL-004 finding 并渲染报告
- **THEN** 报告含 TOOL-004 的 Signal/Evidence/Observed/Attribution/Interpretation 五段,Attribution 段标注"无 token 归因"

#### Scenario: 默认路径逐字节不变

- **WHEN** 在现有 golden 基线 trace(无参数错误调用)上跑默认 pipeline 并渲染报告
- **THEN** 报告输出与 v0.5 基线逐字节一致,无 TOOL-004 内容

#### Scenario: 现有 detector 行为不变

- **WHEN** 新增 TOOL-004 后在既有 trace 上跑完整 pipeline
- **THEN** 现有 5 个 detector(TOOL/CMP/THINK/RETRY/SUB)各自的 finding 数量、置信度、归因数字与新增前完全一致

### Requirement: 确定性

同一 trace 两次运行 SHALL 产生逐条相同的 TOOL-004 finding 列表(顺序、字段、置信度、反证均一致)。

#### Scenario: 两次运行一致

- **WHEN** 同一 trace 两次运行 detector 与完整 pipeline
- **THEN** 两次的 TOOL-004 finding 列表逐条相同,顺序与字段一致
