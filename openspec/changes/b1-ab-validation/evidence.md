# B1 A/B 验证证据 —— 真实 DSH 会话数据调研

> 调研者:数据调研子代理(只读;未修改任何 `agenttrace/` 产品代码)。
> 用途:为 B1(TOOL-001 重复调用 / TOOL-004 无效参数重试 修复前后对比)提供真实数据证据。
> 原则(铁律,全程遵守):`causal_claim = NONE`;只报**观测到的可省量**,不判"修复后一定省 X";
> 失败 attempt 的 attributions 用 `tokens = not applicable` 语义而非硬填 0;报告不混算 Total wasted。
> 本文所有数字均对同一批**本机真实 DSH 会话**(全量 109 会话)计算,可复现。

---

## 0. 方法、样本与复现

### 0.1 样本

`agenttrace.adapters.dsh_adapter.discover_sessions()` 扫描本机 `~/.dsh/sessions/`,发现 **109 个会话**,全部 `has_zstd=True`。逐一解压 + `parse_dsh_jsonl()` 解析,0 个解析错误(全部成功转为 Canonical Trace)。

| 指标 | 值 |
|---|---|
| 会话总数 | 109 |
| 解析成功 | 109 |
| 解析失败 | 0 |
| 全部 step 数 | 11,938 |
| 全部 tool-call 数 | 13,275 |
| 全部 token(input) | 34,601,869 |
| 全部 token(output) | 8,416,194 |
| 全部 token(input+output) | 43,018,063 |

### 0.2 复现脚本与产物(均在本机,只读)

- `research/b1_probe.py` → `research/b1_probe_out.json`(109 会话 × 两 detector 全量明细,含每个 occurrence 的 step 级 usage)
- `research/b1_analyze.py` / `research/b1_t4_check.py` / `research/b1_beforeafter.py` / `research/b1_anchor.py` → 分布与可省量计算
- `research/b1_before_after.json`(3 个代表会话 original vs fixed 前后对比,结构化)

核心调用(未改动产品代码):

```python
from agenttrace.adapters.dsh_adapter import discover_sessions, _decompress_zstd, parse_dsh_jsonl
from agenttrace.detectors.tool_001 import DuplicateToolCallDetector
from agenttrace.detectors.tool_004 import InvalidParamRetryDetector
# trace = parse_dsh_jsonl(tmp)
# DuplicateToolCallDetector().detect(trace)   # TOOL-001
# InvalidParamRetryDetector().detect(trace)   # TOOL-004
```

### 0.3 "修复后"的定义(可省量的观测口径)

为可测量,显式定义为以下两个**反事实重述**,并只报"观测到的可省量":

- **TOOL-001 修复** = 每个相同 fingerprint 组,只保留**首次**调用,去掉**后续重复**调用。
  - 可省 tool-call(硬)= 每组的 `N-1`,全部组累加。
  - 可省 token(受口径影响,见 §5)= 被去掉的 step 的 step 级 usage。因一个 step 可含多个 tool-call,用两种模型:
    - **保守(整 step 可删)**:仅当某 step 上**所有** tool-call 均为"冗余 occurrence"(不属于任何组的首次)时,整 step 删,计入其 step usage。
    - **粗(含任意冗余 occurrence 的 step)**:只要某 step 含 ≥1 冗余 occurrence 即计入其 usage;即使该 step 还含非冗余调用(此时无法删整 step,只能删除该次调用,但 step usage 在观测上不退)。
- **TOOL-004 修复** = 首次就补全参数、一次成功,去掉失败 attempt(以 `adjacent_step` 成功重试配对的失败调用)。

> 说明:"可省"是**观测值**,即"这些调用/这些 step 在此会话中确实出现过、且按定义属于可去掉的那一类";不构成"如果修复就一定能省这么多"的因果断言。(详见 §5 与「B1 设计边界建议」。)

---

## 1. 样本盘面(样本分布)

### 1.1 TOOL-001(重复工具调用)

| 指标 | 值 |
|---|---|
| finding 总数 | **244** |
| 分布会话数 | **45 / 109** |
| 平均每 finding 出现次数 | 2.61(N=1 保留 + 1.61 次冗余) |
| stateless 工具 finding | **0 / 244**(全部为非无状态工具的高置信候选,confidence=0.98) |

**按重复次数 N(同一 fingerprint 出现几次)分布**:

