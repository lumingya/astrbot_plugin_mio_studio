# Mio 绘页插件架构 · OpenAPI v2

## 基线与协议

现有 2.0.0 插件由聊天入口、服务、JSON 任务记录和静态控制面板组成。服务依赖补丁提供的 `/api/v1/catalog`、`production/*` 和画册导出。现有的编号选资源、任务编号和投递流程是升级时的兼容面。v3 改用 `http://127.0.0.1:8788/api/v2`。首次连接先请求公开的 `/meta`，再用 `/whoami` 验证 `Authorization: Bearer mio_…` 和权限范围。元数据发现失败时区分旧版接口、连接错误和令牌错误，并给出升级或配置提示。开发对照固定在上游提交 `7324e2e09985005afe2a9bb58156367259308564` 的 [OpenAPI 快照](https://github.com/lumingya/comfy-comic-studio/blob/7324e2e09985005afe2a9bb58156367259308564/clients/openapi-v2.json)和[客户端指南](https://github.com/lumingya/comfy-comic-studio/blob/7324e2e09985005afe2a9bb58156367259308564/clients/README.md)。

以下路径统一以 `/api/v2` 为前缀。Mio 的 `read`、`write`、`render`、`admin` 范围相互独立，也独立于插件自己的会话权限。普通创作及导出需要 `read + write + render`。模型发现、Mio 配置和 Webhook 管理还需要 `admin`。

| 能力 | 上游操作 | 插件处理 |
| --- | --- | --- |
| 发现、连接和诊断 | GET `/meta`、`/whoami`、`/operations`、`/settings`、`/image-adapters`、`/comfy-adapters`。POST `/settings/models`、`/workflows/{workflow_id}/diagnose` | 逐实例检查版本、权限和配置。模型探测显式触发，展示时丢弃凭据字段。适配器是能力目录，已配置通道和适配器分别展示。 |
| 装配和预览 | GET `/workshop`。POST `/workshop/assemble/preview`、`/workshop/assemble`、`/workshop/tasks` | 预览给出各幕最终提示词、变量来源与缺失项。装配固定分镜、预设和生成配置。用 `/workshop/tasks` 的 `auto_start` 开画，也可单独调用分集渲染。保留 `start_error` 时已创建的作品。 |
| 分镜和预设 | GET、POST `/workshop/storyboards`、`/workshop/presets`。GET、PATCH 各自的 `/{id}` | 选择器用标题和稳定 ID，分组展示预设条目，按选择顺序发送 `preset_ids`。 |
| 工坊队列 | GET、PATCH `/workshop/queue`。POST `/workshop/queue/{start,pause,resume,remove}`、`/workshop/tasks/{series_id}/clone`。PUT `/workshop/queue/order` | 队列操作使用工坊原生参数。全局停启只供管理入口，单人任务控制走具体 job。 |
| 系列和分集 | GET、POST `/series`、`/series/{series_id}/episodes`。GET、PATCH、DELETE `/series/{series_id}` 和 `/episodes/{episode_id}` | 画廊按系列和分集浏览，记录 ID 与作业 ID 各自保存。 |
| 一句话创作 | POST `/series/{series_id}/episodes/generate`，再 POST `/episodes/{episode_id}/render` | 用 `sentence` 生成剧本并收取分集，再用渲染回执追踪出图。若 HTTP 结果不确定，保留本地回执供核对。 |
| 工作流、LoRA、单图试画 | GET `/workflows`、`/workflows/{workflow_id}`、`/workflows/{workflow_id}/loras`。POST `/workflows/{workflow_id}/trial` | LoRA 使用组键和槽位稳定键，通过 `slot_overrides` 覆盖本次生成。试画返回系列、分集及作业。生成来源在工作流、云通道、配置档之间选一项。 |
| 分幕重画和候选图 | POST `/episodes/{episode_id}/panels`。PATCH `/episodes/{episode_id}/panels/{panel_id}`。POST `/episodes/{episode_id}/render`、`/episodes/{episode_id}/takes/{take_id}/{adopt,reject}` | 重画前用上游 revision 更新该幕的提示词（如用户提供），提交含 `panel_ids` 的生成。预览走分幕或候选图 `/image` 路由，取件走作品当前采用状态。 |
| QA、定稿和成品状态 | POST `/episodes/{episode_id}/{qa,finalize}`。GET `/episodes/{episode_id}/{reading,deliverables}` | 质检和定稿是明确的创作动作。检查各幕是否已有可读采用图后再发送成品。 |
| 条漫、切片、画册和动态漫 | GET `/episodes/{episode_id}/strip.png`、`/episodes/{episode_id}/slices`、`/episodes/{episode_id}/export?fmt=slices`、`/album-templates`、`/export/formats`、`/export/presets`。POST `/export/motion` | `/slices` 返回裁切方案，切片文件从分集导出接口获取。动态漫直接返回独立 HTML。Monograph、Nocturne、Folio 是 HTML 模板，便携 PDF/ZIP 有独立参数。 |
| 异步导出 | POST `/episodes/{episode_id}/deliverables/export`、`/export/jobs/{album,strip,portable}`。GET `/export/jobs/{job_id}/file` | 根据格式和分集范围选择路线，确认作业完成后流式下载。 |
| 创作套件 | GET `/creation-kits`。POST `/creation-kits/{series_id}/{preview,assemble}` | 使用套件对应的系列 ID，先展示输入与预览，再按创作权限装配。 |
| 作业 | GET `/jobs`、`/jobs/{job_id}`、`/jobs/{job_id}/events`。POST `/jobs/{job_id}/{pause,resume,cancel,retry}`。DELETE `/jobs/{job_id}` | 跟踪生成和导出的作业以及各分幕进展，识别 `completed`、`failed`、`canceled` 等终态。遇到不确定结果先核对再重试。 |
| Webhook | GET、POST `/webhooks`。PATCH、DELETE `/webhooks/{hook_id}`。POST `/webhooks/{hook_id}/test`。GET `/webhooks/{hook_id}/deliveries` | 保存签名设置，接收候选图和结束事件，用该实例的作业详情确认进度。 |

## 模块与职责

| 模块 | 责任 |
| --- | --- |
| `core/v2_client.py` | 封装 v2 请求、错误及实例生命周期。按实例创建连接池，解析原生 JSON，公开结构化错误和中文建议。只对可重试的 GET 做有限退避，按上游支持情况为写请求保存幂等键。分块下载到同目录临时文件，完成后原子落盘，按字节总量和文件数清理缓存。 |
| `core/service.py` | 汇合实例路由、资源解析、创作队列、作业状态、交付和错误翻译。实例选择优先级为本次请求、会话默认、全局默认。已创建的任务跟随记录的实例。 |
| `core/store.py` | 掌管 SQLite、迁移、安全写入、并发预留、任务索引和交付状态。 |
| `core/governance.py` | 将调用身份映射为 admin、creator、viewer，检查会话、归属、冷却、配额和云端通道白名单。聊天、AI 工具和控制面板调用同一服务入口。 |
| `core/webhook.py` | 验证原始请求、签名和防重放，按实例查作业并唤醒统一状态刷新。 |
| `core/commands.py`、`core/llm_tools.py`、`core/wizard.py` | 解析聊天指令，提供受治理约束的 AI 工具，并以会话和发起者隔离短时向导状态。 |
| `core/card_renderer.py` | 返回可直接发送的 Pillow 图片或相同内容的纯文本。中文字体和 Pillow 均允许缺失。 |
| `core/page_api.py` | 使用 AstrBot 身份边界暴露显式路由、图片代理和文件下载。采用精简 DTO，对浏览器隐藏密钥及完整上游对象。 |
| `web-src/`、`pages/mio-studio/` | Vite 和 TypeScript 源码与离线静态构建产物。通过 AstrBot bridge 调用实际 API。无 bridge 或带 `?demo=1` 时走隔离的内存 Mock，使用同一组视图。 |
| `tests/` | 本地 aiohttp 测试服务验证 API 协议。用临时数据库和虚拟会话覆盖权限、并发、送达、卡片及用户交互。 |

## 存储与状态

使用 Python 标准库的 SQLite，开启 WAL、外键和有限等待。在短写事务内分配任务编号、校验并预留并发和配额、登记审核事件。网络调用和文件操作在事务之外。过期预留和状态待核对的作业可在重启后恢复，避免重新提交可能已计费的生成请求。

| 记录 | 主要字段与用途 |
| --- | --- |
| 任务 | 本地稳定编号、实例 ID、会话 UMO、发起者 ID 和显示名、系列/分集/作业 ID、配置快照、状态、创建与结束时间、交付状态。按实例和作业查询 Webhook，按会话和用户查归属，按状态和入队顺序计算等候位置。 |
| 会话策略 | 每个 UMO 对实例、模板、图片发送方式和通知策略的覆盖。仅管理员或对应的可信会话管理身份可修改。 |
| 配额和预留 | 用户、实例、用量日期、请求时刻、操作类型和占用状态。排队位置使用持久状态计算。并发和冷却检查与预留放在同一事务，可追踪失败请求及是否已经提交上游。 |
| 实例 | 稳定 ID、显示名、连接参数、默认标记和凭据设置。密钥仅供服务端调用，不进入配置响应、任务快照或审计。 |
| 审计和 Webhook 投递 | 用量相关动作、结果码、目标的本地标识，以及已消费的实例/投递 ID。审计省略提示词、凭据、消息正文和异常响应原文。 |

首次打开数据库时，在一个事务中读入同目录 `tasks.json`、保留旧编号和归属、推进编号序列、写入迁移标记，同时留存原文件。旧版 `id` 和 `albumId` 单独保留，仅凭旧 ID 不能推断 v2 的分集或作业。无法关联的旧记录可浏览，远程控制或继续生成时明确提示在 Mio 中核对作品。迁移失败回滚并保留原文件，重启重试时不会重复导入。

## 实时进展与交付

Webhook 回调接受小体积原始请求，核对实例、`X-Mio-Timestamp` 时间窗、`X-Mio-Event` 与正文一致性以及 `X-Mio-Delivery`，以恒时比较验证 `HMAC-SHA256(secret, timestamp + "." + body)`。已处理投递 ID 持久化，防止上游给重试投递重新签名时造成重复。回调独立于面板登录，业务侧仍用 v2 查询确认任务身份和状态。Mio 可推送候选图和终态事件，其余阶段由有活动任务时的自适应轮询补齐。没有回调的部署沿用相同状态刷新与交付逻辑，无待处理任务时停轮询。

通知按会话覆盖全局策略，支持摘要、逐幕图、条漫、导出、质检再发和静默。Webhook 和轮询共用持久交付状态，避免并发领取同一通知。发送前检查文件大小。合并转发失败时退回分条发送，交付异常对发起者可见。浏览器仅访问插件的受控图片与下载地址，上游 Token 保留在服务端。

## 分阶段验收

| Wave | 完成条件 |
| --- | --- |
| 0 | 协议和基线已记录，pytest、pytest-asyncio 和 Ruff 可运行。 |
| 1 | v2 操作、错误映射、多实例、流式下载和缓存有本地测试，旧服务获得明确升级提示。 |
| 2 | SQLite 保留旧编号并防止重复迁移，任务归属、并发、配额、验签和降级轮询通过测试。 |
| 3 | 全量指令及 AI 工具走同一权限制约，向导支持取消和过期，卡片和交付支持降级。 |
| 4 | 八个视图使用正式 API，构建可复现，内置离线演示，AstrBot 原生配置和面板字段一致。 |
| 5 | Chromium 完成桌面亮暗主题、移动端和交互巡检，记录两轮修整及可用于文档的截图。 |
| 6 | 端到端验收、迁移与故障排查指南、版本 3.0.0 和 CI 门禁就绪。 |
