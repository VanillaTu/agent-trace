# Design — detector-tool-004

## Context

现状(v0.5,~114 测试全绿):pipeline 为 `Trace → Detector Registry → Finding[] → Attribution Registry → Attribution[] → Report`;Finding/Attribution 契约见 `agenttrace/detectors/base.py` 与 `agenttrace/attribution/base.py`;报告为五段式 + Summary(`agenttrace/report.py`)。

盲区(见 proposal.md — Why):"工具调用缺参报错 → 同类重试成功"(无效参数重试)是最初设计 05 文档 L558 定义过、但一直未实现的 TOOL 因子。现有 5 个 detector 全部漏检:

- TOOL-001 按 `call_fingerprint(tool_name, arguments)` 分组;失败 attempt 缺参、重试补参,两次参数**不完全一致** → 归一化后 fingerprint 不同 → 不判重复。
- RETRY-001 只消费 `Trace.events` 里的模型层 `llm/retry*` 事件,不消费 `Step.tool_calls` 的 `is_error`/`result` → 工具调用失败后的重试完全不可见。

Canonical Trace 已具备检测所需的全部字段:`ToolCall.call_id / tool_name / arguments / result / is_error`(`agenttrace/core/canonical_trace.py`),adapter 已把 `tool/result` 的 `isError` 与 result 文本落到 `ToolCall.is_error / result`(`agenttrace/adapters/dsh_adapter.py` L206-230)。因此本 change 只需新增 detector + attribution + 注册 + 报告整合,无需改数据模型。

硬约束(不可违背):

- 归因边界:没有证据就不做成本归因;失败 attempt 无 usage → `tokens=None`(not applicable),不是 0。
- Finding.kind(诊断语义)与 Attribution.kind(证据归因)解耦,不强制一一对应。
- 三层评判:确定性规则 → 统计证据 → LLM 语义(LLM 层未实现,off by default);本 change 只落在**规则层**。
- additive:新增 detector 不改变现有 detector 行为,现有测试全绿保持。
- correlation ≠ causation;报告禁止 "Total wasted tokens" 混算。

## Goals / Non-Goals

**Goals:**

- 新增 `TOOL-004 invalid-param-retry`:识别"参数错误失败 attempt + 同类重试成功",输出 `kind=flag` 的"可避免失败尝试"标记 finding。
- 归因 `kind=flag`、`tokens=None`(not applicable),**不估算成本**;证据链定位失败与成功两个位置。
- 注册进 Detector Registry + Attribution Registry + 报告集成,全确定性、additive。
- 分析层开启时为 adjacent_step 证据补一条反证(counter-evidence),保持与现有分析层契约一致。

**Non-Goals:**

- 不改现有 5 个 detector 的检测规则、不改 attribution 引擎算法、不改 pipeline 默认路径、不改报告默认输出。
- 不估算失败 attempt 的 token 成本;不把成功重试的 usage 算成浪费。
- 不引入工具参数 schema(无法从 trace 判定"哪些参数是必需"——只用空参数作为确定性代理)。
- 不实现 LLM 语义层;不新增 FindingKind/Attribution kind 枚举值。

## Decisions

### D1. 触发规则:is_error + 参数错误关键词 / 空参数代理(规则层,确定性)

一个 `ToolCall` 是"参数错误失败 attempt"当且仅当:

1. `tc.is_error is True` 且 `result` 文本(小写)命中 `PARAM_ERROR_KEYWORDS`;或
2. `tc.is_error is True` 且 `arguments` 为空(空串 / `"{}"` / `"null"`)。