| N | finding 数 | 每条可省 tool-call |
|---|---|---|
| 2 | 174 | 1 |
| 3 | 42 | 2 |
| 4 | 9 | 3 |
| 5 | 8 | 4 |
| 6 | 5 | 5 |
| 7 | 1 | 6 |
| 10 | 4 | 9 |
| 11 | 1 | 10 |
| **合计** | **244** | **累计可省 = 394** |

**按工具名分布(top)**:

| 工具 | finding 数 | 工具 | finding 数 |
|---|---|---|---|
| read | 73 | memory_delete | 8 |
| edit | 33 | job_list | 7 |
| pwsh | 19 | list_agents | 7 |
| write | 17 | session_status | 7 |
| read_session | 15 | skill | 5 |
| job_output | 13 | mcp__browser_use__browser_navigate | 4 |
| list_sessions | 12 | glob | 4 |
| 其余(≤3) | 各 1–3 | | |

**高倍率 finding(N≥5,可省集中处)**：

| N | 会话 session_id | 工具 |
|---|---|---|
| 11 | `session-1491c2c7-3cf8-4405-97cf-6c70159660f5` | memory_list |
| 10 | `session-35b1ba69-7281-443d-ade0-eb88a228c910` | mcp__browser_use__browser_get_state |
| 10 | `session-65460504-ec3f-4760-b311-8c97a67845b3` | mcp__browser_use__browser_get_state |
| 10 | `session-f7c0cb5b-2db8-4be5-8391-6439f66eb350` | mcp__browser_use__browser_get_state |
| 10 | `session-a79579f3-f897-4a2c-aae7-e3910a206186` | list_agents |
| 7 | `session-eae82666-d1f4-465b-a082-079df35d4332` | job_output |
| 6 | `session-4ee09ecf-7629-4067-a058-dcfef827ccb3` | list_sessions / list_agents |
| 6 | `session-a79579f3-…` | list_sessions / session_status |
| 6 | `session-5cdccd44-fb56-4d7e-ba82-adc2eaa40d0f` | list_agents |
| 5 | 多个会话 | list_sessions / list_agents / read |

> ⚠️ 语义警示:高倍率 finding 大量集中在**轮询型工具**(`list_agents`、`list_sessions`、`session_status`、`mcp__browser_use__browser_get_state`)。这些工具在长任务中"反复读取当前状态"可能**合法且必要**(Agent 需要在每一步确认世界状态),同一 fingerprint 重复≠一定浪费。这正是"该不该重复是语义判断、不能打包成修复后一定省"的证据(见 §5)。

**完整逐会话 finding 数(45 个 >0,降序)**:

```
session-a79579f3-f897-4a2c-aae7-e3910a206186:27   session-4ee09ecf-7629-4067-a058-dcfef827ccb3:19
session-5cdccd44-fb56-4d7e-ba82-adc2eaa40d0f:19   session-1491c2c7-3cf8-4405-97cf-6c70159660f5:18
session-f517be08-c52e-434c-9945-04aeec1ddb2c:16   session-d2c507cf-236e-4567-83ae-ebaadb62dba1:14
session-f1125a46-873d-470e-9d61-1dfd4d92750f:12   session-3c152886-cb30-417d-934e-67752a37f57f:10
session-4fbc44a5-bf76-416d-9ec7-e8507714a093:10   session-e4994f53-a337-4a68-b8d9-d4abeeb6c497:10
session-eae82666-d1f4-465b-a082-079df35d4332:9    session-f7c0cb5b-2db8-4be5-8391-6439f66eb350:7
session-35b1ba69-7281-443d-ade0-eb88a228c910:6    session-3ab9b08e-731e-405d-aa2f-76d0f4656fe1:6
session-65460504-ec3f-4760-b311-8c97a67845b3:6    session-d66a6224-6838-4144-b69d-1c07af6567bb:6
session-98935ea5-b0e0-4d32-9965-e3a2713159a6:4    session-786e50e5-cde9-41da-82bc-a5df8ba67a1b:4
session-c58962bb-9558-46b2-af2b-2b473663067c:4    session-5cea315c-b206-4f40-8d2c-3c029cbc5304:3
session-14d6bc63-e54f-44bc-8db1-d13caeb6ede2:3    session-e3fe999a-c224-4c70-b4b1-52373209283a:2
9fe0a253-6dba-4eeb-9a19-9ccbf39a6e65:2          ba95c490-2781-44af-9ab0-db625959f605:2
session-286131ab-331a-4d69-bd20-0375d1f64299:2    session-37884c59-fc56-4775-bf63-b8c8d8aa73a7:2
session-5f108af7-10e1-48ec-8904-67199d0ddea1:2    session-c2253ae3-c968-4825-8509-5c904a46066e:2
session-112ce518-4d26-4e86-8a54-69c98175c2dd:1    session-119ef0da-b714-4710-87dc-b6ac93693e6b:1
session-8905a66f-506e-4016-85d4-71b37717098c:1    session-a7a3ed8c-396a-4dd0-86f9-a6c1316e80ad:1
session-a867efb1-cd57-465f-b49e-2907b136d169:1    session-b7f74cde-0d63-4a91-b80a-0cfae63cfa9b:1
743b5984-0a7d-44bb-bacc-a670e8679bef:1          c6122427-70cd-4f84-a83d-da14c89b07d7:1
ce78f433-0adf-4dfb-b2cd-3249995624dd:1          session-3343d283-1966-4786-ad37-3fd769a0a16a:1
session-355adc12-442f-45c4-87f8-1aab66bfdbdc:1    session-710049f7-5413-409d-8bab-d11a68111f9a:1
session-716516ac-b176-407f-92c3-6d0bc74da76e:1    session-a19b7558-90ab-44f1-9ae8-91462e4fde18:1
session-d0791eae-3ee2-4b74-b8c8-11606a8a2d06:1    session-f63ad704-280c-4ef7-9e24-f59d794d13f7:1
03993881-6e78-4ab6-89fe-edbf4f24c4a0:1
```

