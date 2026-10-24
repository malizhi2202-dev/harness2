# Harness² 项目文档

> **版本**：v1 · 2026-10-23
> **配套产物**：[`PRODUCT-DESIGN.html`](PRODUCT-DESIGN.html)（产品设计 + 架构 + 时序 + 难题 + 界面原型）、[`prototype/`](prototype/)（7 个可单独打开的控制台原型页）
> **代码位置**：`/home/malizhi/ai/harness2`（Python 43 模块 / 14,243 行，Apache-2.0）

---

## 0. 一句话

**Harness² 把 RSI（递归自我改进）从「改模型权重」挪到「改模型外面的那层系统」**：权重冻结，被优化的是 harness（系统提示词 / 护栏 / 记忆 / 工具说明 / 子代理 / 技能 / 脚本 / 插件），递归发生在**任务级**与**经验级**两层，学习信号是**同一任务上的行为对照**，不是分数。

---

## 1. 这是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 一个批处理 CLI 框架（`harness2`、`harness2-judge`、`harness2-report`…） | 在线服务 / 多租户平台 |
| 三个专业基准的适配器（LAB / JobBench / WorkBuddy-Bench） | 一个通用 agent 产品 |
| 无凭证可跑的确定性 demo（`harness2-demo`） | 模型能力评测榜 |
| 文件式状态（`runs/` + `library/`），可 diff、可打包 | 带数据库的调度系统 |
| 内容与路径边界（白名单 / 绊线 / 盲选） | OS 级安全沙箱（见 §10） |

定位声明：上游是 Google 开源研究项目，README 明确「不是官方支持的 Google 产品」。本文档把它产品化地讲清楚，不构成 SLA 或安全承诺。

---

## 2. 5 分钟验证（不需要任何模型凭证）

```bash
cd /home/malizhi/ai/harness2
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e '.[render]'
harness2-demo --output runs/demo --k 2
```

demo 用一个确定性编辑者走完整流程：基线脚本把贷记过滤掉了（`if amount > 0`），probe 后总分从 `105.25` 变成 `85.15`，选择器按源 CSV 核对后采纳，反思写出一条「求和要保留符号」的经验并入库。

想看流程与账本：

```bash
jq '.modes.agg.ledger' runs/demo/runs/demo-oc-agg2/ledger-1/summary.json
cat runs/demo/library/k2/agg/accounting/INDEX.md
```

---

## 3. 总体架构

```mermaid
flowchart TD
  CLI["CLI<br/>run_sweep / judge / report / setup / check / demo"] --> SCH["schedule<br/>命名空间 · 任务流 · 续跑"]
  SCH --> REC["recursion<br/>Propose–Probe–Compose"]
  REC --> CTX["context<br/>证据树 + mission 指针"]
  REC --> PRO["proposer<br/>会话 · 重试 · harvest · 缓存"]
  SCH --> SEL["selection<br/>盲选 · 反思"]
  SCH --> LIB["library<br/>领域经验库"]
  SCH --> RM["runmark<br/>指纹"]
  PRO -->|subprocess| SESSION["提案/选择/反思会话<br/>opencode | claude CLI"]
  REC -->|subprocess| SOLVER["求解 rollout<br/>基准 launcher → opencode/codex"]
  SEL -->|subprocess| JUDGE["判分（采纳之后）"]
  CTX --> FS[("文件系统<br/>runs/ · library/ · .audit/")]
  LIB --> FS
  REC --> FS
  JUDGE --> FS
```

没有服务端、没有数据库：**一个 CLI 进程 + 三类子进程 + 一个文件系统**。所有状态是目录与 JSON。

---

## 4. 模块架构

