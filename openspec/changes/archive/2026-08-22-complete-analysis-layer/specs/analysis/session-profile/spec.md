## Purpose

在报告的 Summary 区新增"综合判断"块:汇总 5 类 detector 信号,按"可归因成本 × 置信度"确定性排序,给出该会话最值得调查的 2-3 条与一句会话健康度概述,把零散 finding 合成为会话级分析画像。全部由确定性规则生成,仅分析层开启时出现,不影响现有默认输出。

## ADDED Requirements

### Requirement: Summary 区新增综合判断块

分析层开启时,报告 Summary 区 SHALL 输出"综合判断"块;未开启时 Summary 区 SHALL 保持现状,不出现该块。

- 综合判断块 SHALL 包含:最值得调查的 2-3 条 finding(每条含 rule_id、可归因成本、置信度、简短理由)+ 一句会话健康度概述。
- 排序 SHALL 使用确定性排序键"可归因成本 × 置信度":仅对 kind=cost 的 finding 计算成本维度;无可归因成本的 finding 成本维度按 0 计,排在有成本 finding 之后;并列时 SHALL 依次用置信度、rule_id、finding_idx 打破平局,保证相同输入得到相同排序。
- 综合判断块 SHALL 只使用 attribution 已产出的数字,SHALL NOT 发明或推算 token 成本,SHALL NOT 跨 kind 相加。
- finding 总数不足 3 条时,SHALL 列出实际条数(允许 0-1 条),0 条时 SHALL 注明"无可调查项"。

#### Scenario: 按成本 × 置信度排序

- **WHEN** 分析层开启,且存在多个 finding(含 cost 与非 cost)
- **THEN** 综合判断块按"可归因成本 × 置信度"降序列出最多 3 条,cost finding 排在无可归因成本 finding 之前

#### Scenario: 排序确定性

- **WHEN** 同一 trace 两次运行分析层
- **THEN** 两次综合判断块的内容与顺序逐字节一致

#### Scenario: 无 cost finding 时的处理

- **WHEN** 分析层开启,但没有任何 cost 类 finding
- **THEN** 综合判断块按置信度降序列出非 cost finding(最多 3 条),或注明"无可调查项"

#### Scenario: 默认不出现

- **WHEN** 未开启分析层
- **THEN** 报告 Summary 不包含综合判断块,输出与 v0.5 逐字节一致

### Requirement: 会话健康度概述

综合判断块 SHALL 包含一句会话健康度概述,SHALL 基于 5 类信号分布生成(各类 finding 数量、候选可避免成本合计、反证条目数)。

- 概述 SHALL 由确定性模板生成,SHALL NOT 依赖 LLM 或外部服务。
- 概述 SHALL NOT 断言因果:禁止"该会话浪费了 X tokens"式表述;涉及成本数字时 SHALL 标注"候选可避免"。

#### Scenario: 模板化生成概述

- **WHEN** 分析层开启
- **THEN** 概述为一句话,包含 5 类信号的数量分布与候选可避免成本合计(若有),措辞为模板填充结果

#### Scenario: 概述不越界表述

- **WHEN** 生成的概述包含成本数字
- **THEN** 数字明确标注为"候选可避免",且不出现"浪费/必然损失"式因果断言