### 1.2 TOOL-004(无效参数重试)

| 指标 | 值 |
|---|---|
| finding 总数 | **10** |
| 分布会话数 | **10 / 109**(每会话恰好 1 条) |
| error_pattern | `invalid argument`(10/10) |
| retry_evidence | `adjacent_step`(10/10) |
| 失败 attempt 所在 step | 均为**独立 step(仅 1 个失败调用)**,见 §3 |

**按工具**:`send_session_message`(8)、`ask_user_question`(1)、`cordis_define`(1)。

**共现观察**:这 10 个会话几乎全部是 TOOL-001 高密度的**大型开发会话**(如 `session-a79579f3`、`session-4ee09ecf`、`session-5cdccd44`、`session-f1125a46`、`session-3c152886`、`session-4fbc44a5`、`session-e4994f53`、`session-f517be08`,均 10+ TOOL-001 + 1 TOOL-004)。仅 `session-112ce518`、`session-716516ac` 为小会话。

### 1.3 两条 finding 是否同会话共现

TOOL-004 的 10 个会话,有 8 个同时是高 TOOL-001 会话(TOOL-001≥10),2 个是小会话。也就是说,本机样本里 TOOL-004 几乎不多发,且集中在"同时在大量重复调用"的长会话中。

---

## 2. TOOL-001 修复后(观测可省量)

### 2.1 tool-call 可省(硬指标,口径无关)

- **累计可省 tool-call = 394**(所有 finding 的 `N-1` 之和)。
- 占全部会话总 tool-call(13,275)的 **2.97%**。
- 平均每条 finding 可省 **1.61** 次调用。

### 2.2 token 可省(两种模型,受反事实口径影响)

| 模型 | 可删 step 数 | 涉及的冗余 occurrence 数 | 可省 input | 可省 output | 可省 total |
|---|---|---|---|---|---|
| 保守(整 step 全冗余才删) | 290 | 303 | 1,717,509 | **81,844** | 1,799,353 |
| 粗(含任一冗余 occurrence 即计) | 377 | — | 2,531,166 | **126,313** | 2,657,479 |

> 说明(重要):
> - input 与 output 差异巨大(input 约占总可省的 95%)。**input_tokens 是本 step 的完整上下文**,大部分是任务都需要的历史 context,按项目铁律**不应全额算成 defect cost**。因此**可归因于"冗余生成"的可信子指标是 output token**(保守 81,844 / 粗 126,313)。
> - 粗模型与保守模型之差(377−290=87 step)是"同时含冗余与非冗余调用的共享 step":这类 step 无法整删,即使删掉其中一两次调用,step 级 usage 在观测上**不退**——这类调用**只有 tool-call 数可省、无 step 级 token 可省**。这也是为什么不能把 input 一并当"省"。
> - 上述 token 是"**在反事实中删掉这些 step 后对 trace 重新加总**"的描述性数字,未模拟"删 step 会改变后续所有 step 的 context"的级联效应(该效应无法在静态 trace 上测量,需重跑模型,见 §5)。

