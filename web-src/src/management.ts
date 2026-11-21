import { esc as e, heading, tip, type ViewContext } from "./ui";
import "./management.css";

type Row = Record<string, unknown>;
type Choice = readonly [string, string];
const q = <T extends HTMLElement>(root: ParentNode, selector: string) =>
  root.querySelector<T>(selector);
const obj = (value: unknown): Row =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(obj) : [];
const str = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : typeof value === "number" ? String(value) : fallback;
const lines = (value: unknown) => Array.isArray(value) ? value.map((item) => str(item)).join("\n") : str(value);
const id = (row: Row) => str(row.id);
const title = (row: Row) => str(row.title || row.name || row.label || row.id, "未命名");
const dateTime = (value: unknown) => {
  const dt = new Date(typeof value === "number" ? 1000 * value : str(value));
  return Number.isNaN(dt.getTime()) ? "—" : dt.toLocaleString("zh-CN", { hour12: false });
};
const button = (text: string, attrs = "", variant = "") =>
  `<button type="button" class="mng-btn ${variant}" ${attrs}>${e(text)}</button>`;
const opt = (choices: readonly Choice[], selected: unknown) => choices
  .map(([key, label]) => `<option value="${e(key)}"${key === selected ? " selected" : ""}>${e(label)}</option>`).join("");
const choices = (items: Row[], blank = "沿用默认"): Choice[] =>
  [["", blank], ...items.map((item): Choice => [id(item), title(item)])];
const select = (name: string, values: readonly Choice[], selected: unknown) =>
  `<select class="mng-control" name="${name}">${opt(values, selected)}</select>`;
const input = (name: string, value: unknown, attrs = "") =>
  `<input class="mng-control" name="${name}" value="${e(value)}" ${attrs}>`;
const field = (label: string, content: string, help = "") =>
  `<div class="mng-field"><label>${e(label)}${content}</label>${help ? `<small class="mng-small">${tip(help)} ${e(help)}</small>` : ""}</div>`;
const check = (name: string, label: string, checked: boolean) =>
  `<label class="mng-check"><input type="checkbox" name="${name}" ${checked ? "checked" : ""}> ${e(label)}</label>`;
const textArea = (name: string, value: unknown, placeholder: string) =>
  `<textarea class="mng-control" name="${name}" rows="3" placeholder="${e(placeholder)}">${e(lines(value))}</textarea>`;
const formBtn = (text = "保存设置") =>
  `<div class="mng-actions"><button class="mng-btn primary" type="submit">${e(text)}</button></div>`;
const sectionHead = (number: string, headingText: string, detail = "", actions = "") =>
  `<header class="mng-head"><div><div class="mng-kicker">${e(number)}</div><h2>${e(headingText)}</h2>${detail ? `<p>${e(detail)}</p>` : ""}</div>${actions}</header>`;
const empty = (message: string) => `<div class="mng-empty" role="status">${e(message)}</div>`;
const scope = (ctx: ViewContext) => ({ instance_id: ctx.instance });
const pageSize = 12;

function formData(root: ParentNode, selector: string) {
  const form = q<HTMLFormElement>(root, selector);
  if (!form) throw new Error("表单尚未就绪。");
  return new FormData(form);
}

function safe(ctx: ViewContext, element: HTMLElement | null, work: () => Promise<void>) {
  return async (ev?: Event) => {
    ev?.preventDefault();
    const control = element instanceof HTMLFormElement
      ? q<HTMLButtonElement>(element, 'button[type="submit"]')
      : element instanceof HTMLButtonElement ? element : null;
    if (control?.disabled) return;
    if (control) control.disabled = true;
    try {
      await work();
    } catch (error) {
      ctx.toast(error instanceof Error && error.message ? error.message : "操作没有完成，请重试。", "error");
    } finally {
      if (control) control.disabled = false;
    }
  };
}
function on(root: ParentNode, ctx: ViewContext, selector: string, work: () => Promise<void>, event = "click") {
  const element = q<HTMLElement>(root, selector);
  element?.addEventListener(event, safe(ctx, element, work));
}
function open(ctx: ViewContext, name: string, subtitle: string, content: string) {
  const area = document.createElement("div");
  area.className = "mng-root mng-drawer";
  area.innerHTML = content;
  ctx.drawer({ title: name, subtitle, content: area });
  return area;
}
function mount(ctx: ViewContext, headline: string, content: string) {
  ctx.mount.innerHTML = headline + `<div class="mng-root">${content}</div>`;
}

const KINDS = [
  ["workflows", "工作流"],
  ["storyboards", "分镜"],
  ["presets", "预设"],
  ["channels", "图像通道"],
  ["kits", "创作套件"],
  ["layouts", "画册版式"],
] as const;
const KIND_NOTE: Record<string, string> = {
  workflows: "工作流由绘页管理。查看节点、检查绑定或浏览 LoRA 槽位。",
  storyboards: "按幕浏览脚本，核对预设中需要填入的变量。",
  presets: "角色、场景和风格词条在工坊装配时按变量匹配。",
  channels: "只探测已配置的通道。模型目录不会修改通道设置。",
  kits: "查看绘页中已安装的创作套件。",
  layouts: "查看绘页提供的画册输出风格。",
};

function subtitle(kind: string, row: Row) {
  switch (kind) {
    case "workflows": return `${str(row.nodeCount, "—")} 个节点${row.checkpoint ? " · " + str(row.checkpoint) : ""}`;
    case "storyboards": return `${str(row.frameCount, "—")} 幕 · 分镜稿`;
    case "presets": return `${row.category === "characters" ? "角色" : "场景"} · ${rows(row.entries).length} 个变量`;
    case "channels": return [row.cloud ? "云端" : "本地", row.provider, row.model].filter(Boolean).join(" · ");
    case "kits": return str(row.description, "可在 Mio 绘页进行创作套件管理。");
    default: return str(row.description || row.layout, "画册版式");
  }
}

function drawerFacts(pairs: [string, unknown][]) {
  const valid = pairs.filter(([, value]) => value !== undefined && value !== null && value !== "");
  return valid.length
    ? `<dl class="mng-facts">${valid.map(([key, value]) => `<dt>${e(key)}</dt><dd>${e(value)}</dd>`).join("")}</dl>`
    : "";
}

