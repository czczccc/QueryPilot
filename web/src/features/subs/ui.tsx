// 订阅模块的小部件：海报、原生下拉框、规则字段、需要登录时的引导
import { useCallback, useState, type ComponentProps, type ReactNode } from "react";
import { cn, fieldClasses, Switch, Tooltip } from "@cz/design-system";
import type { ApiResult } from "../../lib/api";
import { useMe } from "../../lib/me";
import { useToast } from "../../lib/toast";

export function Poster({ src, title, size = "md" }: { src?: string | null; title?: string; size?: "tiny" | "sm" | "md" }) {
  const [broken, setBroken] = useState(false);
  const cls = {
    tiny: "h-8 w-6 rounded-xs text-caption",
    sm: "h-14 w-10 rounded-sm text-footnote",
    md: "h-24 w-16 rounded-md text-title-sm max-sm:h-20 max-sm:w-14",
  }[size];
  if (!src || broken) {
    const ch = (title || "?").replace(/[《》\s]/g, "").slice(0, 1) || "?";
    return (
      <span aria-hidden className={cn("grid shrink-0 place-items-center bg-sunken font-medium text-fg-subtle", cls)}>
        {ch}
      </span>
    );
  }
  return (
    <span aria-hidden className={cn("shrink-0 overflow-hidden bg-sunken", cls)}>
      <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} className="size-full object-cover" />
    </span>
  );
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(fieldClasses, "h-control-md cursor-pointer px-2.5 text-body", className)} {...props} />;
}

/** 带说明的开关：整行可点 */
export function SwitchRow({
  checked,
  onChange,
  disabled,
  children,
  tip,
  className,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  children: ReactNode;
  tip?: string;
  className?: string;
}) {
  const row = (
    <label className={cn("inline-flex cursor-pointer items-center gap-2 text-footnote text-fg-muted select-none", disabled && "cursor-not-allowed opacity-60", className)}>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
      <span>{children}</span>
    </label>
  );
  return tip ? <Tooltip content={<span className="max-w-64">{tip}</span>}>{row}</Tooltip> : row;
}

/** 需要登录的操作失败时引导扫码；扫码成功返回 true（调用方可重试） */
export function useLoginIfNeeded() {
  const { me, login } = useMe();
  const toast = useToast();
  return useCallback(
    async (res: ApiResult, why: string): Promise<boolean> => {
      if (res.status === 401 && me.login) return login(res.body.detail || why);
      toast(res.body.detail || "操作失败", "error");
      return false;
    },
    [me.login, login, toast],
  );
}

export const FAIL: ApiResult = { ok: false, status: 0, body: {} };

// ---- 规则字段（订阅设置 / 新订阅的「更多规则」） ----
type RuleSub = Record<string, any> | null;
export type Rule = {
  key: string;
  label: string;
  type: "select" | "text" | "number" | "switch";
  options?: [string, string][];
  placeholder?: string;
  tvOnly?: boolean;
  editOnly?: boolean;
  min?: number;
  empty?: string;
  text?: string;
  hint?: (sub: Record<string, any>) => string;
  disabled?: (sub: Record<string, any>) => boolean;
};

export const SUB_RULES: Rule[] = [
  { key: "resolution", label: "清晰度不低于", type: "select", options: [["", "不限"], ["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"], ["SD", "标清"]] },
  { key: "include", label: "必须包含", type: "text", placeholder: "如 内嵌 国语" },
  { key: "exclude", label: "排除", type: "text", placeholder: "如 枪版 TC" },
  { key: "start_episode", label: "从第几集开始", type: "number", tvOnly: true, min: 1 },
  {
    key: "total_episodes",
    label: "总集数",
    type: "number",
    tvOnly: true,
    min: 1,
    hint: (sub) => (sub.manual_total ? "手动设置" : sub.total_episodes ? "自动获取，可改" : "未知，可手动填"),
  },
  {
    key: "upgrade",
    label: "洗版",
    type: "switch",
    editOnly: true,
    text: "出更高清的就换一份",
    disabled: (sub) => !sub.auto_save,
    hint: (sub) => (sub.auto_save ? "旧版本不会自动删，可在「整理网盘目录」里确认删除" : "需要先打开自动转存"),
  },
  {
    key: "upgrade_to",
    label: "洗版目标",
    type: "select",
    editOnly: true,
    empty: "2160p",
    options: [["2160p", "4K"], ["1080p", "1080p"], ["720p", "720p"]],
    disabled: (sub) => !sub.auto_save,
  },
];