### 2.3 锚点(可复现)

`session-112ce518-…`(1 条 finding):`list_sessions` N=2, arguments=`{}`;occurrence 位于 (turn 4, step 1) total=2311、(turn 5, step 1) total=1541。

`session-98935ea5-…`(4 条,finding 处于多调用 step,非整删):
- `read` N=3,args=`{"file_path":"https://raw.githubusercontent.com/Relistencode/dsh-extension-hub/main/README…"}`;occurrence 位于 (turn3,step3) total=498(calls=2)、(turn3,step4) total=1596(calls=6)、(turn4,step1) total=16300(calls=5)。**同 URL 读 3 次**,但每次都落在含多个调用的 step 中 → 按保守模型**整 step 不可删**(shared step)。这正是"观测到重复,但不能打包成可省"的典型。
- `pwsh` N=2,args=`{"command":"dsh plugin --profile web add github:…"}`;occurrence 位于 (turn7,step1) total=35204、(turn7,step4) total=3323。

---

## 3. TOOL-004 修复后(观测可省量)

### 3.1 核心指标

- **失败 attempt 数 = 10**(=finding 数)。
- **可省 tool-call = 10**(避免 1 次失败调用)。
- **可省"重试往返" = 10**(agent 在失败后 `adjacent_step` 重新发起的那一次调用)。

### 3.2 重要数据修正:失败 attempt 所在 step **有 usage**(非 tokens=0)

TOOL-004 的 detector 归因边界声明"失败 attempt **无 usage**,tokens=not applicable"(`kind=flag`,不做成本归因)。这与 RETRY-001(`llm/retry` 失败尝试 usage=0)是两回事。**但真实数据核对显示**:TOOL-004 的失败 attempt 是一个**被模型生成的 tool-call**,它所在的 step 是一次真实的模型调用,**确实有 usage**。实测 10/10 的失败 step 均为**独立 step(仅 1 个失败调用,n_calls=1, n_fail_calls=1)**,其 step 级 usage 全部可测。

| 会话 session_id | 工具 | 失败 step (turn,step) | failed step total | (in / out) |
|---|---|---|---|---|
| `session-112ce518-…` | send_session_message | (2,13) | 3,138 | 1,889 / 1,249 |
| `session-716516ac-…` | cordis_define | (8,1) | 10,331 | 7,398 / 2,933 |
| `session-f1125a46-…` | send_session_message | (36,1) | 1,049 | 721 / 328 |
| `session-3c152886-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| `session-4ee09ecf-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| `session-4fbc44a5-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| `session-5cdccd44-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| `session-a79579f3-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| `session-e4994f53-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| `session-f517be08-…` | send_session_message | (96,2) | 1,045 | 142 / 903 |
| **合计** | | | **21,833** | **11,002 / 10,831** |

> 锚点示例:`session-716516ac-…` 失败 step (8,1):`cordis_define` err=True,args 含 `{"code":{"host":"return {\n  inject: ['timer', 'web', 'shell']…`;result=`Error: invalid arguments: missing required property "plugin"; missing required property "name"; …`。重试 step (8,2):`cordis_define` err=False,args 补全成 `{"code":{"host":"return {\n  inject: ['timer', 'web', 'shell'],\n …`(补了缺失参数)。失败 step usage=10,331,重试 step usage=5,157。

**结论**:若首次就补全参数,可省的不是"0 token",而是**省掉失败生成的整个 step**(此处合计 21,833 token,其中 output 10,831 为可归因部分;input 11,002 为上下文,按铁律不判定为纯省)。同时省 10 次 tool-call 与 10 次工具级重试往返。

### 3.3 "retry"语义澄清

TOOL-004 的"省 1 次重试往返"指**工具调用层的重试**(失败后 agent 重新发起同一工具调用,由 `adjacent_step` 配对),**不是** `llm/retry`(模型 API 层重试事件)。后者是 RETRY-001 的对象。实测代表会话中 `llm/retry` 事件数在"修复后"保持不变(见 §4),即 **TOOL-001/TOOL-004 修复并不减少 `llm/retry` 事件**。B1 不能把这两类重试混为一谈。

