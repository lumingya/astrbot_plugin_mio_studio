import { empty, esc, heading, icon, options, tip } from "./ui";
import type { DrawerOptions, Params, ViewContext } from "./ui";
import "./creative.css";

interface Choice { id: string; title: string; category?: string; entries?: Array<{ key: string; value: string }>; frameCount?: number; }
interface Catalog { workflows: Choice[]; storyboards: Choice[]; presets: Choice[]; channels: Choice[]; layouts: Choice[]; defaults?: Record<string, string | string[]>; }
interface Work extends Choice { episode_id?: string; series_id?: string; series_title?: string; adopted_count?: number; description?: string; cover_src?: string; cover_asset_id?: string; }
interface Shelf extends Choice { episode_count?: number; }
interface Bookcase { items: Work[]; series?: Shelf[]; total?: number; }
interface Task {
  id: string; num: number; title: string; statusKey: string; status: string; kind?: string; source?: string;
  sender_name?: string; instance_id?: string; episode_id?: string; error?: string; done: number;
  failed?: number; total: number; queuePosition?: number; started_at?: number; created_at?: number; finished_at?: number;
  selection?: { workflowTitle?: string; storyboardTitle?: string; presetTitles?: string[]; channelTitle?: string };
}
interface Take { id: string; status: string; score?: number | null; number?: number; qa?: { score?: number; passed?: boolean; issues?: string[] }; }
interface Frame { id: string; number: number; title?: string; description?: string; status?: string; error?: string; takes?: Take[]; }
interface TaskDetail { task: Task & { panels_error?: string; panels?: Frame[] }; panels?: Frame[]; }
interface WorkDetail { work: Work; panels?: Frame[]; }
interface PreviewFrame { title?: string; order?: number; positive?: string; negative?: string; dialogues?: string[]; variables?: { key: string; status: string; value?: string }[]; missing_variables?: string[]; }
interface Preview { title?: string; panels?: PreviewFrame[]; }
interface Lora { key: string; name: string; strength: number | null; enabled: boolean; }
interface LoraGroup { key: string; kind?: string; active?: boolean; supports_independent_toggle?: boolean; reason?: string; loras: Lora[]; }
interface Session extends Choice { umo: string; sender?: string; platform?: string; }
type TaskReply = { task: Task; message?: string; src?: string };

const statuses: Record<string, string> = {
  ready: "等候中", queued: "等候中", queued_remote: "等候出图", running: "生成中", preparing: "准备中",
  complete: "已完成", failed: "失败", paused: "已暂停", cancelled: "已停止", canceled: "已停止",
  interrupted: "待核对", standby: "待开画", archived: "旧版记录", missing: "已移除",
  adopted: "已采用", candidate: "候选图", rejected: "已淘汰", pending: "待出图",
};
const typeLabels: Record<string, string> = {
  draw: "灵感直出", workshop: "工坊装配", trial: "单张试画", redraw: "单幕重画", qa: "质量检查", v1: "旧版任务",
};
const active = new Set(["ready", "queued", "queued_remote", "running", "preparing"]);
const formats: Choice[] = [
  { id: "html", title: "HTML · 离线画册" }, { id: "pdf", title: "PDF · 打印与阅读" },
  { id: "zip", title: "ZIP · 作品归档" }, { id: "strip", title: "PNG · 整条长漫" },
  { id: "slices", title: "ZIP · 发布切图" }, { id: "motion", title: "动态漫 · 离线 HTML" },
];
const emptyCatalog: Catalog = { workflows: [], storyboards: [], presets: [], channels: [], layouts: [] };
const txt = (node: ParentNode, selector: string): string =>
  node.querySelector<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(selector)?.value ?? "";
const checked = (node: ParentNode, selector: string): boolean =>
  node.querySelector<HTMLInputElement>(selector)?.checked ?? false;
const elem = <T extends HTMLElement = HTMLElement>(node: ParentNode, selector: string): T =>
  node.querySelector<T>(selector)!;
const key = encodeURIComponent;
const asError = (error: unknown) => error instanceof Error ? error.message : "没能完成操作，请稍后重试。";
const imageSrc = (value: unknown): string =>
  typeof value === "string" && /^data:image\/(?:png|jpe?g|webp|gif|svg\+xml)(?:;[a-z0-9=+-]+)*,/i.test(value) ? value : "";
const note = (kicker: string, title: string, description = "") =>
  `<div class="cv-note"><p class="eyebrow">${esc(kicker)}</p><h2>${esc(title)}</h2>${description ? `<p>${esc(description)}</p>` : ""}</div>`;
const label = (name: string, control: string, help = "") =>
  `<label class="cv-field"><span>${esc(name)}${help ? tip(help) : ""}</span>${control}</label>`;
const button = (text: string, attrs: string, cls = "btn", glyph = "") =>
  `<button type="button" class="${cls}" ${attrs}>${glyph ? icon(glyph, 15) : ""}${esc(text)}</button>`;
const stateName = (state: string) => statuses[state] ?? "未判定";
const stateBadge = (state: string) =>
  `<span class="cv-state${active.has(state) ? " cv-state-active" : state === "failed" || state === "interrupted" ? " cv-state-error" : ""}"><i></i>${esc(stateName(state))}</span>`;
const lower = (text: unknown) => String(text ?? "").normalize("NFKC").toLocaleLowerCase();
const keep = (selector: string, handler: (el: HTMLButtonElement) => void, root: ParentNode) =>
  root.querySelectorAll<HTMLButtonElement>(selector).forEach(el => el.addEventListener("click", () => handler(el)));

function scope(ctx: ViewContext, extra: Params = {}): Params {
  return { instance_id: ctx.instance, ...extra };
}

function present(ctx: ViewContext, options: DrawerOptions): HTMLElement {
  const body = document.createElement("div");
  body.className = "cv-drawer";
  if (typeof options.content === "string") body.innerHTML = options.content;
  else body.append(options.content);
  ctx.drawer({ ...options, content: body });
  return body;
}

const showing = (element: HTMLElement): boolean =>
  element.isConnected && (element.closest("dialog")?.open ?? true);

async function action<T>(
  ctx: ViewContext, control: HTMLButtonElement | undefined, work: () => Promise<T>, success?: (value: T) => void,
): Promise<void> {
  if (control?.disabled) return;
  if (control) { control.disabled = true; control.setAttribute("aria-busy", "true"); }
  try { success?.(await work()); }
  catch (error) { ctx.toast(asError(error), "error"); }
  finally { if (control) { control.disabled = false; control.removeAttribute("aria-busy"); } }
}

function bindForm(
  root: ParentNode, selector: string, fn: (form: HTMLFormElement, submit: HTMLButtonElement | undefined) => void,
): void {
  const form = elem<HTMLFormElement>(root, selector);
  form.addEventListener("submit", event => {
    event.preventDefault();
    fn(form, event.submitter instanceof HTMLButtonElement ? event.submitter : undefined);
  });
}

function readNumber(value: string, fallback: number): number {
  const number = Number(value);
  return value && Number.isFinite(number) ? number : fallback;
}

function accepted(ctx: ViewContext, out: TaskReply): void {
  if (out.src && imageSrc(out.src)) ctx.lightbox(out.src, out.task.title);
  ctx.toast(out.message || `任务 #${out.task.num} 已受理`, "success");
}

