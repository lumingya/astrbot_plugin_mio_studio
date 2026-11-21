export type ViewName =
  | 'dashboard' | 'studio' | 'tasks' | 'gallery'
  | 'catalog' | 'delivery' | 'governance' | 'commands';

export interface RouteDetail { tab?: string; id?: string; }
export type ToastKind = "success" | "error" | "info";
export type Params = Record<string, string | number | boolean | undefined | null>;

export interface PanelApi {
  demo: boolean;
  get<T = any>(endpoint: string, params?: Params): Promise<T>;
  post<T = any>(endpoint: string, body?: Record<string, unknown>): Promise<T>;
  download(endpoint: string, params?: Params, filename?: string): Promise<unknown>;
}

export interface DrawerOptions {
  title: string;
  subtitle?: string;
  content: string | HTMLElement;
  wide?: boolean;
}

export interface ViewContext {
  mount: HTMLElement;
  api: PanelApi;
  instance: string;
  route: RouteDetail;
  goto(view: ViewName, detail?: RouteDetail): void;
  toast(message: string, kind?: ToastKind): void;
  drawer(options: DrawerOptions): HTMLElement;
  closeDrawer(): void;
  lightbox(src: string, caption?: string): void;
  refresh(): Promise<void>;
}

const ENTITIES: Record<string, string> = {
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
};
export const esc = (value: unknown): string =>
  String(value ?? "").replace(/[&<>"']/g, (match) => ENTITIES[match]);

const ICONS: Record<string, string> = {
  dashboard: '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  studio: '<path d="m13 7 4 4M3 21l3.7-.8L19 7.9a2.8 2.8 0 0 0 0-4 2.8 2.8 0 0 0-4 0L2.8 16.3 3 21Z"/><path d="M12 21h9"/>',
  tasks: '<rect width="16" height="17" x="4" y="4" rx="2"/><path d="M9 3h6v3H9zM8 11h8m-8 4h8m-8 4h4"/>',
  gallery: '<rect width="17" height="15" x="4" y="3" rx="2"/><path d="M2 8v10a4 4 0 0 0 4 4h11M4 16l6-6 4 4 2-2 5 5"/><circle cx="16.5" cy="7.5" r="1"/>',
  catalog: '<path d="M4 6h16M6 6l1-3h10l1 3M3 6v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6M8 11h8m-8 4h5"/>',
  delivery: '<path d="m22 2-7 20-4-9-9-4 20-7Zm0 0L11 13"/>',
  governance: '<path d="M12 22s7-3.6 7-10V5l-7-3-7 3v7c0 6.4 7 10 7 10Z"/><path d="m9.5 12 1.7 1.7 3.5-3.7"/>',
  commands: '<rect width="18" height="16" x="3" y="4" rx="2"/><path d="m7 9 3 3-3 3m6 0h4"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  external: '<path d="M14 4h6v6m0-6-8 8m6 1v6H5V6h6"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8m0-5v5h-5m5 4a9 9 0 0 1-15 6.7L3 16m0 5v-5h5"/>',
  moon: '<path d="M20 15.7A8.5 8.5 0 0 1 8.3 4 8.5 8.5 0 1 0 20 15.7Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M19 5l-1.4 1.4"/>',
  close: '<path d="m5 5 14 14M19 5 5 19"/>',
  book: '<path d="M3 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-4H3V4Zm18 0h-6a3 3 0 0 0-3 3v14a4 4 0 0 1 4-4h5V4Z"/>',
  spark: '<path d="M12 2c.7 6.7 3.3 9.3 10 10-6.7.7-9.3 3.3-10 10-.7-6.7-3.3-9.3-10-10 6.7-.7 9.3-3.3 10-10Z"/>',
  image: '<rect width="18" height="16" x="3" y="4" rx="2"/><path d="m3 17 6-6 4 4 2-2 6 6"/><circle cx="16" cy="8" r="1"/>',
  grid: '<path d="M3 7h18M3 12h18M3 17h18M8 4v16m8-16v16"/>',
  images: '<rect width="15" height="13" x="5" y="4" rx="2"/><path d="M2 8v9a3 3 0 0 0 3 3h11m-10-6 4-4 3 3 2-2 5 4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  server: '<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><path d="M7 7h.01M7 17h.01"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  search: '<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
  warning: '<path d="m12 3 10 17H2L12 3Zm0 6v4m0 3h.01"/>',
};

export const icon = (name: string, size = 18): string =>
  `<svg aria-hidden="true" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] ?? ICONS.spark}</svg>`;

export function empty(title: string, text = ""): string {
  return `<div class="empty"><span class="empty-icon">${icon("book", 23)}</span><strong>${esc(title)}</strong>${text ? `<p>${esc(text)}</p>` : ""}</div>`;
}

export function heading(kicker: string, title: string, description = "", actions = ""): string {
  return `<header class="view-heading"><div><p class="eyebrow">${esc(kicker)}</p><h1>${esc(title)}</h1>${description ? `<p class="lede">${esc(description)}</p>` : ""}</div>${actions ? `<div class="cluster action-bar">${actions}</div>` : ""}</header>`;
}

export function tip(text: string): string {
  return `<span tabindex="0" class="help-tip" title="${esc(text)}" aria-label="${esc(text)}">?</span>`;
}

export function options(
  rows: ReadonlyArray<{ id: string; title?: string; name?: string; label?: string }>,
  value?: string,
  placeholder?: string,
): string {
  return (placeholder != null ? `<option value="">${esc(placeholder)}</option>` : "")
    + rows.map((row) => `<option value="${esc(row.id)}"${row.id === value ? " selected" : ""}>${esc(row.title || row.name || row.label || row.id)}</option>`).join("");
}
