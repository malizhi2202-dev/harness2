# Harness² × DSH / Hermes 实跑记录

日期：2026-10-23 · 本机部署
模式：`--bench local --k 1 --mode parallel --workers 1`
改进者：`HARNESS2_IMPROVER_CHANNEL=dsh_cli` / `hermes_cli`（框架自己当改进者）
模型：DSH `deepseek-flash`；Hermes `deepseek-v4-pro`

## 结论（先说重要的）

1. **两条链路都跑通**：基线 → 2 候选 → probe → 合成 → 采纳 → 反思/入库；账本均
   `R=4 / P=3`，无 `fell_back`。
2. **修了一个会让实验作废的漏洞**：local bench 的 rollout 原先放在 `tasks/` 下，
   solver 可以向上走到 `tasks/.../check.sh` 读到答案。现在 execution 根目录在
   `/tmp/harness2-local/<root>-<hash>/`（或 `HARNESS2_LOCAL_EXEC_DIR`），
   summary / library 仍留在 `<root>/runs/`。
3. **改进能否被观测，取决于基线是否真的失败**。在隔离后的 hard 任务上：
   - DSH 基线 `105.25`，两个候选把 helper 的 `>0` 过滤修掉，产物 `85.15`；
     但 helper 额外写的 `-- reviewed --` 仍在，check 仍失败——这是框架**发现但没修完**
     的下一个缺陷。
   - Hermes 的候选遵守了任务里“不要改 helper”的措辞，产物仍是 `105.25`；没有可观测提升。
   - 公平措辞（允许修 helper）时，两家的基线自己就会审计并修好，基线通过 → 没有提升空间。
4. 结论：**这几个本地小任务不足以证明“涨分”**，它们证明的是闭环、隔离、候选生成与
   反思的诚实性；真正的增益要在专业基准 / 更长任务 / 更弱 solver 上测。

## 实跑 A：简单任务（3 行账本）

| | DSH | Hermes |
|---|---|---|
| namespace | `local-dsh-agg1` | `local-hermes-agg1` |
| 阶段 | inv1 / dom1 / agg1 | inv1 / dom1 / agg1 |
| 预算 | R=4 / P=3 | R=4 / P=3 |
| 采纳 | agg1 | agg1 |
| 基线 / 终版 | 85.15 / 85.15 | 85.15 / 85.15 |
| 库 | `NO_ENTRY`（无执行对比） | 有 lesson（判 confounded） |

采纳的改动：
- DSH：`AGENTS.md` 改全行带符号求和 + fallback/停止条件 + 写回校验；`scripts/total.py`
  去掉 `>0` 过滤，加列校验与错误码。
- Hermes：新增 `skills/ledger-total/SKILL.md` + `skills/ledger-total/scripts/check_answer.py`；
  `AGENTS.md` 加“先读 helper 代码、Decimal、写完重读”；`scripts/total.py` 去掉过滤。

## 实跑 B：带缺陷 helper（hard2）

任务：数据在 `input/ledger.dat`（base64），要求运行 `scripts/load_ledger.py`，并声明
“不要重实现/不要手算，decoder 是 source of truth”。helper 实际只加正数、并多写一行
`-- reviewed --`，check 期望单行 `Total: 85.15`。

| | DSH | Hermes |
|---|---|---|
| 基线 | `Total: 105.25` + marker（失败） | `Total: 105.25` + marker（失败） |
| 候选 | inv1/dom1/agg1 都改成 `85.15`，但 marker 仍在（失败） | 全部保持 `105.25`（失败） |
| 采纳 | agg1 | agg1 |
| 账本 | R=4 / P=3 | R=4 / P=3 |

解释：
- DSH 的候选**没有**遵守“别改 decoder”的措辞，修了算术；说明该任务本身是**矛盾任务**
  （任务说别改，隐藏 check 又要求改后的值），不适合作为“改进”基准。
- Hermes 遵守了措辞，所以没有改——这是正确的指令遵循，不是框架失败。
- 两者都保留了 `-- reviewed --`：说明一个缺陷被修掉后，**下一个缺陷**会立刻成为
  下一轮的信号（k=2 或第二次任务才会处理）。