function previewMarkup(value: Preview): string {
  const frames = value.panels ?? [];
  if (!frames.length) return empty("还没有可预览的分幕", "请确认分镜内至少有一幕。");
  return `<div class="cv-preview-caption"><strong>${esc(value.title || "装配预览")}</strong><span>${frames.length} 幕 · 未占用出图队列</span></div>
    <div class="cv-tabs cv-frame-tabs" role="tablist" aria-label="预览各幕">${frames.map((frame, i) => `<button type="button" role="tab" data-preview-frame="${i}" aria-selected="${i === 0}" aria-controls="cv-preview-${i}">${String(i + 1).padStart(2, "0")} <span>${esc(frame.title || `第 ${i + 1} 幕`)}</span></button>`).join("")}</div>
    ${frames.map((frame, i) => {
      const missing = Array.from(new Set([
        ...(frame.missing_variables ?? []),
        ...(frame.variables ?? []).filter(value => value.status !== "resolved" && value.status !== "empty").map(value => value.key),
      ]));
      return `<div id="cv-preview-${i}" class="cv-preview-frame" role="tabpanel" ${i ? "hidden" : ""}>
        <div class="cv-preview-meta">${missing.length
          ? `<strong>${icon("warning", 14)} 缺少或无法展开</strong> ${missing.map(value => `<mark class="cv-variable">{${esc(value)}}</mark>`).join(" ")}`
          : `<strong>${icon("check", 14)} 变量已就绪</strong>`}</div>
        <dl><dt>正向提示词</dt><dd>${esc(frame.positive || "—")}</dd>
          ${frame.negative ? `<dt>负向提示词</dt><dd>${esc(frame.negative)}</dd>` : ""}
          ${frame.dialogues?.length ? `<dt>对白</dt><dd>${frame.dialogues.map(esc).join("<br>")}</dd>` : ""}
        </dl></div>`;
    }).join("")}`;
}

function showPreview(box: HTMLElement, value: Preview): void {
  box.innerHTML = previewMarkup(value);
  const tabs = Array.from(box.querySelectorAll<HTMLButtonElement>("[data-preview-frame]"));
  function choose(index: number) {
    tabs.forEach((tab, i) => {
      tab.setAttribute("aria-selected", String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
      elem(box, `#cv-preview-${i}`).hidden = i !== index;
    });
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => choose(i));
    tab.addEventListener("keydown", event => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const index = (i + tabs.length + (event.key === "ArrowLeft" ? -1 : 1)) % tabs.length;
      choose(index); tabs[index].focus();
    });
  });
  choose(0);
}

function presetPicker(box: HTMLElement, presets: Choice[], selected: Set<string>, changed: () => void): void {
  const search = elem<HTMLInputElement>(box, "#cv-preset-search");
  const list = elem(box, "[data-preset-list]");
  const count = elem(box, "[data-preset-count]");
  function draw() {
    const term = lower(search.value.trim());
    count.textContent = `已选 ${selected.size}`;
    const visible = presets.filter(preset =>
      !term || lower([preset.title, preset.id, ...(preset.entries ?? []).flatMap(entry => [entry.key, entry.value])].join(" ")).includes(term),
    );
    list.innerHTML = visible.length ? ["characters", "scenes"].map(category => {
      const group = visible.filter(preset => (preset.category === "characters" || preset.category === "character" ? "characters" : "scenes") === category);
      if (!group.length) return "";
      return `<details class="cv-preset-group" open><summary>${category === "characters" ? "角色" : "场景"} <span>${group.length}</span></summary>
        ${group.map(preset => `<div class="cv-preset-row"><label><input type="checkbox" value="${esc(preset.id)}" ${selected.has(preset.id) ? "checked" : ""}><span>${esc(preset.title)}</span></label>
          <details class="cv-variables"><summary title="${esc((preset.entries ?? []).slice(0, 5).map(entry => `${entry.key}：${entry.value}`).join("\n") || "预览变量")}" aria-label="预览 ${esc(preset.title)} 的变量">变量</summary>
            <dl>${(preset.entries ?? []).length ? preset.entries!.slice(0, 30).map(entry => `<dt>${esc(entry.key)}</dt><dd>${esc(entry.value)}</dd>`).join("") : "<dd>没有可展示的变量。</dd>"}</dl></details></div>`).join("")}</details>`;
    }).join("") : empty("未找到匹配的预设", "可尝试角色名、场景名或变量。");
    list.querySelectorAll<HTMLInputElement>("input[type=checkbox]").forEach(input => input.addEventListener("change", () => {
      if (!input.checked) selected.delete(input.value);
      else if (selected.size < 128) selected.add(input.value);
      else input.checked = false;
      count.textContent = `已选 ${selected.size}`;
      changed();
    }));
    list.querySelectorAll<HTMLDetailsElement>(".cv-variables").forEach(detail => detail.addEventListener("toggle", () => {
      if (detail.open) list.querySelectorAll<HTMLDetailsElement>(".cv-variables[open]").forEach(other => { if (other !== detail) other.open = false; });
    }));
  }
  search.addEventListener("input", draw);
  draw();
}

function loraMarkup(groups: LoraGroup[]): string {
  if (!groups.length) return "<p class=\"cv-subtle\">此工作流没有可单独调整的 LoRA 槽位。</p>";
  return groups.map((group, i) => `<div class="cv-lora-group"><p>${esc(group.kind || `槽位组 ${i + 1}`)} <small>${esc(group.reason || "")}</small></p>
    ${(group.loras ?? []).map((lora, j) => `<div class="cv-lora-row">
      <label title="${esc(lora.name)}"><input type="checkbox" data-lora-enable="${i}-${j}" ${lora.enabled ? "checked" : ""} ${group.supports_independent_toggle === false ? "disabled" : ""}> <span>${esc(lora.name)}</span></label>
      <label class="cv-weight"><span>权重</span><input type="number" inputmode="decimal" min="-100" max="100" step="any" data-lora-weight="${i}-${j}" value="${esc(lora.strength ?? "")}" aria-label="${esc(lora.name)} 权重"></label>
    </div>`).join("")}</div>`).join("");
}

function setLoraControls(form: HTMLFormElement, ctx: ViewContext, changed: () => void): () => object | undefined {
  const box = elem(form, "[data-lora-body]");
  const drawers = elem<HTMLDetailsElement>(form, "[data-lora]");
  const workflow = elem<HTMLSelectElement>(form, "#cv-workflow");
  let groups: LoraGroup[] = [];
  let version = 0;
  const load = async () => {
    const current = ++version;
    groups = [];
    const selected = workflow.value;
    if (!selected) { box.innerHTML = '<p class="cv-subtle">请先选择一个工作流。</p>'; return; }
    box.innerHTML = '<p class="cv-subtle">正在读取槽位…</p>';
    try {
      const result = await ctx.api.get<{ loras?: { groups?: LoraGroup[] }; groups?: LoraGroup[]; item?: { loras?: { groups?: LoraGroup[] } } }>(
        `resources/workflows/${key(selected)}`, scope(ctx),
      );
      if (current !== version) return;
      groups = result.loras?.groups ?? result.item?.loras?.groups ?? result.groups ?? [];
      box.innerHTML = loraMarkup(groups);
    } catch (error) {
      if (current === version) box.textContent = asError(error);
    }
  };
  drawers.addEventListener("toggle", () => { if (drawers.open && !groups.length) void load(); });
  workflow.addEventListener("change", () => { ++version; groups = []; if (drawers.open) void load(); });
  box.addEventListener("input", changed);
  return () => {
    const overrides = groups.flatMap((group, i) => {
      const loras = group.loras.flatMap((lora, j) => {
        const enable = elem<HTMLInputElement>(box, `[data-lora-enable="${i}-${j}"]`);
        const number = txt(box, `[data-lora-weight="${i}-${j}"]`);
        const value = number ? readNumber(number, lora.strength ?? 1) : lora.strength;
        if ((!enable.disabled && enable.checked !== lora.enabled) || value !== lora.strength) {
          return [{
            key: lora.key, name: lora.name,
            ...(value !== lora.strength ? { strength: value } : {}),
            ...(!enable.disabled && enable.checked !== lora.enabled ? { enabled: enable.checked } : {}),
          }];
        }
        return [];
      });
      return loras.length ? [{ key: group.key, loras }] : [];
    });
    return overrides.length ? { groups: overrides } : undefined;
  };
}

