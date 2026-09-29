// Small compositions of HeroUI primitives used across the app.
import { Description, FieldError, Input, InputGroup, Label, Switch, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Button } from "@heroui/react";
import { Desktop, Eye, EyeSlash, Moon, Sun } from "@phosphor-icons/react";
import { clsx, type ClassValue } from "clsx";
import { useState, type Key, type ReactElement, type ReactNode } from "react";
import { twMerge } from "tailwind-merge";
import { useTheme, type ThemeMode } from "../lib/theme";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** The product mark. The file in /brand/logo-mark.svg is the single source of the logo. */
export function Logo({ size = 28, withName = false, className }: { size?: number; withName?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <img src="/brand/logo-mark.svg" width={size} height={size} alt="" aria-hidden className="shrink-0 select-none" draggable={false} />
      {withName && (
        <span className="display text-[16px] font-medium tracking-tight">
          Luma <span className="text-muted">Studio</span>
        </span>
      )}
    </span>
  );
}

export function Field({
  label, value, onChange, placeholder, description, type = "text", mono, name, autoFocus, isRequired, error, autoComplete,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; description?: ReactNode; type?: string;
  mono?: boolean; name?: string; autoFocus?: boolean; isRequired?: boolean; error?: string; autoComplete?: string;
}) {
  return (
    <TextField value={value} onChange={onChange} type={type} name={name} isRequired={isRequired} isInvalid={!!error} className="w-full">
      <Label>{label}</Label>
      <Input placeholder={placeholder} autoFocus={autoFocus} autoComplete={autoComplete} className={cn(mono && "font-mono text-[13px]")} />
      {description && <Description>{description}</Description>}
      {error && <FieldError>{error}</FieldError>}
    </TextField>
  );
}

export function SecretField({ label, value, onChange, placeholder, description, name, autoComplete = "off" }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; description?: ReactNode; name?: string; autoComplete?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <TextField value={value} onChange={onChange} name={name} className="w-full">
      <Label>{label}</Label>
      <InputGroup>
        <InputGroup.Input type={show ? "text" : "password"} placeholder={placeholder} autoComplete={autoComplete} spellCheck={false} className="font-mono text-[13px]" />
        <InputGroup.Suffix className="pe-0">
          <Button isIconOnly size="sm" variant="ghost" aria-label={show ? "Hide" : "Show"} onPress={() => setShow(!show)}>
            {show ? <EyeSlash size={16} /> : <Eye size={16} />}
          </Button>
        </InputGroup.Suffix>
      </InputGroup>
      {description && <Description>{description}</Description>}
    </TextField>
  );
}

export function Toggle({ isSelected, onChange, label, description, size = "md" }: {
  isSelected: boolean; onChange: (v: boolean) => void; label: string; description?: string; size?: "sm" | "md" | "lg";
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-sm text-foreground">{label}</div>
        {description && <div className="text-xs text-muted">{description}</div>}
      </div>
      <Switch isSelected={isSelected} onChange={onChange} size={size} aria-label={label}>
        <Switch.Content>
          <Switch.Control>
            <Switch.Thumb />
          </Switch.Control>
        </Switch.Content>
      </Switch>
    </div>
  );
}

export function Tip({ content, children }: { content: ReactNode; children: ReactElement }) {
  return (
    <Tooltip delay={350}>
      {children}
      <Tooltip.Content>{content}</Tooltip.Content>
    </Tooltip>
  );
}

export function SectionLabel({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-1 pb-1.5">
      <span className="text-xs font-medium uppercase tracking-wide text-muted">{children}</span>
      {right}
    </div>
  );
}

export function fmtK(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

const THEMES: { id: ThemeMode; label: string; icon: typeof Sun }[] = [
  { id: "system", label: "System", icon: Desktop },
  { id: "light", label: "Light", icon: Sun },
  { id: "dark", label: "Dark", icon: Moon },
];

/** System / Light / Dark segmented control (Settings → Appearance). */
export function ThemeSwitch({ className }: { className?: string }) {
  const [mode, setMode] = useTheme();
  return (
    <ToggleButtonGroup selectionMode="single" disallowEmptySelection selectedKeys={new Set([mode])} aria-label="Theme" className={className}
      onSelectionChange={(k) => setMode([...(k as Set<Key>)][0] as ThemeMode)}>
      {THEMES.map(({ id, label, icon: Icon }, i) => (
        <ToggleButton key={id} id={id} aria-label={label}>
          {i > 0 && <ToggleButtonGroup.Separator />}
          <Icon size={16} />{label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

export { THEMES };