async function resourceDrawer(ctx: ViewContext, kind: string, item: Row) {
  const result = id(item) ? obj(await ctx.api.get(`resources/${kind}/${encodeURIComponent(id(item))}`, scope(ctx))) : item;
  const detail = obj(result.resource || result.item || result);
  const shared = drawerFacts([["资源编号", detail.id || item.id], ["类型", KINDS.find(([key]) => key === kind)?.[1]]]);
  let content = shared;
  if (kind === "storyboards") {
    const panels = rows(detail.panels);
    content += (detail.base_prompt ? `<div><h3>全篇提示词</h3><p class="mng-wrap">${e(detail.base_prompt)}</p></div>` : "");
    content += panels.length ? `<div><h3>分幕脚本</h3>${panels.map((panel, index) =>
      `<details class="mng-accordion"><summary>第 ${index + 1} 幕</summary>
        <p>${e(panel.description || "暂无文案")}</p>
        ${panel.prompt ? `<p class="mng-code">${e(panel.prompt)}</p>` : ""}
        ${panel.negative ? `<p class="mng-small">反向提示词：${e(panel.negative)}</p>` : ""}
        ${Array.isArray(panel.variables) && panel.variables.length ? `<p class="mng-small">变量：${e(panel.variables.join(" · "))}</p>` : ""}
      </details>`).join("")}</div>` : empty("这个分镜暂无分幕。");
  } else if (kind === "presets") {
    const entries = rows(detail.entries);
    content += `<div><h3>变量词条</h3>${entries.length
      ? drawerFacts(entries.map((entry) => [str(entry.label || entry.key), entry.value]))
      : empty("这个预设暂无变量。")}</div>`;
  } else if (kind === "workflows") {
    const loraData = obj(result.loras || detail.lora_slots || detail.loras);
    const groups = rows(loraData.groups);
    content += drawerFacts([["节点", detail.nodeCount], ["底模", detail.checkpoint]]);
    content += `<div><h3>LoRA 槽位 ${tip("调权和开关可在精确装配中设置，仅影响这次生成。")}</h3>
      ${groups.length ? groups.map((group, i) =>
        `<details class="mng-accordion"><summary>槽位 ${i + 1} · ${e(group.kind || group.key)}</summary>
          ${drawerFacts(rows(group.loras).map((lora) => [str(lora.name || lora.key), `权重 ${str(lora.strength, "—")}${lora.enabled === false ? " · 已关闭" : ""}`]))}
          ${group.reason ? `<p class="mng-small">${e(group.reason)}</p>` : ""}
        </details>`).join("") : empty("这个工作流没有可用的 LoRA 槽位。")}
      </div><div class="mng-actions">${button("运行工作流诊断", 'data-mng-probe', "primary")}</div><div data-mng-result aria-live="polite"></div>`;
  } else if (kind === "channels") {
    content += drawerFacts([["适配器", detail.provider || detail.kind], ["当前模型", detail.model]]);
    content += id(item) ? `<div class="mng-actions">${button("探测模型目录", "data-mng-probe", "primary")}</div><div data-mng-result aria-live="polite"></div>`
      : '<p class="mng-small">这是 Mio 的默认通道路由。请选择具名通道以探测模型目录。</p>';
  } else {
    content += `<p>${e(detail.description || subtitle(kind, item))}</p>`;
  }
  const root = open(ctx, title(detail.id ? detail : item), "资源目录", content);
  on(root, ctx, "[data-mng-probe]", async () => {
    const isChannel = kind === "channels";
    const payload = obj(await ctx.api.post(isChannel ? "resources/models" : "resources/diagnose", {
      ...scope(ctx),
      ...(isChannel ? { channel_id: id(item) } : { workflow_id: id(item) }),
    }));
    const info = obj(payload.result || payload.diagnosis || payload);
    const results = q(root, "[data-mng-result]");
    if (!results) return;
    if (isChannel) {
      const models = rows(info.models || payload.items);
      results.innerHTML = `<h3>可用模型（${models.length}）</h3>${info.notice ? `<p class="mng-small">${e(info.notice)}</p>` : ""}`
        + (models.length ? drawerFacts(models.slice(0, 100).map(model => [title(model), model.group || model.id])) : empty("未发现可用模型。"));
      return;
    }
    const rawWarnings = Array.isArray(info.warnings) ? info.warnings : [];
    const rawErrors = Array.isArray(info.errors) ? info.errors : [];
    const missingNodes = Object.entries(obj(info.missing_nodes)).map(([key, nodes]) => `缺少节点 ${key}（${Array.isArray(nodes) ? nodes.join(" · ") : "—"}）`);
    const grouped = ["missing_models", "missing_inputs", "invalid_links", "invalid_inputs", "invalid_values"].flatMap(key => rows(info[key]));
    const checks = [...rawErrors, ...rawWarnings, ...missingNodes, ...grouped, ...rows(info.checks || info.issues)];
    const lineItems = checks.map(ch => typeof ch === "string" ? ch : [obj(ch).message || obj(ch).detail || obj(ch).reason || obj(ch).label || obj(ch).title, obj(ch).class_type || obj(ch).node, obj(ch).input].filter(Boolean).join(" · ")).filter(Boolean);
    results.innerHTML = `<h3>诊断结果</h3>${info.summary || info.message ? `<p>${e(info.summary || info.message)}</p>` : ""}`
      + (lineItems.length ? `<ul>${lineItems.map(item => `<li>${e(item)}</li>`).join("")}</ul>` : `<p class="mng-notice">${info.ok === false ? "诊断未通过。请到 Mio 检查工作流绑定。" : "诊断已完成；未报告阻塞项。"}</p>`);
  });
}