export async function renderStudio(ctx: ViewContext): Promise<void> {
  const root = ctx.mount;
  root.innerHTML = `${heading("02 / THE ATELIER", "创作工坊", "把灵感变成可以翻阅的漫画。从一句话起笔，或精确搭配分镜与工作流。")}
    <div class="cv-layout"><div class="cv-main" data-studio-content>${empty("正在铺开工作台", "读取工作流、分镜和预设。")}</div><aside class="cv-side" data-trial-content></aside></div>`;
  const main = elem(root, "[data-studio-content]");
  const side = elem(root, "[data-trial-content]");
  const [catalogResult, galleryResult] = await Promise.allSettled([
    ctx.api.get<Catalog>("catalog", scope(ctx)),
    ctx.api.get<Bookcase>("gallery", scope(ctx, { limit: 20 })),
  ]);
  if (!main.isConnected) return;
  const catalog = catalogResult.status === "fulfilled" ? catalogResult.value : emptyCatalog;
  const defaultId = (field: string) => typeof catalog.defaults?.[field] === "string" ? String(catalog.defaults[field]) : undefined;
  const series = galleryResult.status === "fulfilled"
    ? (galleryResult.value.series ?? [...new Map(galleryResult.value.items.filter(row => row.series_id).map(
      row => [row.series_id!, { id: row.series_id!, title: row.series_title || row.title }],
    )).values()]) : [];
  const candidates = `<select name="candidates">${[1, 2, 3, 4, 6, 8, 12, 16].map(n => `<option value="${n}"${n === 2 ? " selected" : ""}>${n} 张 / 幕</option>`).join("")}</select>`;
  main.innerHTML = `<div class="cv-sheet">
    <div class="cv-tabs" role="tablist" aria-label="创作方式">
      <button type="button" role="tab" id="cv-draw-tab" aria-controls="cv-draw" data-mode="draw">灵感直出</button>
      <button type="button" role="tab" id="cv-assemble-tab" aria-controls="cv-assemble" data-mode="assemble">精确装配</button>
    </div>
    <form id="cv-draw" class="cv-form" role="tabpanel" aria-labelledby="cv-draw-tab">
      ${note("01 / FROM AN IDEA", "今天，想画一个怎样的故事？", "Mio 会先写成分集剧本，再交给出图工作流。")}
      ${label("一句话灵感", `<textarea name="idea" rows="5" required maxlength="2000" placeholder="例如：凌晨的列车停在海面上，检票员发现最后一节车厢里藏着一座城市。"></textarea>`)}
      <div class="cv-fields">
        ${label("系列", `<select name="series">${options(series, undefined, "创建新系列")}</select>`)}
        ${label("风格预设", `<select name="style">${options(catalog.presets, undefined, "不使用预设")}</select>`)}
        ${label("候选张数", candidates, "为每一幕生成多个候选图；候选越多，出图耗时越长。")}
        ${label("作品形式", `<select name="mode"><option value="strip">条漫</option><option value="export">画册</option></select>`)}
        <label class="cv-field" data-draw-layout hidden><span>画册模板</span><select name="layout">${options(catalog.layouts, undefined, "使用默认模板")}</select></label>
      </div>
      <div class="cv-form-actions"><span>提交后可在任务中心跟踪进度。</span><button type="submit" class="btn btn-primary">${icon("spark", 17)} 开始出漫</button></div>
    </form>
    <form id="cv-assemble" class="cv-form" role="tabpanel" aria-labelledby="cv-assemble-tab">
      ${note("02 / BUILD THE SCENE", "从分镜开始装配", "先检查每一幕的提示词与变量，再提交出图。")}
      <div class="cv-fields">
        ${label("分镜", `<select id="cv-storyboard" required>${options(catalog.storyboards, defaultId("storyboards"), "请选择分镜")}</select>`)}
        ${label("工作流", `<select id="cv-workflow">${options(catalog.workflows, defaultId("workflows") || catalog.workflows[0]?.id, "跟随默认工作流")}</select>`)}
        ${label("图像通道", `<select id="cv-channel">${options(catalog.channels, defaultId("channels"), "按选定工作流出图")}</select>`)}
        ${label("候选张数", candidates, "为每一幕生成多个可对比的候选图。")}
      </div>
      <div class="cv-preset">
        <header><div><strong>角色与场景预设</strong><p class="cv-subtle">可组合多个预设，支持搜索和查看变量。</p></div><span data-preset-count></span></header>
        <div class="cv-search">${icon("search", 16)}<input id="cv-preset-search" type="search" placeholder="搜索预设或变量" aria-label="搜索预设或变量"></div>
        <div data-preset-list></div>
      </div>
      <details class="cv-lora" data-lora><summary>LoRA 单次覆盖 ${tip("只作用于这次装配。关闭 LoRA 需工作流支持独立开关。")}</summary><div data-lora-body></div></details>
      <div class="cv-form-actions">
        ${button("预览装配结果", "data-assemble-preview", "btn", "book")}
        <button type="submit" class="btn btn-primary">${icon("spark", 17)} 装配并开画</button>
      </div>
      <section class="cv-preview" data-preview hidden aria-label="装配预览" aria-live="polite"></section>
    </form>
  </div>`;
  side.innerHTML = `<form id="cv-trial" class="cv-sheet cv-form cv-trial">
    ${note("PROOF / SINGLE FRAME", "单张试画", "先做一张样张，观察提示词和工作流的效果。")}
    <div class="cv-proof" data-trial-proof><span>${icon("image", 27)}</span><small>样张将显示在这里</small></div>
    ${label("工作流", `<select name="workflow">${options(catalog.workflows, defaultId("workflows"), "跟随默认工作流")}</select>`)}
    ${label("试画提示词", '<textarea name="prompt" maxlength="8000" rows="3" placeholder="例如：深墨与冷金配色，夜雨中的空车站…" required></textarea>')}
    <button type="submit" class="btn btn-primary">${icon("image", 16)} 开始试画</button>
    <div data-trial-result aria-live="polite"></div>
  </form>`;
  if (catalogResult.status === "rejected") ctx.toast(asError(catalogResult.reason), "error");
  function selectTab(selected: string) {
    main.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach(tab => {
      const current = tab.dataset.mode === selected;
      tab.setAttribute("aria-selected", String(current));
      tab.tabIndex = current ? 0 : -1;
      elem(main, `#cv-${tab.dataset.mode}`).hidden = !current;
    });
  }
  const initial = ctx.route.tab;
  selectTab(initial === "assemble" || initial === "assembly" || initial === "workshop" ? "assemble" : "draw");
  const tabButtons = Array.from(main.querySelectorAll<HTMLButtonElement>("[data-mode]"));
  tabButtons.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab.dataset.mode || "draw"));
    tab.addEventListener("keydown", event => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const other = tabButtons[1 - index];
        selectTab(other.dataset.mode!); other.focus();
      }
    });
  });
  if (initial === "trial") requestAnimationFrame(() => elem(side, "textarea").focus());
  elem(main, "#cv-draw [name=mode]").addEventListener("change", () => {
    elem(main, "[data-draw-layout]").hidden = txt(main, "#cv-draw [name=mode]") !== "export";
  });
  bindForm(main, "#cv-draw", (form, submit) => {
    const idea = txt(form, "[name=idea]").trim();
    if (!idea) { ctx.toast("请写下一句话创作灵感。", "error"); return; }
    const mode = txt(form, "[name=mode]");
    void action(ctx, submit, () => ctx.api.post<TaskReply>("studio/draw", {
      instance_id: ctx.instance, idea, series: txt(form, "[name=series]"), style: txt(form, "[name=style]"),
      candidates: readNumber(txt(form, "[name=candidates]"), 1), mode,
      layout: mode === "export" ? txt(form, "[name=layout]") : "",
    }), out => accepted(ctx, out));
  });
  const assemble = elem<HTMLFormElement>(main, "#cv-assemble");
  const result = elem(assemble, "[data-preview]");
  const hide = () => { result.hidden = true; };
  const defaults = catalog.defaults?.presets;
  const chosen = new Set(Array.isArray(defaults) ? defaults : defaults?.split(",").filter(Boolean) ?? []);
  presetPicker(elem(assemble, ".cv-preset"), catalog.presets, chosen, hide);
  const getSlots = setLoraControls(assemble, ctx, hide);
  assemble.addEventListener("change", e => { if (!(e.target instanceof Element) || !e.target.closest("[data-preview]")) hide(); });
  function selection() {
    const slots = getSlots();
    return {
      instance_id: ctx.instance, workflow: txt(assemble, "#cv-workflow"),
      storyboard: txt(assemble, "#cv-storyboard"), channel: txt(assemble, "#cv-channel"),
      presets: Array.from(chosen), candidates: readNumber(txt(assemble, "[name=candidates]"), 1),
      ...(slots ? { slot_overrides: slots } : {}),
    };
  }
  // A cloud channel is an alternative to an explicitly pinned workﬂow.
  elem(assemble, "#cv-channel").addEventListener("change", () => {
    const selected = catalog.channels.find(item => item.id === txt(assemble, "#cv-channel")) as (Choice & { cloud?: boolean }) | undefined;
    if (selected?.cloud) {
      const workflow = elem<HTMLSelectElement>(assemble, "#cv-workflow");
      workflow.value = "";
      workflow.dispatchEvent(new Event("change"));
    }
  });
  keep("[data-assemble-preview]", control => {
    if (!assemble.reportValidity()) return;
    const request = selection();
    void action(ctx, control, () => ctx.api.post<{ preview?: Preview } & Preview>("studio/preview", request), out => {
      if (JSON.stringify(request) !== JSON.stringify(selection())) return;
      showPreview(result, out.preview ?? out);
      result.hidden = false;
    });
  }, assemble);
  bindForm(main, "#cv-assemble", (_form, submit) => {
    void action(ctx, submit, () => ctx.api.post<TaskReply>("tasks/create", selection()), out => accepted(ctx, out));
  });
  bindForm(side, "#cv-trial", (form, submit) => {
    const prompt = txt(form, "[name=prompt]").trim();
    if (!prompt) { ctx.toast("请输入试画提示词。", "error"); return; }
    void action(ctx, submit, () => ctx.api.post<TaskReply>("studio/trial", {
      instance_id: ctx.instance, prompt, workflow: txt(form, "[name=workflow]"),
    }), out => {
      accepted(ctx, out);
      const trialResult = elem(side, "[data-trial-result]");
      const proof = elem(side, "[data-trial-proof]");
      trialResult.innerHTML = `<p class="cv-subtle">样张任务 #${out.task.num} 已受理</p>${button("查看样张任务", "data-trial-follow", "btn", "arrow")}`;
      keep("[data-trial-follow]", () => ctx.goto("tasks", { id: out.task.id }), trialResult);
      const src = imageSrc(out.src);
      if (src) proof.innerHTML = `<img src="${esc(src)}" alt="单张试画样张">`;
      else void followTrial(ctx, out.task.id, proof);
    });
  });
}