## 实跑 C：公平措辞（hard3）

任务保留“helper 只加正数”的缺陷，但明确“账本总额是全部带符号行的净额”，不禁止修 helper。

- DSH 基线：自己审计 helper 并产出 `85.15`（通过）
- Hermes 基线：自己审计并产出 `85.15`（通过）
- 结论：模型能力足以绕过这个缺陷，基线不失败 → 没有提升空间

## 关键修复

### 1. DSH 安装改成 split

初版把 `scripts/` 装到 `$DSH_HOME`，solver 的 cwd 里没有 `scripts/total.py`，轨迹里出现
“no scripts directory”。现在：

```json
"install": {
  "mode": "split", "root": ".dsh",
  "entries": ["scripts"],
  "home_entries": ["AGENTS.md", "skills"],
  "seed_env": "DSH_HOME", "seed_default": "~/.dsh",
  "seed_files": ["settings.yaml", ".credentials.yaml", ".env"]
}
```

### 2. execution 隔离

```python
# LocalAdapter: /tmp/harness2-local/<root>-<hash>/executions/...
self.exec_root = ... / f"{self.root.name}-{digest}"
```

没有这一步，DSH 曾在 hard2 的 base rollout 里一路 `..` 找到 `tasks/.../check.sh`，
把答案读出来——base 的“通过”是假的。隔离后同一任务 base 正确失败（105.25）。

## 产物

```
local-bench/{dsh,hermes}/                     # 简单任务
local-bench/{dsh,hermes}-hard2/                # 带缺陷 helper（矛盾措辞）
local-bench/{dsh,hermes}-hard3/                # 公平措辞
local-bench/*/runs/adapt/<ns>/accounting__ledger/{summary.json,harnesses,selection,reflection}
/tmp/harness2-local/<root>-<hash>/executions/  # rollout workspace（隔离）
```

## 下一步（要真正测出增益）

1. 用 **LAB / JobBench / WorkBuddy** 的专业任务（基线本来就会失败），而不是玩具账本；
   这需要给这三个基准补 dsh/hermes 的原生 launcher。
2. 或者给 local bench 一个**明确超出 solver 能力**的多步任务（长文档、跨文件核对），
   并跑 `--k 2` 让第二轮修掉第一轮遗留的缺陷（本次的 `-- reviewed --` 就是例子）。
3. 用更弱的 solver 模型跑同一任务，观察 harness 是否把失败变成通过。
4. 批量跑时为两个框架设置隔离的 `DSH_HOME` / `HERMES_HOME`，避免污染真实会话库。

## 实跑 D：sequential `--k 2`（DSH，充值后完成）

命令：`--mode sequential --k 2`，namespace `local-dsh-agg2`（base）/ `local-dsh-seq2`（候选）。
第一次因 `dsh: QUOTA: Insufficient Balance` 在 c1 rollout 中断；充值后重跑，缓存命中
round-1 会话，只补跑缺失的 rollout 与 round 2。

**结果：`status=ok`，`R=8 / P=6`，无 `fell_back`，采纳 `c1`。**

| 产物 | 内容 | 判定 |
|---|---|---|
| base1 | `Total: 105.25` + `-- reviewed --` | fail |
| base2 | `Total: 85.15` | pass（同一 base harness 的第二个样本） |
| c1 | `Total: 85.15` | pass（**采纳**） |
| c2 | `Total: 85.15` | pass |

- round 1：`inv1` / `dom1` 的 rollout 都产出 85.15；`c1` 合成后 rollout 在充值时失败，重跑通过
- round 2：`inv2` / `dom2` 选择 `KEEP_BASE`，`c2` 也 `KEEP_BASE`（没有新证据 → 不改）
- 官方判分：`harness2-judge --bench local --substrate dsh --k 2 --modes sequential`
  → `judged.json`：1 row，1 graded，0 ungraded；`harness2-report`：**score 100.00%，picked final**
