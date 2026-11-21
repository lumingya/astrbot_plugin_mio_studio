import { createApi } from "./api";
import { renderGallery, renderStudio, renderTasks } from "./creative";
import { renderCatalog, renderCommands, renderDelivery, renderGovernance } from "./management";
import { empty, esc, heading, icon, options } from "./ui";
import type { DrawerOptions, RouteDetail, ToastKind, ViewContext, ViewName } from "./ui";
import "./style.css";

type Theme = "light" | "dark";
type Instance = { id: string; label: string };
type Metrics = { images?: number; active?: number; average?: number; resources?: number };
type Overview = { metrics?: Metrics; instances?: Instance[]; default_id?: string };
type Task = { id: string; num: number; title: string; done?: number; total?: number; kind?: string; status?: string; statusKey?: string; queuePosition?: number };
type Work = { id: string; episode_id?: string; title: string; series_title?: string; src?: string; thumbnail?: string; frameCount?: number };
type Status = { ok?: boolean; error?: string; version?: string; tasks?: { active?: number } };

const views: { key: ViewName; label: string; caption: string; short: string }[] = [
  { key: "dashboard", label: "工作台总览", caption: "Dashboard", short: "总览" },
  { key: "studio", label: "创作工坊", caption: "Studio", short: "创作" },
  { key: "tasks", label: "任务中心", caption: "Tasks", short: "任务" },
  { key: "gallery", label: "作品画廊", caption: "Gallery", short: "画廊" },
  { key: "catalog", label: "资源与资产", caption: "Catalog", short: "资源" },
  { key: "delivery", label: "交付与自动化", caption: "Delivery", short: "交付" },
  { key: "governance", label: "权限与实例", caption: "Governance", short: "实例" },
  { key: "commands", label: "指令手册", caption: "Commands", short: "指令" },
];
const byView = new Map(views.map((entry) => [entry.key, entry]));
const statusLabel: Record<string, string> = {
  queued: "等候中", queued_remote: "已入队", ready: "等候中", running: "生成中",
  preparing: "准备中", dispatching: "提交中", paused: "已暂停", pending: "准备中",
  standby: "待启动", complete: "已完成", failed: "失败", interrupted: "需核对",
  cancelled: "已停止", archived: "旧版记录", missing: "已移除",
};
const activeStatuses = new Set(["queued", "queued_remote", "ready", "pending", "dispatching", "preparing", "running"]);
const nav = (entry: typeof views[number], mobile = false) => {
  const label = mobile ? entry.short : entry.label;
  return `<a href="#${entry.key}" data-route="${entry.key}" title="${esc(entry.caption)}">${icon(entry.key, mobile ? 19 : 17)}<span>${esc(label)}</span></a>`;
};

