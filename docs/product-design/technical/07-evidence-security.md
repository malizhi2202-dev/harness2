# 07 · 证据隔离、评分泄漏与执行安全

[返回索引](README.md) · [证据构建](03-stage-context-proposer.md) · [适配边界](05-adapters-substrates-channels.md)

## 安全目标

核心实验目标之一是避免 proposer、selector、reflector 直接看到 grader 的评分结果/隐藏答案，再把它们当成 harness 能力改进依据。代码组合了任务文本约束、路径过滤、产物读取 API、内容绊线和分离的 judge 命令。

这些措施属于**实验信息边界与路径边界**。它们不是通用 prompt injection 防御，也不是 OS sandbox、容器隔离或网络出口策略。

## 评分与改进的数据边界

| 数据 | Proposer / Selector / Reflector | Judge |
|---|---|---|
| benchmark 任务文本 | 收到 adapter 的 task prompt | 读 task id 并调用 scorer |
| source files | 可收到按 `leak_globs` 过滤后的源材料 | scorer 使用 benchmark 自身状态 |
| rollout 轨迹 | 收到由 adapter 统一渲染的行为轨迹 | 通常不需要从模型上下文重建 |
| deliverable | 提案可读 benchmark extractor 输出；selector 查看匿名候选输出 | adapter `score` 在选定执行目录上评分 |
| harness / 候选树 | 提案读当前树；反思读所有 tree/diff | judge 不参与 harness 修改 |
| grader score/verdict | 不放入 `RunView` 或正常 context | 采纳后写到 judged JSON |

`core/judge.py` 明确约束它只在 selection committed 后由 judge path 调用。主递归 prompts 也要求工作描述，不要求 scorer/rubric/score。

## 四道主要检查

### 1. Benchmark 声明答案路径

adapter 的 `BenchSpec.leak_globs` 声明敏感相对路径。`context.copy_source` 在拷贝 source 时跳过匹配项；`assert_no_leak_paths` 会在证据树进入会话前扫描路径。

### 2. Run 目录不被任意扫描进 context

轨迹、交付物、资产由 adapter 指定 API 读取。选择资产复制同样调用 `deliverable_assets`，不会把整个 solver run directory 复制给 selector；该目录可能包含 grader 输出。

### 3. Grader verdict markers 绊线

`context.GRADER_VERDICT_MARKERS` 检查一些已知标记（如 `pass_rate`、`passed_count`、确定性评估字段）：

- `assert_no_grader_verdicts(root)` 检查文件名与指定文本后缀内容；
- `assert_tree_has_no_grader_verdicts(tree)` 在模型编辑的 harness 文件进入 rollout / library 前扫描；
- 触发时抛出带路径、marker、偏移位置的异常，不是只写告警。

这只是已知 marker 的 tripwire。它无法证明任意文案中没有更隐蔽的评分信息；碰线时应追查 adapter 的读取路径，而不是任意放宽 marker。

### 4. Selection 输入匿名且去分数

`RunView` 不带 score；候选 id 由 task/mode/k 洗牌生成；selector 的 prompt/文件不含候选 label、run id 和 harness tree。它仍然读取任务、源文件、产物文本；匿名化减少显式身份偏差，不保证模型无法推断来源。

## 执行层真实威胁边界

候选 c1–c8 包括脚本、skills scripts、OpenCode plugin、Codex hooks。`tree.validate` 可以检查路径、Python 编译、保留脚本、hook JSON 事件/类型等结构，但**它不会做恶意代码审查**，合法脚本仍可能具有危险行为。

Proposer 和 solver 都通过外部程序运行。实际权限由操作系统、CLI 自身权限模式、当前用户、工作目录、网络和容器设置决定；`core` 的路径白名单只约束收回的 harness tree，不能约束子进程任意读取系统文件或访问网络。

因此该工具适用于操作者信任运行环境并能检查产物的本地研究流程。直接对不可信租户暴露时，仍需独立的进程/容器隔离、权限降级、网络出口控制、密钥管理、资源限额和审计策略。

## 与效果相关的限制

- 每个候选只有一次 rollout，随机误差可能被反思模型过度解释。
- 没有循环内留出集：经验库 lesson 可能只适用于当前任务。
- 当前 finals-only selection 没有“保持不变”的显式 base 选项；compose 可通过生成同树 final 表达 KEEP_BASE，但之后仍选该 final。
- 选择/反思模型可能与 proposer 同源；逻辑隔离不等于模型独立性。
- 已知 markers 和 leak globs 不是形式化 noninterference 证明。

详见 [PROJECT-DOC 限制表](../PROJECT-DOC.md) 和 [产品设计难题](../PRODUCT-DESIGN.html)。