```python
# detectors/tool_004.py
PARAM_ERROR_KEYWORDS = (
    "invalid argument",      # proposal 显式:invalid arguments
    "missing required",      # proposal 显式:missing required (argument/parameter/field)
    "invalid_request",       # proposal 显式:invalid_request / invalid_request_error
    "invalid request",       # 同族:invalid request error
    "invalid parameter",
    "required parameter",
    "required argument",
    "missing parameter",
    "missing argument",
    "unexpected argument",
    "unexpected keyword",
)

def _is_empty_args(arguments: str) -> bool:
    return arguments is None or arguments.strip() in ("", "{}", "null", "None")

def _match_param_error(tc) -> str | None:
    if not tc.is_error:
        return None
    text = (tc.result or "").lower()
    for kw in PARAM_ERROR_KEYWORDS:
        if kw in text:
            return kw
    # 空参代理(评审 M3 修复):仅当工具"非无状态"时作为参数错误候选。
    # 无状态工具(STATELESS_TOOLS,如 get_current_time/web_search)合法恒为空参,
    # 若因网络/配额等非参数原因报 is_error,不应误判为"无效参数"。
    if _is_empty_args(tc.arguments) and tc.tool_name not in STATELESS_TOOLS:
        return "empty_args"
    return None
```

- **为什么用子串匹配而非正则/LLM**:规则层铁律要求纯确定性、可审计、零依赖;关键词集合是 proposal 三类触发词的最小超集,大小写不敏感子串即可覆盖 `invalid_request_error` / `Missing required argument: ...` 等真实形态。
- **真实样本锚点(评审 M1,数据驱动)**:BL-001 证据链的真实 `tool/result` 文本为 `Error: invalid arguments: missing required property "text"` → **"invalid arguments" / "missing required" 是已核验关键词**;其余为 proposal 同族的最小扩展。`PARAM_ERROR_KEYWORDS` 冻结为上述三类(invalid arguments / missing required / invalid_request)+ 同族大小写变体,**不做无证据的凭空扩词**;任何扩展需先用真实 `tool/result` 错误文本样本核验后加入(实现前数据核验 gate)。置信度三档(0.95/0.85/0.70)与 `RETRY_STEP_WINDOW`(1)为可配置常量,待真实 BL-001 证据链的 step-gap 分布校准,列入实现前核验项(见 Open Questions)。
- **为什么"缺必需参数"用空参数代理**:Canonical Trace 无参数 schema,无法判定"缺了哪个必需参数";`arguments` 为空 + `is_error` 是唯一可验证的确定性代理。**评审 M3 修复**:无状态工具(`STATELESS_TOOLS` 复用 `tool_001.py`)合法恒为空参,若因非参数原因报错(网络/上游 5xx)会被误判——故空参代理仅对"非无状态"工具生效,且命中记 `error_pattern="empty_args"`、置信度降档(D3),避免过度声称。显式参数关键词命中(1 类)不限工具。
- **为什么 `is_error` 是必要条件**:`result` 文本可能含 "invalid" 字样但调用成功(如工具返回校验说明);`is_error=True` 才能断言这是失败。这排除了成功结果文本误报。
- **为什么非参数错误不触发**:连接超时/权限/上游 5xx 属 RETRY-001 的可靠性范畴或他类问题,不属于"无效参数重试",硬隔离避免 detector 语义重叠。

### D2. 重试配对:call_id 与 adjacent_step 双证据层,按 tool_name 而非 fingerprint

对每个参数错误失败 attempt E,在**后续**调用中找成功重试 S(`is_error=False`):

- **call_id 层**:`S.call_id == E.call_id`(调用身份同一)→ `retry_evidence="call_id"`,confidence 0.95。⚠️ 当前 adapter 每个 call_id 只落一条 `tool/result`,同 call_id 重试**在真实数据下不可达**,仅合成测试覆盖(评审 n1)。
- **adjacent_step 层**:`S.tool_name == E.tool_name` 且 `E.turn_id == S.turn_id`(强制**同 turn**,评审 M5)且 `1 ≤ step_pos(S) − step_pos(E) ≤ RETRY_STEP_WINDOW`(默认 1)→ `retry_evidence="adjacent_step"`。**不配对同 step 内"靠后调用"**(同一条 assistant 消息的多个 tool-call 是并行调用,模型看不到前一个 call 的结果,不可能构成"失败后重试",评审 M2)。

```python
RETRY_STEP_WINDOW = 1  # "相邻 step" 的确定性落点;可配置

def _build_step_order(trace) -> dict[tuple[int,int], int]:
    # 复用 counter_evidence.py 的复合 (turn_id, step_id) → 全局序号约定
    ...
```