```mermaid
flowchart LR
  subgraph CLI["cli/（9 文件 / 1,111 行）"]
    A1[run_sweep] --- A2[judge] --- A3[report] --- A4[setup/check/demo/bridge]
  end
  subgraph CORE["core/（14 文件 / 5,688 行）"]
    B1[schedule] --> B2[recursion]
    B2 --> B3[context]
    B2 --> B4[proposer]
    B2 --> B5[tree]
    B2 --> B6[spec]
    B1 --> B7[selection]
    B1 --> B8[library]
    B2 --> B9[ledger]
    B1 --> B10[runmark]
    B1 --> B11[judge/report]
    B2 --> B12[prompts]
  end
  subgraph SUB["substrate/（7 文件 / 1,911 行）"]
    C1[base Protocol] --- C2[opencode] --- C3[codex]
    C4[manifest] --- C5[registry] --- C6[host]
  end
  subgraph BENCH["benchmarks/（8 文件 / 4,418 行）"]
    D1[jobbench] --- D2[lab] --- D3[workbuddy] --- D4[local] --- D5[tasks/runner/render]
  end
  CLI --> CORE
  CORE -.只依赖 Protocol.-> SUB
  CORE -.只依赖 Protocol.-> BENCH
```

三条不可逆的依赖方向：

1. **core 不知道具体基准**（只面向 `BenchSpec` / `BenchmarkAdapter`）；
2. **core 不知道具体执行器**（只面向 `SurfaceSpec` + `Substrate`）；
3. **证据只从适配器来**（不扫目录猜产物）。

---

## 5. 一次任务的流程（并行模式）

```mermaid
flowchart TD
  START(["任务进入"]) --> BASE["k 次基线 rollout<br/>同一 harness"]
  BASE --> OK{"有任一基线成功?"}
  OK -- 否 --> FAIL["status=base-failed<br/>写 summary 退出"]
  OK -- 是 --> PROP["2k 个提案并发<br/>k×INV + k×DOM"]
  PROP --> VAL{"校验通过?"}
  VAL -- 否 --> RETRY["错误回灌 → 重试 1 次"]
  RETRY --> VAL
  VAL -- 仍失败 --> FALL["回退到当前树<br/>fell_back"]
  VAL -- 是 --> PROBE["每个候选 rollout 1 次"]
  FALL --> PROBE
  PROBE --> COMP["k 个合成器<br/>产出 k 个终版"]
  COMP --> ROLL["终版各 rollout 1 次"]
  ROLL --> SEL["盲选<br/>匿名 · 只看产物"]
  SEL --> ADOPT["采纳一个"]
  ADOPT --> REFL["反思<br/>edits 表 + 一条 lesson"]
  REFL --> LIB["入库（三字段门槛）"]
  ADOPT --> JUDGE["判分（可延后）"]
  JUDGE --> DONE(["写 summary.json"])
```

串行模式把「提案 → probe → 合成」重复 k 轮，每轮以上一轮的 checkpoint 为编辑对象，允许还原更早的 checkpoint。

---

## 6. 核心时序

```mermaid
sequenceDiagram
  autonumber
  participant S as 调度/CLI
  participant P as 提案者会话
  participant X as 求解器
  participant C as 合成器
  participant V as 选择者
  participant R as 反思者

  S->>X: k 次基线 rollout（未改 harness）
  X-->>S: run_dir + 产物 + 轨迹
  loop 2k 个候选（并发）
    S->>P: mission（context/ 指针）
    P-->>S: 编辑后的 context/harness（harvest）
    S->>S: read_tree → 白名单 → validate（失败重试 1 次）
    S->>X: 该候选 rollout 1 次
    X-->>S: 行为对照证据
  end
  loop k 个终版
    S->>C: 全量候选 + 各自 rollout + decisions
    C-->>S: 终版树（merge / 采纳 / KEEP_BASE）
    S->>X: 终版 rollout 1 次
  end
  S->>V: 匿名候选（只给产物）
  V-->>S: choice + why
  S->>R: 采纳后的完整记录
  R-->>S: edits 表 + cue/lesson/evidence
  S->>S: library.add + 重建 INDEX
```

---

## 7. 数据模型（逻辑表 → 物理落地）

