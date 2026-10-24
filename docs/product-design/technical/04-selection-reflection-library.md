# 04 · 盲选、反思与跨任务经验库

[返回索引](README.md) · [递归算法](02-recursion-algorithms.md) · [证据安全](07-evidence-security.md)

## 一、选择池如何确定

`core/selection.py::selection_pool` 是唯一的生产池构建函数：

- agg: `agg1..aggk`
- seq: `c1..ck`
- base rollout 不加入池，所以算法不提供 abstain（弃权）选项。
- pool 中缺失的标签会被跳过；随后会对不可运行/不可交付/渲染失败/空内容候选做过滤。

这是一项方法约定：要求 k 个终版之间相对比较，`KEEP_BASE` 应由 compose 生成一棵与输入树一致的终版来表达。故“盲选一定会选到比 base 好的结果”不是代码保证。

## 二、过滤、匿名与 selector 输入

`run_selection` 对每个候选执行 `_render_pool`：

1. 必须 `RunView.ok` 且 `delivered`。
2. 用 `adapter.deliverable_text` 渲染；异常、空文本就 drop。
3. 若该 benchmark `state_deliverable`，且没有交付物候选，才可能退化为用候选 trajectory 作为 selector 输入。
4. 按 `task_id | k | mode` 的 SHA-1 前缀产生固定 shuffle，候选映射为 A、B…；相同身份重跑匿名次序稳定。
5. 保存完整未截断的候选文本到 `selection/<mode><k>/candidates/<id>.md`。adapter 声明的原始资产只按资产 API 复制，不扫描任意 run 目录。
6. selector 可读 task、source、匿名候选及资产；不提供 label、harness 树、run id 或 grader score。
7. 检查评分 verdict 标记后启动选择会话；回复必须有合法的 choice block，why 可选。

候选编号是可复现的匿名化，不是密码学上的保密或抗串通方案。模型也可能从交付物内容猜到来源。

## 三、选择结果与失败分支

| 条件 | 代码行为 |
|---|---|
| `k=1` 且唯一终版可交付 | `trivial_selection` 直接选它，不启动 selector session |
| 多候选，回复合法 | 存 chosen label/run id/匿名映射/why |
| selector 不可用、超时、choice block 无效 | `pool_kind="finals"` 时选择排序第一个已通过过滤的 final，记 `fallback=true` 和 error |
| 所有 final 都被 drop | selection 失败；没有基线 fallback，因为 base 根本不在池里 |
| adapter 的实现错误 | 通常向外抛出，让该模式记录错误；不会假装是 selector 的普通失败 |

selection 文件在 `runs/<namespace>/<task>/selection/<mode><k>/selection.json`。其身份用于后续 judge 找到 chosen run；selection 本身不计算质量分。

## 四、反思读取什么

只有配置了该模式的 `Library` 且选择成功时，`schedule._run_one_mode` 才调用 `reflect_adoption`。反思会话被提供：

- 被选候选的完整 evidence/context；
- 所有候选树、rollout、交付物和 unified diff；
- 原始 base harness；
- `adoption.json`：匿名 ID 映射、chosen label 和选择理由；
- 每个 stage 的决策 notes/error。

reflector 尝试输出 `<edits>` 和 `<experience>`。experience 字段中 cue、lesson、evidence 三项都必须是非空字符串；confidence 可选。结构缺失或三项任一为空时，`sel.experience` 为空，采纳结果仍然有效，只是不产生可入库 lesson。

`edits` 标签如 `helped|hurt|no observed effect|confounded` 是反思 prompt 的描述分类；它基于同任务观察，不等价于已通过统计检验的因果结论。代码不会用 grader score 给 edits 定标签。

## 五、经验库如何跨任务工作

`core/library.py::Library` 对每个 `(run root, mode, domain)` 管理一份文件库：

```text
<library-root>/<mode>/<domain>/
  INDEX.md
  harnesses/<task-slug>/
    harness/<c1-c8 files...>
    EXPERIENCE.md
```

`Library.add` 只在 `has_experience` 通过时保存。它会再次扫描采纳树和经验文本中的 grader verdict 标记，写完整 tree 与 why/edits/lesson，然后从磁盘重建 `INDEX.md`。`context_dir()` 只有在 index 和经验文件都存在时返回该库。

`schedule.run_domain` 以固定 task 顺序串行处理一个领域，并在每个任务前 `retain` 只保留 stream 中更早 task 的条目。这使新任务看不到当前 task 或未来 task 的经验，减少向后泄漏。不同 domain 的库物理分开；并发仅发生在不同 domain。

**重要：**经验条目里的完整旧 harness 是可读参考，并不会自动作为新任务的起始树。新 task 仍读取 `adapter.base_harness()`；若 proposer 认为某条经验适用，它必须把改动写进当前 task 的 `context/harness/`，重新通过校验和 rollout。

## 六、设计取舍与风险

- **为什么匿名且不看分数？** 降低按来源/模型偏好的倾向，避免评分器输出直接进入优化循环。
- **为什么库按 mode 分开？** 避免 agg 与 seq 的产物互相写入同一 mutable library，保证比较组隔离。
- **为什么保留完整 tree？** 后续任务可查看修改上下文和实现细节；单条 lesson 摘要可能丢掉具体策略。
- **代价：** selector 和 reflector仍由模型完成，可有主观偏差；最终池排除 base，无法直接选择“维持基线”；单 task 反思可能过拟合。
- **隔离说明：** `run_domain` 在 sweep 顺序中的经验会前向传播；跨独立 run 默认使用各自 library root，指定 tag 才可选择共享。

相关代码：`core/selection.py`、`core/library.py`、`core/schedule.py::_run_one_mode/run_domain`、`core/prompts.py::select/experience`。
