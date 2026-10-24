# 08 · CLI 与一次完整运行

[返回索引](README.md) · [架构](01-architecture.md) · [文件状态](06-state-budget-resume.md)

## 运行阶段总览

```mermaid
flowchart LR
  C["配置 / 安装"] --> P["harness2-check / dry-run"]
  P --> R["harness2：run sweep"]
  R --> S["runs 下 summary + executions"]
  S --> J["harness2-judge：独立判分"]
  J --> D["judged.json"]
  D --> RP["harness2-report：汇总"]
```

## 命令职责

| 命令 | 代码入口 | 做什么 / 不做什么 |
|---|---|---|
| `harness2` | `cli/run_sweep.py` | task stream、递归执行、summary 与 sweep record；可 `--dry-run` 查看任务顺序和 namespace |
| `harness2-check` | `cli/check.py` | 检查基准依赖、凭证/执行器和任务可用性，不是实际跑实验 |
| `harness2-setup` | `cli/setup.py` | 准备 pinned benchmark 依赖 |
| `harness2-demo` | `cli/demo.py` | 确定性本地 demo，不需连接模型；证明流程能走通，不代表模型性能 |
| `harness2-judge` | `cli/judge.py` | 独立读取 selection 并调用 benchmark score；可用 `--bases` 单独评估基线 |
| `harness2-report` | `cli/report.py` | 读取 judged 数据和 summary 成本，输出汇总，不运行模型 |
| `harness2-console` | `cli/console.py` | 可选只读文件控制台；rerun 需要显式启动权限标记 |
| `harness2-bridge` | `cli/bridge.py` | 可选本地模型代理，不是默认核心服务 |

## 常用命令

```bash
# 1) 安装项目及可选 artifact renderer
uv pip install -e '.[render]'

# 2) 先确认配置和执行器可用
harness2-check --bench lab --substrate oc

# 3) 预览 task stream、namespace 和 domain，不做模型调用/rollout
harness2 --bench lab --substrate oc --k 1 --mode parallel --dry-run

# 4) 先小规模跑一个 domain，每个 domain 最多两个 task
harness2 --bench lab --substrate oc --k 1 --mode parallel \
  --only-domains accounting --max-tasks 2

# 5) 完成后独立判分，再生成报告
harness2-judge --bench lab --substrate oc --k 1 --mode parallel
harness2-report --bench lab --substrate oc --k 1 --mode parallel
```

具体依赖、路径和凭证要求随 benchmark 变化；运行前以 `docs/SETUP.md`、`docs/BENCHMARKS.md`、`docs/SUBSTRATES.md` 与 `harness2-check` 输出为准。

## 参数如何影响行为

| 参数 | 代码效果 | 常见误区 |
|---|---|---|
| `--bench` | 选择 adapter，jb/wb/lab/local | 名称相似不代表任意 substrate 组合可用 |
| `--subset` | WorkBuddy 的 office/web/code 任务子集 | 其他 benchmark 不接受 |
| `--substrate` | 选择 solver harness/runtime 投影 | 不会自动改变 proposer channel |
| `--k` | 每任务每模式的递归宽度/深度，影响 R=4k/P=3k | 不是 sweep task 数 |
| `--mode` / `--modes` | parallel/agg 和 sequential/seq；run 可接逗号列表 | 落盘 token 使用 agg/seq |
| `--seed` | task stream 顺序、namespace/run identity | 不保证模型输出确定 |
| `--split` / `--split-file` | 任务切分 | split 会影响 task universe，应在 run/judge/report 保持一致 |
| `--workers` | 同时执行的 domain 数 | 不代表同 domain 的 task 并行 |
| `--only-domains` | 只跑列出的 domain | 错误 domain 会校验失败 |
| `--max-tasks` | 每个 domain 的 stream 前 N 个任务 | 不是全 sweep 的任务总上限 |
| `--local-tasks` | local benchmark 的任务目录 | 未提供且无环境变量时 adapter 构建失败 |
| `--dry-run` | 只打印身份和任务顺序后退出 | 不验证完整 solver rollout 是否可运行 |

## run/judge/report 的身份一致性

这些命令必须指向同一组 benchmark、subset、substrate、k、mode、seed 和任务过滤条件。Sweep 会写 `config.json` 记录模型与任务身份；复用同目录但改配置时，CLI 会拒绝混用并建议使用新 runs 目录。Judge model 也会单独记入身份；改判分模型时不能将新旧 judged 结果误并成一组。

## 配置入口

环境变量由 `harness2/config.py` 读取。常见项包括：

- `HARNESS2_RUNS_DIR`：运行文件根目录。
- `HARNESS2_SOLVER_MODEL`、`HARNESS2_CODEX_MODEL` 等：各 substrate 的 solver model。
- `HARNESS2_IMPROVER_MODEL`、`HARNESS2_IMPROVER_CHANNEL`：改进会话配置。
- `HARNESS2_JUDGE_MODEL`：judge 身份/模型配置。
- `HARNESS2_LOCAL_TASKS`：local task root。
- `HARNESS2_*_API_KEY` / provider credential：按具体 runtime/provider 提供。

不要把“subprocess 成功启动”视为“实验配置正确”。先用 check，再 dry-run，最后只跑少量 task 查看真实轨迹和产物。

## 一次 task 的落盘检查清单

1. 找到 `runs/<summary-namespace>/<task>/summary.json`，检查 status、modes、selections 和每个模式 ledger。
2. 在 `harnesses/` 比较 base、候选和 selected tree；确认选择记录的 `chosen_label`。
3. 在对应 execution directory 看 adapter 产物和 trajectory；判断 `ok` 与 `delivered` 是否一致。
4. 在 `selection/<mode><k>/selection.json` 核实匿名映射与 fallback 字段。
5. 若使用经验库，检查 `library/<mode>/<domain>/INDEX.md`、对应 `EXPERIENCE.md` 和完整 harness。
6. 跑 judge 后再看 `judged.json`；`ungraded` 是缺失/失败，不是零分。

## 排错先看哪里

| 现象 | 先看 |
|---|---|
| sweep 一开始失败 | `harness2-check` 和 `preflight.py` 错误；channel/model 字符串是否成对 |
| 全部 base-failed | benchmark launcher、solver CLI、认证和 run directories |
| stage fell_back | `summary.modes[mode].stages[].error`、该 stage 的 context 和 `.audit` |
| rollout ok 但无交付 | adapter `delivered` 与 artifact extractor，不要从 score 推断 |
| summary 不续跑 | `_resume` 输出原因；检查 selection run_dir、产物完整性和 harness fingerprint |
| report 无数据 | 先确认 judge 已写 `judged.json`，参数和 namespace 一致 |

继续阅读：[状态模型](06-state-budget-resume.md) · [安全限制](07-evidence-security.md)
