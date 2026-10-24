# Issue tracker: Local Markdown

本项目的规格和任务使用 `.scratch/` 下的 Markdown 文件管理。

## 文件约定

- 每个功能使用独立目录：`.scratch/<feature-slug>/`。
- 功能规格：`.scratch/<feature-slug>/spec.md`。
- 实施任务：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，
  从 `01` 开始编号，每个任务一个文件，不合并成单个任务文件。
- 普通实施任务在文件顶部使用 `Status:` 记录状态：
  `open`、`in-progress`、`blocked`、`resolved`。
- 讨论与补充记录追加到文件底部的 `## Comments`。
- 功能规格引用 `docs/prd-auto.md` 中的相关要求，
  避免复制整份 PRD 后形成多个不一致版本。

## 技能要求“发布到 issue tracker”时

根据内容创建规格文件或独立任务文件。
此处发布指写入本地文件，不调用外部 issue 服务。

## 技能要求“读取相关任务”时

读取用户给出的任务路径。
如果只给编号，应在对应功能目录内查找；
存在多个同号任务时，不跨功能猜测目标。

## Wayfinding 操作

以下约定供使用探索地图的技能读取：

- 地图：`.scratch/<effort>/map.md`，
  包含 Notes、Decisions-so-far、Fog。
- 子任务：`.scratch/<effort>/issues/<NN>-<slug>.md`。
- `Type:` 为 `research`、`prototype`、`grilling` 或 `task`。
- 探索任务的 `Status:` 为 `open`、`claimed` 或 `resolved`。
- 依赖通过 `Blocked by: NN, NN` 记录，编号仅指同一功能目录。
- 所有依赖均为 `resolved` 时，任务才解除阻塞。
- 按编号选择尚未认领、未解决且无阻塞的任务。
- 开始工作前保存 `Status: claimed`。
- 完成后追加 `## Answer`，更新为 `Status: resolved`，
  并在地图的 Decisions-so-far 中追加结论摘要和任务链接。
