# B1 A/B 验证设计评审报告（pro 异模型，严格性优先）

> 评审模型：deepseek-v4-pro
> 评审对象：`openspec/changes/b1-ab-validation/design.md`（定稿，672 行）
> 评审依据：evidence.md（109 会话实测）+ proposal.md + PROJECT_STATE.md + 同构参考 + 现有 detector 源码
> 评审原则：找缺陷，不找优点；逐条核对硬约束红线

---

## 硬约束红线逐条核对

| # | 红线 | 设计是否守住 | 备注 |
|---|------|-------------|------|
| 1 | additive：不改现有 detector/attribution 行为；默认输出逐字节不变 | ⚠️ **部分存疑** | D7 改名 `_match_param_error` 需改 tool_004.py 源码，与 proposal "源码零改动" 冲突；其余 additive 设计（enable_analysis=False 零影响）正确 |
| 2 | causal_claim=NONE | ✅ 守住 | D3 硬编码 `causal_claim="NONE"`，报告模板显式标注 |
| 3 | 禁混算 Total wasted | ✅ 守住 | D3 明确 `input_token_change` 标注为"上下文变化，非可省成本" |
| 4 | retry 严格分开 | ⚠️ **部分存疑** | D4 硬编码 `llm_retry_change=0` 有实证支持但欠严谨（见 C 项） |
| 5 | 语义判断显式隔离 | ✅ 守住 | D2 定义 `SEMANTIC_DEBATED_TOOLS` frozenset；D3 分离 `semantic_debated_*` 字段 |
| 6 | 报告口径标注 | ✅ 守住 | D5b 模板每条数字标注口径、方法、causal_claim |
| 7 | ABResult 是分析层数据块，不进 Registry | ✅ 守住 | D0 明确不进 `ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES` |
| 8 | 复杂系统只测局部封闭不变量 | ✅ 守住 | 保守模型 + 确定性 + 静态重述，不模拟级联效应 |

---

## 逐项评审

### 【A】D7 改名：`_match_param_error` → `match_param_error`

**【结论】⚠️ 弱问题——违反 proposal "源码零改动" 承诺，但行为不变。**

**【理由】**

1. proposal.md 第 34 行明确写："不改现有 detector 行为：TOOL-001/TOOL-004/attribution **源码零改动**，additive。"（强调为原文）。D7 提出将 `agenttrace/detectors/tool_004.py` 中的 `_match_param_error` 从私有改为公开，虽然函数签名和逻辑不变，但**必须修改 tool_004.py 源码**——至少需要改两处：
   - 第 70 行：`def _match_param_error(tc)` → `def match_param_error(tc)`
   - 第 121 行：`pattern = _match_param_error(tc)` → `pattern = match_param_error(tc)`

2. 这与 proposal 的"源码零改动"承诺直接冲突。即使行为逐字节不变，也违反了"不改 detector 源码"的显式约束。proposal 是用户确认的合同，design 不能单方面放宽。

3. 设计自身已给出备选方案（第 502 行）："在 `agenttrace/analysis/ab_validation.py` 内联实现 `_match_param_error` 的等价逻辑（但需标注'与 tool_004.py 保持同步'）"。这个备选方案**完全避免触碰 tool_004.py**，更符合 additive 铁律。

4. 同构参考：`context_health.py` 的 `call_fingerprint` 是直接从 `core.normalize` import 的公开 API，不需要改 detector 源码。`_match_param_error` 是 tool_004 的私有实现细节，B1 不应该依赖 detector 内部私有函数。

**【是否需改 + 具体改法】**

**建议改。** 采用备选方案：在 `agenttrace/analysis/ab_validation.py` 中内联复制 `_match_param_error` 的等价逻辑。具体做法：

