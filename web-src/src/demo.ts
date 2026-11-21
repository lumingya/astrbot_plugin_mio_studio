import type { Api } from "./api";
import { plate } from "./demo-art";

type RecordData = Record<string, any>;
type Work = {
  id: string; episode_id: string; series_id: string; series_title: string; title: string;
  description: string; frameCount: number; adopted_count: number; cover_src: string;
  cover_url: string; updated_at: string; art: number;
};
type Task = {
  id: string; num: number; title: string; instance_id: string; status: string; statusKey: string;
  kind: string; done: number; total: number; failed: number; error: string;
  created_at: number; started_at: number | null; finished_at: number | null; duration: number | null;
  sender_name: string; platform: string; umo: string; episode_id: string; queuePosition: number | null;
  selection: RecordData; progresses?: boolean;
};
type Panel = {
  id: string; number: number; title: string; description: string; status: string; error?: string;
  takes: Array<{ id: string; number: number; status: string; score: number | null; qa: RecordData | null }>;
};
type Instance = {
  id: string; label: string; base_url: string; has_token: boolean; cloud: boolean; timeout: number;
};

const room = "mock-qq:GroupMessage:atelier";
const reviewers = "mock-qq:GroupMessage:review";
const formats = ["html", "pdf", "zip", "strip", "slices", "motion"];
const layouts = [
  { id: "export-monograph", title: "Monograph", layout: "monograph", source: "builtin", description: "暖白单栏，保留分镜留白。", options: { paper: "#f4eddc", accent: "#a66148", text: "#302621" } },
  { id: "export-nocturne", title: "Nocturne", layout: "nocturne", source: "builtin", description: "深墨页面与暖金眉线。", options: { paper: "#222321", accent: "#bd935d", text: "#f1dfc6" } },
  { id: "export-folio", title: "Folio", layout: "folio", source: "builtin", description: "作品集式大图与双栏节奏。", options: { paper: "#ece4cf", accent: "#55615a", text: "#333530" } },
];
const presets = [
  { id: "preset-messenger", title: "旧城邮差", category: "characters", entries: [{ key: "character", value: "深青制服，铜扣邮包" }, { key: "gesture", value: "扶着伞柄，朝门廊回望" }], groups: [{ id: "figure", title: "人物" }] },
  { id: "preset-botanist", title: "夜间植物学家", category: "characters", entries: [{ key: "character", value: "灰绿长外套，夹着采样笔记" }, { key: "gesture", value: "掀开玻璃温室的帘子" }], groups: [{ id: "figure", title: "人物" }] },
  { id: "preset-tide", title: "退潮之后", category: "scenes", entries: [{ key: "scene", value: "砂岩岸边，潮线留下细碎月光" }, { key: "light", value: "昏黄地平线，低对比逆光" }], groups: [{ id: "setting", title: "场景与光" }] },
  { id: "preset-rain", title: "雨后街角", category: "scenes", entries: [{ key: "scene", value: "砖巷里倒映着旧街灯" }, { key: "light", value: "褪色橙红，微微潮湿" }], groups: [{ id: "setting", title: "场景与光" }] },
  { id: "preset-ink", title: "纸上铅色", category: "scenes", entries: [{ key: "style", value: "干刷线纹，哑光压纸，低饱和墨色" }], groups: [{ id: "style", title: "画风" }] },
];
const storyboards = [
  { id: "board-shore", title: "海灯 · 四幕分镜", frameCount: 4, description: "从潮声到归岸，四幕单页叙事。" },
  { id: "board-lane", title: "雨中邮差 · 六幕分镜", frameCount: 6, description: "旧城街区，沿同一条巷子穿过雾与暮色。" },
  { id: "board-leaf", title: "冬日温室 · 三幕分镜", frameCount: 3, description: "薄雾中的玻璃温室和当天开出的第一朵花。" },
];
const workflows = [
  { id: "workflow-ink", title: "版画分镜 · Ink Press", name: "Ink Press", nodeCount: 24, checkpoint: "mio_story_ink.safetensors", loras: { enabled: true, groups: 2, supports_independent_toggle: true } },
  { id: "workflow-pastel", title: "粉彩画稿 · Soft Proof", name: "Soft Proof", nodeCount: 17, checkpoint: "soft_proof.safetensors", loras: { enabled: true, groups: 1, supports_independent_toggle: true } },
  { id: "workflow-pencil", title: "单色原稿 · Pencil Plate", name: "Pencil Plate", nodeCount: 14, checkpoint: "mio_pencil_02.safetensors", loras: { enabled: false, groups: 0 } },
];
const channels = [
  { id: "local-comfy", title: "本机 ComfyUI", provider: "comfyui", cloud: false, model: "mio_story_ink", default: true },
  { id: "cloud-proof", title: "备用云端校样", provider: "openai_images", cloud: true, model: "illustration-preview", default: false },
];
const kits = [
  { id: "kit-dusk", title: "黄昏采风", description: "暮色外景的分镜、前景和色板。", frameCount: 4 },
  { id: "kit-letter", title: "旧城寄信人", description: "街巷连载的一套角色与场景词。", frameCount: 6 },
];
const boardScenes = ["远景停顿", "主角入画", "线索浮现", "回望与留白", "雨后路口", "寄出之前"];
const directions = [
  "薄光从远处落下来，画面停留在第一个没有对白的瞬间。",
  "人物微微回头。前景保留纸色，三分之一处出现一处暖光。",
  "镜头收近，环境的颜色在水面与窗棂之间交替。",
  "画幅向远处打开，只留一句尚未来得及说完的话。",
];
const statusLabels = { ready: "等待出图", running: "生成中", complete: "已完成", paused: "已暂停", failed: "出图失败", cancelled: "已取消", preparing: "准备中" };
const deliveryModes = [
  ["summary", "只发摘要"], ["images", "逐幕发送图片"], ["strip", "发送完整条漫"],
  ["export", "发送导出成品"], ["qa_then_send", "质检后发送"], ["silent", "仅在查询时显示"],
];
const firstConfig: RecordData = {
  mio: { base_url: "http://127.0.0.1:8788", api_token_set: true, request_timeout: 60, cloud: false },
  defaults: { workflow_id: workflows[0].id, storyboard_id: storyboards[0].id, preset_ids: [], channel_id: channels[0].id },
  export: { layout_id: layouts[0].id, format: "html", theme_color: "", image_profile: "publish", signature: "", show_captions: true, show_prompts: false },
  delivery: { notify_mode: "summary", send_mode: "auto", include_captions: true, max_images: 30, max_file_mb: 50, poll_interval: 5, forward_sender_name: "Mio 绘页" },
  governance: { global_limit: 2, user_active_limit: 1, daily_limit: 8, cooldown_seconds: 10, cloud_daily_limit: 3, blocked_keywords: [] },
  permissions: { admin_only: false, creator_ids: [], viewer_ids: [], allowed_sessions: [], cloud_allowed_ids: [] },
  chat: { render_mode: "auto", llm_tools_enabled: false },
  webhook: { enabled: true, bind_host: "127.0.0.1", bind_port: 8790, public_url: "http://127.0.0.1:8790" },
};

