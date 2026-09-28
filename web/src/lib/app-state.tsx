import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/** 跨页面共享的少量状态：订阅功能是否开启、未读提醒数 */
type AppState = {
  subsEnabled: boolean;
  setSubsEnabled: (v: boolean) => void;
  unread: number;
  setUnread: (n: number) => void;
  /** 订阅数据变了（新订阅、登录状态变化等），订阅页据此重新加载 */
  subsVersion: number;
  bumpSubs: () => void;
};

const Ctx = createContext<AppState>(null as unknown as AppState);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [subsEnabled, setSubsEnabled] = useState(false);
  const [unread, setUnread] = useState(0);
  const [subsVersion, setSubsVersion] = useState(0);
  return (
    <Ctx.Provider value={{ subsEnabled, setSubsEnabled, unread, setUnread, subsVersion, bumpSubs: () => setSubsVersion((v) => v + 1) }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAppState = () => useContext(Ctx);

// ---- 路由：#/ 发现 · #/subs/<tab> 我的订阅 · #/settings 偏好设置 · #/admin 后台 ----
export type Route = { view: "discover" | "subs" | "settings" | "admin"; tab?: string };

function parse(): Route {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const v = parts[0];
  if (v === "subs" || v === "settings" || v === "admin") return { view: v, tab: parts[1] || undefined };
  return { view: "discover" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(parse);
  useEffect(() => {
    const on = () => {
      setRoute(parse());
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function navigate(hash: string, replace = false) {
  if (replace) {
    history.replaceState(null, "", hash);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else location.hash = hash;
}