```python
# ab_validation.py 内联（与 tool_004.py 保持同步）
_PARAM_ERROR_KEYWORDS = (
    "invalid argument", "missing required", "invalid_request",
    "invalid request", "invalid parameter", "required parameter",
    "required argument", "missing parameter", "missing argument",
    "unexpected argument", "unexpected keyword",
)

def _is_param_error(tc) -> str | None:
    """内联自 tool_004._match_param_error，与源头保持同步。
    若 tool_004.py 更新此逻辑，需同步更新本函数。
    """
    if not tc.is_error:
        return None
    text = (tc.result or "").lower()
    for kw in _PARAM_ERROR_KEYWORDS:
        if kw in text:
            return kw
    args = tc.arguments
    if args is None or args.strip() in ("", "{}", "null", "None"):
        if tc.tool_name not in STATELESS_TOOLS:
            return "empty_args"
    return None
```

同时：
- 在 `ab_validation.py` 顶部添加注释：`# _is_param_error 内联自 tool_004._match_param_error，保持同步`
- 在 `tool_004.py` 的 `_match_param_error` 上方添加注释：`# 若修改此函数逻辑，请同步更新 analysis/ab_validation.py 中的 _is_param_error`
- **撤回 D7 对 tool_004.py 的修改**，保持 detector 源码零改动

**严重程度**：弱问题（不阻塞，但应修）。若评审后决定不改，需在 design 中显式记录"proposal 源码零改动承诺已放宽为行为零改动"，并取得用户确认。

---

### 【B】fixed 口径公平性：保守模型是否公平/无偏

**【结论】✅ 公平且诚实。保守模型是正确的设计选择，不应引入"部分删调用"口径。**

**【理由】**

1. 保守模型（整 step 全冗余才删）的"不公平"（低估）是**有意为之且有充分理由**的：
   - evidence §2.2 已证明：共享 step（87 个）的 step 级 usage 无法拆分到单个 tool_call——"即使删掉其中一两次调用，step 级 usage 在观测上不退"。
   - 任何"部分删调用"的 token 口径都是**虚构数字**，无法从 trace 数据中验证。

2. 设计已经做了正确的事：
   - 保守模型用于 `fixed` trace 构造（唯一可复现口径）
   - 粗模型（含任一冗余 occurrence 即计）在**报告**中作为参考口径披露
   - tool-call 下降数（394）是口径无关的硬指标，不受保守/粗模型影响

3. "删共享 step 内单一调用不退 token"不是低估，而是**诚实**——承认静态 trace 的能力边界。强行虚构一个"部分退 token"数字反而违反铁律。

4. 关于"tool_call 可省但 token 不省"：tool-call 下降数（394）已经包含了共享 step 上的冗余调用。这个硬指标不受保守模型影响，因为 tool-call 计数是调用级的，不是 step 级的。设计在 D4 算法第 5 步中 `fixed_tool_calls = original_tool_calls - hard_occurrences - tool004_failed`，其中 `hard_occurrences` 是**所有**确定性重复组的 (N-1) 之和，包括共享 step 上的。所以 tool-call 可省量不会被低估，只是 token 可省量被保守估计。

**【是否需改 + 具体改法】**

**无需改。** 但建议在 design 中补充说明：tool-call 下降数（`tool_call_reduction`）是口径无关的硬指标，包含共享 step 上的冗余调用，不受保守模型限制。当前 design 对此交代不够清晰，可能让读者误以为 tool-call 也被低估。

---

### 【C】retry 分开是否严谨：`llm_retry_change` 硬编码为 0

**【结论】⚠️ 弱问题——实证支持但逻辑不严谨，需补充防御性扫描。**

**【理由】**

1. 实证方面：evidence §4 的 3 个代表会话全部显示 `llm/retry` 事件在修复前后不变（4→4, 4→4, 0→0）。这是有力的实证支持。

2. 逻辑方面：D4 第 6 步（第 304-306 行）硬编码 `llm_retry_fixed = llm_retry_original`，理由是"events 不随 step 删"。但这个假设有一个漏洞：

   - `llm/retry` 事件存储在 `trace.events[]` 中，是独立事件层
   - 但 `llm/retry` 事件在真实数据中可能关联到特定的 step（比如哪个 step 触发了重试）
   - 如果被删除的 step 恰好是触发 `llm/retry` 的那个 step，在真实重跑中该 retry 事件可能不再发生
   - 静态重述无法模拟这个级联效应，所以硬编码 0 是正确的——但**必须显式声明这是静态重述的局限，而非真实世界的结论**

