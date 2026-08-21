## 1. 数据结构扩展(纯 additive)

- [x] 1.1 定义 `CounterEvidence` dataclass(direction / source / detail,全部带默认值)
- [x] 1.2 `Finding` 增加 `counter_evidence: list[CounterEvidence] = field(default_factory=list)`,不破坏现有构造
- [x] 1.3 确认现有 83 测试的 Finding 构造不受新字段影响(跑一遍全量)

## 2. 分析层挂载 + 总开关(确定性铁律)

- [x] 2.1 新增总开关 `enable_analysis`(默认 False),贯穿 pipeline 与 report 双层
- [x] 2.2 pipeline:开关关闭时分析阶段整体跳过(反证为空、置信度不改写)
- [x] 2.3 分析阶段挂载在 attribution(Stage 2)**之后**:画像依赖 attribution 输出,反证/置信度只依赖 findings;明确 Stage 3 位置
- [x] 2.4 report:开关关闭时不渲染"综合判断块",输出与 v0.5 逐字节一致
- [x] 2.5 默认路径逐字节对比测试:同一 trace,开关关闭的输出 == v0.5 基线输出(先固化 v0.5 golden 基线文件作锚点)

## 3. 反证 + 置信度静态规则表(纯函数,无 LLM)

- [x] 3.1 实现 `rule_id → 纯函数` 规则表:输入 Finding + Trace 上下文,输出 (反证列表, 置信度调整)
- [x] 3.2 TOOL-001 规则(注意方向,已由 Pro 评审纠正):**有状态工具**(edit 类,会改变状态)参数完全一致且间隔 ≤ 阈值 → 高置信(≥0.9)无反证;间隔 > N 步 → 降置信并附反证("间隔大,可能是有意操作");**无状态工具**(read/list 类,不改变状态)→ 保持低置信 0.55 + 附反证("无状态工具,可能是有意复查")。参照现有 `tool_001.py` L70-71(有状态 0.98 / 无状态 0.55)
- [x] 3.3 抽取/复用 `STATELESS_TOOLS` 集合定义(现存在于 tool_001.py 内部,分析层需要共享)
- [x] 3.4 CMP/THINK/RETRY/SUB 四类:生成观测性反证(说明"此为观测,非结论"),置信度保持原值
- [x] 3.5 反证阈值 N 初值 5 步;用真实 trace 的间隔分布校准(Open Question,不改变 spec/结构)
- [x] 3.6 规则表全部为纯函数:无随机/时间/外部调用,同一 trace 两次运行结果逐条一致

## 4. 会话级综合画像

- [x] 4.1 实现画像聚合:按"可归因成本 × 置信度"排序(置信度用 **Finding.confidence 精化后的值**,非 attribution 拷贝值——Y2 修复,避免双置信度),非 cost 维度按 0 计、排后
- [x] 4.2 全确定性 tie-break:置信度 → rule_id → finding_idx
- [x] 4.3 健康度概述:模板化一句话,禁止因果断言;成本数字只聚合 attribution 已有输出、标注"候选可避免"
- [x] 4.4 report Summary 渲染"综合判断块"(仅开关开启时):最值得调查的 2-3 条 + 健康度概述
- [x] 4.5 **per-finding 渲染(仅开关开启时)**:五段式每条 finding 后追加 `Confidence` + `Counter-evidence` 两行(Y3 补充,spec R3 / design D5 要求)

## 5. 测试

- [x] 5.1 现有 83 测试全绿(确定性铁律回归)
- [x] 5.2 新增反证规则测试(TOOL-001 间隔大/小、无状态工具、四类观测性反证)
- [x] 5.3 新增置信度调整测试(降置信场景 + 保持原值场景)
- [x] 5.4 新增画像排序测试(成本×置信度排序、tie-break、非 cost 排后)
- [x] 5.5 新增开关门控测试(开启时新行为生效,关闭时逐字节一致)
- [x] 5.6 真实会话 E2E:开关开启跑一次真实 trace,人工核验反证/置信度/画像合理性

## 6. 收尾

- [x] 6.1 `agenttrace/ARCHITECTURE.md` 更新三层评判完整描述(规则+统计+分析层,LLM 标注设计预留)
- [x] 6.2 `09-最初目标对照标注.md` 更新"分析层"状态(部分→完成)
- [x] 6.3 `openspec validate complete-analysis-layer --strict` 通过
- [x] 6.4 汇报:改动文件、测试数(83+N)、真实 E2E 结果、Open Question(阈值 N)结论
- [x] 6.5 `openspec archive complete-analysis-layer` 归档