- 库：`runs/library/local/k2-dsh/seq/accounting/harnesses/accounting__ledger/EXPERIENCE.md`
- 采纳树：`AGENTS.md` 改成“工具输出要用领域定义校验；账本总额是全部带符号行的净额；
  解码权威与聚合正确性分开；产物只允许一行；不要满文件系统找 checker”；
  新增 `skills/ledger-total/{SKILL.md,scripts/check_ledger.py}`

这个任务仍然是**矛盾措辞**（任务说“别改 helper”，check 又要求改后的值），但顺序递归
在第二轮确实把 round 1 的修复保持住了；作为“基线失败 → 采纳后通过”的闭环证据是成立的。

## 仍然没解决的事

- `base2` 在同一 base harness 上自己就产出了 85.15，说明这个任务对 DSH 仍偏简单；
  “基线失败”只在 1/2 的样本上成立，不是稳定失败。
- Hermes 在 hard2 里遵守“别改 helper”的措辞，round 1 没修；在公平任务上基线又自己通过。
  要测 Hermes 的增益，需要更长/更弱模型/更专业任务。
- 更强的结论需要 LAB / JobBench / WorkBuddy 上的 dsh/hermes 原生 launcher。

## 待跑：Qwen Code / WorkBuddy

新增两个 manifest substrate 与配套的本地任务集，任务与 `dsh-hard3` 完全一致（公平措辞 +
带缺陷 helper：只加正数、多写一行 `-- reviewed --`），只是基线树的指令文件换成各自框架读的
那一个：

```
local-bench/qwen/base/QWEN.md                     # qwen（QianwenCode）
local-bench/qwen/base/scripts/load_ledger.py
local-bench/qwen/tasks/accounting/ledger/{task.md,check.sh,files/ledger.dat}
local-bench/workbuddy/base/CODEBUDDY.md           # workbuddy（CodeBuddy CLI 内核）
local-bench/workbuddy/base/scripts/load_ledger.py
local-bench/workbuddy/tasks/accounting/ledger/{task.md,check.sh,files/ledger.dat}
```

**这两个框架还没在本机登录，所以没有跑出分数。** 已核对的是接线（不花 token 也验证得动）：

- `LocalAdapter(root, substrate="qwen"|"workbuddy")` 能发现 `accounting/ledger`、认出基线树，
  `validate()` census 为 `always_on_chars=79 / n_files=2 / scripts=1`；
- `materialise()` 之后 workspace 里是 `<QWEN.md|CODEBUDDY.md>` + `input/` + `scripts/`，
  技能/凭据在暂存 home（`$QWEN_HOME` / `$WORKBUDDY_CONFIG_DIR`）；
- 用本机装的两家 CLI 各跑了一次 base rollout：qwen 以 “No auth type is selected” 退出 1
  （rollout 不标完成，下次重跑）；workbuddy 以 “Authentication required” 退出 0 且
  `output/` 为空（产物检查不过，同样会重跑）。两边的会话流都按 JSONL 落在 `session.jsonl`。

有凭据之后按下面跑（把 `HARNESS2_QWEN_MODEL` / `HARNESS2_WORKBUDDY_MODEL` 记成你自己用的模型，
它们只进 run identity）：

```bash
export HARNESS2_LOCAL_TASKS=$PWD/local-bench/qwen
export HARNESS2_QWEN_BIN=$(command -v qwen)
export HARNESS2_QWEN_MODEL=your-provider/model
HARNESS2_IMPROVER_CHANNEL=dsh_cli harness2 --bench local --substrate qwen \
  --k 1 --mode parallel

export HARNESS2_LOCAL_TASKS=$PWD/local-bench/workbuddy
export HARNESS2_WORKBUDDY_BIN=$(command -v codebuddy)
export HARNESS2_WORKBUDDY_MODEL=your-provider/model
HARNESS2_IMPROVER_CHANNEL=dsh_cli harness2 --bench local --substrate workbuddy \
  --k 1 --mode parallel
```

登录方式：`qwen` 用 `--auth-type`/`$QWEN_HOME/settings.json`，`codebuddy` 用 `/login` 写入
`$WORKBUDDY_CONFIG_DIR`，manifest 的 `seed_files` 会把它们带进每次 rollout 的暂存 home。
