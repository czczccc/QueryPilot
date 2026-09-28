// 订阅数据：SubsSync 负责拉取（loadSubs），订阅页和弹窗通过 useSubsData 读取。
// 结构与旧前端（app/static/app.js 的 loadSubs）一致：订阅、提醒（含已读）、历史、系列设置。
import { useSyncExternalStore } from "react";
import { cidParam, clientId, store } from "../../lib/api";
import { navigate } from "../../lib/app-state";

export type Sub = Record<string, any> & {
  id: number;
  resource: string;
  media?: string;
  state?: string;
};
export type Note = Record<string, any> & { id?: number; ts: number; read?: boolean };
export type HistoryEntry = Record<string, any> & { id: number; resource: string };
export type CollectionInfo = Record<string, any> & { collection_id?: string | number; auto_join?: boolean; name?: string };

export type SubsData = {
  loaded: boolean;
  /** 服务器没开订阅 / 加载失败时的说明 */
  offText: string;
  enabled: boolean;
  subs: Sub[];
  notes: Note[];
  history: HistoryEntry[];
  collections: CollectionInfo[];
};

let data: SubsData = { loaded: false, offText: "正在加载订阅…", enabled: false, subs: [], notes: [], history: [], collections: [] };
const listeners = new Set<() => void>();

function set(patch: Partial<SubsData>) {
  data = { ...data, ...patch };
  listeners.forEach((l) => l());
}

export function useSubsData(): SubsData {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => data,
  );
}

/** 本地改提醒（标为已读 / 删除），界面立即更新 */
export function setNotes(notes: Note[]) {
  set({ notes });
}

async function getJson(url: string): Promise<Response | null> {
  try {
    return await fetch(url);
  } catch {
    return null;
  }
}

let inflight: Promise<void> | null = null;

export function loadSubs(): Promise<void> {
  if (!clientId) return Promise.resolve();
  if (inflight) return inflight.then(() => loadSubs());
  inflight = (async () => {
    try {
      const [subsResp, notifResp, histResp, collResp] = await Promise.all([
        fetch("/api/subscriptions?" + cidParam()),
        fetch("/api/notifications?include_read=true&" + cidParam()),
        getJson("/api/subscriptions/history?" + cidParam()),
        getJson("/api/subscriptions/collections?" + cidParam()),
      ]);
      if (!subsResp.ok || !notifResp.ok) {
        // 记忆未开启：不显示订阅
        if (!data.enabled) set({ loaded: true, offText: "服务器没有开启订阅功能。" });
        return;
      }
      const subs = await subsResp.json();
      const history = histResp && histResp.ok ? await histResp.json().catch(() => []) : [];
      const collections = collResp && collResp.ok ? await collResp.json().catch(() => []) : [];
      const notes = await notifResp.json();
      set({
        loaded: true,
        enabled: true,
        offText: "",
        subs: Array.isArray(subs) ? subs : [],
        history: Array.isArray(history) ? history : [],
        collections: Array.isArray(collections) ? collections : [],
        notes: Array.isArray(notes) ? notes : [],
      });
    } catch {
      // 网络问题：下次再试
      if (!data.enabled) set({ loaded: true, offText: "订阅加载失败，请稍后刷新重试。" });
    }
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}

/** 刷新一次，并在后台第一次检查完成后（约 45 秒）再刷新 */
export function loadSubsTwice() {
  loadSubs();
  setTimeout(loadSubs, 45000);
}

// ---- 标签页 ----
export const TABS = ["tv", "movie", "calendar", "history"] as const;
export type Tab = (typeof TABS)[number];
// 追剧日历暂时不开放
export const HIDDEN_TABS = new Set<string>(["calendar"]);

export function validTab(t: string | null | undefined): t is Tab {
  return !!t && (TABS as readonly string[]).includes(t) && !HIDDEN_TABS.has(t);
}

export function savedTab(): Tab {
  const t = store.getRaw("qp_subs_tab");
  return validTab(t) ? t : "tv";
}

/** 订阅成功后切到对应标签页（旧版：subsTab = subMedia(sub)） */
export function switchTab(tab: Tab) {
  store.set("qp_subs_tab", tab);
  if (/^#\/?subs/.test(location.hash)) navigate("#/subs/" + tab, true);
}

// ---- 小工具 ----
/** 没识别出类型的关键词订阅归到「剧集」 */
export function subMedia(sub: { media?: string }): "tv" | "movie" {
  return sub.media === "movie" ? "movie" : "tv";
}

/** 片名末尾的全角波浪号去掉再拼季 */
export function tidyTitle(t: unknown): string {
  const m = String(t || "")
    .trim()
    .match(/^(.*?)\s*(第\s*\d+\s*季)?$/)!;
  const name = m[1].replace(/[\s～~〜]+$/, "");
  return name && m[2] ? name + " " + m[2] : name || m[2] || "";
}

export function collectionName(name: unknown): string {
  return tidyTitle(String(name || "").replace(/[（(]?系列[）)]?$|\s*Collection$/i, ""));
}