export async function renderCatalog(ctx: ViewContext) {
  const requested = ctx.route.tab;
  let kind = KINDS.some(([key]) => key === requested) ? requested! : "workflows";
  let page = 0;
  let catalog = obj(await ctx.api.get("catalog", scope(ctx)));
  mount(ctx, heading("05 / RESOURCES", "资源与资产", "在装配之前，先核对每个工作流、分镜和预设。"),
    `<section class="mng-section">
      ${sectionHead("REFERENCE SHELF", "创作资源", "", button("刷新目录", 'data-catalog-refresh'))}
      <div class="mng-toolbar">
        <div class="mng-tabs" role="group" aria-label="资源分类">${KINDS.map(([key, label]) =>
          `<button class="mng-tab" type="button" aria-pressed="${key === kind}" data-kind="${key}">${e(label)}</button>`).join("")}</div>
        <input class="mng-control" type="search" placeholder="搜索本分类" aria-label="搜索本分类">
      </div>
      <div class="mng-small" data-catalog-note></div>
      <div class="mng-list" data-catalog-list aria-live="polite"></div>
      <div class="mng-pager" data-catalog-page></div>
    </section>`);
  const root = ctx.mount;
  const list = q(root, "[data-catalog-list]")!;
  const pager = q(root, "[data-catalog-page]")!;
  const search = q<HTMLInputElement>(root, 'input[type="search"]')!;
  const note = q(root, "[data-catalog-note]")!;
  function update() {
    const term = search.value.trim().toLocaleLowerCase();
    const all = rows(catalog[kind]).map((item, i) => ({ item, n: i + 1 }));
    const found = all.filter(({ item }) => [title(item), id(item), subtitle(kind, item)].join(" ").toLocaleLowerCase().includes(term));
    const maxPage = Math.max(0, Math.ceil(found.length / pageSize) - 1);
    page = Math.min(page, maxPage);
    note.textContent = `${KIND_NOTE[kind]} 共 ${found.length} 项。`;
    list.innerHTML = found.length ? found.slice(page * pageSize, (page + 1) * pageSize).map(({ item, n }, index) =>
      `<article class="mng-row"><div class="mng-index">${String(n).padStart(2, "0")}</div>
        <div><h3>${e(title(item))}</h3><p class="mng-sub">${e(subtitle(kind, item))}</p></div>
        ${button("详情", `data-resource-index="${index}" aria-label="查看 ${e(title(item))} 详情"`)}
      </article>`).join("") : empty(term ? "没有匹配的资源，换个关键词试试。" : "这个分类还没有资源。");
    list.querySelectorAll<HTMLButtonElement>("[data-resource-index]").forEach((btn) =>
      btn.addEventListener("click", safe(ctx, btn, async () => {
        const item = found[page * pageSize + Number(btn.dataset.resourceIndex)]?.item;
        if (item) await resourceDrawer(ctx, kind, item);
      })));
    pager.innerHTML = maxPage > 0 ? `${button("上一页", `data-prev ${page === 0 ? "disabled" : ""}`)}
      <span class="mng-small">第 ${page + 1} / ${maxPage + 1} 页</span>${button("下一页", `data-next ${page >= maxPage ? "disabled" : ""}`)}` : "";
    q(pager, "[data-prev]")?.addEventListener("click", () => { page -= 1; update(); });
    q(pager, "[data-next]")?.addEventListener("click", () => { page += 1; update(); });
  }
  root.querySelectorAll<HTMLButtonElement>("[data-kind]").forEach((btn) => btn.addEventListener("click", () => {
    kind = btn.dataset.kind || "workflows";
    page = 0;
    root.querySelectorAll("[data-kind]").forEach(tab => tab.setAttribute("aria-pressed", String(tab === btn)));
    update();
  }));
  search.addEventListener("input", () => { page = 0; update(); });
  on(root, ctx, "[data-catalog-refresh]", async () => {
    catalog = obj(await ctx.api.get("catalog", { ...scope(ctx), force: true }));
    update();
    ctx.toast("资源目录已刷新。", "success");
  });
  update();
}

const MODES = [
  ["summary", "仅摘要", "完成后只发简短结果，可自行取件。"],
  ["images", "逐幕图", "发送已采纳的分幕图片。"],
  ["strip", "整条长漫", "把已排版的长漫发送到会话。"],
  ["export", "文件成品", "按默认格式交付文件。"],
  ["qa_then_send", "质检后发送", "先在绘页触发质检，再发送图片。"],
  ["silent", "不发送通知", "有需要时自行查询和取件。"],
] as const;
const MODE_CHOICES: Choice[] = MODES.map(([key, label]) => [key, label]);
const SEND_CHOICES: Choice[] = [
  ["auto", "自动"], ["forward", "合并转发"], ["batch", "单条多图"], ["separate", "逐条发送"],
];
const FORMATS: Choice[] = [
  ["html", "HTML 画册"], ["pdf", "PDF"], ["zip", "ZIP"], ["strip", "长条漫"],
  ["slices", "切图"], ["motion", "动态漫"],
];
const PROFILE_CHOICES: Choice[] = [
  ["auto", "自动"], ["archive", "无损归档"], ["clean", "清除元数据"], ["publish", "发布压缩"], ["preview", "快速预览"],
];
const labelFor = (values: readonly Choice[], value: unknown, fallback = "沿用全局") =>
  values.find(([key]) => key === value)?.[1] || (value ? str(value) : fallback);
const readConfig = async (ctx: ViewContext) => {
  const result = obj(await ctx.api.get("config"));
  return obj(result.config || result);
};
const saveConfig = (ctx: ViewContext, changes: Row) =>
  ctx.api.post("config", { config: changes });

function modesSection(delivery: Row, exports: Row, catalog: Row) {
  return `<section class="mng-section">
    ${sectionHead("DELIVERY POLICY", "默认完成通知", "规则适用于未单独设置的会话。")}
    <form class="mng-form" data-delivery-form>
      <div class="mng-mode-grid" role="group" aria-label="常用交付方式">${MODES.slice(0, 4).map(([key, label, detail]) =>
        `<label class="mng-mode"><input type="radio" name="notify_mode" value="${key}" ${key === (delivery.notify_mode ?? "summary") ? "checked" : ""}><strong>${e(label)}</strong><span>${e(detail)}</span></label>`).join("")}</div>
      <details class="mng-extra"${MODES.slice(4).some(([key]) => key === delivery.notify_mode) ? " open" : ""}><summary>其他通知方式</summary>
        <div class="mng-mode-grid" role="group" aria-label="其他交付方式">${MODES.slice(4).map(([key, label, detail]) =>
        `<label class="mng-mode"><input type="radio" name="notify_mode" value="${key}" ${key === delivery.notify_mode ? "checked" : ""}><strong>${e(label)}</strong><span>${e(detail)}</span></label>`).join("")}</div>
      </details>
      <div class="mng-form-grid">
        ${field("图片发送方式", select("send_mode", SEND_CHOICES, delivery.send_mode || "auto"), "自动模式在 QQ 优先合并转发，拒收后尝试多图或逐条发送。")}
        ${field("默认导出格式", select("format", FORMATS, exports.format || "html"))}
        ${field("默认画册模板", select("layout_id", choices(rows(catalog.layouts), "使用绘页默认模板"), exports.layout_id))}
        ${field("图片档位", select("image_profile", PROFILE_CHOICES, exports.image_profile || "auto"), "发布压缩可减少体积；无损归档适合留存。")}
        ${field("发送上限（MiB）", input("max_file_mb", delivery.max_file_mb ?? 50, 'type="number" min="1" max="200" required'), "超过限制依然可以从面板下载。")}
        ${field("单次限发图片", input("max_images", delivery.max_images ?? 30, 'type="number" min="1" max="100" required'))}
      </div>
      ${check("include_captions", "发图时带上分幕文案", delivery.include_captions !== false)}
      ${formBtn("保存交付设置")}
    </form>
  </section>`;
}