3. 设计在 Q1（第 600-606 行）已经意识到了这个问题，并提出"实现时验证：对验证集 5 个会话，检查 fixed trace 中是否真的有 step 删除了但其关联的 llm/retry 事件仍在 events 中"。这是好的，但**这个验证应该提升为测试用例**（如 T28），而非仅在 Q1 中提及。

4. 当前设计在报告模板中写"模型 API 重试（llm/retry 事件）| {llm_retry_original} | {llm_retry_fixed} | **{llm_retry_change}（不变）**"——这个"（不变）"可能误导读者以为真实世界中 retry 也不变。应改为"（静态重述下不变；真实重跑可能不同）"。

**【是否需改 + 具体改法】**

**建议改。** 三项修改：

1. **算法**：在 D4 第 6 步中增加防御性扫描——遍历 `trace.events` 中的 `llm/retry` 事件，检查是否有任何事件的关联 step 在被删集合中。若有，记录 warning 日志（不改变 `llm_retry_change=0` 的硬编码，但记录已知局限）。

2. **测试**：新增 T28 `test_llm_retry_events_not_on_deleted_steps`——构造含 `llm/retry` 事件的 trace，验证 `build_ab_validation` 不崩溃，且 `llm_retry_change` 为 0（或记录 warning）。

3. **报告措辞**：将 `（不变）` 改为 `（静态重述下不变；真实重跑中若被删 step 关联 retry 事件，该 retry 可能不再发生）`。

---

### 【D】语义隔离是否会被误用：`SEMANTIC_DEBATED_TOOLS` 边界

**【结论】⚠️ 弱问题——`read` 的 URL 场景存在边界模糊，但整体分类正确。**

**【理由】**

1. 设计将 `read` / `write` / `edit` / `glob` / `pwsh` 归为确定性重复（非 debated），理由是"同文件同参数重复读写是确定性冗余"。这在大多数情况下是正确的。

2. 但 evidence §2.3 锚点（`session-98935ea5`）展示了一个边界案例：`read` N=3 对同一 GitHub raw URL（`https://raw.githubusercontent.com/Relistencode/dsh-extension-hub/main/README…`）重复读 3 次。evidence §5.2 自己也说："同一 `read(同一 URL)` 也可能是有意的重新校验。"

3. 当前 `SEMANTIC_DEBATED_TOOLS` 包含 `read_session`（读取会话历史，可能合法重读）但不包含 `read`（读取文件/URL）。这两个工具的语义差异在于：
   - `read_session`：读取的是**会话历史**（动态内容，随对话进展变化）→ 合法重读
   - `read`：读取的是**文件/URL**（通常静态，但 URL 内容可能变化）→ 大部分情况是冗余，但 URL 场景存疑

4. 设计第 138 行的注释说"read / write / edit / glob / pwsh 等同参重复调用不在 debated 集合中——同文件同参数重复读写是确定性冗余"。这个判断对本地文件成立，但对 HTTP URL 不完全成立。不过，区分 `read(local_file)` vs `read(http_url)` 需要解析参数，过度设计。

5. 当前做法（`read` 整体归确定性）是**可接受的保守选择**，因为：
   - `read` 的 URL 重复在 109 会话中占比很小（大部分 `read` 是读本地文件）
   - 即使误判，也只会**略微高估**硬可省量（而非低估），偏向乐观但不违反诚实边界
   - 通过 `semantic_debated_occurrences` 单独披露 debated 子集，读者可以自行判断

**【是否需改 + 具体改法】**

**建议不改分类，但补充文档说明。** 在附录 A 中增加一行说明：

> `read` 对 HTTP URL 的重复读取在某些场景下可能是有意的重新校验（如检查远程内容是否更新），但由于区分 file:// 与 http(s):// 需要解析参数（过度设计），当前将 `read` 整体归为确定性重复。这可能略微高估硬可省量，但占比极小，且不影响 debated 子集的独立披露。

---

### 【E】验证集跨机：`AB_VALIDATION_SESSIONS` 换机即失效