const api = createApi();
const root = document.querySelector<HTMLElement>("#app")!;
root.innerHTML = `
  <a href="#content" class="skip-link">跳转到内容</a>
  <aside class="sidebar" aria-label="工作室">
    <a class="brand" href="#dashboard" data-route="dashboard">
      <span class="brand-mark">${icon("book", 25)}</span>
      <span><strong>Mio <i>·</i> 绘页</strong><small>INDEPENDENT COMIC STUDIO</small></span>
    </a>
    <p class="nav-caption"><span>01</span> 创作工作区</p>
    <nav aria-label="创作工作区">${views.slice(0, 5).map((entry) => nav(entry)).join("")}</nav>
    <p class="nav-caption"><span>02</span> 工作室管理</p>
    <nav aria-label="工作室管理">${views.slice(5).map((entry) => nav(entry)).join("")}</nav>
    <div class="issue"><span class="issue-mark">M.</span><span>工作室版 <small>OPEN API / V2</small></span></div>
  </aside>
  <div class="main-frame">
    ${api.demo ? `<div class="demo-strip"><span aria-hidden="true"></span><strong>演示模式</strong><span class="demo-extra">· 使用本地样稿与模拟任务</span></div>` : ""}
    <header class="topbar">
      <div class="breadcrumb"><span class="mobile-brand">Mio · 绘页</span><span class="desktop-crumb">MIO STUDIO</span><span class="breadcrumb-slash" aria-hidden="true">/</span><strong id="active-label">工作台总览</strong></div>
      <div class="top-actions">
        <label class="instance-picker"><span aria-hidden="true">${icon("server", 17)}</span><select id="instance" aria-label="当前实例"><option value="primary">主实例</option></select></label>
        <span id="health" class="health" data-health="checking"><i aria-hidden="true"></i><span>连接检查</span></span>
        <a id="activity" class="activity" href="#tasks" data-route="tasks" title="活动队列">${icon("tasks", 17)} <b id="activity-count">0</b><span>活跃</span></a>
        <button type="button" class="icon-button theme-toggle" id="theme" aria-label="切换深墨主题"></button>
      </div>
    </header>
    <main id="content" tabindex="-1"></main>
    <footer class="page-footer"><span>MIO STUDIO / OPEN API V2</span><span>为连环故事而作</span></footer>
  </div>
  <nav aria-label="移动工作区" class="mobile-nav">${views.map((entry) => nav(entry, true)).join("")}</nav>
  <dialog id="drawer" class="drawer"><section class="drawer-frame"><header class="drawer-head"><div><p class="eyebrow">MIO STUDIO / INSPECT</p><h2 id="drawer-title"></h2><p id="drawer-subtitle"></p></div><button class="icon-button" type="button" data-close="drawer" aria-label="关闭抽屉">${icon("close")}</button></header><div class="drawer-content"></div></section></dialog>
  <dialog id="lightbox" class="lightbox" aria-label="图片预览"><figure><button class="icon-button" type="button" data-close="lightbox" aria-label="关闭看图">${icon("close")}</button><img alt=""><figcaption></figcaption></figure></dialog>
  <div id="toasts" class="toast-stack" aria-live="polite" aria-atomic="false"></div>
`;

function storageRead(key: string): string | null {
  try { return localStorage.getItem(`mio-studio-v3.${key}`); } catch { return null; }
}
function storageWrite(key: string, value: string): void {
  try { localStorage.setItem(`mio-studio-v3.${key}`, value); } catch { /* iframe storage is optional */ }
}
function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  const button = document.querySelector<HTMLButtonElement>("#theme")!;
  const description = theme === "dark" ? "切换暖纸主题" : "切换深墨主题";
  button.innerHTML = icon(theme === "dark" ? "sun" : "moon", 18);
  button.setAttribute("aria-label", description);
  button.title = description;
}
const savedTheme = storageRead("theme");
const themePreference = matchMedia("(prefers-color-scheme: dark)");
setTheme(savedTheme === "dark" || savedTheme === "light" ? savedTheme : themePreference.matches ? "dark" : "light");
document.querySelector("#theme")!.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(next);
  storageWrite("theme", next);
});
themePreference.addEventListener("change", ({ matches }) => {
  if (!storageRead("theme")) setTheme(matches ? "dark" : "light");
});

const content = document.querySelector<HTMLElement>("#content")!;
const selector = document.querySelector<HTMLSelectElement>("#instance")!;
const drawerDialog = document.querySelector<HTMLDialogElement>("#drawer")!;
const lightboxDialog = document.querySelector<HTMLDialogElement>("#lightbox")!;
drawerDialog.setAttribute("aria-labelledby", "drawer-title");
let instance = storageRead("instance") || "primary";
let headerCycle = 0;
let viewCycle = 0;
let pollTimer = 0;
let topologyLoaded = false;
let persistedInstance = storageRead("instance");
let headerState: Overview = {};

const routeParam = (value: string | null) => value?.slice(0, 256) || undefined;
function readRoute(): { view: ViewName; detail: RouteDetail } {
  const [name, query] = location.hash.slice(1).split("?", 2);
  const params = new URLSearchParams(query ?? "");
  return {
    view: byView.has(name as ViewName) ? name as ViewName : "dashboard",
    detail: { tab: routeParam(params.get("tab")), id: routeParam(params.get("id")) },
  };
}
function goto(view: ViewName, detail?: RouteDetail) {
  const query = new URLSearchParams();
  if (detail?.tab) query.set("tab", detail.tab);
  if (detail?.id) query.set("id", detail.id);
  const target = `#${view}${query.size ? `?${query}` : ""}`;
  if (location.hash === target) {
    void render();
  } else {
    location.hash = target;
  }
}

