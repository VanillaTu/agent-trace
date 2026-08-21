# Tasks — detector-tool-004(无效参数重试)

> 依据:已定稿并通过实现前评审(含 M1–M5/n1 修复)的 `design.md`。目标:新增 `TOOL-004 invalid-param-retry` detector + attribution,注册、报告集成、分析层反证,全部确定性、纯 additive。
>
> **additive 铁律**:不修改现有 5 个 detector 的检测规则、不修改现有 attribution 引擎算法、不改 `pipeline.py`、不改默认报告输出(golden 基线逐字节一致)。唯一"必改既有测试"是 registry 快照测试 + 文档事实同步(命中面更新,非行为变更)。
>
> 验证命令(收尾统一跑):`python -m pytest tests -q` / `python scripts/check_facts.py` / `openspec validate detector-tool-004 --strict`。

## 1. 新增 detector 文件(D1/D2/D4)

- [x] 1.1 新建 `agenttrace/detectors/tool_004.py`,写入模块 docstring,并定义常量与 import:
  - `PARAM_ERROR_KEYWORDS`(design D1 冻结的 11 个关键词,按原顺序):`invalid argument` / `missing required` / `invalid_request` / `invalid request` / `invalid parameter` / `required parameter` / `required argument` / `missing parameter` / `missing argument` / `unexpected argument` / `unexpected keyword`
  - `RETRY_STEP_WINDOW = 1`
  - 从 `..detectors.tool_001` 复用 `STATELESS_TOOLS`(空参代理仅对"非无状态"工具生效)
  - import:`from ..core.canonical_trace import Trace` 与 `from .base import Detector, EvidenceChain, Finding`
- [x] 1.2 在同一文件实现确定性判定辅助函数(全部纯函数、无随机/时间/外部服务):
  - `_is_empty_args(arguments) -> bool`:`None` 或 `strip()` 后等于 `""` / `"{}"` / `"null"` / `"None"` 时返回 True
  - `_match_param_error(tc) -> str | None`:`tc.is_error` 为假返回 None;`(tc.result or "").lower()` 命中 `PARAM_ERROR_KEYWORDS` 之一则返回命中词;否则 `_is_empty_args(tc.arguments)` 且 `tc.tool_name not in STATELESS_TOOLS` 时返回 `"empty_args"`;否则 None
  - 本地 `_build_step_order(trace) -> dict[tuple[int,int], int]`:`(turn_id, step_id) → 全局序号`(与 `analysis/counter_evidence.py` 同约定;**在本文件本地实现,勿 import analysis 层**,避免 detectors→analysis→detectors 循环 import)
- [x] 1.3 实现 `InvalidParamRetryDetector`(`rule_id="TOOL-004"`,`version="0.1.0"`)的 `detect(self, trace) -> list[Finding]`:
  - 遍历 `trace.turns → turn.steps → step.tool_calls`,用 `_match_param_error` 收集"参数错误失败 attempt"E
  - 对每个 E,在**后续**调用中找成功重试 S(`is_error=False`):
    - **call_id 层**:`S.call_id == E.call_id` → `retry_evidence="call_id"`、confidence `0.95`
    - **adjacent_step 层**:`S.tool_name == E.tool_name` 且 `E.turn_id == S.turn_id`(强制同 turn)且 `1 ≤ 全局序号(S) − 全局序号(E) ≤ RETRY_STEP_WINDOW` → `retry_evidence="adjacent_step"`;命中显式关键词 → confidence `0.85`,`empty_args` → `0.70`
    - **硬约束**:不配对同 step 内"靠后调用"(同一 assistant 消息内多 tool-call 是并行,M2);不配对跨 turn(M5);不要求参数一致(只按 tool_name / call_id)
    - 配对取距离 E 最近的 S;每个 E 至多一条 finding(occurrences=1);无成功重试的 E 不输出
- [x] 1.4 在 `detect()` 内构造 Finding(公共 dataclass,不新增字段):
  - `rule_id="TOOL-004"`,`type="invalid_param_retry"`,`severity="low"`,`kind="flag"`,`occurrences=1`,`fingerprint=""`
  - `confidence` 取三档(D1/D2);`evidence` 用 `EvidenceChain` 两个 link(见 D4:失败 attempt `step {E.step_id} (turn {E.turn_id}): {tool_name} 参数错误 {error_pattern}(args={failed_arguments})`;成功重试 `step {S.step_id} (turn {S.turn_id}): 同类重试成功(retry_evidence={...})`,两者 `observed_value=None`)
  - `details` 完整字段(D4):`tool_name` / `error_pattern` / `error_message`(result[:200])/ `failed_arguments`(E.arguments[:200])/ `retry_arguments`(S.arguments[:200])/ `retry_evidence` / `failed_call_id` / `retry_call_id` / `failed_index`((E.turn_id, E.step_id))/ `retry_index`((S.turn_id, S.step_id))/ `retry_step_window` / `evidence_chain`