**【结论】🔴 强问题——验证集形同虚设，必须固化 fixture 才能实现设计声称的"可复现"。**

**【理由】**

1. 设计 D6 声称验证集用途包括"可复现性：每次跑同一 session_id 得到相同结果（确定性 guarantee）"。但这个"可复现"只在**本机**成立。换一台机器，session_id 不存在，验证集测试全部失败。

2. T23（`test_validation_set_sessions_exist`）只检查 session_id **格式合法性**，不检查是否存在。这导致：
   - 在本机：格式合法 + 会话存在 → 测试通过
   - 在另一台机器：格式合法 + 会话不存在 → 测试也通过（因为 T23 不检查存在）→ **虚假绿**
   - 但 E2E 测试（`@pytest.mark.dsh_data`）会失败 → 但这可以被 skip

3. 设计 Q3 将"导出 Canonical Trace 为 JSON fixture"标为"未来可选"，但这是**实现可复现性的必要条件，不是可选项**。没有 fixture，验证集就不是"验证集"，而是"本机数据快照"。

4. 同构参考：A1（TokenInvariant）和 A2（SessionLineage）的测试都不依赖特定 session_id 是否存在——它们用构造的 fixture 测试。B1 的验证集是唯一依赖本机真实数据的测试。

5. 证据 §0.2 已经有完整的复现脚本和产物（`research/b1_before_after.json`），说明技术上完全可行。

**【是否需改 + 具体改法】**

**必须改。** 两项修改：

1. **将 fixture 导出从"未来可选"提升为 B1 实现的一部分**：
   - 在 `tests/fixtures/` 下新增 `b1_validation_sessions/` 目录
   - 对 `AB_VALIDATION_SESSIONS` 中的 5 个会话，导出 Canonical Trace 为 JSON 文件（用 `CanonicalTrace.to_dict()` 序列化）
   - 文件命名：`{session_id}.json`
   - 新增 `tests/fixtures/b1_validation_sessions/README.md` 说明来源

2. **修改 T23**：从"检查 session_id 格式合法"改为"检查 fixture 文件存在且可解析为 Canonical Trace"：
   ```python
   def test_validation_set_fixtures_exist():
       import json
       from pathlib import Path
       fixtures_dir = Path(__file__).parent / "fixtures" / "b1_validation_sessions"
       for entry in AB_VALIDATION_SESSIONS:
           sid = entry["session_id"]
           fixture_path = fixtures_dir / f"{sid}.json"
           assert fixture_path.exists(), f"Missing fixture: {fixture_path}"
           # 验证可解析
           data = json.loads(fixture_path.read_text())
           assert "turns" in data  # CanonicalTrace 结构
   ```

3. **更新 design Q3**：将决议从"未来可选"改为"实现时导出，固化在 `tests/fixtures/b1_validation_sessions/`"。

---

### 【F】指标完整性：缺 task success rate

**【结论】⚠️ 弱问题——静态重述无法测量 task success rate，但 design 应显式说明此缺失。**

**【理由】**

1. proposal 和 09 文档明确列出 A/B 验证的四项指标：Token Reduction / Tool Call Reduction / Retry Rate / **Task Success Rate**。B1 设计覆盖了前三项，但完全未提及第四项。

2. task success rate 无法从静态 trace 重述中测量，因为：
   - "任务成功"是语义判断，需要理解任务目标
   - 静态重述不重跑模型，无法判断修复后任务是否仍然成功
   - 这属于 evidence §5.2 的"不可量化 / 不能打包成修复后一定省"范畴

3. 但 design 完全沉默（既不在 Goals 中说"我们要测"，也不在 Non-Goals 中说"我们不测"），这是文档缺口。读者会疑惑"09 文档说的 Task Success Rate 去哪了"。

**【是否需改 + 具体改法】**

**建议改。** 在 Non-Goals 中增加一条：

> - **不测量 Task Success Rate**：任务成功率是语义判断（需理解任务目标 + 判断完成质量），静态反事实重述无法测量。若修复后模型行为改变导致任务失败，这属于级联效应（需真实重跑才能观测），不在 B1 静态重述范围内。B1 只覆盖可确定性测量的三项：Token Reduction / Tool Call Reduction / Retry Rate（区分工具级与模型 API 级）。

