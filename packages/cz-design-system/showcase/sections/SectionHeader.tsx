import type { ReactNode } from "react";

/** 章节标题：左上角标出这一节对应的源文件，方便从展示页跳回代码。 */
export function SectionHeader({
  id,
  source,
  title,
  children,
}: {
  id: string;
  source: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <header id={id} className="flex flex-col gap-2">
      <code className="font-mono text-caption font-normal text-fg-subtle">{source}</code>
      <h2 className="text-heading text-fg">{title}</h2>
      {children && <p className="max-w-2xl text-body text-fg-muted">{children}</p>}
    </header>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-card border border-line-subtle bg-surface ${className}`}>{children}</div>;
}