没有数据库；下表是查询视角，`ER` 在 [PRODUCT-DESIGN.html §3.7](PRODUCT-DESIGN.html#s3-7)。

```mermaid
erDiagram
  CELL ||--o{ RUN : "namespace"
  TASK ||--o{ RUN : "task_id"
  TASK ||--o{ STAGE : "task_id"
  TASK ||--o| SELECTION : "task+mode+k"
  TASK ||--o{ LEDGER_ENTRY : "task+mode+k"
  RUN ||--|| ROLLOUT : "run_id"
  STAGE ||--o{ HARNESS_FILE : "harness"
  STAGE ||--o{ AUDIT_CALL : "label"
  SELECTION ||--o| EXPERIENCE : "reflect"
  EXPERIENCE ||--o| LIBRARY_ENTRY : "admit"
  LIBRARY_ENTRY ||--o{ HARNESS_FILE : "full tree"
  LEDGER_ENTRY }o--|| BUDGET_CHECK : "aggregate"
```

| 逻辑表 | 主键 | 物理位置 |
|---|---|---|
| `cell` | `(bench, substrate, mode, k, seed, tag)` | namespace 字符串 |
| `task` | `task_id` | `runs/<ns>/<task>/task.md` + `context/source/` |
| `run` | `run_id` | `executions/<ns>/<task>/<label>/` |
| `stage` | `(task, mode, k, label)` | `summary.modes[].stages[]` + `harnesses/<label>/` |
| `rollout` | `label`（1:1 run） | `context/rollouts/<label>.md` + 执行目录 |
| `selection` | `(task, mode, k)` | `selection/<mode><k>/selection.json` + `summary.selections[]` |
| `experience` | `(task, mode, k)` | `summary.selections[].experience` + `reflection/` |
| `library_entry` | `(bench, mode, k, domain, task)` | `library/<bench>/k<k>/<mode>/<domain>/harnesses/<task>/` |
| `harness_file` | `(harness_id, rel)` | `harnesses/<label>/<rel>` · `context/harness/<rel>` |
| `ledger_entry` | `(task, mode, k, seq)` | `summary.modes[].ledger.entries[]` |
| `budget_check` | `(task, mode, k)` | `summary.modes[].ledger`（R/P/expected） |
| `audit_call` | `(label, fingerprint)` | `<audit>/<label>.<fp>.{mission,system,response}.md + .meta.json + .workspace/` |

一致性：**没有外键、没有事务**；靠「先写临时文件再 `os.replace`」「flock 合并写 summary」「INDEX 从磁盘重建」。

---

## 8. 关键机制

### 8.1 可编辑面（c1–c8）

| 组件 | OpenCode | Codex | 常驻？ |
|---|---|---|---|
| c1 系统提示词 | `systemprompt.md` | `AGENTS.md` | 是 |
| c2 护栏 | `guardrails.md` | `AGENTS.md` | 是 |
| c3 记忆 | `LongTermMEMORY.md` | `AGENTS.md` | 是 |
| c4 工具说明 | `tool_descriptions/tool_guidance.md` | `AGENTS.md` | 是 |
| c5 子代理 | `sub_agents/<name>/AGENT.md` | 同左 | 否 |
| c6 技能 | `skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` | 否 |
| c7 脚本 | `scripts/*.py` | 同左 | 否 |
| c8 插件/hooks | `plugin/*.ts` | `.codex/hooks.json` | 工具循环内 |

常驻上限：OpenCode **24,000** 字符；Codex **8,000** 字符（Codex 自身还会在 32,768 字节截断）。超限 → 校验失败。

编辑契约：**省略即继承**；越界文件 → **整棵提案作废**（不是静默丢弃），错误原文回灌、重试一次。

### 8.2 证据协议

- 证据是文件：`context/{task.md,source,rollouts,deliverables,harness,library,pool,decisions}`；
- prompt 只有指针（`MISSION.md`），轨迹不按 prompt 预算截断；
- 产物用**判分器同款读取器**渲染；
- 三道路障：`leak_globs`（答案键路径）、7 个 grader 标记绊线、`RunView` 不带分数；
- 证据是数据不是指令：明确禁止执行候选 harness 里的脚本。

### 8.3 预算与账本

`R = 4k`（k 基线 + 2k 候选 + k 终版），`P = 3k`（INV + DOM + 合成）；`ledger.assert_identity()` 对不上即 `BudgetError`。选择与反思不计入 R/P。

### 8.4 选择与反思

- 选择池**只含 finals**（基线不入池）；k=1 跳过选择会话；失败回退到已交付基线；
- 反思产出一张 `edits` 表（`helped|hurt|no observed effect|confounded`）+ 至多一条经验；
- 经验入库门槛：`cue` / `lesson` / `evidence` 三字段非空，缺一不入库。

### 8.5 经验库

按 `bench × mode × k × domain` 隔离；条目 = 完整被采纳树 + `EXPERIENCE.md`；`INDEX.md` 每次从磁盘重建。**下一个任务仍从 base harness 起步**，库只作为证据。

### 8.6 续跑与缓存

调用指纹 = `sha256(label, mission, system, 证据路径 + 每个文件字节)[:16]`；命中 → `usd=0`、`cached=True`。任务续跑还校验 chosen run 目录、产物可重读、`harness_fingerprint` 一致；任一不符 → 重跑该任务。

---

## 9. CLI 与配置

| 命令 | 作用 |
|---|---|
| `harness2 --bench lab --k 1 --mode parallel` | 跑递归（可 `--dry-run`） |
| `harness2-judge --bench lab --k 1 --mode parallel` | 采纳后判分（`--bases` 给基线打分） |
| `harness2-report --bench lab --k 1 --mode parallel` | 汇总结果 |
| `harness2-setup lab` | 安装基准与依赖 |
| `harness2-check --bench lab` | 预检 |
| `harness2-demo --output runs/demo --k 2` | 无凭证 demo |
| `harness2-bridge` | 本地模型代理（可选） |

关键参数：`--bench` `--k` `--mode` `--substrate oc|cx` `--workers` `--seed` `--only-domains` `--max-tasks`（**每域**上限）`--dry-run` `--split-file`。

关键环境变量：`HARNESS2_RUNS_DIR`、`HARNESS2_SOLVER_MODEL`、`HARNESS2_IMPROVER_MODEL`、`HARNESS2_CODEX_BASE_URL` / `_API_KEY`、`HARNESS2_JUDGE_MODEL`。

**run / judge / report 必须同 bench、executor、subset、k、mode、seed。**

---

## 10. 已知限制（与 PRODUCT-DESIGN §7 对应）

| # | 限制 | 影响 | 最小加固 |
|---|---|---|---|
| 1 | 候选只 probe 1 次（n=1） | 因果判定弱 | top-1 候选 n=2 重复 |
| 2 | 循环内无留出验证 | 过拟合只能事后发现 | test 集固定，只评估不进库 |
| 3 | 组件数量/体积无上限 | 树与库膨胀、上下文变贵 | `validate_tree` 加体积/数量上限 |
| 4 | 脚本/插件/hooks 无内容审查 | 任意代码随执行器权限运行 | import 白名单 + hooks 审核 |
| 5 | 执行器网络不限 | 可下载公开 rubric | egress 白名单 |
| 6 | 改进/选择/判分可同源 | 自我偏好 | judge 换独立模型 |
| 7 | 选择与反思不计入 R/P | 真实成本被低估 | 纳入账本 + token 预算 |
| 8 | 无 OS 沙箱（Codex 关闭） | 多租户不可直接暴露 | 容器 + 只读挂载 |
| 9 | 测试仅 229 行 | 回归风险 | 补 workflow / 校验器测试 |
| 10 | k=1 无跨候选比较 | 单步结论更弱 | 池中加匿名基线做 A/B |
| 11 | dsh/hermes 只跑 local 基准 | 三个专业基准仍需原生 launcher | 按基准实现 launcher（见 docs/SUBSTRATES.md） |
| 12 | dsh/hermes 轨迹是文本 stdout | 看不到工具级动作 | 接 DSH 会话 JSONL / Hermes ShareGPT 渲染 |

---

## 11. 代码地图

| 文件 | 职责 |
|---|---|
| `core/schedule.py` | 命名空间、任务流、单任务/域/sweep、续跑、summary 合并 |
| `core/recursion.py` | 两种模式的阶段编排与失败回退 |
| `core/context.py` | 证据树、mission、泄漏与 verdict 绊线 |
| `core/tree.py` | c1–c8 读写、校验、diff、多样性 |
| `core/proposer.py` | 改进者会话、超时重试、harvest、审计、指纹缓存 |
| `core/selection.py` | 候选清洗、匿名、盲选、反思、经验提炼 |
| `core/library.py` | 领域库：入库、索引、检索 |
| `core/prompts.py` | 全部作业文本（INV/DOM/合成/选择/反思） |
| `core/ledger.py` | 预算恒等式 |
| `core/spec.py` | 基准/执行器/通道抽象 |
| `substrate/opencode.py` | OpenCode 投影、三种安装模式、事件流渲染 |
| `substrate/codex.py` | Codex 投影、codex_root 安装、exec 参数、thread-item 渲染 |
| `substrate/manifest.py` | 声明式 substrate：SurfaceSpec/安装/exec/轨迹 + GenericSubstrate |
| `substrate/registry.py` | 名称 → Substrate；内置 + manifest 发现 + 别名 |
| `substrate/host.py` | 通用 host rollout：workspace + 任务文件 + CLI + 产物快照 |
| `substrate/manifests/{dsh,hermes}.json` | DSH / Hermes 的真实可编辑面与调用契约 |
| `benchmarks/local.py` | 任意目录任务 × 任意 manifest substrate（check.sh 判定） |
| `benchmarks/*.py` | 三基准：任务发现、执行、轨迹/产物、判分 |

---

## 12. 术语

见 [PRODUCT-DESIGN.html §8](PRODUCT-DESIGN.html#s8)。

---

## 13. 文档与原型清单

| 文件 | 内容 |
|---|---|
| `PRODUCT-DESIGN.html` | 产品设计（10 章）：定位、概念、架构、模块图、技术方案、数据模型、ER、时序、指南、7 页原型、10 个难题、术语、FAQ |
| `PROJECT-DOC.md` | 本文档：快速开始、架构、流程、时序、ER、机制、限制、代码地图 |
| `prototype/workbench.html` | 总览：run 身份、领域矩阵、异常事件 |
| `prototype/run.html` | 单任务：基线 / 阶段 / 采纳 / 反思 |
| `prototype/pipeline.html` | Propose–Probe–Compose 四列 + diff 抽屉 |
| `prototype/evidence.html` | 证据树 + 内容 + 纪律 |
| `prototype/library.html` | 领域库：INDEX / EXPERIENCE / 门槛 / 空态 |
| `prototype/ledger.html` | 账本：恒等式、规模换算、指纹 |
| `prototype/report.html` | 报告：base vs refined、判分口径、复现 |
| `prototype/assets/` | 设计令牌与外壳（复用同机参考文档的工业暗色系统） |
| `../SUBSTRATES.md` | 执行器接入：OpenCode / Codex / DSH / Hermes / 自定义 manifest / local 基准 |

---

## 附录 A · 实测数字（2026-10-23）

- Python：43 模块 / 14,142 行（core 14 / 5,897；substrate 7 / 1,925；benchmarks 8 / 4,431；cli 9 / 1,121）
- 测试：2 文件 / 650 行（含 substrate + local 端到端）
- CLI 入口：7 个
- 可编辑组件：8 个；基准：3 个；安装模式：4 种
- 预算：每任务 4k rollouts / 3k 编辑会话
- 常驻上限：OpenCode 24,000 / Codex 8,000 字符；Codex 版本 0.144.6
- demo 运行：`runs/demo`（ledger-1 / ledger-2，k=2，agg + seq，R=8 / P=6）

## 附录 B · 本文档的验证方式

- 原型页均通过 headless Chromium `--dump-dom` 冒烟（脚本注入、外部资源为零）；
- 文档内所有 iframe / CSS / JS 均为相对路径本地文件；
- 演示数字的来源在正文中逐处标注（demo 产物 / README 口径 / 演示占位）。

## 附录 C · 许可

代码 Apache-2.0；文档与原型随仓库分发。上游声明「不是官方支持的 Google 产品」。