- **为什么按 tool_name 配对、不按 fingerprint**:TOOL-004 的语义恰恰是"缺参→补参重试",两次调用参数必然不一致,fingerprint 一定不同;这正是 TOOL-001 漏检的根因。按 tool_name("同类")配对是本 detector 与 TOOL-001 的本质区别。
- **为什么 `RETRY_STEP_WINDOW=1`**:proposal 明确"相邻 step";窗口=1 是"宁可漏报不误报"的保守默认。**强制同 turn** + 窗口=1 使配对严格限制在"同一 turn 内紧随其后的 step",跨 turn 边界不计(M5 修复:turn 末失败 + 下一 turn 首成功中间隔着用户新消息,不是模型"看到失败后自主修正")。
- **为什么不配对同 step 靠后调用**:同一条 assistant 消息内多个 tool-call 是并行/批量,模型没看到第一个 call 的结果就发出了第二个,不可能是"失败后重试"——配对它会产生语义性假阳性(M2 修复)。
- **为什么 call_id 层单独成档**:call 身份同一是最硬的证据,无需依赖"同类+相邻"推断,置信度理应更高;它也是防御性分支——现 adapter 每个 call_id 只落一条 `tool/result`,同 call_id 的重试在多数日志里不会出现,但平台重发/复用时存在,spec 完整性要求覆盖。
- **为什么每个失败 attempt 一条 finding、occurrences=1**:失败 attempt 是独立的可避免失败单元;多失败→一成功的链式场景里,只有最靠近成功的那个失败 attempt 落在窗口内被标记(保守),其余不标记。这与 RETRY-001 每条 retryId 一条 finding 的粒度一致。
- **为什么不把成功重试的 usage 算进成本**:重试是"必要修正",其成功调用不是浪费;归因只标记失败 attempt 的"可避免性",不碰任何 usage(D3)。

### D3. 归因边界与 kind 语义:Finding=flag,Attribution=flag,tokens=None

- **Finding.kind = `flag`**:四个既有 kind 中,cost 要求可估 token(本 change 明确不做),observation 是"观测资源量"(CMP shadowed / SUB 拓扑),reliability 专指模型层 retry。TOOL-004 是"候选缺陷模式标记",落在 flag 桶最诚实。`type="invalid_param_retry"`,`severity="low"`。
- **Attribution.kind = `flag`**:归因到的"东西"就是"可避免失败尝试"这个标记本身,无资源/成本可归因,与 Finding 语义一致;二者一致不违反"解耦"铁律(解耦是"不强制绑定",非"必须不一致",RETRY-001 已是 reliability↔reliability 的先例)。
- **tokens=None 三处**(`direct.tokens / propagated.tokens / unattributed_tokens`):失败 attempt 无 usage → not applicable,不是 0。这是铁律的直接落地,与 RETRY-001 完全同构。

```python
# attribution/tool_004.py
Attribution(
    finding_id=f"finding-{f.rule_id}-{idx}",
    rule_id=f.rule_id, finding_idx=idx, kind="flag",
    direct=DirectAttribution(
        baseline_step_ids=[],            # 无 baseline 概念
        candidate_step_ids=[f.details["failed_index"]],            # 失败 attempt (turn_id,step_id) 复合键(评审 m2)
        tokens=None,
    ),
    propagated=PropagatedAttribution(step_ids=[], tokens=None),
    unattributed_tokens=None,
    confidence=f.confidence,
)
```

- **为什么 confidence 三档(0.95 / 0.85 / 0.70)**:call_id=0.95(call 身份同一,证据最硬);adjacent_step+显式关键词=0.85(有明确参数错误措辞);adjacent_step+empty_args=0.70(仅空参数代理,可能被误读为其它错误)。三档纯由证据类型推导,确定性、可复现。
- **为什么 `candidate_step_ids` 存复合 key 而非裸 step_id(评审 m2)**:`step_id` 每 turn 重新编号,裸 int 跨 turn 有歧义(TOOL-001 attribution 曾踩过真实 bug)。与 TOOL-001 一致存 `(turn_id, step_id)` 复合键;tokens=None 下仅信息性,但避免把已知反模式引入新 engine。精确定位仍在 `details["failed_index"]/["retry_index"]` 与 evidence。

