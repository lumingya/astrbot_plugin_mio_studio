/* Mio 绘页 control panel — talks to the plugin backend through window.AstrBotPluginPage. */
(function () {
  "use strict";
  const bridge = window.AstrBotPluginPage;
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const state = { config: null, options: null, catalog: null, status: null, tasks: [], timer: null };

  function toast(message, kind) {
    const box = document.createElement("div");
    box.className = `toast ${kind || ""}`;
    box.textContent = message;
    $("#toasts").appendChild(box);
    setTimeout(() => box.remove(), kind === "err" ? 7000 : 3200);
  }
  async function api(method, endpoint, payload) {
    try {
      return method === "GET" ? await bridge.apiGet(endpoint, payload || {}) : await bridge.apiPost(endpoint, payload || {});
    } catch (error) {
      throw new Error((error && error.message) || String(error));
    }
  }
  const get = (e, p) => api("GET", e, p);
  const post = (e, b) => api("POST", e, b);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function busy(btn, on, text) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.disabled = true; if (text) btn.textContent = text; }
    else { btn.disabled = false; if (btn.dataset.label) btn.textContent = btn.dataset.label; }
  }
  function fillSelect(el, items, value, opts) {
    opts = opts || {};
    el.innerHTML = "";
    if (opts.blank) el.appendChild(new Option(opts.blank, ""));
    (items || []).forEach((item, i) => {
      const label = `${i + 1}. ${item.title || item.id}${opts.suffix ? opts.suffix(item) : ""}`;
      const option = new Option(label, item.id);
      el.appendChild(option);
    });
    if (el.multiple) {
      const wanted = new Set(Array.isArray(value) ? value : String(value || "").split(",").filter(Boolean));
      $$("option", el).forEach((o) => { o.selected = wanted.has(o.value); });
    } else {
      el.value = value || "";
      if (el.value !== (value || "") && el.options.length) el.selectedIndex = 0;
    }
  }
  const selectedValues = (el) => $$("option:checked", el).map((o) => o.value);

  // ------------------------------------------------------------- tabs
  function wireTabs() {
    $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === tab.dataset.tab));
      if (tab.dataset.tab === "tasks") loadTasks();
    }));
  }

  // ----------------------------------------------------------- status
  function renderStatus(status) {
    const pill = $("#conn-pill");
    pill.classList.remove("ok", "warn", "err");
    let text;
    if (!status.configured) { pill.classList.add("warn"); text = "未配置 Token"; }
    else if (!status.ok) { pill.classList.add("err"); text = "Mio 离线"; }
    else if (!status.patched) { pill.classList.add("warn"); text = `Mio ${status.version || ""} · 未应用补丁`; }
    else { pill.classList.add("ok"); text = `Mio ${status.version || ""} 在线`; }
    $("#conn-text").textContent = text;
    const detail = $("#conn-detail");
    detail.classList.toggle("err", !!status.error);
    const lines = [];
    lines.push(`地址：${status.baseUrl}`);
    if (status.error) lines.push(`问题：${status.error}`);
    if (status.capabilities) lines.push(`能力：目录 ${(status.capabilities.catalog || []).join("/") || "—"}；导出 ${(status.capabilities.albumExportFormats || []).join("/") || "—"}；生产队列 ${status.capabilities.productionQueue ? "是" : "否"}`);
    if (status.tasks) lines.push(`本插件任务：${status.tasks.total} 个，进行中 ${status.tasks.active} 个；轮询：${status.poller ? "运行中" : "空闲"}`);
    detail.textContent = lines.join("\n");
  }
  async function loadStatus() {
    try { state.status = await get("status"); renderStatus(state.status); }
    catch (e) { $("#conn-text").textContent = "读取状态失败"; $("#conn-pill").classList.add("err"); toast(e.message, "err"); }
  }

  // ----------------------------------------------------------- config
  function applyConfig(cfg) {
    state.config = cfg;
    $("#f-base_url").value = cfg.mio.base_url || "";
    $("#f-request_timeout").value = cfg.mio.request_timeout;
    $("#f-api_token").value = "";
    $("#token-hint").textContent = cfg.mio.api_token_set ? `已设置（${cfg.mio.api_token_masked}）；留空保存表示不修改` : "未设置（本地免密模式）";
    $("#f-admin_only").checked = !!cfg.permissions.admin_only;
    $("#f-allowed_sessions").value = (cfg.permissions.allowed_sessions || []).join("\n");
    $("#f-title_template").value = cfg.defaults.title_template || "";
    $("#f-concurrency").value = cfg.defaults.concurrency;
    $("#f-seed").value = cfg.defaults.seed;
    fillSelect($("#f-seed_mode"), state.options.seed_modes.map((o) => ({ id: o.value, title: o.label })), cfg.defaults.seed_mode);
    fillSelect($("#f-format"), state.options.formats.map((f) => ({ id: f, title: f.toUpperCase() })), cfg.export.format);
    fillSelect($("#f-image_profile"), state.options.image_profiles.map((o) => ({ id: o.value, title: o.label })), cfg.export.image_profile);
    $("#f-theme_color").value = cfg.export.theme_color || "";
    $("#f-signature").value = cfg.export.signature || "";
    $("#f-show_captions").checked = !!cfg.export.show_captions;
    $("#f-show_prompts").checked = !!cfg.export.show_prompts;
    fillSelect($("#f-send_mode"), state.options.send_modes.map((o) => ({ id: o.value, title: o.label })), cfg.delivery.send_mode);
    $("#f-max_images").value = cfg.delivery.max_images;
    $("#f-poll_interval").value = cfg.delivery.poll_interval;
    $("#f-include_captions").checked = !!cfg.delivery.include_captions;
    $("#f-forward_sender_name").value = cfg.delivery.forward_sender_name || "";
    $("#f-forward_sender_uin").value = cfg.delivery.forward_sender_uin || "";
    // notify radios
    const box = $("#notify-modes");
    box.innerHTML = "";
    state.options.notify_modes.forEach((mode) => {
      const label = document.createElement("label");
      label.className = "radio" + (mode.value === cfg.delivery.notify_mode ? " active" : "");
      label.innerHTML = `<input type="radio" name="notify_mode" value="${mode.value}" ${mode.value === cfg.delivery.notify_mode ? "checked" : ""}/><span>${esc(mode.label)}<small>${esc(notifyHint(mode.value))}</small></span>`;
      label.addEventListener("change", () => $$(".radio", box).forEach((r) => r.classList.toggle("active", $("input", r).checked)));
      box.appendChild(label);
    });
    // seed_mode select uses plain numbering; strip the "1. " prefix look for these small enums
    ["#f-seed_mode", "#f-format", "#f-image_profile", "#f-send_mode"].forEach((sel) => $$("option", $(sel)).forEach((o) => { o.textContent = o.textContent.replace(/^\d+\.\s/, ""); }));
    applyCatalogToForm();
  }
  function notifyHint(mode) {
    return {
      images: "任务完成后自动把每一幕图片发回发起任务的会话（QQ 默认合并转发）",
      summary: "只发送一条完成摘要，之后用 /mio_get # 取件",
      export: "任务完成后自动用「默认导出格式 + 默认模板」导出并发送文件",
      silent: "不主动通知，只能用 /mio_status /mio_get 查询",
    }[mode] || "";
  }
  function applyCatalogToForm() {
    const cat = state.catalog, cfg = state.config;
    if (!cat || !cfg) return;
    fillSelect($("#f-channel_id"), cat.channels, cfg.defaults.channel_id, { blank: "（自动：优先 ComfyUI 通道）", suffix: (c) => ` · ${c.provider}` });
    fillSelect($("#f-workflow_id"), cat.workflows, cfg.defaults.workflow_id, { blank: "（自动：第一个工作流）" });
    fillSelect($("#f-preset_ids"), cat.presets, cfg.defaults.preset_ids, { suffix: (p) => ` · ${p.category === "scenes" ? "场景" : "角色"}` });
    fillSelect($("#f-layout_id"), cat.layouts, cfg.export.layout_id, { blank: "（自动：第一个模板）", suffix: (l) => ` · ${l.layout}` });
    renderLayoutPreview();
    // new-task form
    fillSelect($("#n-storyboard"), cat.storyboards, "", { suffix: (s) => ` · ${s.frameCount} 幕` });
    fillSelect($("#n-workflow"), cat.workflows, cfg.defaults.workflow_id, { blank: "（默认）" });
    fillSelect($("#n-presets"), cat.presets, cfg.defaults.preset_ids, { suffix: (p) => ` · ${p.category === "scenes" ? "场景" : "角色"}` });
    fillSelect($("#n-channel"), cat.channels, cfg.defaults.channel_id, { blank: "（默认）", suffix: (c) => ` · ${c.provider}` });
    renderCatalog();
  }
  function renderLayoutPreview() {
    const id = $("#f-layout_id").value;
    const item = (state.catalog.layouts || []).find((l) => l.id === id) || state.catalog.layouts[0];
    const box = $("#layout-preview");
    if (!item) { box.innerHTML = '<span>Mio 中没有导出模板</span>'; return; }
    const o = item.options || {};
    box.innerHTML = `<div class="swatches">${["accent", "background", "paper", "text"].map((k) => `<span class="swatch" style="background:${esc(o[k] || "#ccc")}" title="${k}"></span>`).join("")}</div><span><b>${esc(item.title)}</b> · ${esc(item.layout)}${item.builtin ? " · 内置" : ""}${item.description ? " — " + esc(item.description) : ""}</span>`;
  }
  function renderCatalog() {
    const cat = state.catalog;
    const groups = [
      ["workflows", "工作流 /mio_ls_wf", (w) => `${w.nodeCount} 节点`],
      ["storyboards", "分幕 / 分镜 /mio_ls_sb", (s) => `${s.frameCount} 幕`],
      ["presets", "预设 /mio_ls_ps", (p) => (p.category === "scenes" ? "场景" : "角色") + (p.entries || []).filter((e) => e.value && e.key !== "character_display_name").slice(0, 2).map((e) => ` · ${e.key}=${String(e.value).slice(0, 16)}`).join("")],
      ["channels", "图像通道 /mio_ls_ch", (c) => `${c.provider}${c.model ? " / " + c.model : ""}`],
      ["layouts", "导出模板 /mio_ls_tpl", (l) => l.layout],
    ];
    const defaults = cat.defaults || {};
    $("#catalog").innerHTML = groups.map(([kind, title, meta]) => {
      const items = cat[kind] || [];
      const def = defaults[kind];
      const isDef = (id) => Array.isArray(def) ? def.includes(id) : def === id;
      return `<div class="group"><h3>${esc(title)}（${items.length}）</h3>${items.length ? `<ol>${items.map((it) => `<li>${esc(it.title || it.id)}${isDef(it.id) ? '<span class="badge">默认</span>' : ""}<div class="meta">${esc(meta(it))}</div></li>`).join("")}</ol>` : '<div class="empty">无</div>'}</div>`;
    }).join("");
  }
  async function loadConfig() {
    const data = await get("config");
    state.options = data.options;
    applyConfig(data.config);
  }
  async function loadCatalog(force) {
    try {
      state.catalog = await get("catalog", force ? { force: 1 } : {});
      applyCatalogToForm();
    } catch (e) {
      $("#catalog").innerHTML = `<div class="empty">无法读取 Mio 资源：${esc(e.message)}</div>`;
    }
  }
  async function saveConfig(changes, btn) {
    busy(btn, true, "保存中…");
    try {
      const data = await post("config", { config: changes });
      applyConfig(data.config);
      toast("已保存", "ok");
      await loadStatus();
    } catch (e) { toast(e.message, "err"); }
    finally { busy(btn, false); }
  }

  // ------------------------------------------------------------ tasks
  function taskCard(t) {
    const pct = t.total ? Math.round((t.done / t.total) * 100) : 0;
    const label = (state.tasks.statusLabels || {})[t.statusKey] || t.statusKey;
    const ops = [];
    if (["running", "ready", "preparing"].includes(t.statusKey)) ops.push(`<button class="btn small" data-act="pause">暂停</button>`, `<button class="btn small" data-act="cancel">停止</button>`);
    if (["paused", "standby", "failed", "interrupted"].includes(t.statusKey)) ops.push(`<button class="btn small" data-act="resume">继续</button>`);
    if (t.done > 0) {
      ops.push(`<button class="btn small" data-dl="html">HTML</button>`, `<button class="btn small" data-dl="zip">ZIP</button>`, `<button class="btn small" data-dl="pdf">PDF</button>`);
      if (t.umo) ops.push(`<button class="btn small" data-send="export">发文件到会话</button>`, `<button class="btn small" data-send="images">发图片到会话</button>`);
    }
    ops.push(`<button class="btn small danger" data-act="remove">移除</button>`);
    const created = t.created_at ? new Date(t.created_at * 1000).toLocaleString() : "";
    return `<div class="task ${esc(t.statusKey)}" data-id="${esc(t.id)}" data-num="${t.num}">
      <div><span class="title">#${t.num} ${esc(t.title)}</span> <span class="state ${esc(t.statusKey)}">${esc(label)}</span></div>
      <div>${t.total ? `${t.done}/${t.total} 幕${t.failed ? `（${t.failed} 失败）` : ""}` : ""}</div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="meta">${esc(created)}${t.sender_name ? ` · ${esc(t.sender_name)}` : ""}${t.umo ? ` · ${esc(t.umo)}` : ""}${t.selection ? ` · ${esc([t.selection.workflowTitle, t.selection.storyboardTitle, (t.selection.presetTitles || []).join("+"), t.selection.channelTitle].filter(Boolean).join(" / "))}` : ""}${t.error ? `<br/><span style="color:var(--err)">${esc(t.error)}</span>` : ""}</div>
      <div class="ops" style="grid-column:1/-1">${ops.join("")}</div>
    </div>`;
  }
  async function loadTasks() {
    try {
      const data = await get("tasks", { limit: 50 });
      state.tasks = data;
      const box = $("#tasks");
      box.innerHTML = data.items.length ? data.items.map(taskCard).join("") : '<div class="empty">还没有任务。用 /mio_use 或上面的「新建任务」创建。</div>';
      if (data.others) box.insertAdjacentHTML("beforeend", `<div class="empty">Mio 队列中另有 ${data.others} 个非本插件创建的任务</div>`);
      const active = data.items.some((t) => ["running", "ready", "preparing"].includes(t.statusKey));
      clearTimeout(state.timer);
      if (active && $(".tab.active").dataset.tab === "tasks") state.timer = setTimeout(loadTasks, 4000);
    } catch (e) { $("#tasks").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  function wireTasks() {
    $("#tasks").addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button");
      if (!btn) return;
      const card = btn.closest(".task");
      const id = card.dataset.id;
      busy(btn, true);
      try {
        if (btn.dataset.act) {
          if (btn.dataset.act === "remove" && !confirm(`移除任务 #${card.dataset.num}？（Mio 图库中的画册保留）`)) return;
          const res = await post(`tasks/${encodeURIComponent(id)}/action`, { action: btn.dataset.act });
          toast(res.message || "完成", "ok");
          await loadTasks();
        } else if (btn.dataset.dl) {
          const layout = $("#f-layout_id").value;
          await bridge.download(`tasks/${encodeURIComponent(id)}/export`, { format: btn.dataset.dl, layout: layout || "" });
        } else if (btn.dataset.send) {
          const res = await post(`tasks/${encodeURIComponent(id)}/send`, { mode: btn.dataset.send });
          toast(res.message || "已发送", "ok");
        }
      } catch (e) { toast(e.message, "err"); }
      finally { busy(btn, false); }
    });
    $("#btn-refresh-tasks").addEventListener("click", loadTasks);
    $("#btn-new-task").addEventListener("click", async () => {
      $("#new-task").classList.toggle("hidden");
      try {
        const s = await get("sessions");
        $("#session-list").innerHTML = (s.items || []).map((x) => `<option value="${esc(x.umo)}">${esc(x.sender || "")}</option>`).join("");
      } catch (_) { /* optional */ }
    });
    $("#btn-create-task").addEventListener("click", async () => {
      const btn = $("#btn-create-task");
      busy(btn, true, "创建中…");
      try {
        const res = await post("tasks/create", {
          storyboard: $("#n-storyboard").value,
          workflow: $("#n-workflow").value || "0",
          presets: selectedValues($("#n-presets")),
          channel: $("#n-channel").value || "0",
          title: $("#n-title").value,
          umo: $("#n-umo").value,
        });
        toast(`任务 #${res.task.num} 已创建并开始`, "ok");
        $("#new-task").classList.add("hidden");
        await loadTasks();
      } catch (e) { toast(e.message, "err"); }
      finally { busy(btn, false); }
    });
  }

  // ------------------------------------------------------------- wire
  function wire() {
    wireTabs();
    wireTasks();
    $("#btn-token-eye").addEventListener("click", () => { const i = $("#f-api_token"); i.type = i.type === "password" ? "text" : "password"; });
    $("#btn-save-connect").addEventListener("click", (e) => saveConfig({ mio: { base_url: $("#f-base_url").value, api_token: $("#f-api_token").value, request_timeout: $("#f-request_timeout").value } }, e.currentTarget).then(() => loadCatalog(true)));
    $("#btn-test").addEventListener("click", async (e) => { busy(e.currentTarget, true, "测试中…"); await loadStatus(); busy(e.currentTarget, false); toast(state.status && state.status.ok ? "Mio 在线" : (state.status && state.status.error) || "失败", state.status && state.status.ok ? "ok" : "err"); });
    $("#btn-save-perm").addEventListener("click", (e) => saveConfig({ permissions: { admin_only: $("#f-admin_only").checked, allowed_sessions: $("#f-allowed_sessions").value } }, e.currentTarget));
    $("#btn-refresh-catalog").addEventListener("click", async (e) => { busy(e.currentTarget, true, "刷新中…"); await loadCatalog(true); busy(e.currentTarget, false); });
    $("#btn-save-defaults").addEventListener("click", (e) => saveConfig({ defaults: {
      channel_id: $("#f-channel_id").value, workflow_id: $("#f-workflow_id").value, preset_ids: selectedValues($("#f-preset_ids")).join(","),
      title_template: $("#f-title_template").value, concurrency: $("#f-concurrency").value, seed_mode: $("#f-seed_mode").value, seed: $("#f-seed").value,
    } }, e.currentTarget));
    $("#f-layout_id").addEventListener("change", renderLayoutPreview);
    $("#btn-save-export").addEventListener("click", (e) => saveConfig({
      export: { format: $("#f-format").value, layout_id: $("#f-layout_id").value, image_profile: $("#f-image_profile").value, theme_color: $("#f-theme_color").value, signature: $("#f-signature").value, show_captions: $("#f-show_captions").checked, show_prompts: $("#f-show_prompts").checked },
      delivery: { notify_mode: ($('input[name="notify_mode"]:checked') || {}).value || "summary", send_mode: $("#f-send_mode").value, max_images: $("#f-max_images").value, poll_interval: $("#f-poll_interval").value, include_captions: $("#f-include_captions").checked, forward_sender_name: $("#f-forward_sender_name").value, forward_sender_uin: $("#f-forward_sender_uin").value },
    }, e.currentTarget));
  }

  async function boot() {
    if (!bridge) {
      document.body.innerHTML = '<div class="empty" style="margin:40px">请在 AstrBot WebUI 的插件页面中打开（缺少 AstrBotPluginPage bridge）。</div>';
      return;
    }
    wire();
    try { await bridge.ready(); } catch (_) { /* fall through */ }
    try { await loadConfig(); } catch (e) { toast(`读取配置失败：${e.message}`, "err"); }
    await loadStatus();
    await loadCatalog(false);
  }
  boot();
})();
