import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";

/** /api/me 的配额信息（搜索 SSE 的 quota 事件也是这个结构） */
export type Quota = {
  ai: boolean;
  reason?: string | null;
  message?: string | null;
  used?: number;
  limit?: number;
  remaining?: number | null;
  logged_in?: boolean;
  login_required?: boolean;
  searches_remaining?: number | null;
};

export type Me = {
  login: boolean; // 服务器开放了扫码登录
  invite_required: boolean;
  logged_in: boolean;
  nickname: string | null;
  banned: string | null;
  quota: Quota | null;
};

/** /api/save/status */
export type SaveStatus = { enabled: boolean; login: boolean; logged_in: boolean; nickname?: string | null; token_mode: boolean };

type MeCtx = {
  me: Me;
  save: SaveStatus;
  reload: () => Promise<void>;
  setQuota: (q: Quota | null) => void;
  setBanned: (text: string) => void;
  /** 打开扫码登录；成功返回 true。reason 显示在标题下面 */
  login: (reason?: string) => Promise<boolean>;
  logout: () => Promise<void>;
};

const EMPTY_ME: Me = { login: false, invite_required: false, logged_in: false, nickname: null, banned: null, quota: null };
const EMPTY_SAVE: SaveStatus = { enabled: false, login: false, logged_in: false, token_mode: false };

const Ctx = createContext<MeCtx>(null as unknown as MeCtx);

/** 登录弹窗的请求：由 <QuarkLoginDialog> 读取并在结束时 resolve */
export type LoginRequest = { reason?: string; resolve: (ok: boolean) => void } | null;
const LoginCtx = createContext<{ request: LoginRequest; finish: (ok: boolean) => void }>({ request: null, finish: () => {} });

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me>(EMPTY_ME);
  const [save, setSave] = useState<SaveStatus>(EMPTY_SAVE);
  const [request, setRequest] = useState<LoginRequest>(null);
  const reqRef = useRef<LoginRequest>(null);

  const reload = useCallback(async () => {
    const [m, s] = await Promise.all([api<Me>("/api/me"), api<SaveStatus>("/api/save/status")]);
    if (m.ok) setMe({ ...EMPTY_ME, ...m.body });
    if (s.ok) setSave({ ...EMPTY_SAVE, ...s.body });
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const login = useCallback((reason?: string) => {
    if (reqRef.current) reqRef.current.resolve(false);
    return new Promise<boolean>((resolve) => {
      const r = { reason, resolve };
      reqRef.current = r;
      setRequest(r);
    });
  }, []);

  const finish = useCallback(
    (ok: boolean) => {
      const r = reqRef.current;
      reqRef.current = null;
      setRequest(null);
      if (ok) reload();
      r?.resolve(ok);
    },
    [reload],
  );

  const logout = useCallback(async () => {
    const r = await api("/api/quark/logout", "POST");
    if (!r.ok) throw new Error("logout failed");
    await reload();
  }, [reload]);

  const value: MeCtx = {
    me,
    save,
    reload,
    setQuota: (q) => setMe((m) => ({ ...m, quota: q })),
    setBanned: (text) => setMe((m) => ({ ...m, banned: text })),
    login,
    logout,
  };
  return (
    <Ctx.Provider value={value}>
      <LoginCtx.Provider value={{ request, finish }}>{children}</LoginCtx.Provider>
    </Ctx.Provider>
  );
}

export const useMe = () => useContext(Ctx);
export const useLoginRequest = () => useContext(LoginCtx);