### D4. 数据结构:details 契约与证据链

```python
Finding(
    rule_id="TOOL-004",
    type="invalid_param_retry",
    severity="low",
    confidence=conf,          # D3 三档
    occurrences=1,
    kind="flag",
    evidence=chain.links,     # 2 links:失败 + 成功
    fingerprint="",           # 无稳定指纹(参数不一致)
    details={
        "tool_name": name,
        "error_pattern": pattern,          # 命中关键词 或 "empty_args"
        "error_message": result_text[:200],
        "failed_arguments": E.arguments[:200],
        "retry_arguments": S.arguments[:200],
        "retry_evidence": "call_id" | "adjacent_step",
        "failed_call_id": E.call_id,
        "retry_call_id": S.call_id,
        "failed_index": (E.turn_id, E.step_id),
        "retry_index": (S.turn_id, S.step_id),
        "retry_step_window": RETRY_STEP_WINDOW,
        "evidence_chain": chain,
    },
)
```

证据链两个 link:失败 attempt `step {E.step_id} (turn {E.turn_id}): {tool_name} 参数错误 {error_pattern}(args={failed_arguments})`;成功重试 `step {S.step_id} (turn {S.turn_id}): 同类重试成功(retry_evidence={...})`。`observed_value` 均为 None(无资源量可观测)。

- **为什么 `details` 同时保留 failed/retry 两侧的 call_id、index、arguments**:报告与后续审计要能独立定位两个位置;error_message/arguments 截断到 200 字符,避免长结果撑爆报告。
- **为什么 `fingerprint=""`**:TOOL-004 不按 fingerprint 分组,字段留空表示"不适用",不伪造指纹。

### D5. 注册:Registry 驱动,零 pipeline 改动

- `agenttrace/detectors/__init__.py`:`from .tool_004 import InvalidParamRetryDetector`,追加进 `ALL_DETECTORS`。
- `agenttrace/attribution/__init__.py`:`from .tool_004 import Tool004AttributionEngine`,`ALL_ATTRIBUTION_ENGINES["TOOL-004"] = Tool004AttributionEngine`。
- pipeline(`agenttrace/pipeline.py`)**零改动**:registry 遍历自动接新 detector/engine;`finding_idx` 由现有逻辑按 rule 分组赋值,无需特判。

- **为什么零 pipeline 改动**:`test_pipeline_no_rule_specific_branch` 断言 `diagnose` 无 `if f.rule_id ==` 分支;新 detector 走纯 registry 注入,不引入任何 rule 特判,守护住架构 checkpoint。

### D6. 报告集成:五段式 + 无 token 归因分支 + summary 计数

`agenttrace/report.py` 四处 additive 修改:

1. `RULE_META["TOOL-004"]`(signal/interpretation):
   - signal = `无效参数重试:工具调用因参数错误失败,同类重试成功`
   - interpretation = `模式标记(可避免的失败尝试)——失败 attempt 无 usage,不估算 token 成本;建议核查参数构造逻辑`
2. `_observed` 增加分支:details 含 `error_pattern` 时输出 `tool={tool_name} error={error_pattern} retry={retry_evidence}`。
3. `_attribution_line` 在默认分支前增加 `if att.kind == "flag": return "无 token 归因(失败 attempt 无 usage,tokens=not applicable)"`。
4. summary 计数:`flag_n = by_rule_count.get("THINK-001", 0) + by_rule_count.get("TOOL-004", 0)`。

