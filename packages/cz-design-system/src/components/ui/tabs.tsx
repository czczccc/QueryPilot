import { motion } from "framer-motion";
import { Tabs as TabsPrimitive } from "radix-ui";
import { createContext, useContext, useId, useState, type ComponentProps } from "react";
import { cn } from "../../lib/cn";
import { transition } from "../../motion";

const TabsContext = createContext<{ value?: string; id: string }>({ id: "" });

/** 分段标签：选中背景块用 layoutId 在标签之间滑动，而不是闪现。 */
export function Tabs({
  value: valueProp,
  defaultValue,
  onValueChange,
  className,
  ...props
}: ComponentProps<typeof TabsPrimitive.Root>) {
  const [inner, setInner] = useState(defaultValue);
  const value = valueProp ?? inner;
  const id = useId();
  return (
    <TabsContext.Provider value={{ value, id }}>
      <TabsPrimitive.Root
        value={value}
        onValueChange={(v) => {
          if (valueProp === undefined) setInner(v);
          onValueChange?.(v);
        }}
        className={cn("flex flex-col gap-4", className)}
        {...props}
      />
    </TabsContext.Provider>
  );
}

export function TabsList({ className, ...props }: ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn(
        "inline-flex w-fit items-center gap-0.5 rounded-md border border-line-subtle bg-sunken p-0.5",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({ className, value, children, ...props }: ComponentProps<typeof TabsPrimitive.Trigger>) {
  const ctx = useContext(TabsContext);
  const active = ctx.value === value;
  return (
    <TabsPrimitive.Trigger
      value={value}
      className={cn(
        "relative inline-flex h-7 items-center gap-1.5 rounded-sm px-3 text-footnote font-medium",
        "text-fg-muted transition duration-fast ease-standard hover:text-fg",
        "outline-none focus-visible:shadow-ring data-[state=active]:text-fg",
        "[&_svg]:size-3.5",
        className,
      )}
      {...props}
    >
      {active && (
        <motion.span
          layoutId={`cz-tab-${ctx.id}`}
          className="absolute inset-0 rounded-sm border border-line-subtle bg-surface shadow-xs"
          transition={transition.layout}
        />
      )}
      <span className="relative z-10 inline-flex items-center gap-1.5">{children}</span>
    </TabsPrimitive.Trigger>
  );
}

export function TabsContent({ className, ...props }: ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("outline-none", className)} {...props} />;
}