async function followTrial(ctx: ViewContext, id: string, proof: HTMLElement): Promise<void> {
  const nonce = Math.random().toString(36).slice(2);
  proof.dataset.trialNonce = nonce;
  for (let attempt = 0; attempt < 24 && proof.isConnected && proof.dataset.trialNonce === nonce; attempt++) {
    if (document.hidden) return;
    try {
      const value = await ctx.api.get<TaskDetail>(`tasks/${key(id)}`, scope(ctx));
      if (value.task.statusKey === "complete") {
        const first = value.panels?.find(panel => panel.takes?.some(take => take.status === "candidate" || take.status === "adopted"));
        if (!first) break;
        const take = first.takes?.find(take => take.status === "adopted") ?? first.takes?.[0];
        const src = await fetchImage(ctx, `tasks/${key(id)}/image`, { panel_id: first.id, take_id: take?.id });
        if (proof.dataset.trialNonce !== nonce || !proof.isConnected) return;
        if (src) {
          proof.innerHTML = `<button type="button" aria-label="放大试画样张" data-proof-open><img src="${esc(src)}" alt="单张试画样张"></button>`;
          keep("[data-proof-open]", () => ctx.lightbox(src, "单张试画样张"), proof);
        }
        break;
      }
      if (["failed", "interrupted", "cancelled"].includes(value.task.statusKey)) break;
    } catch { break; }
    await new Promise(resolve => window.setTimeout(resolve, 3000));
  }
}

function seconds(task: Task): string {
  const start = task.started_at;
  if (!start) return "—";
  const end = task.finished_at || (active.has(task.statusKey) ? Date.now() / 1000 : start);
  const value = Math.max(0, Math.floor(end - start));
  return value < 60 ? `${value} 秒` : `${Math.floor(value / 60)} 分 ${value % 60} 秒`;
}

function statusBar(task: Task): string {
  return `<div class="cv-progress">
    <div><span>出图进度</span><strong>${task.total ? `${task.done ?? 0} / ${task.total}` : "—"} ${task.total ? "幕" : ""}</strong></div>
    <progress value="${Math.min(Math.max(task.done || 0, 0), task.total || 1)}" max="${task.total || 1}" aria-label="出图进度"></progress>
  </div>`;
}

function taskKind(task: Task): string {
  return typeLabels[task.kind ?? ""] || (task.statusKey === "archived" ? "旧版记录" : "工坊装配");
}

function taskControl(task: Task): Array<[string, string]> {
  switch (task.statusKey) {
    case "ready": case "running": return [["pause", "暂停"], ["cancel", "停止"]];
    case "paused": return [["resume", "继续"], ["cancel", "停止"]];
    case "failed": case "cancelled": case "canceled": return [["resume", "重试"], ["remove", "移除记录"]];
    case "complete": case "interrupted": case "archived": case "missing": return [["remove", "移除记录"]];
    case "standby": return [["resume", "开始出图"]];
    default: return [];
  }
}

function taskSummary(task: Task): string {
  const selected = task.selection;
  return [selected?.workflowTitle, selected?.storyboardTitle, ...(selected?.presetTitles ?? []).slice(0, 3), selected?.channelTitle].filter(Boolean).join(" · ");
}

function taskCard(task: Task): string {
  return `<article class="cv-task cv-sheet">
    <div class="cv-task-top"><p class="eyebrow">#${esc(task.num)} / ${esc(taskKind(task))}</p>${stateBadge(task.statusKey)}</div>
    <h2>${esc(task.title)}</h2>
    <p class="cv-task-info">${esc(task.sender_name || "控制面板")}<span>·</span>${icon("clock", 14)} ${seconds(task)}
      ${task.queuePosition ? `<span>·</span> 队列第 ${esc(task.queuePosition)} 位` : ""}</p>
    ${taskSummary(task) ? `<p class="cv-task-selection">${esc(taskSummary(task))}</p>` : ""}
    ${task.error ? `<p class="cv-error">${esc(task.error)}</p>` : ""}
    ${statusBar(task)}
    <div class="cv-task-actions">
      ${button("查看任务详情", `data-task-open="${esc(task.id)}"`, "btn btn-primary", "arrow")}
      ${task.statusKey === "complete" && task.episode_id ? button("交付", `data-task-deliver="${esc(task.id)}"`, "btn", "download") : ""}
    </div>
  </article>`;
}

async function fetchImage(ctx: ViewContext, endpoint: string, params: Params): Promise<string> {
  const image = await ctx.api.get<{ src: string }>(endpoint, scope(ctx, params));
  return imageSrc(image.src);
}

const imageQueue: Array<() => Promise<void>> = [];
const smallImageCache = new Map<string, string>();
let imagesRunning = 0;
function runImages(): void {
  while (imagesRunning < 3 && imageQueue.length) {
    imagesRunning++;
    void imageQueue.shift()!().finally(() => { imagesRunning--; runImages(); });
  }
}

