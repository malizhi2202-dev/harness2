# Harness² 技术详解

> 面向需要沿代码理解实现的读者。每篇都回答四个问题：涉及哪些代码、算法/机制如何运作、它解决什么问题、当前边界在哪里。
> 本组说明以仓库当前实现为准；“设计目标”和“已实现行为”分开描述。代码路径均相对仓库根目录。

## 阅读地图

| 顺序 | 专题 | 主要问题 |
|---|---|---|
| 1 | [系统架构与代码边界](01-architecture.md) | 从 CLI 到 rollout、方法层与插件接口怎么接起来？ |
| 2 | [任务递归算法](02-recursion-algorithms.md) | 并行/串行模式各自如何生成候选、取得证据与选终版？ |
| 3 | [Stage、证据与提案会话](03-stage-context-proposer.md) | 单次改进调用怎样建上下文、调用模型、收回并验证树？ |
| 4 | [盲选、反思与经验库](04-selection-reflection-library.md) | 如何匿名选择、提炼经验、跨任务提供参考？ |
| 5 | [Benchmark、Substrate 与 Channel](05-adapters-substrates-channels.md) | 基准、求解执行器、改进模型通道如何解耦？ |
| 6 | [文件状态、预算与续跑](06-state-budget-resume.md) | 结果保存在哪里、预算怎样核对、如何安全续跑？ |
| 7 | [证据隔离与安全边界](07-evidence-security.md) | 如何减少评分泄漏？哪些执行风险仍然存在？ |
| 8 | [CLI 与一次完整运行](08-cli-runbook.md) | 参数如何进入调度，run/judge/report 的职责是什么？ |
| 9 | [Harness 可编辑面](09-harness-surface.md) | c1–c8 如何映射到 SurfaceSpec、路径规则和校验器？ |

## 图与文档入口

- [双层递归概念架构图（SVG）](../architecture.svg)：模式、反馈回路、八个可编辑面。
- [产品设计与交互原型（HTML）](../PRODUCT-DESIGN.html)：产品背景、概念、模块图、数据图及原型。
- [项目速览（Markdown）](../PROJECT-DOC.md)：快速开始、主要机制与代码地图。
- [Substrate 接入说明](../../SUBSTRATES.md)：现有执行器的接入/配置细节。

## 先掌握三条实现事实

1. **一个任务的基线只物理执行一次。** `schedule.run_task` 得到的 k 个基线 `RunView` 会传给所请求的各递归模式；每个模式自己的账本会把这 k 个基线计入 R。因此双模式同时跑时，账本是“每模式口径”，不是物理进程总数。
2. **并行模式有阶段屏障。** 2k 个提案 stage 全部结束后才批量试跑候选；候选证据齐备后才运行 k 个独立合成器；所有终版再各跑一次。
3. **经验不是自动套用的 harness。** `library_dir` 里的条目被复制进提案上下文；新任务的工作树仍从 `adapter.base_harness()` 读取。

## 数字和术语口径

- `k`：每个任务、每个模式的规模参数；不是任务总数。
- `R=4k`、`P=3k`：每种模式的账本恒等式。R 是 rollout 条目数；P 是改进 stage/session 条目数。重试次数另记，selector 与 reflection 不包含在 P 中。
- `RunView.ok` 与 `RunView.delivered` 不等价：前者表示执行目录存在/执行返回，后者表示 benchmark 判断产物可交付。
- “并发”只说明代码使用线程池；实际最大宽度由配置控制。默认值请以 `harness2/config.py` 为准。
- `AGG`、`COMPOSE` 是提示词/阶段类型；`agg`、`seq` 是落盘模式 token。

## 源码版本提示

专题正文会指到关键函数而非复制整个源文件。若实现后续变化，以函数源码和对应测试为准；更改算法时应同步更新对应专题和架构图。
