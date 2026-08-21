# Pro 评审报告:change#2 分析层实现(异模型评审)

- 日期:2026-08-22
- 评审模型:DeepSeek V4 Pro(异模型,workflow 指定 provider=deepseek-official / model=deepseek-v4-pro)
- 评审对象:change#2 `complete-analysis-layer` 实现(已归档 `2026-08-22-complete-analysis-layer`)
- 评审范围:14 个必读文件(specs/design/tasks/实现/测试/golden)+ 3 个参考文件,复现验证 2 个边界 bug

## 结论

**有条件通过** → 修复 2 个 Major 后**通过**。

核心铁律(R0 确定性 / R1 置信度方向 / R2 归因边界 / Y2 画像排序键 / kind 解耦 / LLM 预留 / additive)全部落实,无 scope creep、无第 6 个 detector、未发明 token 成本。

## Major(已修复)

### M1:空 findings + `enable_analysis=True` 时综合判断块不渲染
- 证据:`report.py:169-171` 提前 return,`_render_profile_block` 从未执行
- 违反:session-profile spec 的"0 条 → 无可调查项"场景
- 修复:`report.py` 空 findings 分支在开启分析时惰性 `build_profile([], [])` 并渲染空画像块;关闭时保持 v0.5 逐字节一致
- 测试:`test_empty_findings_analysis_renders_no_items`

### M2:TOOL-001 缺 details/occurrence_indexes 时置信度被错误拔高(0.55 → 0.9)
- 证据:`counter_evidence.py` `_raw_args_identical` len<2 返回 True + `_max_adjacent_gap` 返回 0 → 落入高置信分支 `max(conf, 0.9)`
- 违反:归因边界精神("无证据不拔高"保守原则)
- 修复:`_max_adjacent_gap` 证据不足返回 `None` 哨兵;`_raw_args_identical` len<2 返回 False;`_tool_001` 入口显式保守返回原值
- 测试:`test_tool_001_missing_details_keeps_original_confidence`、`test_tool_001_unlocatable_occurrences_keeps_original_confidence`

## Minor(处置)

| 项 | 问题 | 处置 |
|---|---|---|
| 多 occurrence ≥3 混合聚类 | max-adjacent-gap 整条降级,丢失局部粒度 | 注释记录取舍,记为 Open Question(不改逻辑) |
| stateless 短路 | 吞掉"间隔大"反证 | 注释记录决策(design D3 组合场景未定义) |
| threshold_n 非法值 | None→TypeError、≤0→全量降级 | `analyze_finding` 入口校验抛 ValueError + 测试 |
| 0-token cost 排序 | 与 non-cost 同分 0,置信度 tie-break 决定先后 | spec 未定义,补"全 non-cost 排序"测试固化现状 |

## Nit(处置)

| 项 | 问题 | 处置 |
|---|---|---|
| 健康度概述缺"建议优先核查 {top_rule}" | 与 design D4 模板未对齐 | 已补(build_profile 拼接) |
| 硬编码"5 类 detector" | 新增 detector 时文案失效 | 已去数量词硬编码 |
| 同一步多同指纹 call 只取第一个 | 极罕见 | 注释说明 |
| `or 0` 折叠 None/0 | 语义合并 | 注释说明(cost kind 恒为 int) |
| 测试 zip 配对 | 列表错位静默通过 | 加 len 断言 |

## 测试

- 修复前:107 passed(83 + 24)
- 修复后:**114 passed**(+7:空 findings 渲染、缺证据保守 ×2、非法阈值、全 non-cost 排序、默认路径置信度不改写显式断言、开启时报告逐字节确定性)
- golden 逐字节守护(`test_disable_analysis_byte_identical_to_v05`)在修复后仍通过 → 默认路径无回归

## 真实会话 E2E(修复后复验)

会话 `<session-id>`(110 turns / 495 steps / 467 tool_calls):

- 关(默认):纯 v0.5 五段式输出,无分析块
- 开(--analysis):综合判断块 top-3(TOOL-001#3 1544 tokens 置信度 0.50 等)+ per-finding Confidence/Counter-evidence + 健康度概述(含"建议优先核查 TOOL-001")

## golden 机制可靠性(评审特别回答)

方向正确、当前有效,但有 4 点风险:①自指性(无法审计 golden 冻结时间点,建议记录生成 commit);②只覆盖单一 comprehensive trace;③换行/编码脆弱(依赖 autocrlf);④只验证文本不验证对象级不变量。**结论:作为默认输出回归锚点合格,不阻塞;列为后续改进候选。**