---

### 【G】测试完备性：T1-T27 覆盖缺口

**【结论】⚠️ 弱问题——有 3 个明确缺口，建议补 4 个测试用例。**

**【理由】**

逐项对照覆盖矩阵：

| 覆盖维度 | 现有测试 | 是否充分 |
|----------|---------|---------|
| ABResult 默认值 | T1 | ✅ |
| 空 trace | T2 | ✅ |
| 无重复 | T3 | ✅ |
| tool-call 下降（N=2, N=3, 多组） | T4, T5, T6 | ✅ |
| 整 step 可删 | T7 | ✅ |
| 共享 step 不可删 | T8 | ✅ |
| output token 下降 | T9 | ✅ |
| input token 分离 | T10 | ✅ |
| llm_retry 不变 | T11 | ✅ |
| 工具级 vs llm retry | T12 | ✅ |
| 语义隔离（纯 debated） | T13 | ✅ |
| 混合（确定性+debated） | T14 | ✅ |
| causal_claim=NONE | T15 | ✅ |
| method=static_restatement | T16 | ✅ |
| 确定性 | T17 | ✅ |
| TOOL-004 失败 step | T18 | ✅ |
| TOOL-004 output tokens | T19 | ✅ |
| 并集去重 | T20 | ✅ |
| additive/金钟罩 | T21 | ✅ |
| CLI --ab 启用 analysis | T22 | ✅ |
| 验证集格式 | T23 | ✅（但虚，见 E 项） |
| 无 finding 全零 | T24 | ✅ |
| report 渲染（有 AB 块） | T25 | ✅ |
| report 渲染（无 AB 块） | T26 | ✅ |
| fingerprint 一致性 | T27 | ✅ |

**覆盖缺口**：

1. **金钟罩逐字节不变**：T21 只验证 `ab_result is None`，但**没有验证完整报告输出逐字节不变**。A1 和 A2 实现中都有 `test_disable_analysis_byte_identical_to_v05` 这个金钟罩测试。B1 必须新增等价测试，否则无法证明"默认输出逐字节不变"（红线 1）。

2. **fixed_* 字段计算正确性**：T4-T9 验证了 reduction 字段，但**没有验证 `fixed_steps`、`fixed_tool_calls`、`fixed_output_tokens`、`fixed_input_tokens`、`fixed_total_tokens` 这些字段的计算正确性**。应该有一条测试验证 `fixed_* = original_* - reduction_*` 恒等式。

3. **debated tool-call 不计入 fixed 的 tool-call 下降**：D4 算法第 5 步说 `fixed_tool_calls = original_tool_calls - hard_occurrences - tool004_failed`，其中 debated 的 tool-call 不扣除。但没有测试验证这一点。T13 验证了 `tool_call_reduction=0`（纯 debated 场景），但未验证 `fixed_tool_calls == original_tool_calls`（debated tool-call 仍在 fixed 中）。

4. **`tool001_finding_count` vs `tool001_finding_count_debated` 拆分**：没有测试验证这两个计数字段的正确性。

**【是否需改 + 具体改法】**

**建议补 4 个测试用例**：

| # | 新增用例 | 覆盖缺口 |
|---|---------|---------|
| T28 | `test_golden_report_byte_identical_with_analysis_disabled` | 金钟罩：enable_analysis=False 时完整报告输出与 v0.5 逐字节一致 |
| T29 | `test_fixed_fields_equal_original_minus_reduction` | fixed_* = original_* - reduction_* 恒等式 |
| T30 | `test_debated_tool_calls_preserved_in_fixed` | debated 的 tool-call 在 fixed 中保留（fixed_tool_calls 不扣减 debated） |
| T31 | `test_finding_counts_split_deterministic_vs_debated` | tool001_finding_count 与 tool001_finding_count_debated 正确拆分 |

其中 T28 是最关键的缺失——没有它，红线 1（默认输出逐字节不变）就没有测试证明。

