import { cva, type VariantProps } from "class-variance-authority";
import { motion } from "framer-motion";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";
import { press } from "../../motion";
import { Spinner } from "./spinner";

export const buttonVariants = cva(
  [
    "relative inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap select-none",
    "rounded-control font-medium transition duration-fast ease-standard",
    "outline-none focus-visible:shadow-ring",
    "disabled:pointer-events-none disabled:opacity-45",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent shadow-xs hover:bg-accent-hover",
        secondary: "border border-line bg-surface text-fg shadow-xs hover:border-line-strong hover:bg-sunken",
        soft: "bg-accent-soft text-accent-fg hover:bg-accent-soft-hover",
        ghost: "text-fg-muted hover:bg-hover hover:text-fg active:bg-pressed",
        danger: "bg-danger text-on-accent shadow-xs hover:bg-danger/90",
        link: "h-auto px-0 text-accent-fg underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-control-sm rounded-sm px-2 text-footnote",
        md: "h-control-md px-3 text-body",
        lg: "h-control-lg px-4 text-body-lg",
        "icon-sm": "size-control-sm rounded-sm",
        icon: "size-control-md",
        "icon-lg": "size-control-lg",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    /** 把样式交给子元素（例如 <a>），此时不带按下动效 */
    asChild?: boolean;
    /** 加载中：禁用并显示转圈 */
    loading?: boolean;
  };

export function Button({ className, variant, size, asChild, loading, disabled, children, ...props }: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);
  if (asChild) {
    return (
      <Slot.Root className={classes} {...props}>
        {children}
      </Slot.Root>
    );
  }
  const {
    onDrag: _onDrag,
    onDragStart: _onDragStart,
    onDragEnd: _onDragEnd,
    onAnimationStart: _onAnimationStart,
    ...rest
  } = props;
  return (
    <motion.button
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      whileTap={variant === "link" ? undefined : press.whileTap}
      transition={press.transition}
      {...rest}
    >
      {loading && <Spinner />}
      {children}
    </motion.button>
  );
}