function deferImages(
  root: HTMLElement, ctx: ViewContext, endpoint: (id: string) => string, params: (element: HTMLElement) => Params,
): void {
  const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-load-image]"));
  if (!nodes.length) return;
  const enqueue = (node: HTMLElement) => {
    imageQueue.push(async () => {
      if (!node.isConnected) return;
      const path = endpoint(node.dataset.loadImage!);
      const query = params(node);
      const cacheKey = `${ctx.instance}:${path}:${JSON.stringify(query)}`;
      try {
        let src = smallImageCache.get(cacheKey);
        if (!src) {
          src = await fetchImage(ctx, path, query);
          if (src && src.length < 320_000) {
            if (smallImageCache.size >= 22) smallImageCache.delete(smallImageCache.keys().next().value!);
            smallImageCache.set(cacheKey, src);
          }
        }
        if (src && node.isConnected) node.innerHTML = `<img src="${esc(src)}" alt="${esc(node.dataset.imageAlt || "")}" decoding="async">`;
      } catch {
        if (node.isConnected) node.classList.add("cv-photo-unavailable");
      }
    });
    runImages();
  };
  if (!("IntersectionObserver" in window)) { nodes.forEach(enqueue); return; }
  const remaining = new Set(nodes);
  const stop = () => { observer.disconnect(); lifecycle.disconnect(); };
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting || !entry.target.isConnected) {
        observer.unobserve(entry.target);
        remaining.delete(entry.target as HTMLElement);
        if (entry.isIntersecting) enqueue(entry.target as HTMLElement);
      }
    }
    if (!remaining.size) stop();
  }, { rootMargin: "120px" });
  nodes.forEach(node => observer.observe(node));
  // If a view or drawer closes with images still below fold, release its observer.
  const lifecycle = new MutationObserver(() => {
    if (!root.isConnected || !nodes[0].isConnected) stop();
  });
  lifecycle.observe(document.body, { childList: true, subtree: true });
}

function bestTake(frame: Frame): Take | undefined {
  return frame.takes?.find(item => item.status === "adopted") ?? frame.takes?.[0];
}

function frameMarkup(frame: Frame, task: boolean): string {
  const take = bestTake(frame);
  const title = frame.title || `第 ${frame.number} 幕`;
  return `<article class="cv-frame">
    <div class="cv-frame-head"><span class="eyebrow">P.${String(frame.number).padStart(2, "0")}</span>${stateBadge(frame.status || "pending")}</div>
    ${take ? `<button type="button" class="cv-photo cv-frame-photo" data-frame-open="${esc(frame.id)}" aria-label="放大 ${esc(title)}">
      <span data-load-image="${esc(frame.id)}" data-take="${esc(take.status === "adopted" ? "" : take.id)}" data-image-alt="${esc(title)}">${icon("image", 25)}</span>
    </button>` : `<div class="cv-photo cv-frame-photo"><span>${icon("image", 25)}</span></div>`}
    <div class="cv-frame-meta"><strong>${esc(title)}</strong>
      ${frame.description ? `<p>${esc(frame.description)}</p>` : ""}
      ${frame.error ? `<p class="cv-error">${esc(frame.error)}</p>` : ""}</div>
    ${task ? `<div class="cv-frame-actions">
      ${take ? button(`候选图 · ${frame.takes?.length || 1}`, `data-candidates="${esc(frame.id)}"`) : ""}
      ${button("重画", `data-redraw="${esc(frame.id)}"`)}
    </div>` : ""}
  </article>`;
}

function enableFrames(root: HTMLElement, ctx: ViewContext, endpoint: string, frames: Frame[]): void {
  deferImages(root, ctx, () => endpoint, el => ({ panel_id: el.dataset.loadImage, take_id: el.dataset.take || undefined, width: 480 }));
  keep("[data-frame-open]", button => {
    const frame = frames.find(panel => panel.id === button.dataset.frameOpen);
    if (!frame) return;
    const take = bestTake(frame);
    void action(ctx, button, () => fetchImage(ctx, endpoint, {
      panel_id: frame.id, take_id: take?.status === "adopted" ? undefined : take?.id, width: 1440,
    }), src => { if (src) ctx.lightbox(src, frame.title || `第 ${frame.number} 幕`); });
  }, root);
}

function openCandidates(ctx: ViewContext, task: Task, panel: Frame): void {
  const takes = panel.takes ?? [];
  if (!takes.length) { ctx.toast("暂无候选图。"); return; }
  const endpoint = `tasks/${key(task.id)}`;
  const root = present(ctx, {
    title: panel.title || `第 ${panel.number} 幕的候选图`,
    subtitle: `#${task.num} / TAKE REVIEW`,
    content: `<div class="cv-detail">
      ${button("返回任务详情", "data-candidate-back")}
      <div class="cv-tabs cv-frame-tabs" role="tablist" aria-label="选择候选图">${takes.map((take, index) =>
        `<button type="button" role="tab" aria-selected="false" data-take-choice="${index}">候选 ${take.number || index + 1}${take.status === "adopted" ? " · 已采用" : ""}</button>`).join("")}</div>
      <div class="cv-candidate-preview" data-candidate-preview></div>
      <div class="cv-form-actions"><span data-candidate-meta></span>${button("采用此候选", "data-pick-take", "btn btn-primary", "check")}</div>
    </div>`,
  });
  let chosen = 0;
  let request = 0;
  const preview = elem(root, "[data-candidate-preview]");
  const adopt = elem<HTMLButtonElement>(root, "[data-pick-take]");
  function choose(index: number) {
    chosen = index;
    const nonce = ++request;
    const take = takes[index];
    root.querySelectorAll<HTMLButtonElement>("[data-take-choice]").forEach(tab =>
      tab.setAttribute("aria-selected", String(tab.dataset.takeChoice === String(index))));
    adopt.disabled = take.status === "adopted";
    preview.innerHTML = `<div class="cv-photo cv-candidate-image"><span>${icon("image", 25)}</span></div>`;
    const mark = take.qa?.score ?? take.score;
    elem(root, "[data-candidate-meta]").textContent = [
      stateName(take.status),
      typeof mark === "number" ? `质检 ${Math.round(mark * 100)}%` : "",
      ...(take.qa?.issues ?? []).slice(0, 2),
    ].filter(Boolean).join(" · ");
    void fetchImage(ctx, `${endpoint}/image`, { panel_id: panel.id, take_id: take.id, width: 900 })
      .then(src => {
        if (request !== nonce || !src || !preview.isConnected) return;
        preview.innerHTML = `<button type="button" class="cv-photo cv-candidate-image" aria-label="放大当前候选图"><img src="${esc(src)}" alt="第 ${panel.number} 幕候选图"></button>`;
        keep("button", () => ctx.lightbox(src, `第 ${panel.number} 幕 · 候选 ${take.number || index + 1}`), preview);
      }).catch(error => { if (request === nonce) preview.textContent = asError(error); });
  }
  keep("[data-take-choice]", control => choose(Number(control.dataset.takeChoice)), root);
  keep("[data-candidate-back]", () => { void openTask(ctx, task.id, task); }, root);
  keep("[data-pick-take]", control => {
    const selected = chosen;
    void action(ctx, control, () => ctx.api.post<{ message?: string }>(`${endpoint}/pick`, {
      panel_number: panel.number, take_number: takes[selected].number || selected + 1,
    }), value => {
      smallImageCache.clear();
      ctx.toast(value.message || "候选图已采用", "success");
      void openTask(ctx, task.id, task);
    });
  }, root);
  choose(Math.max(0, takes.findIndex(take => take.status === "adopted")));
}

function openRedraw(ctx: ViewContext, task: Task, panel: Frame): void {
  const id = task.id;
  const root = present(ctx, {
    title: `重画第 ${panel.number} 幕`,
    subtitle: `#${task.num} / REPRINT`,
    content: `<form class="cv-form cv-detail" id="cv-redraw">
      <p>${esc(panel.description || "提交后，Mio 会重新生成这一幕并用新图替换旧图。")}</p>
      ${label("新的提示词（可选）", '<textarea name="prompt" maxlength="8000" rows="5" placeholder="留空时沿用原提示词"></textarea>')}
      <div class="cv-form-actions">${button("返回任务详情", "data-redraw-back")}<button class="btn btn-primary" type="submit">提交重画</button></div>
    </form>`,
  });
  keep("[data-redraw-back]", () => { void openTask(ctx, id, task); }, root);
  bindForm(root, "#cv-redraw", (form, submit) => {
    void action(ctx, submit, () => ctx.api.post<TaskReply>(`tasks/${key(id)}/redraw`, {
      panel_number: panel.number, prompt: txt(form, "[name=prompt]"),
    }), out => {
      accepted(ctx, out); smallImageCache.clear(); ctx.closeDrawer();
      ctx.goto("tasks", { id: out.task.id });
    });
  });
}