const safeHTML = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, ch =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
const string = (value: unknown) => typeof value === "string" ? value.trim() : "";
const row = (value: unknown): RecordData =>
  value && typeof value === "object" && !Array.isArray(value) ? value as RecordData : {};
const limit = (value: unknown, fallback: number, maximum: number) => {
  const number = Number(value ?? fallback);
  return Number.isInteger(number) && number > 0 ? Math.min(number, maximum) : fallback;
};
const at = (seconds: number) => new Date(seconds * 1000).toISOString();
const indexOfId = (id: unknown) => Number(String(id ?? "").match(/(?:p|take-)(\d+)/)?.[1] ?? 1) - 1;
const err = (message: string): never => { throw new Error(message); };

function createWork(id: string, series: string, seriesTitle: string, title: string, about: string, art: number, count: number, when: number): Work {
  const cover = plate(art);
  return { id, episode_id: id, series_id: series, series_title: seriesTitle, title, description: about,
    frameCount: count, adopted_count: count, cover_src: cover, cover_url: cover, updated_at: at(when), art };
}

function taskOf(num: number, title: string, instance: string, status: string, total: number, done: number, now: number, episode: string, kind = "workshop"): Task {
  return {
    id: `task-${num}`, num, title, instance_id: instance, status, statusKey: status, kind, done, total,
    failed: status === "failed" ? Math.max(1, total - done) : 0,
    error: status === "failed" ? "此工作流缺少一个本地模型，请到资源目录运行诊断。" : "",
    created_at: now, started_at: status === "ready" ? null : now + 7,
    finished_at: status === "complete" ? now + 107 : null, duration: status === "complete" ? 100 : null,
    sender_name: num % 3 === 0 ? "控制面板" : num % 2 ? "澄岚" : "阿墨",
    platform: num % 3 === 0 ? "panel" : "qq", umo: room, episode_id: episode,
    queuePosition: status === "ready" ? 1 : null,
    selection: { workflowTitle: workflows[num % workflows.length]!.title, storyboardTitle: storyboards[num % storyboards.length]!.title, presetTitles: [presets[num % presets.length]!.title] },
  };
}

