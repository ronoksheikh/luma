import * as DialogP from "@radix-ui/react-dialog";
import * as SwitchP from "@radix-ui/react-switch";
import * as TabsP from "@radix-ui/react-tabs";
import * as TooltipP from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { Loader2, X } from "lucide-react";
import * as React from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ------------------------------------------------------------------ Button
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 select-none",
  {
    variants: {
      variant: {
        primary: "bg-gradient-to-r from-accent-2 to-accent text-[#1a1206] hover:brightness-110 shadow-[0_0_24px_-8px_var(--color-accent)]",
        default: "bg-raised border border-line-strong text-fg hover:bg-line",
        ghost: "text-muted hover:text-fg hover:bg-raised",
        danger: "bg-bad/15 text-bad border border-bad/30 hover:bg-bad/25",
        outline: "border border-line-strong text-fg hover:bg-raised",
      },
      size: { sm: "h-7 px-2.5 text-xs", md: "h-9 px-3.5", lg: "h-11 px-5 text-base", icon: "h-8 w-8", iconSm: "h-7 w-7" },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, loading, children, disabled, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...props}>
    {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
    {children}
  </button>
));
Button.displayName = "Button";

// ------------------------------------------------------------------ Inputs
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn(
      "h-9 w-full rounded-lg border border-line-strong bg-bg px-3 text-sm text-fg placeholder:text-faint focus:border-accent/60 focus:outline-none",
      className,
    )}
    {...props}
  />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full rounded-lg border border-line-strong bg-bg px-3 py-2 text-sm text-fg placeholder:text-faint focus:border-accent/60 focus:outline-none resize-none",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";

export function Label({ children, className, htmlFor }: { children: React.ReactNode; className?: string; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn("block text-xs font-medium text-muted mb-1.5", className)}>
      {children}
    </label>
  );
}

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn("h-9 w-full rounded-lg border border-line-strong bg-bg px-2.5 text-sm text-fg focus:border-accent/60 focus:outline-none", className)}
      {...props}
    />
  );
}

// ------------------------------------------------------------------ Badge
const badgeVariants = cva("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium border", {
  variants: {
    tone: {
      neutral: "bg-raised border-line text-muted",
      ok: "bg-ok/10 border-ok/25 text-ok",
      warn: "bg-warn/10 border-warn/25 text-warn",
      bad: "bg-bad/10 border-bad/25 text-bad",
      info: "bg-info/10 border-info/25 text-info",
      accent: "bg-accent/10 border-accent/25 text-accent",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

// ------------------------------------------------------------------ Switch
export function Switch({ checked, onCheckedChange, id, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; id?: string; label?: string }) {
  return (
    <SwitchP.Root
      id={id}
      aria-label={label}
      checked={checked}
      onCheckedChange={onCheckedChange}
      className="relative h-5 w-9 shrink-0 rounded-full bg-line-strong data-[state=checked]:bg-accent transition-colors"
    >
      <SwitchP.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-fg shadow transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-[#1a1206]" />
    </SwitchP.Root>
  );
}

// ------------------------------------------------------------------ Tabs
export const Tabs = TabsP.Root;
export function TabsList({ className, ...props }: TabsP.TabsListProps) {
  return <TabsP.List className={cn("flex items-center gap-0.5 border-b border-line px-2", className)} {...props} />;
}
export function TabsTrigger({ className, ...props }: TabsP.TabsTriggerProps) {
  return (
    <TabsP.Trigger
      className={cn(
        "relative flex items-center gap-1.5 px-2.5 py-2.5 text-xs font-medium text-muted hover:text-fg data-[state=active]:text-fg",
        "after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full data-[state=active]:after:bg-accent",
        className,
      )}
      {...props}
    />
  );
}
export function TabsContent({ className, ...props }: TabsP.TabsContentProps) {
  return <TabsP.Content className={cn("min-h-0 flex-1 outline-none data-[state=inactive]:hidden", className)} {...props} />;
}

// ------------------------------------------------------------------ Dialog
export function Dialog({ open, onOpenChange, title, description, children, wide }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <DialogP.Root open={open} onOpenChange={onOpenChange}>
      <DialogP.Portal>
        <DialogP.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <DialogP.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[92vw] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-line-strong bg-panel p-5 shadow-2xl",
            wide ? "max-w-4xl" : "max-w-xl",
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <DialogP.Title className="text-base font-semibold">{title}</DialogP.Title>
              {description && <DialogP.Description className="mt-1 text-sm text-muted">{description}</DialogP.Description>}
            </div>
            <DialogP.Close asChild>
              <Button variant="ghost" size="iconSm" aria-label="Close">
                <X className="h-4 w-4" />
              </Button>
            </DialogP.Close>
          </div>
          {children}
        </DialogP.Content>
      </DialogP.Portal>
    </DialogP.Root>
  );
}

// ------------------------------------------------------------------ Tooltip
export function Tip({ content, children }: { content: React.ReactNode; children: React.ReactElement }) {
  return (
    <TooltipP.Root delayDuration={250}>
      <TooltipP.Trigger asChild>{children}</TooltipP.Trigger>
      <TooltipP.Portal>
        <TooltipP.Content sideOffset={6} className="z-50 max-w-xs rounded-md border border-line-strong bg-raised px-2 py-1 text-xs text-fg shadow-lg">
          {content}
        </TooltipP.Content>
      </TooltipP.Portal>
    </TooltipP.Root>
  );
}
export const TooltipProvider = TooltipP.Provider;

// ------------------------------------------------------------------ misc
export function ProgressBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-line", className)}>
      <div className="h-full rounded-full bg-gradient-to-r from-accent-2 to-accent transition-[width] duration-300" style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-muted", className)} />;
}

export function Empty({ icon, title, children }: { icon?: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <div className="text-sm font-medium text-muted">{title}</div>
      {children && <div className="max-w-xs text-xs text-faint">{children}</div>}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-3 pb-1.5 pt-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">{children}</h3>
      {right}
    </div>
  );
}
