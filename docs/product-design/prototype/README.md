# Harness² 控制台原型

这个目录有**两种形态**的同一套界面，服务两个不同目的：

| 产物 | 是什么 | 用途 |
|---|---|---|
| `workbench / run / pipeline / evidence / library / ledger / report.html` | 7 个**静态演示页**，每页独立、可上下滚动 | 嵌进 `PRODUCT-DESIGN.html` §6，逐页讲信息架构 |
| **`app.html`** | **单页前端实现**（自包含，零外链） | 用真实交互验证同一套信息架构：路由、筛选、抽屉、导出、四态、快捷键 |
| `assets/` | 设计令牌 `base.css`、图标 `sprite.js`、浮层引擎 `proto.js` | 静态页共用；`app.html` 不依赖它们，样式与图标内联 |

## 打开方式

```bash
# 直接双击或：
xdg-open docs/product-design/prototype/app.html     # Linux
open docs/product-design/prototype/app.html         # macOS
```

不需要服务器、不需要构建：`app.html` 不引用任何外部文件、CDN 或字体。

## `app.html` 里有什么

- **9 个视图**：总览、**实时活动**、任务运行、流水线、证据浏览器、经验库、预算账本、结果报告、
  **配置与状态**，`#/` hash 路由（`#/run/<task>`、`#/library/<domain>` 可深链）。
- **真实交互**（不是截图）：
  - 领域筛选、任务搜索、任务下拉切换；
  - 流水线候选卡片 → 右侧抽屉（diff / notes / 五项校验）；
  - 证据树展开 + 页签切换 + 文件选择；
  - 经验库领域切换、条目切换、office 空态；
  - 报告 refined/base 口径切换；
  - 账本 / 报告 / 流水线的**真实导出**（CSV / JSON 下载）；
  - 重跑走二次确认弹窗；破坏性动作不默认执行。
- **四态**：顶栏「状态」菜单可切 loading（骨架）/ 空 / 错误 / 正常，每页有专用空态与错误文案
  （例如「judge 429 → unscored，不按 0 分入账」）。
- **可访问性**：全站文字对上背景 ≥ 4.5:1（含抽屉与浮层），键盘可操作（`g 1–7` 跳页、`?` 快捷键、
  `Esc` 关浮层、`/` 聚焦搜索、`[` 收侧栏），焦点环可见，`aria-current` / `aria-selected` /
  `aria-expanded` / `role` 齐备，`prefers-reduced-motion` 降级。
- **响应式**：桌面三栏、平板两栏、手机单栏（侧栏改为抽屉），表格在容器内横向滚动。

## 看真实数据：harness2-console

`app.html` 直接打开是**演示数据**（不读盘）。要用它看 harness2 真实跑出来的东西：

```bash
# 只读服务：每次打开/刷新都重新读盘
.venv/bin/python -m harness2.cli.console --runs runs --runs local-bench --port 8099
# -> http://127.0.0.1:8099/           控制台（会显示「真实数据」标记）
# -> http://127.0.0.1:8099/api/state  页面渲染的那份模型（可 curl 看）

# 局域网访问：绑所有网卡（启动横幅会打印 LAN 地址）
.venv/bin/python -m harness2.cli.console --runs runs --runs local-bench --port 8099 --bind 0.0.0.0
# -> http://192.168.36.129:8099/      同一套只读控制台（无鉴权，只在可信网络里这么开）

# 或者把同一份模型烤进一个独立 HTML，发给别人看
.venv/bin/python -m harness2.cli.console --runs runs --export /tmp/console-snapshot.html
```

装过包之后命令是 `harness2-console`（pyproject 里有 entry point）。

读的东西（缺什么就显示缺什么，不补数字）：

| 位置 | 用途 |
|---|---|
| `<runs>/**/summary.json` | run 身份、base、stages、finals、similarity、ledger、selection、reflection |
| `<runs>/executions/.../{answer.txt,trajectory.md,session.log,output/}` | 产物与轨迹（真实内容） |
| `<runs>/library/<k>/<mode>/<domain>/{INDEX.md,harnesses/*/EXPERIENCE.md}` | 经验库条目（cue/lesson/edits 从磁盘解析） |
| `<runs>/**/judged*.json`（可选） | 判分输出；**没有就显示「还没有 judged.json」**，不编造 Δ |
| `<task>/selection/*/selection.json`、`reflection/*/edits.md` | 决策与反思 |

