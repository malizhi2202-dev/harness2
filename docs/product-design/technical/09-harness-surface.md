# 09 · Harness 可编辑面：c1–c8、SurfaceSpec 与树校验

[返回索引](README.md) · [Stage 校验流程](03-stage-context-proposer.md) · [执行器适配](05-adapters-substrates-channels.md)

## 这八类组件在代码里是什么

c1–c8 是产品解释层的分组，不是八个固定类。代码将它们表示为 `{relative_path: file_text}` 的 `Tree`，并由 substrate 提供的 `SurfaceSpec` 决定允许路径、always-on 文件、文本上限、skill 目录和 hooks 规则。

| 组件 | 中文含义 | OpenCode 常见位置 | Codex 常见位置 | 运行时作用 |
|---|---|---|---|---|
| c1 | 系统提示词 | `systemprompt.md` | `AGENTS.md` 中对应段落 | 描述角色、任务策略与回答风格 |
| c2 | 护栏 | `guardrails.md` | `AGENTS.md` | 约束权限、行为边界和校验要求 |
| c3 | 常驻记忆 | `LongTermMEMORY.md` | `AGENTS.md` | 每次请求都可能进入上下文的背景信息 |
| c4 | 工具指引 | `tool_descriptions/tool_guidance.md` | `AGENTS.md` | 指导模型怎样使用可用工具 |
| c5 | 子代理 | `sub_agents/<name>/AGENT.md` | `sub_agents/<name>/AGENT.md` | 为特定任务提供独立角色/工作分工 |
| c6 | 技能 | `skills/<name>/SKILL.md` | `.agents/skills/<name>/SKILL.md` | 描述按需触发的可复用工作流 |
| c7 | 脚本 | `scripts/*.py` 或 skill scripts | `scripts/*.py` | 提供可执行的确定性辅助动作 |
| c8 | 插件 / hooks | `plugin/<name>.ts` | `.codex/hooks.json` | 在工具循环中集成事件处理/扩展行为 |

实际路径以 `OPENCODE_SURFACE`、`CODEX_SURFACE` 或 manifest 里的 surface 为准。Codex 的 c1–c4 可能都在一个 AGENTS.md 中；c1–c8 是职责映射，不代表每个 runtime 都有八个文件，也不代表 manifest substrate 一定支持全部八类。

## SurfaceSpec 如何成为路径白名单

`core/tree.py::SurfaceSpec` 包含 always-on 路径、字符上限、skill dir/name rule、skill trigger 提示、subagent mode 要求、plugin dir、hooks file/schema/event 集以及运行时能力开关。`_rx(surface)` 将这些声明组合成路径正则。

`normalize_rel` 首先规范分隔符并拒绝绝对路径及 `..` traversal。`is_allowed` 逐项判断 always-on、hook、skill、subagent、script、plugin 等允许 pattern。`read_tree` 只读可允许路径，并把 rejected 路径返回调用方；`write_tree` 则把规范 Tree 写成执行器需要的文件布局（可选 seed/stub 文件）。

编辑契约是 **省略即继承**：模型只需返回修改文件，未输出的文件由原始输入 tree 保留。它不是“漏掉的文件删除”语义；只有显式通过文件操作删除后，workspace 内容变化才会反映为 tree 的删除。

## 验证阶段和检查目的

`tree.validate(surface, tree, ...)` 累积错误后一次抛出 `ValueError`，让 proposer 能在下一轮修正完整错误列表：

1. **always-on 预算：** 合并常驻文件后不得超过 surface cap；可配置拒绝空常驻文件。
2. **skill 结构：** skill path 与 name pattern 匹配；frontmatter/description 合法；常驻文本应能触发技能（自动触发修复见后文）。
3. **subagent 结构：** 目录/文件名、frontmatter 与 executor mode 符合 surface 约定；`BenchSpec.allow_subagents` 可再限制基准是否允许。
4. **脚本检查：** 相关 Python 通过 `compile`；引用的脚本文件存在；基准和 core 的 reserved basename 不被覆盖。
5. **hooks 形状：** JSON 可解析，schema、事件名、hook handler 类型符合当前 substrate validator。
6. **adapter 专属规则：** `_accept_tree` 之后还会调 `adapter.validate_tree(tree)`。
7. **verdict tripwire：** 再扫一次 grader marker，避免评分字样进入可执行或经验树。

`validate` 检查结构、可加载性和已声明约束，不评估策略语义是否有效，也不审查任意脚本/插件是否恶意。

## autotrigger 为什么存在

某些 agent framework 仅在常驻说明中被点名的 skill 才会触发。`tree.autotrigger` 对新 skill 查找 description，若 always-on 文本里缺触发词且剩余预算足够，则往 `autotrigger_file` 补一行触发说明；自动 advertise skill 的 surface 不需要此修复。若补一行会超预算，则跳过，随后 validator 仍可能报告不可加载问题。

它修复“skill 文件存在但模型永远想不到调用”的可发现性问题，不会增加 skill 本身能力。

## 为什么 harness 做成 Tree，而不是直接改执行工作区

1. proposer 操作的是一次会话的 `context/harness` 副本，不是 benchmark 源码或长期运行目录。
2. `read_tree` 将不同 runtime 文件路径统一成逻辑 Tree；`SurfaceSpec` 决定 projection。
3. 候选校验后才将 Tree 写入 `harnesses/<label>` 并交给 adapter/substrate 执行。
4. `diff_summary` 依据路径和文件内容说明变更，供合成、反思和审计使用。

这样把“候选编辑”与“真实运行环境部署”分开，并使每棵候选可独立重现。不过一旦 validation 通过，候选里的合法脚本/hook/plugin 仍会以 solver 子进程权限运行。

## 每类组件能解决的问题与代价

- **c1–c4 常驻文本：** 可持续塑造默认行为，代价是每次上下文都会付 token/字符成本；因此有 always-on cap。
- **c5/c6 模块化角色/技能：** 只在需要时触发或交给专门角色，避免所有细节常驻；代价是触发条件/元数据错误会造成静默不可用。
- **c7 脚本：** 让可确定的转换/校验从自然语言推理中移出；代价是执行器权限、依赖和副作用需要额外治理。
- **c8 hooks/plugins：** 在工具生命周期中介入；表达力强，风险和调试复杂度也最高。

继续阅读：[证据与安全](07-evidence-security.md) · [stage 校验](03-stage-context-proposer.md) · [c1–c8 产品概念表](../PRODUCT-DESIGN.html#s2-1)
