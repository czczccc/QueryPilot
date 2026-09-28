import { useEffect, useRef, useState } from "react";
import { Button, Card, Label, Switch, cn, fieldClasses } from "@cz/design-system";
import { api, clientId, withCid } from "../lib/api";
import { PrivacyDialogs, type PrivacyView } from "../features/settings/PrivacyDialogs";

type Prefs = { min_resolution: string; prefer_subtitle: boolean; prefer_hdr: boolean };

/** #/settings：搜索偏好 + 账户与隐私 */
export function SettingsPage() {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [saved, setSaved] = useState("");
  const [privacy, setPrivacy] = useState<PrivacyView>(null);
  const savedTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!clientId) return;
    api(withCid("/api/prefs")).then((r) => {
      if (!r.ok) return; // 记忆未开启：不显示偏好设置
      setPrefs({ min_resolution: r.body.min_resolution || "", prefer_subtitle: !!r.body.prefer_subtitle, prefer_hdr: !!r.body.prefer_hdr });
    });
  }, []);

  async function update(patch: Partial<Prefs>) {
    const next = { ...prefs!, ...patch };
    setPrefs(next);
    const r = await api(withCid("/api/prefs"), "PUT", {
      min_resolution: next.min_resolution || null,
      prefer_subtitle: next.prefer_subtitle,
      prefer_hdr: next.prefer_hdr,
    });
    setSaved(r.ok ? "✓ 已保存" : "保存失败");
    clearTimeout(savedTimer.current);
    savedTimer.current = window.setTimeout(() => setSaved(""), 2000);
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-heading text-fg">偏好设置</h1>
        <p className="text-body text-fg-muted">搜索偏好和你的数据。</p>
      </header>

      {prefs && (
        <Card className="flex flex-col gap-5 p-6">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="text-title-sm text-fg">搜索偏好</h2>
            <span aria-live="polite" className={cn("text-footnote", saved === "保存失败" ? "text-danger-fg" : "text-success-fg")}>
              {saved}
            </span>
          </div>
          <div className="flex flex-col gap-4">
            <Row label="默认最低清晰度" htmlFor="pref-min-res">
              <select
                id="pref-min-res"
                className={cn(fieldClasses, "h-control-md w-32 px-3 text-body")}
                value={prefs.min_resolution}
                onChange={(e) => update({ min_resolution: e.target.value })}
              >
                <option value="">不限</option>
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
                <option value="2160p">4K</option>
              </select>
            </Row>
            <Row label="优先带字幕" htmlFor="pref-sub">
              <Switch id="pref-sub" checked={prefs.prefer_subtitle} onCheckedChange={(v) => update({ prefer_subtitle: v })} />
            </Row>
            <Row label="优先 HDR" htmlFor="pref-hdr">
              <Switch id="pref-hdr" checked={prefs.prefer_hdr} onCheckedChange={(v) => update({ prefer_hdr: v })} />
            </Row>
          </div>
          <p className="text-footnote text-fg-subtle">agent 会把默认清晰度当作搜索目标，偏好的字幕/HDR 资源排在前面。偏好保存在服务器上，对应当前浏览器。</p>
        </Card>
      )}

      <Card className="flex flex-col gap-4 p-6">
        <h2 className="text-title-sm text-fg">账户与隐私</h2>
        <p className="text-footnote text-fg-muted">登录夸克和退出登录在右上角的账户按钮里。转存会保存到你自己的夸克网盘。</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => setPrivacy("privacy")}>
            查看隐私说明
          </Button>
          <Button variant="secondary" size="sm" className="text-danger-fg" onClick={() => setPrivacy("delete")}>
            删除我的数据
          </Button>
        </div>
      </Card>

      <PrivacyDialogs view={privacy} onChange={setPrivacy} />
    </div>
  );
}

function Row({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <Label htmlFor={htmlFor} className="font-normal">
        {label}
      </Label>
      {children}
    </div>
  );
}