实时性（后端就是这个小服务，不是静态文件）：

| 接口 | 行为 |
|---|---|
| `GET /api/state` | 每次调用都重新读盘生成模型；带 `ETag`（全部产物的 path+mtime+size 的 sha1），带 `If-None-Match` 命中时直接 **304**，不重建模型。`?focus=<ns>` 选 run，`?task=<task_id>` 选要展开的 run_detail |
| `GET /api/activity` | **此刻在写什么**：`/proc` 里在跑的 harness2 进程（pid/cmd/cwd/elapsed）、窗口内最近写入的事件（按 kind/label 分类）、每个 run×task 由已存在产物推断的阶段。`?minutes=` / `?limit=` |
| `GET /api/config` | 服务端（python/监听/页面文件）、数据源目录、PATH 上可用的 harness2/执行器 CLI、`HARNESS2_*` 环境变量（密钥打码）、接口清单 |
| `GET /api/rerun?run=&task=` | 生成「重跑这个任务」的**真实命令**（沿用同一 namespace / k / mode，按 domain 限定，`--max-tasks 1`）；默认只生成不执行 |
| `POST /api/rerun` | 仅当启动加 `--allow-run`：在本机以子进程执行上面那条命令，返回 pid + 日志路径；未开启时 403 |
| `GET /api/events` | **SSE**：连上先发 `event: ready`，之后每 2s 比一次 revision，变了就发 `event: change`；空闲 10s 发心跳注释 |
| `GET /healthz` | `{"ok": true}` |
| `POST *` | 405（只读） |

页面侧：顶栏有「自动开/关」「读于 HH:MM:SS」，以及一个实时活动胶囊（`N 个进程在跑 · 最近写入 Xs 前`，点进实时活动页）；
实时活动页每 5s 轮询 `/api/activity`，展示**此刻**的进程、写入事件和阶段推断（并明说粒度≈一次 rollout / 一个阶段）。
配置页读 `/api/config`，对应产品设计里原本占位的「执行器与模型 / 安全与网络」。自动开着时收到 `change` 会**立即重新读盘并重绘**；
如果此时抽屉/弹层开着、或你正在输入，会先挂起并显示「有新数据 · 点击同步」，点一下才应用（避免打断操作）。
浏览器没有 `EventSource` 时退回 5s 轮询同一接口。

行为约定：

- **只读**：服务只有 `GET /`、`GET /api/state`、`GET /healthz`；`POST` 返回 405，没有写路径，也没有任意文件接口。
- 默认绑 `127.0.0.1`；`--focus <namespace>` 指定 run，页面左上角可在多个 namespace 间切换。
- 页面顶栏「刷新」= 重新读盘（不是假骨架）；「重跑 / 新建 run / 重建索引」在真实模式下只提示去跑 CLI。
- run 数、任务数、R/P、产物、轨迹都来自磁盘；`平均增益` 在无判分时显示「—」。

## 数据口径（重要）

所有数字都是**演示值**，与 7 个静态页共用同一条「演示宇宙」：

- run：`demo-oc-agg2`，ledger-1 基线 `105.25` → 采纳后 `85.15`（来自 `runs/demo` 的确定性 demo）；
- LAB `+11.8pp`、JobBench `+4.5`、WorkBuddy `+11.5` 是 README 报告口径的 Δ，**绝对分原文未给**，
  所以表格里留「—」而不是编造；
- `app.html` **不读取** `runs/`、`summary.json` 或 `library/`——没有查询层。它验证的是交互与信息架构，
  不是新的实测结论。真实数据仍以 run 目录下的 `summary.json` 为准。

## 改这个界面时

- 颜色/间距只从 `base.css` 的令牌派生（`app.html` 内联同一套 `:root` 变量）；
- 新增视图：在 `VIEW_META` 注册渲染函数，再把入口加进 `NAV`；
- 改完用无头浏览器自查（本仓库用 Playwright 跑过）：
  控制台无报错、每个视图 `scrollWidth` 不溢出、抽屉/弹层可开可关、对比度 ≥ 4.5:1。
