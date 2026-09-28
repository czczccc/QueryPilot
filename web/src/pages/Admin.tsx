import { useEffect, useRef, useState } from "react";
import { Button, Card, Input, Tabs, TabsContent, TabsList, TabsTrigger } from "@cz/design-system";
import { useToast } from "../lib/toast";
import { Bans, Invites, Overview, Usage, Users } from "../features/admin/panels";
import { adminApi, AskProvider, AuthError, getToken, setToken } from "../features/admin/shared";

const TABS = [
  ["overview", "概览"],
  ["users", "账号"],
  ["usage", "每日用量"],
  ["invites", "邀请码"],
  ["bans", "封禁 IP"],
] as const;

/** #/admin：站长后台（口令只存在当前标签页） */
export function AdminPage() {
  const toast = useToast();
  const [unlocked, setUnlocked] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [input, setInput] = useState("");
  const [tab, setTab] = useState("overview");
  const [version, setVersion] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const lock = (message: string) => {
    setToken("");
    setUnlocked(false);
    setError(message);
    setInput("");
    setTimeout(() => inputRef.current?.focus());
  };

  const onError = (e: unknown) => {
    if (e instanceof AuthError) lock(e.message);
    else toast((e as Error)?.message || "请求失败", "error");
  };

  const unlock = async () => {
    setChecking(true);
    try {
      await adminApi("/overview"); // 口令不对会在这里抛 AuthError
      setError("");
      setTab("overview");
      setUnlocked(true);
    } catch (e) {
      onError(e);
    }
    setChecking(false);
  };

  useEffect(() => {
    if (getToken()) unlock();
    else inputRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!unlocked) {
    return (
      <div className="mx-auto flex w-full max-w-md flex-col gap-6 pt-8">
        <Card className="flex flex-col gap-4 p-6">
          <div className="flex flex-col gap-1">
            <h1 className="text-title text-fg">站长后台</h1>
            <p className="text-footnote text-fg-muted">
              输入服务器 .env 里的 <code className="rounded-sm bg-sunken px-1 font-mono">ADMIN_TOKEN</code>。口令只保存在当前标签页，关掉就会忘记。
            </p>
          </div>
          <form
            className="flex gap-2"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              const t = input.trim();
              if (!t) return;
              setToken(t);
              unlock();
            }}
          >
            <Input
              ref={inputRef}
              type="password"
              autoComplete="current-password"
              placeholder="管理口令"
              aria-label="管理口令"
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
            <Button type="submit" loading={checking} disabled={checking} className="shrink-0">
              进入
            </Button>
          </form>
          {error && (
            <p role="alert" className="text-footnote text-danger-fg">
              {error}
            </p>
          )}
        </Card>
      </div>
    );
  }

  return (
    <AskProvider>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-heading text-fg">站长后台</h1>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => setVersion((v) => v + 1)}>
              刷新
            </Button>
            <Button variant="ghost" size="sm" onClick={() => lock("")}>
              退出后台
            </Button>
          </div>
        </header>
        <Tabs value={tab} onValueChange={setTab}>
          <div className="overflow-x-auto">
            <TabsList aria-label="后台分区">
              {TABS.map(([v, l]) => (
                <TabsTrigger key={v} value={v}>
                  {l}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value="overview">{tab === "overview" && <Overview onError={onError} version={version} />}</TabsContent>
          <TabsContent value="users">{tab === "users" && <Users onError={onError} version={version} />}</TabsContent>
          <TabsContent value="usage">{tab === "usage" && <Usage onError={onError} version={version} />}</TabsContent>
          <TabsContent value="invites">{tab === "invites" && <Invites onError={onError} version={version} />}</TabsContent>
          <TabsContent value="bans">{tab === "bans" && <Bans onError={onError} version={version} />}</TabsContent>
        </Tabs>
      </div>
    </AskProvider>
  );
}