---

### 【H】fixed 构造的可复现性：算法与 evidence 的一致性

**【结论】⚠️ 弱问题——算法逻辑与 evidence 口径一致，但缺少验证算法输出的回归测试。**

**【理由】**

1. 算法（D4）与 evidence §4 的保守口径一致：
   - 都按 fingerprint 分组
   - 都采用"整 step 全冗余才删"的保守模型
   - 都做并集去重（TOOL-001 + TOOL-004）
   - 都使用 step 级 usage 加总

2. 但有一个细微差异需要确认：evidence §4 的 3 个代表会话是**手动计算**的，而 `build_ab_validation` 是**自动算法**。两者口径一致不代表结果一致——算法实现可能有 bug。

3. 当前测试套件（T1-T27）全部使用**构造的 trace**（fixture），没有一条测试用**真实会话数据**验证算法输出与 evidence 的已知数字一致。这意味着：
   - 算法可能正确实现了设计意图
   - 但可能与 evidence 的手动计算结果不一致（算法 bug 或口径理解偏差）

4. 验证集 E2E（第 590-594 行）会跑真实数据，但只验证"下界满足"（`expected_tool001_min` 等），不验证"精确匹配 evidence 的已知数字"。

**【是否需改 + 具体改法】**

**建议改。** 新增一条回归测试（在 fixture 固化后）：

| # | 新增用例 | 覆盖要点 |
|---|---------|---------|
| T32 | `test_algorithm_matches_evidence_anchor_sessions` | 对 3 个代表会话（固化 fixture），验证 `build_ab_validation` 输出与 evidence §4 的手动计算结果一致（deleted_steps、tool_call_reduction、output_token_reduction 等关键字段精确匹配） |

这条测试的价值：
- 证明算法实现与 evidence 调研口径一致
- 防止未来修改算法时漂移
- 让"可复现"从声明变成可验证的事实

---

## 总评与建议

### 设计质量评估

设计整体**质量高**，8 条硬约束红线中 6 条完全守住，2 条有弱问题（A 和 C）。设计决策（D0-D7）与 evidence 的诚实边界对齐良好，保守模型、语义隔离、retry 分开等关键设计选择正确。

### 阻塞项（必须修才能进入实现）

**无硬阻塞项。** 但以下 3 项为**强建议**（建议在实现前修，否则实现时会产生返工）：

| 项 | 严重程度 | 必须修？ | 理由 |
|----|---------|---------|------|
| **E：固化 fixture** | 强 | **是** | 不固化则验证集形同虚设，T23 虚假绿，跨机不可复现 |
| **A：撤回 D7 改名** | 中 | **建议** | 违反 proposal "源码零改动"承诺；备选方案（内联）零代价 |
| **G：补金钟罩测试 T28** | 中 | **建议** | 无此测试则无法证明红线 1（默认输出逐字节不变） |

### 建议修但非阻塞

| 项 | 改法摘要 |
|----|---------|
| C：llm_retry_change 防御性扫描 | 增加扫描逻辑 + 修正报告措辞 |
| D：read URL 边界说明 | 在附录 A 补充说明 |
| F：task success rate 显式排除 | 在 Non-Goals 中增加一条 |
| G：补 T29-T31 测试 | 补 fixed_* 恒等式、debated 保留、finding 拆分测试 |
| H：补 T32 回归验证 | 固化 fixture 后验证算法与 evidence 一致 |

### 设计是否可进入实现

**条件性可进入。** 前提是先修 E（固化 fixture）+ A（撤回 D7 改名）。这两项如果拖到实现阶段再改，会导致：
- E：实现时没有 fixture 可测，验证集 E2E 只能在本机跑 → 实现会话（session-d2c507cf）可能无法在本机运行测试
- A：实现时改了 tool_004.py 源码 → 与 proposal 冲突 → 验收时可能被退回

建议：**先修 E + A，再交实现**。其余 C/D/F/G/H 可在实现阶段并行修，不阻塞实现启动。

---

*评审完成时间：2026-08-22*
*评审模型：deepseek-v4-pro*