function loraGroups(workflow: string) {
  const first = [{ key: "ink-lines", name: "Gravure Lines", strength: 0.82, clip: 0.8, enabled: true },
    { key: "warm-paper", name: "Warm Paper Grain", strength: 0.46, clip: 0.45, enabled: true }];
  const second = [{ key: "muted-pastel", name: "Muted Color Proof", strength: 0.65, clip: 0.67, enabled: false }];
  return { groups: workflow === "workflow-pencil" ? [] : [
    { key: "press-layer", kind: "lora", active: true, supports_independent_toggle: true, min: 0, max: 2, loras: first },
    ...(workflow === "workflow-ink" ? [{ key: "finish-layer", kind: "lora", active: true, supports_independent_toggle: true, min: 0, max: 2, loras: second }] : []),
  ] };
}

export function createDemoApi(): Api {
  const now = Math.floor(Date.now() / 1000);
  let next = 29;
  let defaultId = "primary";
  const config = structuredClone(firstConfig);
  const instances: Instance[] = [
    { id: "primary", label: "青岚工作室", base_url: "http://127.0.0.1:8788", has_token: true, cloud: false, timeout: 60 },
    { id: "backup", label: "远山校样台", base_url: "https://proof.example.invalid", has_token: true, cloud: true, timeout: 90 },
  ];
  const library = [
    createWork("ep-night", "series-post", "夜间邮路", "穿过夜色的末班车", "灯火向后退去，直到最后一道光越过湖面。", 0, 6, now - 720),
    createWork("ep-shore", "series-tide", "潮汐档案", "把潮声装进一封信", "退潮后的纸片里，藏着整片海的回声。", 1, 4, now - 4200),
    createWork("ep-glass", "series-glass", "冬日温室", "窗里留住了春天", "她跟着光走进一座沉睡的温室。", 2, 8, now - 7600),
    createWork("ep-lane", "series-post", "夜间邮路", "雨从旧城石缝里醒来", "寄信人在雨夜的晚风里重新认出了一条巷子。", 3, 5, now - 9240),
  ];
  const tasks = [
    taskOf(28, "从温室走到海岸", "primary", "running", 6, 3, now - 237, "ep-glass", "draw"),
    taskOf(27, "旧街上的第二封信", "primary", "ready", 4, 0, now - 162, ""),
    taskOf(26, "穿过夜色的末班车", "primary", "complete", 6, 6, now - 820, "ep-night"),
    taskOf(25, "雨后街灯 · 试画", "primary", "failed", 1, 0, now - 970, "", "trial"),
    taskOf(24, "把潮声装进一封信", "backup", "complete", 4, 4, now - 4200, "ep-shore"),
    taskOf(23, "窗里留住了春天", "primary", "complete", 8, 8, now - 7600, "ep-glass"),
    taskOf(22, "小巷剪影 · 试画", "backup", "paused", 1, 0, now - 870, "ep-lane", "trial"),
  ];
  const sessions: RecordData[] = [
    { umo: room, platform: "qq", sender: "周日画室", lastUsed: now - 237, rule: { notify_mode: "strip", layout_id: layouts[0].id } },
    { umo: reviewers, platform: "qq", sender: "漫画编辑部 · 内部审阅", lastUsed: now - 820, rule: { notify_mode: "qa_then_send", send_mode: "separate" } },
    { umo: "mock-qq:FriendMessage:editor", platform: "qq", sender: "编辑私聊", lastUsed: now - 2380, rule: {} },
  ];
  const hooks = new Set(["primary"]);
  const log: RecordData[] = [
    { id: "dlv-demo-03", event: "job.completed", status: 204, attempts: 1, at: at(now - 713), error: "" },
    { id: "dlv-demo-02", event: "take.created", status: 204, attempts: 1, at: at(now - 791), error: "" },
    { id: "dlv-demo-01", event: "job.failed", status: 503, attempts: 3, at: at(now - 2275), error: "接收端重启，改由轮询更新。" },
  ];
  const audit: RecordData[] = [
    { id: 112, action: "task_create", actor_id: "控制面板", instance_id: "primary", task_id: "task-28", outcome: "ok", created_at: now - 237 },
    { id: 111, action: "export", actor_id: "澄岚", instance_id: "primary", task_id: "task-26", outcome: "ok", created_at: now - 715 },
    { id: 110, action: "trial", actor_id: "阿墨", instance_id: "primary", task_id: "task-25", outcome: "workflow_missing_model", created_at: now - 970 },
  ];
  const adopted = new Map<string, number>();
  const reviewed = new Set<string>();

  const instanceOf = (input: RecordData) => {
    const id = string(input.instance_id) || defaultId;
    return instances.find(item => item.id === id) ?? err("没有找到这个 Mio 实例。");
  };
  const task = (id: string) => tasks.find(item => item.id === id || String(item.num) === id) ?? err("任务已移除，或该编号不存在。");
  const work = (id: string) => library.find(item => item.episode_id === id) ?? err("没有找到这册作品。");
  const session = (umo: string) => sessions.find(item => item.umo === umo) ?? err("请先选择一个已知会话。");
  const instanceList = () => ({ instances, default_id: defaultId });
  const logAction = (action: string, instance_id: string, task_id = "") => {
    audit.unshift({ id: (audit[0]?.id ?? 0) + 1, action, actor_id: "控制面板", instance_id, task_id, outcome: "ok", created_at: Date.now() / 1000 });
  };
  const jobsFor = (id: string) => {
    for (const item of tasks) {
      if (!item.progresses || item.status !== "running") continue;
      const elapsed = Date.now() / 1000 - item.started_at!;
      item.done = Math.min(item.total, Math.floor(elapsed / 1.7));
      if (item.done < item.total) continue;
      item.status = item.statusKey = "complete";
      item.finished_at = Date.now() / 1000;
      item.duration = Math.ceil(elapsed);
      item.episode_id = `ep-created-${item.num}`;
      const added = createWork(item.episode_id, "series-demo", "工作室样张", item.title,
        "本地演示校样。分幕由内置插画组成。", item.num % 4, item.total, item.finished_at);
      library.unshift(added);
    }
    return tasks.filter(item => item.instance_id === id);
  };
  const panels = (book: Work, count = book.frameCount): Panel[] => Array.from({ length: Math.min(16, count) }, (_, index) => {
    const key = `${book.id}/${index + 1}`;
    const chosen = adopted.get(key) ?? 1;
    const takes = Array.from({ length: 2 }, (_, alternate) => ({
      id: `take-${index + 1}-${alternate + 1}`, number: alternate + 1,
      status: chosen === alternate + 1 ? "adopted" : "candidate",
      score: index === 0 ? (alternate ? 0.87 : 0.94) : null,
      qa: reviewed.has(book.id) ? { score: alternate ? 0.85 : 0.94, passed: true, issues: alternate ? ["建议保持原有留白。"] : [] } : null,
    }));
    return { id: `p${index + 1}`, number: index + 1, title: boardScenes[index % boardScenes.length]!,
      description: directions[index % directions.length]!, status: "adopted", takes };
  });
  const taskBook = (item: Task) => item.episode_id ? work(item.episode_id) : err("这条任务尚未生成可预览的分集。");
  const status = (instance: Instance) => {
    const group = jobsFor(instance.id);
    return { ok: instance.has_token, configured: instance.has_token, api: "2.0", version: "2.8.1",
      instance_id: instance.id, baseUrl: instance.base_url, scopes: ["read", "write", "render", "admin"],
      error: instance.has_token ? "" : "Mio 已响应，请填写令牌。", tasks: { total: group.length,
        active: group.filter(item => ["ready", "running", "preparing"].includes(item.status)).length } };
  };
  const addTask = (title: string, instance: Instance, input: RecordData, kind: string, count: number) => {
    const seconds = Date.now() / 1000;
    const entry = taskOf(next++, title, instance.id, "running", count, 0, seconds, "", kind);
    entry.started_at = seconds;
    entry.progresses = true;
    entry.sender_name = "控制面板";
    entry.platform = "panel";
    entry.umo = string(input.umo);
    if (entry.umo) session(entry.umo);
    tasks.unshift(entry);
    logAction(kind, instance.id, entry.id);
    return { task: entry, ...(kind === "trial" ? { src: plate(3) } : {}),
      message: `任务 #${entry.num} 已受理。` };
  };
  const catalog = () => ({ workflows, storyboards, presets, channels, layouts, kits,
    defaults: { workflows: config.defaults.workflow_id, storyboards: config.defaults.storyboard_id,
      presets: config.defaults.preset_ids, channels: config.defaults.channel_id, layouts: config.export.layout_id } });
  const image = (book: Work, input: RecordData) => {
    const panel = indexOfId(input.take_id || input.panel_id);
    if (panel >= book.frameCount || panel < 0) err("没有找到该分幕。");
    const alternate = string(input.take_id).endsWith("-2") ? 1 : 0;
    return { src: plate(book.art + panel + alternate) };
  };
  const get = (endpoint: string, input: RecordData): unknown => {
    const instance = instanceOf(input);
    const [route, id = "", part = ""] = endpoint.split("/").map(decodeURIComponent);
    if (route === "overview") {
      const group = jobsFor(instance.id);
      const completed = group.filter(t => t.status === "complete");
      const active = group.filter(t => t.status === "ready" || t.status === "running").length;
      const average = completed.length ? Math.round(completed.reduce((sum, t) => sum + (t.duration || 0), 0) / completed.length) : 0;
      return { ...instanceList(), metrics: { images: completed.reduce((sum, t) => sum + t.done, 0), active, average,
        resources: workflows.length + storyboards.length + presets.length + channels.length + layouts.length + kits.length } };
    }
    if (route === "status") return status(instance);
    if (route === "instances") return instanceList();
    if (route === "config") return { config, options: { notify_modes: deliveryModes.map(([value, label]) => ({ value, label })), formats } };
    if (route === "catalog") return catalog();
    if (route === "resources") {
      const items: RecordData = catalog();
      const resource = items[id]?.find((item: { id: string }) => item.id === part) ?? err("没有找到这个资源。");
      if (id === "workflows") return { resource: { ...resource, loras: loraGroups(part) }, loras: loraGroups(part) };
      if (id === "storyboards") return { resource: { ...resource, panels: Array.from({ length: resource.frameCount }, (_, i) => ({
        title: boardScenes[i], number: i + 1, order: i, description: directions[i % directions.length],
        prompt: "{character} 走进 {scene}，画面保留纸纹与雾气。", variables: ["character", "scene"],
      })) } };
      return { resource };
    }
    if (route === "tasks") {
      const group = jobsFor(instance.id);
      if (!id) return { items: group.slice(0, limit(input.limit, 50, 400)), others: 0, statusLabels };
      const entry = task(id);
      if (part === "image") return image(taskBook(entry), input);
      if (!part) {
        const view = entry.episode_id ? panels(taskBook(entry), entry.status === "running" ? entry.done : undefined) : [];
        return { task: { ...entry, panels: view }, panels: view };
      }
    }
    if (route === "gallery") {
      if (id) {
        const episode = work(id);
        if (part === "image") return image(episode, input);
        if (!part) return { work: { ...episode, instance_id: instance.id }, panels: panels(episode) };
      } else {
        const keyword = string(input.query || input.q || input.search).toLocaleLowerCase();
        const books = instance.id === "backup" ? library.filter(book => book.art === 1 || book.art === 3) : library;
        const items = books.filter(book => (!input.series_id || book.series_id === input.series_id) &&
          `${book.title} ${book.series_title} ${book.description}`.toLocaleLowerCase().includes(keyword));
        const series = [...new Set(books.map(item => item.series_id))].map(id => ({
          id, title: books.find(item => item.series_id === id)!.series_title,
          episode_count: books.filter(item => item.series_id === id).length,
        }));
        return { items: items.slice(0, limit(input.limit, 48, 100)), series, total: items.length };
      }
    }
    if (route === "sessions") return { items: sessions };
    if (route === "webhooks") return { registered: hooks.has(instance.id), listening: Boolean(config.webhook.enabled),
      deliveries: hooks.has(instance.id) && instance.id === "primary" ? log : [] };
    if (route === "audit") return { items: audit.slice(0, limit(input.limit, 50, 200)), quota: {
      used: 2 + tasks.filter(item => item.progresses).length, cloud_used: 1, daily_limit: config.governance.daily_limit } };
    return err("演示暂未提供此查询。");
  };

  const preview = (input: RecordData) => {
    const board = storyboards.find(item => item.id === input.storyboard) ?? err("请选择一个分镜。");
    const ids = Array.isArray(input.presets) ? input.presets : string(input.presets).split(",");
    const values = Object.fromEntries(presets.filter(item => ids.includes(item.id)).flatMap(item => item.entries.map(e => [e.key, e.value])));
    const missing = ["character", "scene"].filter(key => !values[key]);
    return { preview: { title: board.title, missing, panels: Array.from({ length: board.frameCount }, (_, i) => {
      const positive = `${values.character || "{character}"}，${values.scene || "{scene}"}。第 ${i + 1} 幕。${directions[i % directions.length]}`;
      return { id: `preview-${i + 1}`, order: i, title: boardScenes[i], positive, prompt: positive, negative: "强反差，艳丽色彩，杂乱文字",
        missing, missing_variables: missing, variables: ["character", "scene"].map(key => ({
          key, name: key, status: values[key] ? "resolved" : "missing", value: values[key] || "", missing: !values[key],
        })) };
    }) } };
  };

  const patchSections = (patch: RecordData) => {
    for (const [section, values] of Object.entries(patch)) {
      if (!Object.hasOwn(config, section)) continue;
      const current = config[section];
      for (const [key, value] of Object.entries(row(values))) {
        if (Object.hasOwn(current, key)) current[key] = value;
      }
    }
  };
  const post = (endpoint: string, input: RecordData): unknown => {
    const [route, id = "", part = ""] = endpoint.split("/").map(decodeURIComponent);
    if (route === "instances") {
      const target = string(input.id || input.instance_id);
      if (!/^[a-z0-9][a-z0-9_-]{0,47}$/.test(target)) err("实例编号需为小写字母、数字或连字符。");
      const existing = instances.find(item => item.id === target);
      if (input.action === "remove") {
        if (!existing || target === "primary") err("不能移除主实例，或实例不存在。");
        const index = instances.findIndex(item => item.id === target);
        if (tasks.some(item => item.instance_id === target && ["running", "ready"].includes(item.status))) err("此实例还有进行中的任务。");
        instances.splice(index, 1);
        if (defaultId === target) defaultId = "primary";
      } else if (input.action === "default") {
        if (!existing) err("请先保存该实例。");
        defaultId = target;
      } else if (input.action === "save" || input.action === "probe") {
        const address = string(input.base_url) || existing?.base_url || "";
        const timeout = Number(input.timeout ?? existing?.timeout ?? 60);
        let parsed: URL;
        try { parsed = new URL(address); } catch { return err("请输入完整的 HTTP(S) 实例地址。"); }
        if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) err("实例地址不能包含凭据、查询串或片段。");
        if (!Number.isFinite(timeout) || timeout < 1 || timeout > 600) err("超时应在 1 至 600 秒之间。");
        const draft = { id: target, label: string(input.label) || existing?.label || target, base_url: address,
          timeout, cloud: input.cloud === undefined ? Boolean(existing?.cloud) : input.cloud === true,
          has_token: input.token === null ? false : Boolean(string(input.token) || existing?.has_token) };
        if (input.action === "probe") return { ...instanceList(), status: status(draft) };
        if (existing) Object.assign(existing, draft);
        else instances.push(draft);
      } else err("请选择一个实例操作。");
      logAction(`instance_${input.action}`, target);
      return instanceList();
    }

    const instance = instanceOf(input);
    if (route === "config") {
      patchSections(row(input.config || input));
      logAction("config_update", instance.id);
      return { config };
    }
    if (route === "studio") {
      if (id === "preview") return preview(input);
      if (id === "draw") {
        const idea = string(input.idea || input.sentence);
        if (!idea) err("先写下一句话作为创作灵感。");
        return addTask(idea.slice(0, 48), instance, input, "draw", 4);
      }
      if (id === "trial") {
        const prompt = string(input.prompt);
        if (!prompt) err("请填写试画提示词。");
        return addTask(`试画 · ${prompt.slice(0, 36)}`, instance, input, "trial", 1);
      }
    }
    if (route === "tasks") {
      if (id === "create") {
        const board = storyboards.find(item => item.id === input.storyboard) ?? err("请选择一个分镜。");
        return addTask(string(input.title) || board.title, instance, input, "workshop", board.frameCount);
      }
      const entry = task(id);
      if (part === "action") {
        const operation = string(input.action);
        const state = { pause: "paused", resume: "running", cancel: "cancelled" }[operation as "pause"];
        if (operation === "remove") {
          if (["running", "ready", "paused"].includes(entry.status)) err("请先停止任务，再移除任务记录。");
          tasks.splice(tasks.indexOf(entry), 1);
        } else if (state) {
          entry.status = entry.statusKey = state;
          if (state === "running") entry.started_at = Date.now() / 1000 - entry.done * 1.7;
          entry.queuePosition = null;
        } else err("未知任务操作。");
        logAction(operation, entry.instance_id, entry.id);
        return { message: `任务 #${entry.num} 已更新。` };
      }
      if (part === "send") {
        session(string(input.umo || entry.umo));
        taskBook(entry);
        logAction("send", entry.instance_id, entry.id);
        return { message: "已模拟发送至目标会话。" };
      }
      if (part === "redraw" || part === "pick" || part === "qa") {
        const book = taskBook(entry);
        if (part === "qa") {
          reviewed.add(book.id);
          logAction("qa", entry.instance_id, entry.id);
          return { task: entry, message: "演示质检已完成，可查看候选图评分。" };
        }
        const number = Number(input.panel_number ?? input.panel);
        if (!Number.isInteger(number) || number < 1 || number > book.frameCount) err("请从任务详情选择一个分幕。");
        if (part === "pick") {
          const take = Number(input.take_number ?? input.candidate);
          if (take !== 1 && take !== 2) err("这个候选图不存在。");
          adopted.set(`${book.id}/${number}`, take);
          logAction("adopt_take", entry.instance_id, entry.id);
          return { message: `第 ${number} 幕已采纳候选图 ${take}。` };
        }
        adopted.set(`${book.id}/${number}`, 2);
        logAction("redraw", entry.instance_id, entry.id);
        return { task: entry, message: `第 ${number} 幕已换为新校样。` };
      }
    }
    if (route === "gallery" && part === "send") {
      session(string(input.umo));
      work(id);
      logAction("gallery_send", instance.id);
      return { message: "已模拟向目标会话发送成品。" };
    }
    if (route === "resources" && id === "diagnose") {
      const wf = workflows.find(item => item.id === (input.id || input.workflow_id || input.workflow));
      if (!wf) err("请选择一个工作流。");
      const broken = wf?.id === "workflow-pencil";
      return { diagnosis: { ok: !broken, issues: Number(broken), missing_models: broken ?
        [{ node: "12", class_type: "CheckpointLoaderSimple", input: "ckpt_name", reason: "本机未安装 mio_pencil_02.safetensors" }] : [],
        warnings: broken ? ["补齐缺少的检查点后可重新诊断。"] : [] } };
    }
    if (route === "resources" && id === "models") return { source: "bundled", notice: "这些模型名称来自离线演示。",
      models: [{ id: "mio_story_ink", name: "Mio Story Ink", group: "版画校样" },
        { id: "soft_proof", name: "Soft Proof v2", group: "粉彩校样" }, { id: "mio_pencil_02", name: "Mio Pencil 02", group: "线稿" }] };
    if (route === "sessions" && id === "rule") {
      const target = session(string(input.umo));
      for (const [field, value] of Object.entries(row(input.changes))) {
        if (!["instance_id", "notify_mode", "send_mode", "layout_id", "format", "max_images", "max_file_mb"].includes(field)) continue;
        if (value === null || value === "") delete target.rule[field];
        else target.rule[field] = value;
      }
      return { rule: target.rule };
    }
    if (route === "webhooks") {
      if (input.action === "register") {
        if (!config.webhook.enabled || !config.webhook.public_url) err("请先启用签名监听并填写回调基址。");
        hooks.add(instance.id);
      } else if (input.action === "remove") hooks.delete(instance.id);
      else if (input.action === "test" && hooks.has(instance.id)) {
        const entry = { id: `dlv-demo-${log.length + 1}`, event: "ping", status: 204, attempts: 1, at: new Date().toISOString(), error: "" };
        if (instance.id === "primary") log.unshift(entry);
        return entry;
      } else err("请先注册当前实例的回调。");
      logAction(`webhook_${input.action}`, instance.id);
      return get("webhooks", input);
    }
    return err("演示暂未提供此操作。");
  };

  return {
    demo: true,
    async get<T>(endpoint: string, input?: object) {
      return structuredClone(get(endpoint.replace(/^\//, ""), row(input))) as T;
    },
    async post<T>(endpoint: string, input?: object) {
      return structuredClone(post(endpoint.replace(/^\//, ""), row(input))) as T;
    },
    async download(endpoint, params, filename) {
      const input = row(params);
      const [route, id = "", part] = endpoint.split("/").filter(Boolean).map(decodeURIComponent);
      if (part !== "export" || (route !== "tasks" && route !== "gallery")) return err("没有找到可下载的样张。");
      const book = route === "tasks" ? taskBook(task(id)) : work(id);
      const format = formats.includes(input.format) ? input.format : "html";
      const result = await sampleFile(book, format, input);
      const name = filename || `${book.title}.${result.ext}`;
      saveBlob(result.blob, name);
      return { filename: name };
    },
  };
}

function sampleAlbum(book: Work, input: RecordData, format: string) {
  const chosen = layouts.find(item => item.id === input.layout) ?? layouts[0]!;
  const art = Array.from({ length: Math.min(book.frameCount || 1, 12) }, (_, index) =>
    `<figure><img src="${plate(book.art + index)}" alt="第 ${index + 1} 幕">${input.show_captions === false ?
      "" : `<figcaption>第 ${index + 1} 幕 · ${directions[index % directions.length]}</figcaption>`}</figure>`).join("");
  const theme = /^#[a-fA-F0-9]{6}$/.test(string(input.theme_color)) ? string(input.theme_color) : chosen.options.accent;
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${safeHTML(book.title)}</title><style>
body{margin:0;padding:8vw;background:${chosen.options.paper};color:${chosen.options.text};font:16px Georgia,"Songti SC",serif}main{max-width:940px;margin:auto}header{border-top:3px solid ${theme};padding:1rem 0 2rem}
small,figcaption{font-size:.8rem}h1{font-weight:500;font-size:clamp(2rem,4vw,4rem)}figure{margin:0 0 2rem}img{display:block;width:100%;height:auto}figcaption{padding:.6rem 0}footer{border-top:1px solid ${theme};padding-top:1rem}
</style><main><header><small>MIO · OFFLINE PROOF / ${safeHTML(chosen.title.toUpperCase())}</small><h1>${safeHTML(book.title)}</h1><p>${safeHTML(book.description)}</p></header>${art}<footer>${safeHTML(input.signature || "Mio 绘页 · 离线样张")}${format === "motion" ? " · 动态漫版式样张" : ""}</footer></main></html>`;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function strip(blobNumber: number, count: number) {
  const canvas = document.createElement("canvas");
  const width = 720, height = 460;
  canvas.width = width;
  canvas.height = Math.min(12, Math.max(1, count)) * height;
  const context = canvas.getContext("2d");
  if (!context) return err("当前浏览器不能生成画面文件。");
  await Promise.all(Array.from({ length: canvas.height / height }, async (_, index) => {
    const image = new Image();
    image.src = plate(blobNumber + index);
    await image.decode();
    context.drawImage(image, 0, index * height, width, height);
  }));
  return canvas;
}

async function canvasBlob(canvas: HTMLCanvasElement, type: string): Promise<Blob> {
  return await new Promise((resolve, reject) => canvas.toBlob(
    value => value ? resolve(value) : reject(new Error("无法生成图片文件。")), type, 0.9,
  ));
}

async function sampleFile(book: Work, format: string, input: RecordData): Promise<{ blob: Blob; ext: string }> {
  const html = sampleAlbum(book, input, format);
  if (format === "strip") return { blob: await canvasBlob(await strip(book.art, book.frameCount), "image/png"), ext: "png" };
  if (format === "pdf") return { blob: await samplePdf(book), ext: "pdf" };
  if (format === "zip" || format === "slices") {
    const encoder = new TextEncoder();
    const files = [{ name: "mio-proof.html", data: encoder.encode(html) }];
    if (format === "slices") {
      for (let index = 0; index < Math.min(12, book.frameCount); index++) {
        const png = await canvasBlob(await strip(book.art + index, 1), "image/png");
        files.push({ name: `panel-${index + 1}.png`, data: new Uint8Array(await png.arrayBuffer()) });
      }
    }
    return { blob: zipFiles(files), ext: "zip" };
  }
  return { blob: new Blob([html], { type: "text/html;charset=utf-8" }), ext: "html" };
}

// ISO/IEC 21320 store-mode sample archive: no runtime dependency on a ZIP library.
function zipFiles(files: Array<{ name: string; data: Uint8Array<ArrayBuffer> }>): Blob {
  const parts: BlobPart[] = [], central: BlobPart[] = [];
  let offset = 0, centralSize = 0;
  for (const { name, data } of files) {
    const filename = new TextEncoder().encode(name);
    const local = new DataView(new ArrayBuffer(30)), entry = new DataView(new ArrayBuffer(46));
    let crc = 0xffffffff;
    for (const byte of data) {
      crc ^= byte;
      for (let b = 0; b < 8; b++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    crc = (crc ^ 0xffffffff) >>> 0;
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true);
    local.setUint32(14, crc, true); local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true); local.setUint16(26, filename.length, true);
    entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true);
    entry.setUint32(16, crc, true); entry.setUint32(20, data.length, true); entry.setUint32(24, data.length, true);
    entry.setUint16(28, filename.length, true); entry.setUint32(42, offset, true);
    parts.push(local.buffer, filename, data);
    central.push(entry.buffer, filename);
    offset += local.byteLength + filename.length + data.length;
    centralSize += entry.byteLength + filename.length;
  }
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
  end.setUint32(12, centralSize, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end.buffer], { type: "application/zip" });
}

async function samplePdf(book: Work): Promise<Blob> {
  const canvas = await strip(book.art, 1);
  const jpeg = await canvasBlob(canvas, "image/jpeg");
  const w = canvas.width, h = canvas.height;
  const encoder = new TextEncoder();
  const output: BlobPart[] = [];
  let length = 0;
  const offsets = [0];
  const write = (text: string) => {
    const bytes = encoder.encode(text);
    output.push(bytes);
    length += bytes.length;
  };
  const object = (number: number, contents: string) => {
    offsets[number] = length;
    write(`${number} 0 obj\n${contents}\nendobj\n`);
  };
  const drawing = `q ${w} 0 0 ${h} 0 0 cm /Art Do Q`;
  write("%PDF-1.4\n");
  object(1, "<< /Type /Catalog /Pages 2 0 R >>");
  object(2, "<< /Type /Pages /Count 1 /Kids [3 0 R] >>");
  object(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Art 4 0 R >> >> /Contents 5 0 R >>`);
  offsets[4] = length;
  write(`4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${w} /Height ${h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.size} >>\nstream\n`);
  output.push(jpeg); length += jpeg.size;
  write("\nendstream\nendobj\n");
  object(5, `<< /Length ${drawing.length} >>\nstream\n${drawing}\nendstream`);
  const xref = length;
  const space = String.fromCharCode(32), newline = String.fromCharCode(10);
  write(`xref${newline}0 6${newline}0000000000 65535 f${space}${newline}`);
  for (const offset of offsets.slice(1)) write(`${String(offset).padStart(10, "0")} 00000 n${space}${newline}`);
  write(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  return new Blob(output, { type: "application/pdf" });
}
