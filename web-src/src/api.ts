import { createDemoApi } from "./demo";

export interface Api {
  readonly demo: boolean;
  get<T = any>(endpoint: string, params?: object): Promise<T>;
  post<T = any>(endpoint: string, body?: object): Promise<T>;
  download(endpoint: string, params?: object, filename?: string): Promise<{ filename: string }>;
}

type Bridge = Pick<Api, "get" | "post" | "download"> & {
  apiGet<T>(endpoint: string, params?: object): Promise<T>;
  apiPost<T>(endpoint: string, body?: object): Promise<T>;
};

export function createApi(): Api {
  const bridge = (window as Window & { AstrBotPluginPage?: Partial<Bridge> }).AstrBotPluginPage;
  if (
    new URLSearchParams(window.location.search).get("demo") === "1" ||
    typeof bridge?.apiGet !== "function" ||
    typeof bridge?.apiPost !== "function" ||
    typeof bridge?.download !== "function"
  ) {
    return createDemoApi();
  }
  const live = bridge as Pick<Bridge, "apiGet" | "apiPost" | "download">;
  return {
    demo: false,
    get: <T>(endpoint: string, params?: object) => live.apiGet<T>(endpoint, params),
    post: <T>(endpoint: string, body?: object) => live.apiPost<T>(endpoint, body),
    download: (endpoint, params, filename) => live.download(endpoint, params, filename),
  };
}