function webhookLog(entries: Row[]) {
  if (!entries.length) return empty("还没有投递记录。注册后可发送测试事件。");
  return `<table class="mng-table"><thead><tr><th>事件</th><th>结果</th><th>时间</th></tr></thead><tbody>
    ${entries.slice(0, 20).map(row => {
      const ok = typeof row.status === "number" && row.status >= 200 && row.status < 300;
      return `<tr><td>${e(row.event || "ping")}</td><td>${ok ? "成功" : "失败"} · ${e(row.status || row.error || "未返回状态")}</td><td>${e(dateTime(row.at || row.created_at))}</td></tr>`;
    }).join("")}</tbody></table>`;
}

function sessionList(sessions: Row[]) {
  if (!sessions.length) return empty("暂无已知会话。在目标群或私聊中运行 /mio ping 后可设置规则。");
  return `<div class="mng-list">${sessions.map((room, n) => {
    const rule = obj(room.rule || room.policy);
    return `<article class="mng-row"><span class="mng-index">${String(n + 1).padStart(2, "0")}</span>
      <div><h3>${e(room.label || room.name || room.umo)}</h3>
      <p class="mng-sub">${e([room.platform, labelFor(MODE_CHOICES, rule.notify_mode), labelFor(SEND_CHOICES, rule.send_mode)].filter(Boolean).join(" · "))}</p></div>
      ${button("编辑规则", `data-session-index="${n}"`)}
    </article>`;
  }).join("")}</div>`;
}

function sessionDrawer(ctx: ViewContext, room: Row, catalog: Row, instances: Row[], onSaved: () => Promise<void>) {
  const rule = obj(room.rule || room.policy);
  const drawer = open(ctx, "会话交付规则", str(room.label || room.name || room.umo),
    `<p class="mng-small">留空则沿用全局设置。规则仅对该会话生效。</p>
     <form data-session-form class="mng-form">
       ${field("默认实例", select("instance_id", choices(instances, "沿用全局"), rule.instance_id))}
       ${field("完成通知", select("notify_mode", [["", "沿用全局"], ...MODE_CHOICES], rule.notify_mode))}
       ${field("图片发送", select("send_mode", [["", "沿用全局"], ...SEND_CHOICES], rule.send_mode))}
       ${field("导出格式", select("format", [["", "沿用全局"], ...FORMATS], rule.format))}
       ${field("画册模板", select("layout_id", choices(rows(catalog.layouts), "沿用全局"), rule.layout_id))}
       ${field("预设 ID", input("preset_ids", Array.isArray(rule.preset_ids) ? rule.preset_ids.join(",") : rule.preset_ids, 'placeholder="例如 preset-ink,preset-rain"'), "多个预设用逗号分隔。可在资源详情查看 ID。")}
       ${formBtn("保存会话规则")}
     </form>`);
  on(drawer, ctx, "[data-session-form]", async () => {
    const fields = formData(drawer, "[data-session-form]");
    const changes = Object.fromEntries([...fields].map(([key, value]) => [key, str(value).trim() || null]));
    await ctx.api.post("sessions/rule", { umo: room.umo, changes });
    ctx.closeDrawer();
    await onSaved();
    ctx.toast("会话规则已更新。", "success");
  }, "submit");
}

export async function renderDelivery(ctx: ViewContext) {
  const [config, rawCatalog, rawSessions, rawOverview, rawHook] = await Promise.all([
    readConfig(ctx),
    ctx.api.get("catalog", scope(ctx)).catch(() => ({})),
    ctx.api.get("sessions"),
    ctx.api.get("instances").catch(() => ctx.api.get("overview", scope(ctx))).catch(() => ({})),
    ctx.api.get("webhooks", scope(ctx)).catch((error: unknown) => ({ loadError: error instanceof Error ? error.message : "无法读取回调状态。" })),
  ]);
  const hookConfig = obj(config.webhook);
  const catalog = obj(rawCatalog);
  const instances = rows(obj(rawOverview).instances);
  let hook = obj(rawHook);
  mount(ctx, heading("06 / DELIVERY", "交付与自动化", "为不同会话安排适合的交付方式，并检查任务回调的投递状态。"),
    modesSection(obj(config.delivery), obj(config.export), catalog)
    + `<section class="mng-section">
      ${sectionHead("EVENTS", "Webhook 实时通知", "绘页可以主动提示任务状态。回调不可达时，系统会继续轮询。", '<span class="mng-status" data-hook-status role="status"></span>')}
      <form class="mng-form" data-webhook-form>
        ${check("enabled", "启用签名回调监听", hookConfig.enabled === true)}
        <div class="mng-form-grid">
          ${field("公开回调基址", input("public_url", hookConfig.public_url, 'type="url" placeholder="https://bot.example.com"'), "请输入 Mio 能访问的 HTTP(S) 基址。注册时由插件保存签名密钥。")}
          ${field("本地监听地址", input("bind_host", hookConfig.bind_host || "127.0.0.1", 'required'), "默认只监听本机。跨机器部署可使用可信反向代理。")}
          ${field("监听端口", input("bind_port", hookConfig.bind_port ?? 8790, 'type="number" min="1" max="65535" required'))}
        </div>
        ${formBtn("保存监听设置")}
      </form>
      <div class="mng-extra mng-actions" data-hook-actions></div>
      <div class="mng-log" data-hook-log aria-live="polite"></div>
    </section>
    <section class="mng-section">
      ${sectionHead("SESSION RULES", "会话独立规则", "为各个群聊与私聊分别选择通知、模板和发送方式。")}
      <div data-session-list></div>
    </section>`);
  const root = ctx.mount;
  const log = q(root, "[data-hook-log]")!;
  const indicator = q(root, "[data-hook-status]")!;
  const actions = q(root, "[data-hook-actions]")!;
  const sessionNode = q(root, "[data-session-list]")!;
  function renderRooms(sessions: Row[]) {
    sessionNode.innerHTML = sessionList(sessions);
    sessionNode.querySelectorAll<HTMLButtonElement>("[data-session-index]").forEach(btn => btn.addEventListener("click",
      safe(ctx, btn, async () => {
        const room = sessions[Number(btn.dataset.sessionIndex)];
        if (room) sessionDrawer(ctx, room, catalog, instances, async () => {
          const next = obj(await ctx.api.get("sessions"));
          renderRooms(rows(next.items));
        });
      }),
    ));
  }
  async function loadHook() {
    hook = obj(await ctx.api.get("webhooks", scope(ctx)));
    renderHook();
  }
  function renderHook() {
    indicator.dataset.ok = String(Boolean(hook.registered && hook.listening));
    indicator.textContent = hook.loadError ? "暂不可用" : hook.registered && hook.listening ? "回调已注册" : "轮询模式";
    actions.innerHTML = (hook.registered
      ? button("发送测试", "data-hook-test", "primary") + button("取消注册", "data-hook-remove", "danger")
      : button("一键注册", "data-hook-register", "primary")) + button("刷新记录", "data-hook-refresh");
    log.innerHTML = hook.loadError ? empty(str(hook.loadError)) : webhookLog(rows(hook.deliveries));
    on(actions, ctx, "[data-hook-register]", async () => {
      await ctx.api.post("webhooks", { action: "register", ...scope(ctx) });
      await loadHook();
      ctx.toast("绘页已注册回调。", "success");
    });
    on(actions, ctx, "[data-hook-test]", async () => {
      const result = obj(await ctx.api.post("webhooks", { action: "test", ...scope(ctx) }));
      const delivery = obj(result.delivery || result);
      await loadHook();
      if (typeof delivery.status === "number" && delivery.status >= 200 && delivery.status < 300)
        ctx.toast("测试事件已送达。", "success");
      else ctx.toast("测试已完成，请查看投递记录。", "info");
    });
    on(actions, ctx, "[data-hook-remove]", async () => {
      await ctx.api.post("webhooks", { action: "remove", ...scope(ctx) });
      await loadHook();
      ctx.toast("已取消注册，任务仍可轮询。", "success");
    });
    on(actions, ctx, "[data-hook-refresh]", loadHook);
  }
  on(root, ctx, "[data-delivery-form]", async () => {
    const fields = formData(root, "[data-delivery-form]");
    await saveConfig(ctx, {
      delivery: {
        notify_mode: fields.get("notify_mode") || "summary",
        send_mode: fields.get("send_mode"),
        max_file_mb: Number(fields.get("max_file_mb")),
        max_images: Number(fields.get("max_images")),
        include_captions: fields.has("include_captions"),
      },
      export: {
        format: fields.get("format"), layout_id: fields.get("layout_id"), image_profile: fields.get("image_profile"),
      },
    });
    ctx.toast("默认交付方式已保存。", "success");
  }, "submit");
  on(root, ctx, "[data-webhook-form]", async () => {
    const fields = formData(root, "[data-webhook-form]");
    await saveConfig(ctx, {
      webhook: {
        enabled: fields.has("enabled"), public_url: fields.get("public_url"),
        bind_host: fields.get("bind_host"), bind_port: Number(fields.get("bind_port")),
      },
    });
    await loadHook();
    ctx.toast("回调监听设置已保存。", "success");
  }, "submit");
  renderHook();
  renderRooms(rows(obj(rawSessions).items));
}