function toast(message: string, kind: ToastKind = "info") {
  const stack = document.querySelector<HTMLElement>("#toasts")!;
  while (stack.childElementCount > 3) stack.firstElementChild?.remove();
  const notice = document.createElement("div");
  notice.className = `toast toast-${kind}`;
  notice.setAttribute("role", kind === "error" ? "alert" : "status");
  notice.innerHTML = `<span aria-hidden="true">${icon(kind === "error" ? "warning" : kind === "success" ? "check" : "info")}</span><span>${esc(message)}</span><button type="button" aria-label="关闭提示">${icon("close", 14)}</button>`;
  stack.append(notice);
  const timer = window.setTimeout(() => notice.remove(), kind === "error" ? 7600 : 3800);
  notice.querySelector("button")?.addEventListener("click", () => {
    window.clearTimeout(timer);
    notice.remove();
  });
}

function drawer({ title, subtitle, content: body, wide }: DrawerOptions): HTMLElement {
  drawerDialog.querySelector<HTMLElement>("#drawer-title")!.textContent = title;
  const subhead = drawerDialog.querySelector<HTMLElement>("#drawer-subtitle")!;
  subhead.textContent = subtitle ?? "";
  subhead.hidden = !subtitle;
  drawerDialog.dataset.wide = String(Boolean(wide));
  const pane = drawerDialog.querySelector<HTMLElement>(".drawer-content")!;
  if (typeof body === "string") pane.innerHTML = body;
  else pane.replaceChildren(body);
  if (!drawerDialog.open) drawerDialog.showModal();
  pane.scrollTop = 0;
  return pane;
}
function closeDrawer() { if (drawerDialog.open) drawerDialog.close(); }

function imageSource(src: string): boolean {
  return /^data:image\/(?:png|jpeg|jpg|webp|gif|svg\+xml)(?:;base64|;charset=utf-8)?,/i.test(src) ||
    /^blob:/i.test(src) || /^\.\/?[^\\]*$/i.test(src);
}
function showLightbox(src: string, caption = "") {
  if (!imageSource(src)) { toast("暂时无法显示这张图片。", "error"); return; }
  const img = lightboxDialog.querySelector("img")!;
  img.src = src;
  img.alt = caption || "作品原图";
  lightboxDialog.querySelector<HTMLElement>("figcaption")!.textContent = caption;
  if (!lightboxDialog.open) lightboxDialog.showModal();
}
lightboxDialog.addEventListener("close", () => lightboxDialog.querySelector("img")!.removeAttribute("src"));
for (const dialog of [drawerDialog, lightboxDialog]) {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
    if ((event.target as Element).closest(`[data-close="${dialog.id}"]`)) dialog.close();
  });
}