---

## 4. 代表会话 original vs fixed 前后对比(核心产出)

采用**保守**修复口径(去掉整 step 全冗余的 TOOL-001 step + 独立的 TOOL-004 失败 step;并集去重)。"fixed" = 对 trace 删掉这些 step 后**重新加总**。

### 会话 1:`session-a79579f3-f897-4a2c-aae7-e3910a206186`(TOOL-001=27, TOOL-004=1)

| 指标 | original | fixed | 差(可省) |
|---|---|---|---|
| steps | 1,087 | 1,050 | −37 |
| tool-calls | 1,148 | 1,110 | −38 |
| token input | 1,837,931 | 1,820,964 | −16,967 |
| token output | 828,617 | 816,395 | −12,222 |
| token total | 2,666,548 | 2,637,359 | −29,189 |
| `llm/retry` events | 4 | 4 | **0(不变)** |

> 冗余调用:全部 48,可整删 step 内 37,共享 step 内 11。删 step:TOOL-001=36, TOOL-004=1。

### 会话 2:`session-1491c2c7-3cf8-4405-97cf-6c70159660f5`(TOOL-001=18, TOOL-004=0)

| 指标 | original | fixed | 差(可省) |
|---|---|---|---|
| steps | 170 | 150 | −20 |
| tool-calls | 422 | 393 | −29 |
| token input | 606,889 | 584,086 | −22,803 |
| token output | 115,454 | 110,335 | −5,119 |
| token total | 722,343 | 694,421 | −27,922 |
| `llm/retry` events | 4 | 4 | **0(不变)** |

> 冗余调用:全部 34,可整删 step 内 29,共享 step 内 5。删 step:TOOL-001=20(含 N=11 的 memory_list 高倍率组)。这里 tool_calls/step 比例高(422/170),整删 20 步即省 29 次调用;input 节省占比极高,属于"上下文主导"的典型。

### 会话 3:`session-112ce518-4d26-4e86-8a54-69c98175c2dd`(TOOL-001=1, TOOL-004=1)

| 指标 | original | fixed | 差(可省) |
|---|---|---|---|
| steps | 65 | 63 | −2 |
| tool-calls | 78 | 76 | −2 |
| token input | 268,574 | 265,851 | −2,723 |
| token output | 68,046 | 66,090 | −1,956 |
| token total | 336,620 | 331,941 | −4,679 |
| `llm/retry` events | 0 | 0 | 0 |

> 删除 1 个 TOOL-001 step(整删)+ 1 个 TOOL-004 失败 step;是"低密度小会话"的代表:修复影响很小。

**小结**:在 3 个代表会话中,保守修复带来 tool-call 下降 **2–38 次/会话**、token total 下降 **4.7k–29k/会话**。**`llm/retry` 事件在三者中均不变**,证明本快照口径下 TOOL-001/004 修复只影响工具调用层,不触动模型 API 重试。

---

## 5. 方法论核实(诚实的边界)

### 5.1 可客观量化、可复现

- **tool-call 数下降**:确定性、口径无关(每 finding 省 N−1,累计 394)。可复现。
- **删除的 step 数**:确定性(在保守模型下,由"step 上所有 tool-call 均为冗余 occurrence"这个纯规则决定)。可复现。
- **output token 下降**:可复现(删除 step 的 step 级 output 加总,保守 81,844 / 粗 126,313)。**建议作为 token 维度最可信的子指标**。
- **失败 attempt 数 / 其 step 数**:确定性(10/10),可复现。

### 5.2 不可量化 / 不能打包成"修复后一定省"

- **该不该重复调用是语义判断**:`list_agents`、`list_sessions`、`browser_get_state`、`session_status` 等轮询型工具,在长任务中反复读取状态可能合法且必要;同 fingerprint 重复≠浪费。同一 `read(同一 URL)` 也可能是有意的重新校验。**不能由 detector 或调研断言"这些一定可省"**。
- **input token 是上下文,不是 defect cost**:被删 step 的 input_tokens 是完整上下文,大部分是任务本来就需要的历史 context(占可省 total 的 ~95%)。按项目铁律**不得全额算成省**。
- **级联效应不可静态测量**:若真的不再重复调用,后续所有 step 的 input context 都会变(因为少了重复调用的结果/error),这不是"把该 step usage 减掉"能描述的,需要**重跑模型**才能测。任何静态"修复后 token"都只是本反事实重述,不是真实重跑的因果结果。
- **`causal_claim` 必须 = NONE**:这里的"修复后可省"是**观测描述**("这些调用/step 确实以重复/失败形态出现,按定义属可去掉类"),不是因果断言("修复必然减少 X")。