const QUOTAS = [
  ["global_limit", "全局并发", 2, 1, 50, "所有实例共用，超出后任务排队。"],
  ["user_active_limit", "单人未完成任务", 2, 1, 50, "含等待、提交和执行中的任务。"],
  ["daily_limit", "单人每日任务", 10, 0, 10000, "按服务器日期计算；0 为不限。"],
  ["cooldown_seconds", "提交冷却（秒）", 5, 0, 86400, "同一人在两次出图请求间的最短等待。"],
  ["cloud_daily_limit", "单人每日云端任务", 3, 0, 10000, "云端任务也计入普通日额度；0 为不限。"],
] as const;
const ACTIONS: Record<string, string> = {
  workshop: "工坊装配", draw: "灵感出漫", trial: "单图试画",
  redraw: "重画分幕", qa: "分幕质检", adopt_take: "采纳候选",
  cancel: "停止任务", pause: "暂停任务", resume: "继续任务", remove: "移除记录",
  webhook_register: "注册回调", instance_default: "切换默认实例",
  instance_save: "保存实例", instance_remove: "移除实例",
};

function instanceList(instances: Row[], defaultId: unknown) {
  if (!instances.length) return empty("尚未接入实例。请从主实例或新增实例开始。");
  return `<div class="mng-instances">${instances.map((instance, n) => {
    const isDefault = instance.id === defaultId;
    return `<article class="mng-instance">
      <div class="mng-head"><h3>${e(title(instance))}</h3>${isDefault ? '<span class="mng-status" data-ok="true">默认实例</span>' : ""}</div>
      <p class="mng-sub mng-wrap">${e(instance.base_url || "尚未填写地址")}</p>
      <p class="mng-small">${instance.cloud ? "云端配额" : "本地配额"} · ${instance.has_token ? "令牌已设置" : "尚未设置令牌"}</p>
      <div class="mng-actions">
        ${button("测试连接", `data-inst-probe="${n}"`)}
        ${button("编辑", `data-inst-edit="${n}"`)}
        ${!isDefault ? button("设为默认", `data-inst-default="${n}"`) : ""}
      </div>
    </article>`;
  }).join("")}</div>`;
}

function probeMessage(response: unknown): readonly [string, boolean] {
  const payload = obj(response);
  const status = obj(payload.status || payload);
  if (status.ok !== true) return [str(status.error || status.message, "无法验证连接，请确认地址和令牌。"), false];
  const scopes = Array.isArray(status.scopes) ? status.scopes.join(" · ") : "";
  return [[`连接成功${status.api ? ` · API ${str(status.api)}` : ""}`, scopes].filter(Boolean).join(" · "), true];
}