export type RuleValues = Record<string, string | boolean>;

export function ruleValue(rule: Rule, sub: RuleSub): string | boolean {
  const cur = sub ? sub[rule.key] : null;
  if (rule.type === "switch") return !!cur;
  return cur == null || cur === "" ? rule.empty || "" : String(cur);
}

export function initialRules(rules: Rule[], sub: RuleSub): RuleValues {
  return Object.fromEntries(rules.map((r) => [r.key, ruleValue(r, sub)]));
}

/** 字段是否禁用：规则自身条件 + 洗版目标只在洗版打开时可选 */
function ruleDisabled(rule: Rule, sub: RuleSub, values: RuleValues): boolean {
  if (rule.disabled && sub && rule.disabled(sub)) return true;
  if (rule.key === "upgrade_to" && sub && "upgrade" in values && !values.upgrade) return true;
  return false;
}

/** 读取改过的值（number 空着不传；text/select 空串表示清除） */
export function readRules(rules: Rule[], values: RuleValues, sub: RuleSub): Record<string, any> {
  const out: Record<string, any> = {};
  rules.forEach((rule) => {
    if (ruleDisabled(rule, sub, values)) return;
    const v = values[rule.key];
    if (rule.type === "switch") {
      if (v !== ruleValue(rule, sub)) out[rule.key] = v;
      return;
    }
    const raw = String(v ?? "").trim();
    if (raw === ruleValue(rule, sub)) return;
    if (rule.type === "number") {
      const n = parseInt(raw, 10);
      if (n >= 1) out[rule.key] = n;
    } else out[rule.key] = raw;
  });
  return out;
}

export function RuleFields({
  rules,
  values,
  onChange,
  sub,
  idPrefix,
}: {
  rules: Rule[];
  values: RuleValues;
  onChange: (v: RuleValues) => void;
  sub: RuleSub;
  idPrefix: string;
}) {
  return (
    <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
      {rules.map((rule) => {
        const id = idPrefix + "-" + rule.key;
        const disabled = ruleDisabled(rule, sub, values);
        const v = values[rule.key];
        const put = (nv: string | boolean) => onChange({ ...values, [rule.key]: nv });
        return (
          <div key={rule.key} className={cn("flex min-w-0 flex-col gap-1.5", disabled && "opacity-60")}>
            <label htmlFor={id} className="text-footnote font-medium text-fg">
              {rule.label}
            </label>
            {rule.type === "switch" ? (
              <label className="inline-flex h-control-md items-center gap-2 text-footnote text-fg-muted">
                <Switch id={id} checked={!!v} disabled={disabled} onCheckedChange={put} />
                {rule.text}
              </label>
            ) : rule.type === "select" ? (
              <Select id={id} value={String(v)} disabled={disabled} onChange={(e) => put(e.target.value)}>
                {rule.options!.map(([ov, t]) => (
                  <option key={ov} value={ov}>
                    {t}
                  </option>
                ))}
              </Select>
            ) : (
              <input
                id={id}
                type={rule.type}
                value={String(v)}
                disabled={disabled}
                placeholder={rule.placeholder}
                maxLength={rule.type === "text" ? 100 : undefined}
                min={rule.min}
                inputMode={rule.type === "number" ? "numeric" : undefined}
                onChange={(e) => put(e.target.value)}
                className={cn(fieldClasses, "h-control-md px-3 text-body")}
              />
            )}
            {rule.hint && sub && <span className="text-caption text-fg-subtle">{rule.hint(sub)}</span>}
          </div>
        );
      })}
    </div>
  );
}