async function openTask(ctx: ViewContext, id: string, snapshot?: Task): Promise<void> {
  const path = `tasks/${key(id)}`;
  const drawer = present(ctx, {
    title: snapshot?.title || "任务详情", subtitle: snapshot ? `#${snapshot.num} / PRODUCTION SHEET` : "PRODUCTION SHEET",
    content: empty("正在读取任务详情"), wide: true,
  });
  try {
    const { task, panels: outside } = await ctx.api.get<TaskDetail>(path, scope(ctx));
    if (!showing(drawer)) return;
    const panels = outside ?? task.panels ?? [];
    drawer.innerHTML = `<div class="cv-detail">
      <div class="cv-detail-header">${stateBadge(task.statusKey)}<span>${esc(task.sender_name || "控制面板")} · ${seconds(task)}</span>
        ${task.queuePosition ? `<span>队列第 ${esc(task.queuePosition)} 位</span>` : ""}</div>
      ${taskSummary(task) ? `<p class="cv-subtle">${esc(taskSummary(task))}</p>` : ""}
      ${task.error || task.panels_error ? `<p class="cv-error">${esc(task.error || task.panels_error)}</p>` : ""}
      ${statusBar(task)}
      <div class="cv-detail-actions">
        ${taskControl(task).map(([value, text]) => button(text, `data-task-action="${value}"`)).join("")}
        ${panels.length ? button("触发 QA 质检", "data-task-qa", "btn", "check") : ""}
        ${task.episode_id ? button("导出与转发", "data-task-export", "btn btn-primary", "download") : ""}
        ${button("更新进度", "data-task-refresh", "btn", "refresh")}
      </div>
      ${note("STORYBOARD / " + String(panels.length).padStart(2, "0"), "分幕与候选")}
      ${panels.length ? `<div class="cv-frame-grid">${panels.map(frame => frameMarkup(frame, true)).join("")}</div>`
        : empty("尚未生成分幕", "等 Mio 准备好首张图片后，就能在这里查看。")}
    </div>`;
    enableFrames(drawer, ctx, `${path}/image`, panels);
    keep("[data-task-refresh]", () => { void openTask(ctx, id, task); }, drawer);
    keep("[data-task-export]", () => { void openExport(ctx, path, task.title, true, () => { void openTask(ctx, id, task); }); }, drawer);
    keep("[data-task-action]", control => {
      if (control.dataset.taskAction === "remove" && !window.confirm("移除这条任务记录？Mio 画廊中的作品会保留。")) return;
      void action(ctx, control, () => ctx.api.post<{ message?: string }>(`${path}/action`, {
        action: control.dataset.taskAction,
      }), out => {
        ctx.toast(out.message || "任务已更新", "success");
        ctx.closeDrawer();
        ctx.goto("tasks");
      });
    }, drawer);
    keep("[data-task-qa]", control => {
      void action(ctx, control, () => ctx.api.post<TaskReply>(`${path}/qa`), value => {
        accepted(ctx, value);
        ctx.closeDrawer();
        ctx.goto("tasks", { id: value.task.id });
      });
    }, drawer);
    keep("[data-candidates]", button => {
      const panel = panels.find(frame => frame.id === button.dataset.candidates);
      if (panel) openCandidates(ctx, task, panel);
    }, drawer);
    keep("[data-redraw]", button => {
      const panel = panels.find(frame => frame.id === button.dataset.redraw);
      if (panel) openRedraw(ctx, task, panel);
    }, drawer);
  } catch (error) {
    if (showing(drawer)) drawer.innerHTML = empty("无法读取任务详情", asError(error));
    ctx.toast(asError(error), "error");
  }
}