function editInstance(ctx: ViewContext, instance: Row | undefined, reload: () => Promise<void>) {
  const current = instance || {};
  const identifier = id(current);
  const drawer = open(ctx, instance ? "编辑实例" : "新增 Mio 实例", instance ? title(current) : "INSTANCE SETUP",
    `<form class="mng-form" data-inst-form>
      ${field("实例 ID", input("id", identifier, `required maxlength="48" pattern="[a-z0-9][a-z0-9_\\-]{0,47}" ${identifier ? "readonly" : 'placeholder="backup"'}`), "用小写字母、数字、下划线或连字符，可在聊天命令中指定。")}
      ${field("显示名称", input("label", str(current.label || current.title), `required maxlength="80" ${identifier === "primary" ? "readonly" : 'placeholder="例如 云端绘页"'}`))}
      ${field("Mio 服务地址", input("base_url", current.base_url, 'type="url" required placeholder="http://127.0.0.1:8788" maxlength="1800"'), "填写服务根地址。远程服务请使用 HTTPS。")}
      ${field("开放 API 令牌", input("token", "", 'type="password" autocomplete="new-password" placeholder="mio_…" maxlength="200"'), current.has_token ? "地址不变时留空即可保留原令牌。" : "在 Mio 中创建并复制完整令牌。此处不会显示已保存的原文。")}
      ${field("超时（秒）", input("timeout", current.timeout ?? 60, 'type="number" min="5" max="600" required'))}
      ${check("cloud", "按云端配额计费", current.cloud === true)}
      <div class="mng-actions">
        <button type="submit" class="mng-btn primary">保存实例</button>
        ${button("测试草稿", "data-probe-draft")}
        ${identifier && identifier !== "primary" ? button("移除实例", "data-remove-draft", "danger") : ""}
      </div>
      <div data-inst-feedback role="status"></div>
    </form>`);
  const form = q<HTMLFormElement>(drawer, "[data-inst-form]")!;
  const feedback = q(drawer, "[data-inst-feedback]")!;
  function readDraft() {
    if (!form.reportValidity()) return null;
    const data = new FormData(form);
    const instanceId = str(data.get("id")).trim();
    const url = str(data.get("base_url")).trim();
    if (!/^[a-z0-9][a-z0-9_-]{0,47}$/.test(instanceId)) throw new Error("实例 ID 仅支持小写字母、数字、下划线和连字符。");
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      throw new Error("请输入完整的 Mio 服务地址。");
    }
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash)
      throw new Error("服务地址须为 HTTP(S)，不可含账号、查询或片段。");
    const token = str(data.get("token")).trim();
    if (token && !/^mio_tok_[A-Za-z0-9]+_[A-Za-z0-9_-]{20,150}$/.test(token)) {
      throw new Error("请从 Mio 复制完整的开放 API 令牌。");
    }
    return {
      id: instanceId, label: str(data.get("label")).trim(),
      base_url: url, ...(token ? { token } : {}), timeout: Number(data.get("timeout")), cloud: data.has("cloud"),
    };
  }
  on(drawer, ctx, "[data-inst-form]", async () => {
    const draft = readDraft();
    if (!draft) return;
    await ctx.api.post("instances", { action: "save", ...draft });
    ctx.closeDrawer();
    ctx.toast("实例已保存。", "success");
    await reload();
  }, "submit");
  on(drawer, ctx, "[data-probe-draft]", async () => {
    const draft = readDraft();
    if (!draft) return;
    const [message, ok] = probeMessage(await ctx.api.post("instances", { action: "probe", ...draft }));
    feedback.innerHTML = `<p class="mng-notice">${e(message)}</p>`;
    ctx.toast(message, ok ? "success" : "error");
  });
  on(drawer, ctx, "[data-remove-draft]", async () => {
    const removal = q<HTMLButtonElement>(drawer, "[data-remove-draft]")!;
    if (removal.dataset.confirm !== "yes") {
      removal.dataset.confirm = "yes";
      removal.textContent = "确认移除";
      feedback.innerHTML = `<p class="mng-notice">移除此连接后，已有作品仍保留在 Mio。再次点击确认。</p>`;
      return;
    }
    await ctx.api.post("instances", { action: "remove", id: identifier });
    ctx.closeDrawer();
    ctx.toast("实例已移除。", "success");
    await reload();
  });
}

function auditLog(payload: Row) {
  const entries = rows(payload.items);
  const quota = obj(payload.quota);
  const cap = typeof quota.used === "number"
    ? `<p class="mng-small">当前面板会话今日使用 ${e(quota.used)} 次，其中云端 ${e(quota.cloud_used ?? 0)} 次。</p>` : "";
  return cap + (entries.length
    ? `<table class="mng-table"><thead><tr><th>操作</th><th class="mng-optional">实例</th><th>结果</th><th>时间</th></tr></thead><tbody>
      ${entries.slice(0, 30).map(row => `<tr><td>${e(ACTIONS[str(row.action)] || row.action)}</td>
        <td class="mng-optional">${e(row.instance_id || "—")}</td><td>${e(row.outcome === "ok" ? "成功" : row.outcome || "—")}</td><td>${e(dateTime(row.created_at || row.at))}</td></tr>`).join("")}
      </tbody></table>`
    : empty("还没有审计记录。实例操作或新任务提交后会出现。"));
}