function updateNav(view: ViewName) {
  document.querySelector("#active-label")!.textContent = byView.get(view)?.label ?? "工作台总览";
  document.querySelectorAll("[data-route]").forEach((node) => {
    if ((node as HTMLElement).dataset.route === view) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  document.title = `${byView.get(view)?.label ?? "工作台总览"} · Mio 绘页`;
}

async function loadTopology(): Promise<void> {
  try {
    headerState = await api.get<Overview>("overview", { instance_id: instance });
    let rows = headerState.instances ?? [];
    if (!rows.length) rows = [{ id: "primary", label: "主实例" }];
    if (!topologyLoaded && !persistedInstance) {
      instance = headerState.default_id || rows[0].id;
    }
    if (!rows.some((row) => row.id === instance)) instance = headerState.default_id || rows[0].id;
    selector.innerHTML = options(rows, instance);
  } catch {
    if (!Array.from(selector.options).some((row) => row.value === instance)) instance = "primary";
  } finally {
    topologyLoaded = true;
  }
}
function schedulePoll(active: number) {
  window.clearTimeout(pollTimer);
  if (active > 0 && !document.hidden) pollTimer = window.setTimeout(() => { void updateStatus(); }, 11000);
}
async function updateStatus(showChecking = false) {
  const health = document.querySelector<HTMLElement>("#health")!;
  const label = health.querySelector("span")!;
  const slot = document.querySelector<HTMLElement>("#activity-count")!;
  const cycle = ++headerCycle;
  if (showChecking) {
    health.dataset.health = "checking";
    label.textContent = "连接检查";
  }
  try {
    const status = await api.get<Status>("status", { instance_id: instance });
    if (cycle !== headerCycle) return;
    health.dataset.health = status.ok ? "online" : "offline";
    health.title = status.ok ? `Mio ${status.version ?? "v2"}`.trim() : status.error || "Mio 未就绪";
    label.textContent = status.ok ? "在线" : "离线";
    const active = status.tasks?.active ?? headerState.metrics?.active ?? 0;
    slot.textContent = String(active);
    schedulePoll(active);
  } catch {
    if (cycle !== headerCycle) return;
    health.dataset.health = "offline";
    health.title = "无法连接，请查看实例设置。";
    label.textContent = "离线";
    slot.textContent = "0";
    schedulePoll(0);
  }
}

const coverTheme = [
  { bg: "#bf9b76", ink: "#342524", light: "#efe0b7" },
  { bg: "#798077", ink: "#303b36", light: "#ede2ad" },
  { bg: "#9c6a58", ink: "#482e32", light: "#f3d7ae" },
];
function fallbackCover(index: number): string {
  const colors = coverTheme[index % coverTheme.length];
  const midpoint = 80 + index * 20;
  return `<svg class="cover-fallback" aria-hidden="true" viewBox="0 0 230 150" preserveAspectRatio="xMidYMid slice"><path fill="${colors.bg}" d="M0 0h230v150H0z"/><circle cx="${midpoint}" cy="59" r="31" fill="${colors.light}"/><path fill="${colors.ink}" d="M0 128 35 99l24 16 66-49 33 34 25-12 47 35v27H0Z"/><path d="M0 125 62 111l56 11 58-19 54 17M0 142l71-6 63 5 96-13" fill="none" stroke="${colors.light}" opacity=".6"/><path d="M26 0v150m7-150v150m173-150v150" stroke="${colors.light}" opacity=".35"/></svg>`;
}

const heroArt = `<svg aria-hidden="true" viewBox="0 0 496 298" preserveAspectRatio="xMidYMid slice" class="hero-art-svg">
  <defs>
    <pattern id="hero-grain" width="4" height="4" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".42" fill="#302525" opacity=".34"/></pattern>
    <pattern id="hero-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(37)"><path d="M0 0v6" fill="none" stroke="#f4d8ae" stroke-width=".7" opacity=".52"/></pattern>
  </defs>
  <path fill="#bf9a80" d="M0 0h496v298H0z"/><path fill="#d7ae86" d="M0 131c104 5 199-28 272-17 71 11 135 39 224 22v162H0Z"/>
  <circle cx="354" cy="103" r="67" fill="#efe0b1"/><circle cx="354" cy="103" r="79" fill="none" stroke="#f3dcb3" stroke-width=".75"/>
  <path d="m0 218 50-14 51-37 38 5 70-61 38 29 39-16 57 50 63 7 90 38v79H0Z" fill="#986d63"/>
  <path d="M0 228q31-7 72-29l43 4 38-27 44 14 90-54 39 21 35-5 64 45 71 21v80H0Z" fill="#69504f"/>
  <path d="M0 245q64-8 104-35l49 10 105-63 45 15 40 3 24 32 58 21 71 6v64H0Z" fill="url(#hero-hatch)"/>
  <path d="m0 261 68-17 26 2 68-22 39 12 90-17 42 15 55-15 108 24v55H0Z" fill="#473a3c"/>
  <path d="M0 270c48-12 108-10 150-10 86 0 168-29 346-1m-496 25c95-3 135-18 209-13 122 9 201-19 287 2" stroke="#f4d8ae" stroke-width=".8" opacity=".64" fill="none"/>
  <path fill="url(#hero-grain)" d="M0 0h496v298H0z"/>
  <path d="M34 20v257M41 20v257m412-257v257" stroke="#f0dab6" opacity=".4" fill="none"/>
  <text x="48" y="42" fill="#4d3634" font-size="10" font-family="Georgia" letter-spacing="2.8">MIO / GRAPHIC STUDIES</text>
  <text x="432" y="268" fill="#f1e3ca" font-size="11" text-anchor="end" font-family="Georgia" letter-spacing="1.4">PLATE № 01</text>
</svg>`;

function duration(value: number | undefined) {
  if (!value || value < 0) return "—";
  if (value < 60) return `${Math.round(value)} 秒`;
  return `${Math.floor(value / 60)} 分 ${Math.round(value % 60)} 秒`;
}
async function renderDashboard(ctx: ViewContext) {
  ctx.mount.innerHTML = `
    ${heading("01 / THE WORKBENCH", "工作台总览", "从灵感、分镜到成册，一处掌握你的漫画创作。", `<button class="btn" type="button" id="dashboard-refresh">${icon("refresh", 16)} 刷新</button>`)}
    <section class="dashboard-hero" aria-labelledby="hero-title">
      <div class="hero-body">
        <span class="hero-kicker">MIO / 创作手记</span>
        <h2 id="hero-title">把灵感画成<br>一篇故事。</h2>
        <p>先写一句话，再把它交给分镜、工作流和画册。作品准备好了，Mio 会把它送回会话。</p>
        <div class="cluster hero-actions">
          <button type="button" class="btn primary" data-studio="draw">${icon("spark", 17)} 一句话出漫 ${icon("arrow", 16)}</button>
          <button type="button" class="btn" data-studio="assembly">工坊装配</button>
          <button type="button" class="btn subtle" data-studio="trial">单图试画</button>
        </div>
      </div>
      <div class="hero-art">${heroArt}</div>
    </section>
    <div class="metric-grid" id="metrics"></div>
    <div class="dashboard-columns">
      <section aria-labelledby="active-title" class="dash-section">
        <div class="section-heading"><div><p class="eyebrow">02 / IN PROGRESS</p><h2 id="active-title">进行中任务</h2></div><a href="#tasks" class="text-link">任务中心 ${icon("arrow", 15)}</a></div>
        <div id="dashboard-tasks" class="stack" aria-live="polite"></div>
      </section>
      <section aria-labelledby="recent-title" class="dash-section">
        <div class="section-heading"><div><p class="eyebrow">03 / RECENT EDITIONS</p><h2 id="recent-title">最近作品</h2></div><a href="#gallery" class="text-link">全部作品 ${icon("arrow", 15)}</a></div>
        <div id="dashboard-works" class="cover-grid"></div>
      </section>
    </div>`;
  for (const button of ctx.mount.querySelectorAll<HTMLButtonElement>("[data-studio]")) {
    button.onclick = () => { ctx.goto("studio", { tab: button.dataset.studio }); };
  }
  ctx.mount.querySelector<HTMLButtonElement>("#dashboard-refresh")!.onclick = () => { void ctx.refresh(); };
  const params = { instance_id: ctx.instance };
  const data = await Promise.allSettled([
    ctx.api.get<Overview>("overview", params),
    ctx.api.get<{ items?: Task[] }>("tasks", { ...params, limit: 30 }),
    ctx.api.get<{ items?: Work[] }>("gallery", { ...params, limit: 3 }),
  ]);
  const [overview, taskResult, workResult] = data;
  const stats = overview.status === "fulfilled" ? overview.value.metrics ?? {} : {};
  const items = [
    ["images", "今日出图", stats.images ?? "—", "今日累计完成的图像"],
    ["tasks", "活跃队列", stats.active ?? "—", "排队及生成中的任务"],
    ["clock", "平均耗时", duration(stats.average), "已完成任务的平均耗时"],
    ["catalog", "接入资源", stats.resources ?? "—", "工作流、分镜及预设"],
  ];
  ctx.mount.querySelector("#metrics")!.innerHTML = items.map(([glyph, name, value, note]) => `
    <article class="metric card"><div class="metric-head"><span>${esc(name)}</span><span class="metric-icon">${icon(String(glyph), 17)}</span></div><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join("");
  const tasks = ctx.mount.querySelector<HTMLElement>("#dashboard-tasks")!;
  const works = ctx.mount.querySelector<HTMLElement>("#dashboard-works")!;
  const active = taskResult.status === "fulfilled" ? (taskResult.value.items ?? []).filter((item) => activeStatuses.has(item.status || item.statusKey || "")).slice(0, 3) : [];
  tasks.innerHTML = active.length ? active.map((item) => {
    const percent = item.total ? Math.round(100 * (item.done ?? 0) / item.total) : 0;
    const key = item.status || item.statusKey || "";
    return `<button type="button" class="task-summary card" data-task="${esc(item.id)}"><div><span class="work-index">#${esc(item.num)}</span><strong>${esc(item.title)}</strong><span class="task-state">${esc(statusLabel[key] || "进行中")}</span></div><div><progress max="100" value="${Math.max(0, Math.min(100, percent))}" aria-label="任务进度"></progress><small>${item.queuePosition ? `第 ${esc(item.queuePosition)} 位` : `${esc(item.done ?? 0)} / ${esc(item.total ?? 0)} 幕`}</small></div></button>`;
  }).join("") : empty(taskResult.status === "rejected" ? "任务暂不可见" : "队列暂时空闲", "新的创作会出现在这里。");
  tasks.querySelectorAll<HTMLButtonElement>("button[data-task]").forEach((button) => {
    button.onclick = () => ctx.goto("tasks", { id: button.dataset.task });
  });
  const shelves = workResult.status === "fulfilled" ? workResult.value.items ?? [] : [];
  works.innerHTML = shelves.length ? shelves.slice(0, 3).map((item, index) => {
    const src = item.src || item.thumbnail;
    const art = src && imageSource(src) ? `<img src="${esc(src)}" alt="" loading="lazy">` : fallbackCover(index);
    return `<button class="cover-preview" type="button" data-work="${esc(item.episode_id || item.id)}"><div class="cover-art">${art}</div><span class="work-index">ED. 0${index + 1}</span><strong>${esc(item.title)}</strong><small>${esc(item.series_title || (item.frameCount ? `${item.frameCount} 幕` : "Mio 绘页"))}</small></button>`;
  }).join("") : empty(workResult.status === "rejected" ? "作品暂不可见" : "还没有画册", "第一篇故事可以从创作工坊开始。");
  works.querySelectorAll<HTMLButtonElement>("button[data-work]").forEach((button) => {
    button.onclick = () => ctx.goto("gallery", { id: button.dataset.work });
  });
}

const renderers: Record<ViewName, (ctx: ViewContext) => void | Promise<void>> = {
  dashboard: renderDashboard, studio: renderStudio, tasks: renderTasks, gallery: renderGallery,
  catalog: renderCatalog, delivery: renderDelivery, governance: renderGovernance, commands: renderCommands,
};
async function render() {
  const cycle = ++viewCycle;
  const { view, detail } = readRoute();
  updateNav(view);
  const mount = document.createElement("div");
  mount.className = `view view-${view}`;
  mount.innerHTML = '<p class="loading-view" role="status">正在打开工作台…</p>';
  content.replaceChildren(mount);
  const current = () => viewCycle === cycle;
  const ctx: ViewContext = {
    mount, api, instance, route: detail,
    goto: (name, route) => { if (current()) goto(name, route); },
    toast: (message, kind) => { if (current()) toast(message, kind); },
    drawer: (opts) => current() ? drawer(opts) : document.createElement("div"),
    closeDrawer: () => { if (current()) closeDrawer(); },
    lightbox: (src, caption) => { if (current()) showLightbox(src, caption); },
    refresh: async () => { if (current()) await refresh(true); },
  };
  try {
    await renderers[view](ctx);
  } catch (error) {
    if (!current()) return;
    const message = error instanceof Error ? error.message : "暂时无法读取此视图，请稍后重试。";
    mount.innerHTML = heading("MIO STUDIO", byView.get(view)!.label) + `<div class="card error-view">${empty("工作台暂不可用", message)}<button type="button" class="btn" id="current-retry">重试</button></div>`;
    mount.querySelector<HTMLButtonElement>("#current-retry")!.onclick = () => { void refresh(true); };
    toast(message, "error");
  }
}

async function refresh(topology: boolean) {
  if (topology) await loadTopology();
  await Promise.all([render(), updateStatus(true)]);
}
selector.addEventListener("change", () => {
  instance = selector.value;
  persistedInstance = instance;
  storageWrite("instance", instance);
  closeDrawer();
  if (lightboxDialog.open) lightboxDialog.close();
  const { view, detail } = readRoute();
  if (detail.id) goto(view, { tab: detail.tab });
  void refresh(false);
});
window.addEventListener("hashchange", () => {
  if (location.hash === "#content") { content.focus(); return; }
  closeDrawer();
  if (lightboxDialog.open) lightboxDialog.close();
  window.scrollTo({ top: 0 });
  void render();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) window.clearTimeout(pollTimer);
  else void updateStatus();
});
void refresh(true);
