import type { ButtonHTMLAttributes } from "react";

export type Variant = "primary" | "secondary";

const BASE =
  "inline-flex items-center justify-center rounded-md px-5 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-foreground hover:opacity-90",
  secondary:
    "border border-border bg-surface text-foreground hover:bg-surface-raised",
};

/**
 * The button's classes, for the rare case that needs an anchor instead.
 *
 * An error boundary offers "go to the home page", which is navigation and must
 * therefore be a link — a `<button>` that navigates is invisible to "open in
 * new tab" and to the browser's own history affordances. Exporting the classes
 * keeps that link looking like every other control without introducing a
 * second, drifting button primitive.
 */
export function buttonClassName(variant: Variant = "primary", className = ""): string {
  return `${BASE} ${VARIANTS[variant]} ${className}`.trim();
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export function Button({
  variant = "primary",
  className = "",
  type = "button",
  ...props
}: ButtonProps) {
  return <button type={type} className={buttonClassName(variant, className)} {...props} />;
}