### 5.3 样本局限(对 B1 的影响)

- 样本为**单机 109 会话**,全部来自本机 DSH 开发/数据/分析会话(大量既含重复调用又含无效参数重试的长会话),分布有明显偏斜:TOOL-001 集中在 45 会话、高倍率集中在少数长会话;TOOL-004 仅 10 条、且每会话恰好 1 条。
- **重点警示**:TOOL-004 样本过少(10 条),A/B 统计力弱;且绝大多数是 `send_session_message`(8/10)。B1 若以 TOOL-004 为主指标,结论脆弱;更适合作为**描述性观测 + 机制说明**,而非强计量对比。
- 本机样本同一批会话里,存在 **forked-session / 多会话共现**(A2 已证实 lineage),修复一个父会话可能影响子会话的 context;静态单会话加总无法覆盖这种跨会话耦合。

---

## B1 设计边界建议(基于以上数据)

1. **`causal_claim = NONE`(铁律,必须守)**。B1 是**描述性前后对比**(把同一真实会话的 trace 分别以 original 与 fixed 口径重述),**不是**实验组/对照组的因果实验。任何输出不能写"修复后一定省 X"。

2. **指标分层,不要混算**:
   - **硬指标(主)**:`tool-call 下降`(每 finding N−1)与 `删除 step 数`。这两项确定性、可复现、口径无关。
   - **token 指标(可信子指标)**:`output token 下降`(保守 81,844 / 粗 126,313)。**不要**把 `input token` 计入"省"(它是上下文,占 95%)。
   - **retry 指标**:TOOL-001/004 只省**工具调用层重试**(TOOL-004 的 10 次),**不省 `llm/retry` 事件**(实测 0 变化)。报告必须把"工具级重试"与"模型 API 重试(RETRY-001)"分开,严禁混算为同一指标。

3. **报告口径必须标注**(每条数字都要写清):是"整 step 可删"还是"含冗余 occurrence 即计";token 是"input+output"还是"仅 output";是"静态重述"还是"真实重跑"。给出**区间/两口径**,不要硬塞单一数字。

4. **TOOL-004 样本不足**:10 条且集中在开发会话,统计意义有限。建议 B1 将 TOOL-004 定位为**机制/成因说明**(为什么无效参数会导致一次失败往返 + 失败生成的 step 确实有 usage),而不单独放大其量化效果;或者扩大样本(更多机器/更多真实会话)后再做计量。

5. **把"语义判断"显式隔离**:列一份"哪些重复/失败是**高置信可省**(确定性重复,如 `read`/`write`/`edit` 同参、参数错误失败)与"哪些是**轮询/重校验**(`list_*`、`get_state`、`session_status`),后者标记为 `semantic=debated`,不计入"硬可省"。B1 的"省"数字应基于**确定性重复子集**,并单独披露语义不明子集的数量(否则会被高估)。

6. **可复现性验证**:(a) 同一批 trace 跑两遍结果一致;(b) 换一台机器(不同 `~/.dsh/sessions`)该数字会变——因此 B1 应**随数据给出 session_id 与脚本**,而不是给一个脱离样本的"通用节省率"。推荐在 B1 落一份 `before_after` 结构化结果(`research/b1_before_after.json` 可作为模板)。

---

### 附:一句话结论(供 B1 对齐)

在没有因果断言的约束下,本机 109 真实会话观测到:**TOOL-001 244 条 finding(45 会话),按保守修复可去掉 290 个"仅重复"step、省 394 次调用,其中可归因的 output token 约 8.2 万(粗口径 12.6 万);TOOL-004 10 条 finding(10 会话),可省 10 次工具调用与 10 次工具级重试往返,失败生成 step 合计 usage 21,833(input 11,002 / output 10,831)。** 但这些是**观测可省量**,不是"修复后必然节省"的因果结论;其中 input token 与"轮询型重复"的高度不确定性,是 B1 设计必须显式写入边界的部分。