## 2. 新增 attribution engine(D3)

- [x] 2.1 新建 `agenttrace/attribution/tool_004.py`,实现 `Tool004AttributionEngine`:
  - `attribute(self, trace, findings) -> list[Attribution]`:仅处理 `f.rule_id == "TOOL-004"`,每条 finding 产出一条
  - `kind="flag"`;`direct=DirectAttribution(baseline_step_ids=[], candidate_step_ids=[f.details["failed_index"]], tokens=None)`(candidate 存 `(turn_id, step_id)` 复合键,评审 m2)
  - `propagated=PropagatedAttribution(step_ids=[], tokens=None)`;`unattributed_tokens=None`;`confidence=f.confidence`
  - **铁律**:失败 attempt 无 usage → `tokens=None`(not applicable),三处全 None,不是 0;不把成功重试的 usage 算进失败 attempt

## 3. 注册(D5,零 pipeline 改动)

- [x] 3.1 `agenttrace/detectors/__init__.py`:`from .tool_004 import InvalidParamRetryDetector`,追加到 `ALL_DETECTORS` **末尾**(`SubagentDelegationDetector` 之后)
- [x] 3.2 `agenttrace/attribution/__init__.py`:`from .tool_004 import Tool004AttributionEngine`,`ALL_ATTRIBUTION_ENGINES["TOOL-004"] = Tool004AttributionEngine`
- [x] 3.3 确认 `agenttrace/pipeline.py` **零改动**:registry 遍历自动接新 detector/engine,`finding_idx` 由现有按 rule 分组逻辑赋值;`diagnose` 不出现 `if f.rule_id ==` 分支(守护 `test_pipeline_no_rule_specific_branch`)

## 4. 报告集成(D6,report.py 四处 additive)

- [x] 4.1 `agenttrace/report.py` 的 `RULE_META` 新增 `"TOOL-004"`:
  - `signal = "无效参数重试:工具调用因参数错误失败,同类重试成功"`
  - `interpretation = "模式标记(可避免的失败尝试)——失败 attempt 无 usage,不估算 token 成本;建议核查参数构造逻辑"`
- [x] 4.2 `_observed()` 增加分支:当 `d` 含 `"error_pattern"` 时追加 `tool={d['tool_name']} error={d['error_pattern']} retry={d['retry_evidence']}`
- [x] 4.3 `_attribution_line()` 在默认分支**之前**增加:`if att.kind == "flag": return "无 token 归因(失败 attempt 无 usage,tokens=not applicable)"`(现有没有任何 finding 的 attribution kind 为 flag,此分支只影响 TOOL-004)
- [x] 4.4 Summary 计数:`flag_n = by_rule_count.get("THINK-001", 0) + by_rule_count.get("TOOL-004", 0)`(不改 `KIND_LABELS`,守护逐字节)

## 5. 分析层反证 + 画像(D7)

- [x] 5.1 `agenttrace/analysis/counter_evidence.py` 新增纯函数 `_tool_004(finding, trace, threshold_n)`:
  - `finding.details.get("retry_evidence") == "adjacent_step"` → 返回 `[CounterEvidence(direction="相邻同类成功可能是新的独立调用而非重试(无 callId 关联,参数已修正)", source="rule", detail=f"tool={finding.details.get('tool_name','?')}")]` 与 `finding.confidence`
  - 否则(call_id 身份同一)→ 返回 `([], finding.confidence)`,无反证、置信度保持
- [x] 5.2 在 `RULES` 字典注册 `"TOOL-004": _tool_004`(并把模块头部 docstring 规则表注释补一行 TOOL-004)
- [x] 5.3 `agenttrace/analysis/profile.py` 的 `REASON_BY_RULE` 新增 `"TOOL-004": "无效参数重试(可避免失败尝试标记)"`

## 6. 新增测试 `tests/test_tool_004.py`(D8)