export async function renderGovernance(ctx: ViewContext) {
  const getInstances = async () => obj(await ctx.api.get("instances").catch(() => ctx.api.get("overview")));
  const [config, initial, audit] = await Promise.all([
    readConfig(ctx), getInstances(),
    ctx.api.get("audit").catch((error: unknown) => ({ loadError: error instanceof Error ? error.message : "无法读取审计日志。" })),
  ]);
  const settings = obj(config.governance);
  const permissions = obj(config.permissions);
  const chat = obj(config.chat);
  mount(ctx, heading("07 / GOVERNANCE", "权限、配额与多实例", "管理服务路由、创作资源与日常使用规则。"),
    `<section class="mng-section">
      ${sectionHead("MIO ENDPOINTS", "实例管理", "默认实例可随时切换。任务始终保留创建时使用的实例。", button("新增实例", "data-new-inst", "primary"))}
      <div data-inst-grid></div>
      <div data-inst-status class="mng-log" role="status"></div>
    </section>
    <section class="mng-section">
      ${sectionHead("ACCESS CONTROL", "角色与会话范围", "管理员管理面板；创作者可生成，访客可查阅和取件。")}
      <form data-permissions-form class="mng-form">
        ${check("admin_only", "只允许管理员使用", permissions.admin_only === true)}
        <div class="mng-form-grid">
          ${field("允许会话", textArea("allowed_sessions", permissions.allowed_sessions, "适配器:GroupMessage"), "填完整 UMO 或平台、群聊前缀。每行一项，留空允许所有会话。")}
          ${field("创作者名单", textArea("creator_ids", permissions.creator_ids, "适配器:用户 ID"), "名单为空时，授权会话内的成员可以创作。")}
          ${field("只读访客", textArea("viewer_ids", permissions.viewer_ids, "用户 ID"), "同时出现在创作者与访客名单时，普通用户按访客处理。")}
          ${field("云端白名单", textArea("cloud_allowed_ids", permissions.cloud_allowed_ids, "适配器:用户 ID"), "仅列出的普通用户可用付费云端通道。")}
        </div>
        ${formBtn("保存角色与范围")}
      </form>
    </section>
    <section class="mng-section">
      ${sectionHead("RESOURCE LIMITS", "运行额度", "控制同时运行的任务与使用频率。")}
      <form data-quota-form class="mng-form">
        <div class="mng-form-grid">
          ${QUOTAS.map(([key, label, fallback, min, max, help]) =>
            field(label, input(key, settings[key] ?? fallback, `type="number" min="${min}" max="${max}" required`), help)).join("")}
          ${field("限制关键词", textArea("blocked_keywords", settings.blocked_keywords, "每行一个关键词"), "灵感或提示词命中时不提交。")}
        </div>
        ${formBtn("保存额度")}
      </form>
    </section>
    <section class="mng-section">
      ${sectionHead("CHAT & ASSISTANT", "聊天与 AI 创作", "配置对话工具和聊天卡片。")}
      <form data-chat-form class="mng-form">
        ${check("llm_tools_enabled", "开放 AI 对话创作工具", chat.llm_tools_enabled === true)}
        ${field("卡片渲染", select("render_mode", [["auto", "自动"], ["image", "图片优先"], ["text", "纯文本"]], chat.render_mode || "auto"), "没有 Pillow 或可用中文字体时，自动使用纯文本。")}
        ${formBtn("保存聊天设置")}
      </form>
    </section>
    <section class="mng-section">
      ${sectionHead("ACTIVITY LOG", "近期审计记录", "查看近期的创作和管理操作。", button("刷新日志", "data-audit-refresh"))}
      <div data-audit-log aria-live="polite"></div>
    </section>`);
  const root = ctx.mount;
  const grid = q(root, "[data-inst-grid]")!;
  const status = q(root, "[data-inst-status]")!;
  const log = q(root, "[data-audit-log]")!;
  const onSave = async () => {
    renderInstances(await getInstances());
    // Refresh the shell selector after the persisted topology changes.
    await ctx.refresh();
  };
  function renderInstances(payload: Row) {
    const instances = rows(payload.instances || payload.items);
    grid.innerHTML = instanceList(instances, payload.default_id);
    grid.querySelectorAll<HTMLButtonElement>("[data-inst-probe]").forEach(btn => btn.addEventListener("click",
      safe(ctx, btn, async () => {
        const selected = instances[Number(btn.dataset.instProbe)];
        if (!selected) return;
        const [message, ok] = probeMessage(await ctx.api.post("instances", { action: "probe", id: selected.id }));
        status.innerHTML = `<p class="mng-notice">${e(title(selected))}：${e(message)}</p>`;
        ctx.toast(message, ok ? "success" : "error");
      }),
    ));
    grid.querySelectorAll<HTMLButtonElement>("[data-inst-edit]").forEach(btn => btn.addEventListener("click", () => {
      const selected = instances[Number(btn.dataset.instEdit)];
      if (selected) editInstance(ctx, selected, onSave);
    }));
    grid.querySelectorAll<HTMLButtonElement>("[data-inst-default]").forEach(btn => btn.addEventListener("click",
      safe(ctx, btn, async () => {
        const selected = instances[Number(btn.dataset.instDefault)];
        if (!selected) return;
        await ctx.api.post("instances", { action: "default", id: selected.id });
        ctx.toast(`默认实例已设为 ${title(selected)}。`, "success");
        await onSave();
      }),
    ));
  }
  on(root, ctx, "[data-new-inst]", async () => { editInstance(ctx, undefined, onSave); });
  const asList = (value: FormDataEntryValue | null) => str(value).split(/[\n,，]/).map(entry => entry.trim()).filter(Boolean);
  on(root, ctx, "[data-permissions-form]", async () => {
    const data = formData(root, "[data-permissions-form]");
    const limits = ["allowed_sessions", "creator_ids", "viewer_ids", "cloud_allowed_ids"];
    await saveConfig(ctx, { permissions: { admin_only: data.has("admin_only"), ...Object.fromEntries(limits.map(key => [key, asList(data.get(key))])) } });
    ctx.toast("角色与会话范围已保存。", "success");
  }, "submit");
  on(root, ctx, "[data-quota-form]", async () => {
    const data = formData(root, "[data-quota-form]");
    const governance = Object.fromEntries(QUOTAS.map(([key]) => [key, Number(data.get(key))]));
    await saveConfig(ctx, { governance: { ...governance, blocked_keywords: asList(data.get("blocked_keywords")) } });
    ctx.toast("运行额度已保存。", "success");
  }, "submit");
  on(root, ctx, "[data-chat-form]", async () => {
    const data = formData(root, "[data-chat-form]");
    await saveConfig(ctx, { chat: { render_mode: data.get("render_mode"), llm_tools_enabled: data.has("llm_tools_enabled") } });
    ctx.toast("聊天设置已保存。", "success");
  }, "submit");
  on(root, ctx, "[data-audit-refresh]", async () => {
    log.innerHTML = auditLog(obj(await ctx.api.get("audit")));
    ctx.toast("审计日志已刷新。", "success");
  });
  renderInstances(initial);
  const auditData = obj(audit);
  log.innerHTML = auditData.loadError ? empty(str(auditData.loadError)) : auditLog(auditData);
}

const COMMANDS = [
  ["/mio ls [wf|sb|ps|ch|tpl|kit]", "查看工作流、分镜、预设、通道或模板编号。", "List numbered workflows, boards, presets, channels or templates."],
  ["/mio use 1 2 3 [通道]", "以选定的工作流、分镜和预设装配；0 使用默认。", "Assemble with a workflow, storyboard and presets; 0 uses the default."],
  ["/mio preview 1 2 3", "预览装配文本，检查缺失变量。", "Preview the assembled script and missing variables."],
  ["/mio draw <灵感> [--style 1] [--tpl 1] [--candidates 2]", "一句话出漫；在群里也可用 /mio 画。", "Create a comic from a short idea; supports a style, template and candidate count."],
  ["/mio trial <提示词> [工作流]", "试画单张图；别名 /mio 试画。", "Generate one test image with an optional workflow."],
  ["/mio wizard", "三步创作向导；回复“取消”退出。", "Start the three-step wizard; reply 取消 to exit."],
  ["/mio jobs", "查看当前会话中自己的任务编号。", "List your tasks in this conversation."],
  ["/mio status [任务#]", "查看任务进度或队列位置。", "Check progress and queue position."],
  ["/mio redraw <任务#> <分幕#> [提示词]", "重画一幕，可重新填写提示词。", "Redo one panel with an optional replacement prompt."],
  ["/mio pick <任务#> <分幕#> <候选#>", "采纳指定分幕候选。", "Adopt a candidate for a panel."],
  ["/mio qa <任务#>", "触发分幕质检，结束后通知结果。", "Run quality checks and get a completion message."],
  ["/mio pause|resume|cancel|rm <任务#>", "暂停、继续、停止或移除自己的任务。", "Pause, resume, cancel or remove your task."],
  ["/mio get [任务#] [格式] [模板]", "取回任务成品，支持 html、pdf、zip、strip、slices、motion、img。", "Retrieve a task output as HTML, PDF, ZIP, strip, slices, motion or images."],
  ["/mio works [关键词]", "搜索历史作品；别名 /mio 作品。", "Search previously created works."],
  ["/mio export <作品#> [格式] [模板]", "取回作品目录中的成品。", "Export a work from the gallery."],
  ["/mio quota", "查看今日额度、冷却与队列。", "Check today's limits, cooldown and queue length."],
  ["/mio mode <方式>", "群主或管理员调整当前会话的交付策略。", "Set delivery mode for the current conversation if you manage it."],
  ["/mio ping · /mio help", "检查 Mio 连接；打开命令帮助。", "Check Mio connection; open command help."],
] as const;

