# 06 · 文件状态、预算账本与断点续跑

[返回索引](README.md) · [递归算法](02-recursion-algorithms.md) · [盲选与经验库](04-selection-reflection-library.md)

## 为什么选择文件式状态

Harness² 没有数据库。文件可以直接 diff、grep、打包和人工审阅；代价是没有数据库事务、跨 run 查询引擎和多租户隔离。运行数据按 namespace 和 task 分目录，summary 是该任务汇总的主要事实源。

典型目录形状（具体 artifact 子目录依 adapter 而不同）：

```text
runs/<namespace>/<task>/
  summary.json
  harnesses/<label>/              各 stage/final 的 harness 快照
  context/<label>/                提案阶段证据树
  executions/<namespace>/<task>/<label>/   solver rollout
  selection/<mode><k>/selection.json
  reflection/<mode><k>/            反思输入/edits/experience
```

改进模型的 `.audit`、提案缓存和 harvested workspace 使用 `Proposer.audit_dir` 指向的目录；经验库存于配置的 library root 下，并按 mode/domain 分开。

## Namespace 和 run identity

`core/schedule.py::Namespace` 以 benchmark、substrate、mode、k、seed、tag 组成实验身份。Namespace 决定 summary/run 目录，隔离不同实验配置；同一 task 下 run id 再附加 label（如 `base1`、`inv1`、`agg1`）。

复现至少需要一致的 benchmark、substrate、split/task subset、k、mode、seed、tag、模型配置和依赖版本。seed 参与 task stream ordering/命名空间；它不会让外部模型输出天然确定。

## summary 的并发合并

不同模式或调用方可能先后给同一个 summary 写入部分数据。`schedule._write_summary`：

1. 对同路径 `.lock` 文件使用 `fcntl.flock(LOCK_EX)`。
2. 读旧 JSON，将 `modes` 和 `selections` 按 mode token 合并；错误列表去重并根据当前成功结果清理已解决错误。
3. 写入同目录临时文件 `<summary>.json.tmp`。
4. `os.replace(tmp, summary)` 原子替换，避免读者观察到半截 JSON。
5. 释放文件锁。

这不是跨多个 run 目录的事务。锁主要保护同一 summary 的合并；不同 artifact 与 summary 仍可能在进程中途留下部分结果。

## R/P 账本：断言不是预测器

`core/ledger.py` 保存每个 mode/task 的 entries，`assert_identity()` 检查 label 不重复后再核对：

```text
expected_rollouts(k) = 4k
expected_sessions(k) = 3k
```

session entry 是一个逻辑 Stage；一次 Stage 内的传输 retry / 校验 retry 会增加 `session_attempts`，但不增加 P 条目。Selection 与 Reflection 的 Proposer 调用不计入 P。judge 也不计入 R/P。

若 label 重复，即使总数碰巧凑齐，也抛 `BudgetError`：同一 run id 可能覆盖前一结果，故不能只检查数量。账本按 phase 完成后的稳定输入顺序记 entry，因此线程完成顺序不会改变 label 列表。

注意这些预算**逐 mode 计算**。当 agg/seq 同任务一起运行，base RunView 共享、物理执行一次；两份 ledger 各包含 k 个 base entries。k=2 时每模式 R=8/P=6；双模式合计物理 solver rollout=14，而非 16。

## 两种不同指纹

| 指纹 | 代码 | 输入与用途 |
|---|---|---|
| proposer call fingerprint | `core/proposer.py::_call_fingerprint` | SHA-256 前缀；纳入 label/mission/system、context 绝对路径、相对路径与每个文件字节；用于模型会话缓存 |
| harness fingerprint | `core/runmark.py::fingerprint` | SHA-1 前缀；按相对路径与内容计算 Tree 身份；忽略 pycache/pyc/.DS_Store 和完成 marker；用于 rollout 完成标记和 chosen harness 续跑核验 |

路径也参与 proposer fingerprint：同文本但处于不同模式/task workspace 的调用不会被误当成相同 session。

## 两类可复用结果

1. **Proposer session cache：** `_cached` 查找同 label+fingerprint 的 response；要求 harvest 时必须有可用 workspace；缓存回复 cost=0。它只复用改进者会话。
2. **Solver rollout reuse：** adapter 可借助 run id 和 `.adapt_complete` marker 判断是否已完成；marker 记录 harness fingerprint 和版本。各 adapter 的幂等/复用细节不同，WorkBuddy 以 trial 数据组织。

不要把“summary 可续跑”“proposer 调用缓存”“solver rollout 已存在”称作同一个缓存层。

## summary 续跑算法

`schedule._resume` 只有在 summary `status == "ok"` 且覆盖当前请求所有 mode 时才尝试跳过。每个被选 mode 还须有 `ok` selection 和 chosen label；然后逐一核验 chosen run_dir、可读交付物（或被选 trajectory）、可选 `artifacts_intact`、harness tree 存在和 harness fingerprint 一致。

任一核验失败会打印具体原因并重新跑 task，不尝试拼接“部分新结果 + 部分旧结果”。续跑成功时将 cached selections/summary 载入，并 `_restore_libraries` 把此前 task 的合格经验恢复到 domain library。

这是一种保守策略：避免界面/报告把树已改、产物已删的旧选择伪装成当前结果；代价是一个 mode 的 chosen artifact 损坏可能导致整个 task 请求重跑。

## Judge 与 Report 是独立的读写周期

`harness2-judge` 读取已写入 summary 的选择，调用 adapter `score`，把 `Scored` rows 输出到 judged JSON。失败或 scorer 返回 None 是 ungraded，不是 0 分。它允许给 base 单独判分；基线并不因此进入 selection 池。

`harness2-report` 汇总 judged 文件与 run 记录；未评分任务从评分分母排除。report 不重新执行 task，也不负责修复 judge 失败。

继续阅读：[CLI 流程](08-cli-runbook.md) · [adapter 的 artifact 语义](05-adapters-substrates-channels.md) · [经验库](04-selection-reflection-library.md)
