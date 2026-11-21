# astrbot_plugin_mio_studio · Mio 绘页

把本地部署的 [Mio · 绘页（comfy-comic-studio）](https://github.com/lumingya/comfy-comic-studio) 接进 AstrBot：
在聊天里用 **编号** 选择「工作流 + 分幕 + 预设」，一条 `/mio use 1 2 3` 就交给 Mio 装配并生成整本画册；
生成完成后按控制面板里选定的策略通知（自动发图 / 只发摘要 / 自动导出文件），随时用 `/mio get` 以 HTML（可选导出模板）/ ZIP / PDF 取回成品。

* 适配 **AstrBot v4.27.3**（`astrbot_version: ">=4.27,<5"`）
* 需要提供 **开放 API v2** 的 Mio · 绘页（默认端口 `8788`）
* 提供独立的 **控制面板页面**（AstrBot WebUI → 插件页面 → Mio 绘页），用于配置默认参数、导出模板与通知策略

## 安装

1. **准备 Mio**：启动支持开放 API v2 的版本，在 Mio 的 API 令牌设置中创建带 `read`、`write` 和 `render` 权限的令牌。
2. **安装插件**：AstrBot WebUI → 插件 → 从本地上传 `astrbot_plugin_mio_studio.zip`（或解压到 `data/plugins/astrbot_plugin_mio_studio/`）。依赖仅 `aiohttp`。
3. **填写连接**：「插件页面 → Mio 绘页 → 连接」填入 Mio 地址（例如 `http://127.0.0.1:8788`）和新令牌，再运行 `/mio ping` 检查连通。

## 聊天指令

| 指令 | 作用 |
| --- | --- |
| `/mio ls` | 列出工作流 / 分幕（分镜）/ 预设，自动编号 |
| `/mio ls wf` `/mio ls sb` `/mio ls ps` | 分别只列工作流 / 分幕 / 预设 |
| `/mio ls ch` `/mio ls tpl` | 列出图像通道 / 导出模板（编号供 `/mio use` 第 4 参数与 `/mio get` 使用） |
| `/mio use <工作流#> <分幕#> <预设#[,预设#]> [通道#]` | 用第 N 个工作流 + 第 M 个分幕 + 预设创建任务并立即开始；预设可多选（`3,4`），参数填 `0` 表示用默认值 |
| `/mio use <分幕#>` | 只给分幕，其余用控制面板里的默认工作流 / 预设 / 通道 |
| `/mio jobs` | 我的任务列表（任务编号 `#` 稳定不变） |
| `/mio status [#]` | 查看进度、错误（默认最近一个任务） |
| `/mio get [#] [html\|zip\|pdf\|img] [模板#]` | 导出并发送成品文件（缺省用面板里的默认格式 / 默认模板）；`img` 直接发送每一幕图片 |
| `/mio pause #` `/mio resume #` `/mio cancel #` `/mio rm #` | 暂停 / 继续（失败、停止的任务会补跑未完成分幕）/ 停止 / 移除记录 |
| `/mio ping` `/mio help` | 连接检查 / 帮助 |

示例：

```
/mio ls
/mio use 1 1 1          → 🚀 任务 #1 已创建并开始生成 …
/mio status 1           → 🎨 生成中 5/12 幕
                        （完成后按策略自动通知）
/mio get 1              → 📦 导出完成：xxx.html（默认模板）+ 文件
/mio get 1 html 3       → 用 /mio ls tpl 里第 3 个模板导出
/mio get 1 img          → 逐幕发送图片（QQ 默认合并转发）
```

## 控制面板

WebUI 左侧「插件页面 → Mio 绘页」：

* **连接**：Mio 地址、令牌、超时；权限（仅管理员 / 会话白名单）
* **生成默认值**：默认通道、默认工作流、默认预设（多选）、标题模板（`{story} {preset} {workflow} {channel} {date} {time}`）、并发、种子模式；下方展示 Mio 资源一览（编号与聊天一致）
* **导出与通知**：默认导出格式、默认导出模板（下拉自 Mio 的版式）、图片处理档位、主题色 / 署名 / 文案 / 提示词开关；**完成通知策略**四选一：
  * 自动发送每一幕图片
  * 只发完成摘要（之后 `/mio get` 取件）
  * 自动用默认模板导出并发送文件
  * 不通知（仅查询）
  另有图片发送方式（自动 / 合并转发 / 逐条 / 单条多图）、最大图片数、轮询间隔
* **任务**：查看进度、暂停 / 继续 / 停止 / 移除、下载 HTML / ZIP / PDF、把文件或图片发到会话；也可以直接在面板里新建任务
* **指令速查**

面板里的修改会写回插件配置（与 AstrBot 的插件配置对话框同一份数据）。

## 工作原理

* 资源目录、装配、生成和导出都由 Mio 处理。插件通过 `/api/v2` 提交工坊任务，跟踪单个作业，再下载成品。
* 分幕中的 `{character}` `{outfit}` `{style}` `{scene}` 等占位符由 Mio 用所选预设填充，ComfyUI 工作流也由 Mio 服务端按映射编译，无需浏览器。
* 任务记录保存在 `data/plugin_data/astrbot_plugin_mio_studio/tasks.sqlite3`。首次启动会归档同目录的旧 `tasks.json`，保留原文件与任务编号。旧连接地址如指向旧版 Mio，请换为 v2 地址和相应令牌。

## 常见问题

* **提示旧版 API**：先升级 Mio，然后用新地址和开放 API 令牌重新连接。
* **`/mio use` 报“Mio 装配失败：…”**：多为通道未配置密钥 / ComfyUI 不可达 / 工作流未映射，错误文本来自 Mio，请在 Mio 页面中修正。
* **合并转发不显示**：仅 OneBot 系平台（aiocqhttp / NapCat 等）支持，可在面板把发送方式改为“逐条发送”。

## 许可证

MIT