async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(text); return; } catch { /* iframe may disallow Clipboard */ }
  }
  const previous = document.activeElement;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.className = "mng-visually-hidden";
  document.body.append(area);
  try {
    area.select();
    if (!document.execCommand("copy")) throw new Error("无法自动复制，请选中命令后手动复制。");
  } finally {
    area.remove();
    if (previous instanceof HTMLElement) previous.focus();
  }
}

function referenceTable(language: "zh" | "en") {
  return `<dl class="mng-reference">${COMMANDS.map(([command, zh, en]) =>
    `<dt>${e(command)}</dt><dd>${e(language === "zh" ? zh : en)}</dd>`).join("")}</dl>`;
}

function numbered(items: Row[], fallback: string): Choice[] {
  return [["0", fallback], ...items.map((row, i): Choice => [String(i + 1), `${i + 1}. ${title(row)}`])];
}

export async function renderCommands(ctx: ViewContext) {
  const loaded = obj(await ctx.api.get("catalog", scope(ctx)).catch(() => ({})));
  const workflows = rows(loaded.workflows);
  const storyboards = rows(loaded.storyboards);
  const presets = rows(loaded.presets);
  const channels = rows(loaded.channels).filter(row => id(row) !== "");
  const templates = rows(loaded.layouts);
  mount(ctx, heading("08 / COMMAND BOOK", "指令手册与构建器", "把面板中的选择转成可以直接粘贴到 AstrBot 会话的命令。"),
    `<section class="mng-section">
      ${sectionHead("COMMAND COMPOSER", "构建聊天指令", "下拉列表使用当前 Mio 实例的实际资源编号。")}
      <form class="mng-form" data-command-form>
        <div class="mng-tabs" role="group" aria-label="指令类型">
          <button class="mng-tab" type="button" data-command="use" aria-pressed="true">工坊装配 · /mio use</button>
          <button class="mng-tab" type="button" data-command="get" aria-pressed="false">任务取件 · /mio get</button>
        </div>
        <div data-command-fields></div>
        <label class="mng-field">可复制命令<input class="mng-control mng-code" data-command-output readonly type="text" spellcheck="false" /></label>
        ${!workflows.length && !storyboards.length ? '<p class="mng-notice">当前实例尚未返回创作资源，装配会使用默认值。可在实例管理中检查连接。</p>' : ""}
        <div class="mng-actions">${button("复制命令", "data-command-copy", "primary")}</div>
      </form>
    </section>
    <section class="mng-section">
      ${sectionHead("REFERENCE", "完整指令速查", "在支持的命令后追加 --instance 实例ID，可临时路由到另一实例。",
      '<div class="mng-tabs" role="group" aria-label="手册语言"><button type="button" class="mng-tab" aria-pressed="true" data-language="zh">简体中文</button><button type="button" class="mng-tab" aria-pressed="false" data-language="en">English</button></div>')}
      <div data-command-reference>${referenceTable("zh")}</div>
    </section>`);
  const root = ctx.mount;
  const form = q<HTMLFormElement>(root, "[data-command-form]")!;
  const controls = q(root, "[data-command-fields]")!;
  const result = q<HTMLInputElement>(root, "[data-command-output]")!;
  let kind = "use";
  function update() {
    const data = new FormData(form);
    let command: string;
    if (kind === "get") {
      const task = str(data.get("task")).trim();
      if (task && !/^[0-9]{1,12}$/.test(task)) {
        result.value = "";
        return;
      }
      const format = str(data.get("format"), "html");
      const layout = str(data.get("layout"));
      command = ["/mio get", task, format, layout === "0" ? "" : layout].filter(Boolean).join(" ");
    } else {
      const channel = str(data.get("channel"));
      command = ["/mio use", str(data.get("workflow"), "0"), str(data.get("storyboard"), "0"), str(data.get("preset"), "0"), channel === "0" ? "" : channel].filter(Boolean).join(" ");
    }
    if (kind === "use" && ctx.instance && ctx.instance !== "primary")
      command += ` --instance ${ctx.instance}`;
    result.value = command;
  }
  function switchFields() {
    const chOffset = rows(loaded.channels).length !== channels.length;
    const channelNumbers = chOffset
      ? [["0", "沿用默认"], ...rows(loaded.channels).map((item, i): Choice => [String(i + 1), `${i + 1}. ${title(item)}`]).filter((_, i) => id(rows(loaded.channels)[i]) !== "")]
      : numbered(channels, "沿用默认");
    controls.innerHTML = `<div class="mng-form-grid">${kind === "get"
      ? field("任务编号", input("task", "", 'type="text" inputmode="numeric" pattern="[0-9]{1,12}" placeholder="最近任务"'), "留空时取最近任务。")
        + field("导出格式", select("format", [...FORMATS, ["img", "逐幕图"]], "html"))
        + field("模板", select("layout", numbered(templates, "使用默认模板"), "0"))
      : field("工作流", select("workflow", numbered(workflows, "使用默认工作流"), "0"))
        + field("分镜", select("storyboard", numbered(storyboards, "使用默认分镜"), storyboards.length ? "1" : "0"))
        + field("预设", select("preset", numbered(presets, "使用默认预设"), "0"))
        + field("图像通道", select("channel", channelNumbers as Choice[], "0"))
      }</div>`;
    update();
  }
  root.querySelectorAll<HTMLButtonElement>("[data-command]").forEach(btn => btn.addEventListener("click", () => {
    kind = btn.dataset.command || "use";
    root.querySelectorAll("[data-command]").forEach(tab => tab.setAttribute("aria-pressed", String(tab === btn)));
    switchFields();
  }));
  root.querySelectorAll<HTMLButtonElement>("[data-language]").forEach(btn => btn.addEventListener("click", () => {
    const focus = btn.dataset.language === "en" ? "en" : "zh";
    root.querySelectorAll("[data-language]").forEach(tab => tab.setAttribute("aria-pressed", String(tab === btn)));
    q(root, "[data-command-reference]")!.innerHTML = referenceTable(focus);
  }));
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  form.addEventListener("submit", ev => ev.preventDefault());
  on(root, ctx, "[data-command-copy]", async () => {
    if (!result.value) throw new Error("请输入数字任务编号，或留空取最近任务。");
    await copyText(result.value);
    ctx.toast("命令已复制。", "success");
  });
  switchFields();
}
