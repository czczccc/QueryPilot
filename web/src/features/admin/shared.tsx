import { createContext, useContext, useState, type ReactNode } from "react";
import { Badge, Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input, cn } from "@cz/design-system";

// 站长后台：所有请求带 X-Admin-Token；口令只存在 sessionStorage（关掉标签页就忘）
const TOKEN_KEY = "qp_admin_token";
let memToken = "";

export function getToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return memToken;
  }
}

export function setToken(t: string) {
  memToken = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 存储不可用：只放内存 */
  }
}

export class AuthError extends Error {}

export async function adminApi<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Admin-Token": getToken() };
  if (opts.body) headers["Content-Type"] = "application/json";
  const resp = await fetch("/api/admin" + path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (resp.status === 401) throw new AuthError(data.detail || "管理口令不正确");
  if (resp.status === 404 && path === "/overview") throw new AuthError("服务器没有配置 ADMIN_TOKEN，后台未开启");
  if (!resp.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败（" + resp.status + "）");
  return data;
}

// ---- 格式化 ----
const nf = new Intl.NumberFormat("zh-CN");
export const num = (n: number | null | undefined) => (n == null ? "-" : nf.format(n));
export function tokens(n: number | null | undefined): string {
  if (!n) return "0";
  if (n >= 1e6) return "约 " + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return "约 " + (n / 1e3).toFixed(1) + "k";
  return "约 " + n;
}
export function ts(sec: number | null | undefined): string {
  if (!sec) return "-";
  const d = new Date(sec * 1000);
  const pad = (x: number) => String(x).padStart(2, "0");
  return d.getMonth() + 1 + "/" + d.getDate() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
}
export function subjectLabel(row: { subject?: string; nickname?: string | null }): string {
  const s = row.subject || "";
  if (s === "system") return "后台任务";
  if (s.startsWith("ip:")) return "未登录 " + s.slice(3);
  return row.nickname || s.replace(/^user:/, "");
}
export function todayStr(): string {
  const d = new Date();
  const pad = (x: number) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

// ---- 表格 ----
export type Col<T> = { label: string; num?: boolean; key?: keyof T; render?: (row: T) => ReactNode };

export function DataTable<T>({ cols, rows, empty }: { cols: Col<T>[]; rows: T[]; empty?: string }) {
  return (
    <div className="-mx-1 overflow-x-auto px-1">
      <table className="w-full border-collapse text-footnote">
        <thead>
          <tr className="border-b border-line-subtle">
            {cols.map((c) => (
              <th key={c.label} className={cn("px-3 py-2 font-medium whitespace-nowrap text-fg-subtle", c.num ? "text-right" : "text-left")}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {!rows.length ? (
            <tr>
              <td colSpan={cols.length} className="px-3 py-10 text-center text-fg-subtle">
                {empty || "暂无数据"}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} className="border-b border-line-subtle last:border-0 hover:bg-hover">
                {cols.map((c) => {
                  const v = c.render ? c.render(row) : c.key ? (row[c.key] as ReactNode) : null;
                  return (
                    <td key={c.label} className={cn("px-3 py-2.5 align-middle text-fg", c.num && "text-right tabular-nums whitespace-nowrap")}>
                      {v == null || v === "" ? (c.render ? v : "-") : v}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export function Pill({ tone, children }: { tone?: "success" | "danger" | "neutral"; children: ReactNode }) {
  return (
    <Badge tone={tone ?? "neutral"} className="ml-1.5 max-w-60 truncate">
      {children}
    </Badge>
  );
}

export function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card border border-line-subtle bg-surface p-4">
      <span className="text-caption text-fg-subtle">{label}</span>
      <span className="truncate text-title text-fg tabular-nums">{value}</span>
      {sub && <span className="truncate text-caption text-fg-muted">{sub}</span>}
    </div>
  );
}

// ---- 代替 window.prompt / confirm 的弹窗 ----
type Ask = { title: string; description?: string; input?: { value: string; placeholder?: string }; danger?: boolean; ok?: string; resolve: (v: string | null) => void };
type AskFn = (a: Omit<Ask, "resolve">) => Promise<string | null>;
const AskCtx = createContext<AskFn>(async () => null);
export const useAsk = () => useContext(AskCtx);

export function AskProvider({ children }: { children: ReactNode }) {
  const [ask, setAsk] = useState<Ask | null>(null);
  const [value, setValue] = useState("");
  const fn: AskFn = (a) =>
    new Promise((resolve) => {
      setValue(a.input?.value ?? "");
      setAsk({ ...a, resolve });
    });
  const end = (v: string | null) => {
    ask?.resolve(v);
    setAsk(null);
  };
  return (
    <AskCtx.Provider value={fn}>
      {children}
      <Dialog open={!!ask} onOpenChange={(o) => !o && end(null)}>
        <DialogContent>
          {ask && (
            <form
              className="flex flex-col gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                end(ask.input ? value : "");
              }}
            >
              <DialogHeader>
                <DialogTitle>{ask.title}</DialogTitle>
                {ask.description && <DialogDescription className="whitespace-pre-line">{ask.description}</DialogDescription>}
              </DialogHeader>
              {ask.input && <Input autoFocus value={value} placeholder={ask.input.placeholder} onChange={(e) => setValue(e.target.value)} />}
              <DialogFooter>
                <Button type="button" variant="secondary" onClick={() => end(null)}>
                  取消
                </Button>
                <Button type="submit" variant={ask.danger ? "danger" : "primary"}>
                  {ask.ok || "确定"}
                </Button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </AskCtx.Provider>
  );
}
