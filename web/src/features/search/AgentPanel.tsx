import { ChevronDown } from "lucide-react";
import { AgentSteps, Button, StatusDot, type AgentStep } from "@cz/design-system";
import { TOOL_NAME, stepText, type Step } from "./agent";

export type AgentView = {
  steps: Step[];
  /** 末尾的「正在…」占位：运行中才有 */
  pending: string | null;
  title: string;
  running: boolean;
  failed: boolean;
  collapsed: boolean;
  /** 结果出来后才能收起/展开 */
  collapsible: boolean;
};

export function AgentPanel({ view, elapsed, onToggle }: { view: AgentView; elapsed: number; onToggle: () => void }) {
  const items: AgentStep[] = view.steps.map((s, i) => {
    const error = !!(s.observation && s.observation.error);
    return {
      id: String(i),
      status: error ? "error" : "done",
      title: (
        <span className="whitespace-normal break-words">
          <span className="mr-2 font-medium text-fg">{TOOL_NAME[s.tool] || s.tool}</span>
          {stepText(s)}
          {s.thought && <span className="mt-0.5 block text-footnote text-fg-subtle">{s.thought}</span>}
        </span>
      ),
      meta: (s.duration_ms / 1000).toFixed(1) + "s",
    };
  });
  if (view.pending) items.push({ id: "pending-" + view.steps.length, status: "running", title: view.pending });

  return (
    <section aria-label="agent 过程" className="rounded-card border border-line-subtle bg-surface p-4 sm:p-5">
      <header className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <StatusDot state={view.running ? "running" : view.failed ? "error" : "success"} />
          <h2 className="truncate text-body font-medium text-fg">{view.title}</h2>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {view.running && <span className="font-mono text-caption text-fg-subtle tabular-nums">{elapsed}s</span>}
          {view.collapsible && (
            <Button variant="ghost" size="sm" aria-expanded={!view.collapsed} onClick={onToggle}>
              {view.collapsed ? "展开过程" : "收起"}
              <ChevronDown className={view.collapsed ? "transition duration-fast ease-standard" : "rotate-180 transition duration-fast ease-standard"} />
            </Button>
          )}
        </div>
      </header>
      {!view.collapsed && items.length > 0 && <AgentSteps steps={items} className="mt-4" />}
    </section>
  );
}
