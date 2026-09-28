// 与后端交互的小工具：client_id、JSON 请求、限流提示、SSE 解析、复制。
// 接口与旧前端（app/static/app.js）完全一致，后端一个都不改。

function makeClientId(): string | null {
  try {
    let id = localStorage.getItem("qp_client_id");
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/[^0-9a-z]/gi, "");
      localStorage.setItem("qp_client_id", id);
    }
    return id;
  } catch {
    return null; // 存储不可用：不带偏好
  }
}

export const clientId = makeClientId();

export function cidParam(): string {
  return "client_id=" + encodeURIComponent(clientId ?? "");
}

/** 在 URL 上补 client_id */
export function withCid(url: string): string {
  return url + (url.includes("?") ? "&" : "?") + cidParam();
}

export type ApiResult<T = any> = { ok: boolean; status: number; body: T & { detail?: any } };

/** 被限流（429）时的友好提示：带上服务器给的 Retry-After 等待时间 */
export function rateLimitText(resp: Response, body: any): string {
  const raw = body && typeof body.detail === "string" ? body.detail : "";
  const base = raw && !/请求过于频繁/.test(raw) ? raw.replace(/[。.]$/, "") : "操作太频繁了";
  const secs = Math.ceil(Number(resp.headers.get("Retry-After")) || Number(body && body.retry_after) || 0);
  if (/秒后|明天/.test(base)) return base;
  if (secs <= 0) return base + "，请稍后再试";
  if (secs >= 3600) return base + "，明天再试";
  return base + "（" + (secs < 60 ? secs + " 秒" : Math.ceil(secs / 60) + " 分钟") + "后可以再试）";
}

/** JSON 请求：返回 { ok, status, body }，网络错误时 status 为 0；429 时 body.detail 换成友好提示 */
export async function api<T = any>(url: string, method = "GET", payload?: unknown, headers?: Record<string, string>): Promise<ApiResult<T>> {
  const opts: RequestInit = { method, headers: { ...(headers || {}) } };
  if (payload !== undefined) {
    (opts.headers as Record<string, string>)["Content-Type"] = "application/json";
    opts.body = JSON.stringify(payload);
  }
  try {
    const resp = await fetch(url, opts);
    const body = await resp.json().catch(() => ({}));
    if (resp.status === 429) body.detail = rateLimitText(resp, body);
    return { ok: resp.ok, status: resp.status, body };
  } catch {
    return { ok: false, status: 0, body: { detail: "网络连接失败，请检查网络后重试" } as any };
  }
}

/** 订阅接口的小封装（自动带 client_id） */
export function subApi<T = any>(path: string, method = "GET", payload?: unknown): Promise<ApiResult<T>> {
  return api<T>(withCid("/api/subscriptions" + path), method, payload);
}

/** 自己解析 SSE（而不是 EventSource），这样能拿到 401/403/429 的状态码和提示 */
export async function readSSE(resp: Response, onEvent: (event: string, data: string) => void): Promise<void> {
  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) onEvent(event, data.join("\n"));
    }
  }
}

export function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // 局域网 http 访问时没有 clipboard API，退回老办法
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (ok) resolve();
    else reject(new Error("copy failed"));
  });
}

/** 本地存储读写，无痕模式等不可用时静默忽略 */
export const store = {
  get<T>(key: string, fallback: T): T {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : (JSON.parse(v) as T);
    } catch {
      return fallback;
    }
  },
  getRaw(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: unknown) {
    try {
      localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
    } catch {
      /* 忽略 */
    }
  },
};