- **为什么 `_attribution_line` 需要 flag 分支**:现有 THINK-001 的 attribution kind 是 `observation`(归因到 reasoningTokens),当前**没有任何** finding 的 attribution kind 是 `flag`;不补分支的话 TOOL-004 会落入默认分支渲染成 `观测资源 0 tokens(非 avoidable)`,既错(0≠not applicable)又误导。该分支只影响 TOOL-004,不影响既有输出。
- **为什么 summary 把 TOOL-004 计入 flag 计数**:flag 行语义是"统计/模式标记"数量,TOOL-004 属此桶;golden 基线 trace 无 TOOL-004 finding,故该行在默认路径逐字节不变。
- **为什么不改 `KIND_LABELS`(仍是 "Statistical flags / 统计强度标记")**:改标签会破坏 `test_disable_analysis_byte_identical_to_v05` 的逐字节对比。TOOL-004 finding 归入 flag 节、以 RULE_META 的专属 signal/interpretation 区分语义;章节标题为 v0.5 遗留措辞,不构成契约,记入 Open Questions 供未来统一措辞。

### D7. 分析层反证:adjacent_step 附反证,call_id 无反证

`agenttrace/analysis/counter_evidence.py` 增加 `_tool_004` 纯函数并注册进 `RULES`:

```python
def _tool_004(finding, trace, threshold_n):
    if finding.details.get("retry_evidence") == "adjacent_step":
        return [CounterEvidence(
            direction="相邻同类成功可能是新的独立调用而非重试(无 callId 关联,参数已修正)",
            source="rule",
            detail=f"tool={finding.details.get('tool_name','?')}",
        )], finding.confidence
    return [], finding.confidence  # call_id:身份同一,无反证
```

`agenttrace/analysis/profile.py` 的 `REASON_BY_RULE["TOOL-004"] = "无效参数重试(可避免失败尝试标记)"`。

- **为什么 adjacent_step 反证、call_id 无反证**:adjacent_step 是"同类+相邻"推断,存在"成功调用其实是一次新的独立调用"的推翻方向,必须显式列出;call_id 是 call 身份同一的直接证据,无可推翻方向。置信度**保持** detector 原值(与 RETRY/CMP/SUB 等观测性规则"附反证、保置信"的既有模式一致),因为 detector 已在 confidence 三档中折价(0.85/0.70)。
- **为什么只动这两处且都在 `enable_analysis` 门控内**:分析层默认关闭;关闭时 `refine_findings` 不被调用,TOOL-004 的 counter_evidence 恒为空,默认输出不变。

### D8. 测试策略与唯一必改的既有测试

新增 `tests/test_tool_004.py`,覆盖 spec 全部场景(构造 Trace 直接用 `ToolCall(call_id=..., tool_name=..., arguments=..., result=..., is_error=...)`):

1. **触发**:result 命中各关键词 → 检出;空参数+is_error → 检出(empty_args);非参数错误 → 不检出。
2. **配对**:相邻 step(同 turn)同类成功 → 检出;同 step 靠后同类成功 → **不检出**(M2 反例:同一消息内并行调用非重试);同 call_id → 检出;无成功重试 → 不检出;参数不一致仍配对。
3. **归因**:`direct.tokens is None`、`propagated.tokens is None`、`unattributed_tokens is None`、`total_tokens == 0` 但语义 not applicable;kind=="flag"。
4. **置信度**:call_id=0.95 / adjacent_step+关键词=0.85 / adjacent_step+empty=0.70;值域 0.0–1.0。
5. **确定性**:同一 trace 两次 detect 逐条一致。
6. **反证**:`analyze_finding` 对 adjacent_step 返回 1 条反证且保置信;对 call_id 返回空。
7. **additive**:在既有 golden 基线 trace 上 `diagnose` → TOOL-004 finding 为 0 条,报告逐字节不变;在含参数错误的 trace 上,其余 5 个 detector 输出与新增前一致。
8. **contract**:TOOL-004 finding/attribution 走公共 Finding/Attribution dataclass(不新增字段),`set(f.__dataclass_fields__)` 与既有断言一致。

**必改的既有测试(2 处,属 registry 快照 / doc-fact 更新,非 detector 行为变更)**:

1. `tests/test_checkpoint.py::test_registry_has_five_detectors` 硬编码了 `ALL_DETECTORS == [5 个]` 与 `ALL_ATTRIBUTION_ENGINES.keys() == {5 个}`。新增第 6 个 detector 后该断言必然失败。改为 6 个(TOOL-004 追加于 `SUB-001` 之后),引擎 key 集合同步加 "TOOL-004"。
2. **`scripts/check_facts.py` 文档事实校验门(评审 M4)**:新增第 6 个 detector(5→6)+ 新增测试(114→114+N)后,`check_facts.py` 会把 `FACTS.md` 的"detector 数 5 个"、`PROJECT_STATE.md`/`README.md`/`ARCHITECTURE.md`/`08-团队组织设计.md`/`09-最初目标对照标注.md` 的测试数判不一致并 exit 1。须同步这些文档的 detector 数与测试数,并跑 `check_facts.py` 确认通过。

因此严谨说法是"**113 个既有测试行为不变绿 + 1 个 registry 快照测试更新 + 文档事实同步(check_facts)**",而非"114 全绿保持"——additive 铁律指"不改变现有 detector 行为",registry 快照与文档事实同步属命中面更新,不违反。

- 其余既有测试无需改动:golden 基线(`build_comprehensive_trace`)与 `_full_trace`/`_multi_trace` 的 tool_call 均 `is_error=False` 且 `result=""`,TOOL-004 零输出,默认报告逐字节不变;`test_attribution_kind_semantics`/`test_semantic_matrix_4x4` 基于的 trace 同样无参数错误,不受影响。

## Risks / Trade-offs

- [empty_args 代理可能误报"非参数类错误 + 空参数"] → 仅当 `is_error=True` 且 arguments 为空才命中,且置信度降为 0.70、分析层附反证;不声称具体参数名。
- [窗口=1 可能漏检"失败→(中间无关 step)→成功"的间接重试] → 保守默认,符合"宁可漏报不误报"归因边界;窗口参数化,未来用真实 BL-001 证据链分布校准,不改 spec 结构。
- [跨 turn 的同类调用可能被误判为重试] → adjacent_step 反证显式覆盖;`RETRY_STEP_WINDOW=1` 使跨 turn 仅发生在 turn 边界 step 差 1 的窄场景。
- [kind=flag 落在 "Statistical flags" 章节标题下,措辞略窄] → 不改 KIND_LABELS(守护逐字节);RULE_META 用专属 signal/interpretation 区分;Open Questions 记录未来统一措辞。
- [call_id 层在当前 adapter 下几乎不触发] → 属 spec 完整性的防御性分支,成本为零;主落地路径是 adjacent_step,已被测试覆盖。
- [必改 `test_registry_has_five_detectors`] → 明确登记为 registry 快照更新,与"additive、不改变现有 detector 行为"不冲突;验收标准 = 该测试更新后全绿 + 默认路径逐字节对比测试全绿。

## Migration Plan

无 breaking、无数据迁移、无 schema 变更:

1. 新增 `agenttrace/detectors/tool_004.py`(detector)+ `agenttrace/attribution/tool_004.py`(engine)。
2. 注册进 `detectors/__init__.py` 与 `attribution/__init__.py`。
3. `report.py` 四处 additive 集成(D6);`analysis/counter_evidence.py` + `analysis/profile.py` 各加一行(D7)。
4. 更新 `tests/test_checkpoint.py::test_registry_has_five_detectors` → 6 个(D8)。
5. 新增 `tests/test_tool_004.py`。
6. 回归:全量 pytest,既有 ~114 测试(除 registry 快照更新外)全绿 + 新增测试全绿 + **跑 `scripts/check_facts.py`(文档事实门,同步后须通过)**。

回滚策略:删除注册与新增文件即回滚;无任何数据/输出兼容负担。

## Open Questions

- `RETRY_STEP_WINDOW` 最终默认值(1)待用真实 BL-001 证据链分布校准;`PARAM_ERROR_KEYWORDS` 是否需按真实 `tool/result` 错误措辞扩词(实现时用盲区审计样本核一遍)。
- `KIND_LABELS` 的 "Statistical flags / 统计强度标记" 章节措辞未来是否泛化为 "Flags / 标记"(当前为守护逐字节而不改)。
- 是否在未来为 TOOL-004 引入"重试参数差异 diff"(失败 args vs 成功 args 的缺失键集合)以提升证据可解释性(本次不做,依赖无 schema)。