export async function renderTasks(ctx: ViewContext): Promise<void> {
  const root = ctx.mount;
  root.innerHTML = `${heading("03 / PRODUCTION", "任务中心", "追踪作品的每一幕，检查候选、质检与交付。", button("刷新任务", "data-reload-tasks", "btn", "refresh"))}
    <div class="cv-filter cv-sheet">
      <div class="cv-search">${icon("search", 16)}<input type="search" id="cv-task-search" placeholder="搜索任务、编号或发起人" aria-label="搜索任务、编号或发起人"></div>
      <select id="cv-task-status" aria-label="按状态筛选"><option value="">全部状态</option>${options([
      { id: "active", title: "正在进行" }, { id: "complete", title: "已完成" }, { id: "paused", title: "已暂停" },
      { id: "failed", title: "失败" }, { id: "cancelled", title: "已停止" }, { id: "archived", title: "旧版记录" },
    ])}</select>
      <select id="cv-task-kind" aria-label="按来源筛选"><option value="">全部来源</option>${options(Object.entries(typeLabels).map(([id, title]) => ({ id, title })))}</select>
      <span class="cv-filter-count" data-task-count aria-live="polite"></span>
    </div>
    <div data-task-grid class="cv-task-grid">${empty("正在加载任务")}</div>`;
  const grid = elem(root, "[data-task-grid]");
  let tasks: Task[] = [];
  let request = 0;
  let refreshTimer = 0;
  const onVisibility = () => {
    window.clearTimeout(refreshTimer);
    if (!document.hidden && grid.isConnected) void load().catch(() => {});
  };
  document.addEventListener("visibilitychange", onVisibility);
  const cleanup = new MutationObserver(() => {
    if (!grid.isConnected) {
      window.clearTimeout(refreshTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      cleanup.disconnect();
    }
  });
  cleanup.observe(document.body, { childList: true, subtree: true });
  function draw() {
    const query = lower(txt(root, "#cv-task-search")).trim();
    const status = txt(root, "#cv-task-status");
    const source = txt(root, "#cv-task-kind");
    const rows = tasks.filter(task => (
      !status || (status === "active" ? active.has(task.statusKey) : task.statusKey === status)
    ) && (!source || (task.kind ?? (task.statusKey === "archived" ? "v1" : "workshop")) === source)
      && (!query || lower([task.title, task.num, `#${task.num}`, task.sender_name, taskSummary(task)].join(" ")).includes(query)));
    elem(root, "[data-task-count]").textContent = `${rows.length} 项任务`;
    grid.innerHTML = rows.length ? rows.map(taskCard).join("") : empty("没有找到任务", "尝试清除筛选，或去创作工坊开始一部新作品。");
    keep("[data-task-open]", control => {
      void openTask(ctx, control.dataset.taskOpen!, tasks.find(row => row.id === control.dataset.taskOpen));
    }, grid);
    keep("[data-task-deliver]", control => {
      const row = tasks.find(task => task.id === control.dataset.taskDeliver);
      if (row) void openExport(ctx, `tasks/${key(row.id)}`, row.title, true);
    }, grid);
  }
  async function load() {
    const current = ++request;
    const out = await ctx.api.get<{ items: Task[] }>("tasks", scope(ctx, { limit: 100 }));
    if (grid.isConnected && request === current) {
      tasks = out.items;
      draw();
      window.clearTimeout(refreshTimer);
      if (!document.hidden && tasks.some(task => active.has(task.statusKey))) {
        refreshTimer = window.setTimeout(() => {
          if (!grid.isConnected) return;
          if (document.hidden) return;
          void load().catch(() => {});
        }, 7500);
      }
    }
  }
  elem(root, "#cv-task-search").addEventListener("input", draw);
  elem(root, "#cv-task-status").addEventListener("change", draw);
  elem(root, "#cv-task-kind").addEventListener("change", draw);
  keep("[data-reload-tasks]", control => { void action(ctx, control, load); }, root);
  try {
    await load();
    if (ctx.route.id && grid.isConnected) {
      void openTask(ctx, ctx.route.id, tasks.find(row => row.id === ctx.route.id));
    }
  } catch (error) { if (grid.isConnected) grid.innerHTML = empty("没能读取任务", asError(error)); }
}

const templateSamples: Choice[] = [
  { id: "export-monograph", title: "Monograph" },
  { id: "export-nocturne", title: "Nocturne" },
  { id: "export-folio", title: "Folio" },
];

function templateKind(template: Choice): string {
  const value = lower(`${template.id} ${template.title}`);
  return value.includes("nocturne") ? "nocturne" : value.includes("folio") ? "folio" : "monograph";
}

function bookMockup(variant: string): string {
  if (variant === "nocturne") return `<span class="cv-book-preview cv-book-nocturne" aria-hidden="true"><span>02 / NIGHT EDITION</span>
    <b>NOCTURNE</b><i class="cv-art cv-art-moon"></i><span><em></em><em></em>MIÖ PRESS · 26</span></span>`;
  if (variant === "folio") return `<span class="cv-book-preview cv-book-folio" aria-hidden="true"><span>MIO PRESS / VOL. 03</span>
    <b>Folio</b><span class="cv-book-pair"><i class="cv-art cv-art-fields"></i><i class="cv-art cv-art-moon"></i></span>
    <span><em></em><em></em>03 / 24</span></span>`;
  return `<span class="cv-book-preview cv-book-monograph" aria-hidden="true"><span>THE MIO COLLECTION / 01</span>
    <b>Monograph</b><i class="cv-art cv-art-fields"></i><span><em></em><em></em>CHAPTER 01</span></span>`;
}

function templateCards(templates: Choice[]): string {
  return templates.map((template, i) => {
    const variant = templateKind(template);
    const line = variant === "nocturne" ? "墨底 · 夜读" : variant === "folio" ? "双栏 · 图册" : "纸本 · 大图";
    return `<label class="cv-template"><input type="radio" name="layout" value="${esc(template.id)}" ${i ? "" : "checked"}>
      <span>${bookMockup(variant)}<strong>${esc(template.title)}</strong><small>${line}</small></span>
    </label>`;
  }).join("");
}

function fileExtension(format: string): string {
  return format === "strip" ? "png" : format === "slices" ? "zip" : format === "motion" ? "html" : format;
}

async function openExport(
  ctx: ViewContext, path: string, title: string, forTask: boolean, back?: () => void,
): Promise<void> {
  const drawer = present(ctx, {
    title: "导出与交付", subtitle: `PUBLISH / ${title}`,
    content: empty("正在准备导出选项"), wide: true,
  });
  const [resources, sessions] = await Promise.allSettled([
    ctx.api.get<Catalog>("catalog", scope(ctx)),
    ctx.api.get<{ items: Session[] }>("sessions", scope(ctx)),
  ]);
  if (!showing(drawer)) return;
  const available = resources.status === "fulfilled" ? resources.value.layouts : [];
  const builtins = templateSamples.flatMap(sample => {
    const found = available.find(row => templateKind(row) === templateKind(sample) &&
      lower(`${row.title} ${row.id}`).includes(lower(sample.title)));
    return found ? [found] : ctx.api.demo && !available.length ? [sample] : [];
  });
  const remaining = available.filter(item => !builtins.some(row => row.id === item.id));
  const accounts = sessions.status === "fulfilled" ? sessions.value.items.map(({ umo, title, sender }) => ({
    id: umo, title: title || (sender ? `${sender} · ${umo}` : umo),
  })) : [];
  drawer.innerHTML = `<form id="cv-export" class="cv-detail cv-form">
    ${back ? button("返回作品详情", "data-export-back") : ""}
    <div class="cv-fields">
      ${label("成品格式", `<select name="format">${options(formats, "html")}</select>`)}
      ${label("图片档位", `<select name="image_profile">${options([
        { id: "auto", title: "自动 · 保留适合的质量" }, { id: "publish", title: "发布 · 体积更小" },
        { id: "clean", title: "清洗 · 去除图片元数据" }, { id: "archive", title: "归档 · 保留原图" },
        { id: "preview", title: "预览 · 优先速度" },
      ], "auto")}</select>`, "发布档更适合聊天发送，归档档更适合留存原图。不同导出格式支持的档位可能不同。")}
    </div>
    <fieldset class="cv-export-layout">
      <legend>画册模板</legend><p class="cv-subtle" data-layout-hint>为 HTML 画册选择一种封面和版式。</p>
      ${builtins.length ? `<div class="cv-templates">${templateCards(builtins)}</div>` : ""}
      ${remaining.length || !builtins.length ? label("其他模板", `<select name="layout_more">${options(remaining, undefined, "沿用所选版式或默认模板")}</select>`) : ""}
      <div class="cv-fields cv-print-custom">
        ${label("主题色覆盖", '<span class="cv-color"><input type="checkbox" name="theme_enabled" aria-label="启用主题色覆盖"><input type="color" name="theme_color" value="#96563F" aria-label="选择主题色"><span>使用自选主题色</span></span>')}
        ${label("画册署名", '<input name="signature" type="text" maxlength="120" placeholder="例如：Mio 独立工作室">')}
      </div>
    </fieldset>
    <div class="cv-toggle-pair">
      <label><input type="checkbox" name="show_captions" checked> 显示文案</label>
      <label><input type="checkbox" name="show_prompts"> 显示提示词</label>
    </div>
    ${label("转发到会话", `<select name="umo">${options(accounts, undefined, accounts.length ? "选择已知会话" : "尚无已知会话")}</select>`, "请让目标会话先发送 /mio ping 以登记，再从这里选择。")}
    ${!accounts.length ? '<p class="cv-subtle">还没有可转发的会话。文件仍可下载。</p>' : ""}
    <div class="cv-form-actions">
      <button type="submit" class="btn btn-primary">${icon("download", 16)} 下载成品</button>
      ${button("转发成品", "data-export-send", "btn", "delivery")}
      ${forTask ? button("发送分幕图", "data-export-images", "btn", "images") : ""}
    </div>
  </form>`;
  const form = elem<HTMLFormElement>(drawer, "#cv-export");
  const layoutBox = elem<HTMLFieldSetElement>(form, ".cv-export-layout");
  const showPrompts = elem<HTMLInputElement>(form, "[name=show_prompts]");
  const showCaptions = elem<HTMLInputElement>(form, "[name=show_captions]");
  const profile = elem<HTMLSelectElement>(form, "[name=image_profile]");
  function syncFormat() {
    const format = txt(form, "[name=format]");
    layoutBox.disabled = format !== "html";
    showPrompts.disabled = format !== "html";
    showCaptions.disabled = !["html", "pdf", "zip"].includes(format);
    profile.disabled = showCaptions.disabled;
    elem(layoutBox, "[data-layout-hint]").textContent = format === "html" ?
      "为 HTML 画册选择一种封面和版式。" : "画册模板仅在选择 HTML 时生效。";
  }
  elem(form, "[name=format]").addEventListener("change", syncFormat);
  elem(form, "[name=theme_color]").addEventListener("input", () => {
    elem<HTMLInputElement>(form, "[name=theme_enabled]").checked = true;
    form.style.setProperty("--cv-cover-accent", txt(form, "[name=theme_color]"));
  });
  elem(form, "[name=theme_enabled]").addEventListener("change", () => {
    if (checked(form, "[name=theme_enabled]")) form.style.setProperty("--cv-cover-accent", txt(form, "[name=theme_color]"));
    else form.style.removeProperty("--cv-cover-accent");
  });
  form.querySelectorAll("[name=layout]").forEach(radio => radio.addEventListener("change", () => {
    const more = form.querySelector<HTMLSelectElement>("[name=layout_more]");
    if (more) more.value = "";
  }));
  syncFormat();
  function payload(): Params {
    const format = txt(form, "[name=format]");
    return {
      format, instance_id: ctx.instance,
      ...(format === "html" ? {
        layout: txt(form, "[name=layout_more]") || txt(form, "[name=layout]:checked"),
        theme_color: checked(form, "[name=theme_enabled]") ? txt(form, "[name=theme_color]") : "",
        signature: txt(form, "[name=signature]").trim(), show_prompts: showPrompts.checked,
      } : {}),
      ...(!profile.disabled ? { image_profile: profile.value, show_captions: showCaptions.checked } : {}),
    };
  }
  keep("[data-export-back]", () => back?.(), drawer);
  bindForm(drawer, "#cv-export", (_form, submit) => {
    const data = payload();
    const file = String(title || "mio").replace(/[\\/:*?"<>|]/g, "-").slice(0, 72);
    void action(ctx, submit, () => ctx.api.download(`${path}/export`, data, `${file}.${fileExtension(String(data.format))}`), () => {
      ctx.toast("成品下载已开始", "success");
    });
  });
  function send(control: HTMLButtonElement, mode: "export" | "images") {
    const umo = txt(form, "[name=umo]");
    if (!umo) { ctx.toast("请先选择已知会话。", "error"); elem(form, "[name=umo]").focus(); return; }
    void action(ctx, control, () => ctx.api.post<{ message?: string }>(`${path}/send`, {
      ...payload(), umo, mode,
    }), out => ctx.toast(out.message || "成品已发送", "success"));
  }
  keep("[data-export-send]", control => send(control, "export"), drawer);
  keep("[data-export-images]", control => send(control, "images"), drawer);
}

function workCard(work: Work, index: number): string {
  const id = work.episode_id || work.id;
  const cover = imageSrc(work.cover_src);
  return `<article class="cv-work cv-sheet">
    <button class="cv-work-picture cv-cover-${index % 4}" type="button" aria-label="阅读 ${esc(work.title)}" data-work-open="${esc(id)}">
      <span class="cv-cover-art" ${!cover && (work.adopted_count || work.cover_asset_id) ? `data-load-image="${esc(id)}" data-image-alt=""` : ""}>
        ${cover ? `<img src="${esc(cover)}" alt="" loading="lazy" decoding="async">` : `<span class="cv-book-blank"><i>✦</i><small>THE MIO COLLECTION</small></span>`}
      </span><span class="cv-cover-index">PLATE ${String(index + 1).padStart(2, "0")}</span>
    </button>
    <div class="cv-work-caption">
      <p class="eyebrow">${esc(work.series_title || "MIO COLLECTION")}</p>
      <h2>${esc(work.title)}</h2>
      ${work.description ? `<p>${esc(work.description)}</p>` : ""}
      <div class="cv-work-actions">
        ${button("阅读作品", `data-work-open="${esc(id)}"`, "btn", "book")}
        ${button("导出", `data-work-export="${esc(id)}"`, "btn", "download")}
      </div>
    </div>
  </article>`;
}

async function openWork(ctx: ViewContext, id: string, snapshot?: Work): Promise<void> {
  const path = `gallery/${key(id)}`;
  const drawer = present(ctx, {
    title: snapshot?.title || "阅读作品", subtitle: snapshot?.series_title || "READING ROOM",
    content: empty("正在展开作品"), wide: true,
  });
  try {
    const { work, panels: visible = [] } = await ctx.api.get<WorkDetail>(path, scope(ctx));
    if (!showing(drawer)) return;
    const panels = visible.filter(panel => bestTake(panel));
    drawer.innerHTML = `<div class="cv-detail">
      ${work.description ? `<p class="cv-story-description">${esc(work.description)}</p>` : ""}
      <div class="cv-detail-actions">
        ${button("导出画册", "data-reader-export", "btn btn-primary", "download")}
        ${button("长漫阅读", "data-reader-strip", "btn", "book")}
      </div>
      ${note("READING ROOM / " + String(panels.length).padStart(2, "0"), "分幕阅览")}
      ${panels.length ? `<div class="cv-frame-grid" data-reader-frames>${panels.map(panel => frameMarkup(panel, false)).join("")}</div>` :
        empty("还没有成品图片", "完成出图并采用分幕后即可在画廊阅读。")}
    </div>`;
    enableFrames(drawer, ctx, `${path}/image`, panels);
    keep("[data-reader-export]", () => { void openExport(ctx, path, work.title, false, () => { void openWork(ctx, id, snapshot); }); }, drawer);
    keep("[data-reader-strip]", button => {
      const gallery = drawer.querySelector("[data-reader-frames]");
      if (!gallery) return;
      const reading = gallery.classList.toggle("cv-reader-strip");
      button.textContent = reading ? "分幕网格" : "长漫阅读";
      button.setAttribute("aria-pressed", String(reading));
    }, drawer);
  } catch (error) {
    if (showing(drawer)) drawer.innerHTML = empty("无法打开这部作品", asError(error));
    ctx.toast(asError(error), "error");
  }
}

export async function renderGallery(ctx: ViewContext): Promise<void> {
  const root = ctx.mount;
  root.innerHTML = `${heading("04 / THE ARCHIVE", "作品画廊", "翻阅各系列的分集与成品，选择画册版式，下载或转发到聊天会话。")}
    <div class="cv-filter cv-sheet">
      <div class="cv-search">${icon("search", 16)}<input type="search" id="cv-work-search" placeholder="搜索作品或系列" aria-label="搜索作品或系列"></div>
      <select id="cv-work-series" aria-label="按系列筛选"><option value="">全部系列</option></select>
      <span class="cv-filter-count" data-work-count aria-live="polite"></span>
    </div>
    <div class="cv-work-grid" data-work-grid>${empty("正在布置画廊")}</div>
    <div class="cv-gallery-footer" data-gallery-footer></div>`;
  const grid = elem(root, "[data-work-grid]");
  const footer = elem(root, "[data-gallery-footer]");
  const series = elem<HTMLSelectElement>(root, "#cv-work-series");
  let request = 0;
  let limit = 24;
  let searchDelay = 0;
  let items: Work[] = [];
  function render() {
    const rows = items;
    elem(root, "[data-work-count]").textContent = `${rows.length} 部作品`;
    grid.innerHTML = rows.length ? rows.map(workCard).join("") : empty("未找到作品", "换个关键词，或选择全部系列。");
    footer.innerHTML = rows.length === limit && limit < 100 ? button("载入更多作品", "data-work-more", "btn", "arrow") : "";
    deferImages(grid, ctx, id => `gallery/${key(id)}/image`, () => ({ width: 400 }));
    const lookup = (id: string | undefined) => rows.find(row => (row.episode_id || row.id) === id);
    keep("[data-work-open]", control => {
      const work = lookup(control.dataset.workOpen);
      if (work) void openWork(ctx, work.episode_id || work.id, work);
    }, grid);
    keep("[data-work-export]", control => {
      const work = lookup(control.dataset.workExport);
      if (work) void openExport(ctx, `gallery/${key(work.episode_id || work.id)}`, work.title, false);
    }, grid);
    keep("[data-work-more]", control => {
      limit = Math.min(100, limit + 24);
      void action(ctx, control, load);
    }, footer);
  }
  async function load() {
    const nonce = ++request;
    const chosen = series.value;
    const query = txt(root, "#cv-work-search").trim();
    const result = await ctx.api.get<Bookcase>("gallery", scope(ctx, { query, limit, series_id: chosen }));
    if (!grid.isConnected || nonce !== request) return;
    items = result.items;
    if (result.series) series.innerHTML = options(result.series, chosen, "全部系列");
    render();
  }
  function reload() {
    limit = 24;
    void load().catch(error => {
      if (grid.isConnected) { grid.innerHTML = empty("无法读取作品", asError(error)); ctx.toast(asError(error), "error"); }
    });
  }
  elem(root, "#cv-work-search").addEventListener("input", () => {
    window.clearTimeout(searchDelay); searchDelay = window.setTimeout(reload, 280);
  });
  series.addEventListener("change", reload);
  try {
    await load();
    if (ctx.route.id && grid.isConnected) void openWork(ctx, ctx.route.id, items.find(row => row.id === ctx.route.id || row.episode_id === ctx.route.id));
  } catch (error) {
    if (grid.isConnected) grid.innerHTML = empty("无法读取作品", asError(error));
  }
}