- [x] 6.1 触发规则:构造 `ToolCall(call_id=..., tool_name=..., arguments=..., result=..., is_error=True)` 的 trace,断言——result 命中关键词(至少覆盖 invalid arguments / missing required / invalid_request 三个代表词)→ 检出;空参数(`"{}"`)+ `is_error=True`(非无状态工具)→ 检出且 `error_pattern=="empty_args"`;非参数错误(如连接超时文本)→ 不检出
- [x] 6.2 配对:相邻 step 同类成功 → 检出且 `retry_evidence=="adjacent_step"`;**同 step 靠后成功 → 不检出**(M2 反例);同 `call_id` 后续成功 → 检出且 `retry_evidence=="call_id"`;无成功重试 → 不检出;失败 attempt 参数 `"{}"`、重试参数 `'{"path":"a.py"}'` → 仍按 tool_name 配对检出
- [x] 6.3 归因边界:对检出 finding 跑 attribution 引擎,断言 `direct.tokens is None`、`propagated.tokens is None`、`unattributed_tokens is None`、`total_tokens == 0`(语义 not applicable,非 0)、`attribution.kind == "flag"`
- [x] 6.4 置信度三档:`call_id` → `0.95`;`adjacent_step` + 显式关键词 → `0.85`;`adjacent_step` + `empty_args` → `0.70`;并断言 `0.0 <= confidence <= 1.0`
- [x] 6.5 确定性:同一 trace 两次 `detect()`,断言 TOOL-004 finding 列表逐条相同(顺序、字段、置信度)
- [x] 6.6 反证:`analyze_finding` 对 `adjacent_step` finding 返回 1 条反证且 `confidence` 保持原值;对 `call_id` finding 返回空反证
- [x] 6.7 additive(关键):在 `tests/golden/golden_report.py::build_comprehensive_trace()` 上跑 `diagnose` → TOOL-004 finding 为 0 条,`render_report` 输出与 `tests/golden/v05_baseline_report.md` 逐字节一致;另构造一个含参数错误的 trace,断言其余 5 个 detector 的 finding 数量/置信度/归因数字与新增前一致
- [x] 6.8 contract:断言 TOOL-004 的 Finding / Attribution 走公共 dataclass,`set(f.__dataclass_fields__)` 与既有 Finding / Attribution 字段集一致(不新增字段);报告五段式渲染含 TOOL-004 的 Signal/Evidence/Observed/Attribution/Interpretation 且 Attribution 段出现"无 token 归因"

## 7. 必改既有测试 + 文档事实同步(D8 / Migration Plan)

- [x] 7.1 更新 `tests/test_checkpoint.py::test_registry_has_five_detectors`(唯一"必改"的行为快照测试):改名 `test_registry_has_six_detectors`,断言改为
  - `[d.rule_id for d in ALL_DETECTORS] == ["TOOL-001", "CMP-001", "THINK-001", "RETRY-001", "SUB-001", "TOOL-004"]`
  - `set(ALL_ATTRIBUTION_ENGINES.keys()) == {"TOOL-001", "CMP-001", "THINK-001", "RETRY-001", "SUB-001", "TOOL-004"}`
- [x] 7.2 运行 `python -m pytest tests --collect-only -q`,记录新测试总数(应为 `114 + N`,`N` 为 test_tool_004.py 的用例数),作为后续文档同步的真值
- [x] 7.3 同步 `FACTS.md` Auto 表:`detector 数` `5 个 detector` → `6 个 detector`;`测试总数` `114` → 新总数
- [x] 7.4 同步其余 5 份文档的**测试总数**(把 `114` 类声明改为新总数)与 **detector 谱系**(新增 TOOL-004 行,"五类"→"六类"):
  - `agenttrace/README.md`:测试数、`Detector Registry (5 个)`/`Attribution Registry (5 个)` → 6 个、"五类 detector 谱系"表加 TOOL-004 行
  - `agenttrace/ARCHITECTURE.md`:测试数、Detector Registry 列表加 TOOL-004、"已实现的 Detector"表与"五类语义谱系"表加 TOOL-004 行
  - `PROJECT_STATE.md`:测试数、代码结构加 `tool_004.py`、"五类 detector 谱系"表加 TOOL-004 行
  - `08-团队组织设计.md`:测试数(如 `114/114 全绿`)
  - `09-最初目标对照标注.md`:测试数、"实现 5 个" → "实现 6 个"(在 Taxonomy 现状列补 TOOL-004)
- [x] 7.5 运行 `python scripts/check_facts.py`,按输出的 `文件:行号` 逐处修正剩余不一致,直到 exit 0(注意:只改当前状态文档,不改 `reports/` 与 `openspec/changes/archive/`)

## 8. 回归验证(收尾)

- [x] 8.1 `python -m pytest tests -q` 全绿:既有 113 个测试行为不变 + 1 个 registry 快照测试更新后通过 + 新增 `test_tool_004.py` 全过;确认 `test_disable_analysis_byte_identical_to_v05` 与 `test_pipeline_no_rule_specific_branch` 仍绿(默认路径逐字节 + 零 rule 特判)
- [x] 8.2 `python scripts/check_facts.py` 通过(exit 0)
- [x] 8.3 `openspec validate detector-tool-004 --strict` 通过
