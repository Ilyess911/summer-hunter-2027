"use client"

import { AlertTriangle, Inbox, RotateCw } from "lucide-react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ACTION_LABEL, STATUS_LABEL, TIMING_LABEL, pay } from "@/lib/format"
import type { Compensation } from "@/lib/types"
import { cn } from "@/lib/utils"

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b px-6 pb-4 pt-6">
      <div className="min-w-0 space-y-1">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Section({ title, action, children, className }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-xl border bg-card", className)}>
      <div className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 className="text-[13px] font-medium text-foreground/90">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

/** A 0-100 score with a thin bar; unknown stays an em dash, never a zero. */
export function Score({ value, label, provisional }: { value: number | null | undefined; label?: string; provisional?: boolean }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground tabular">—</span>
  const v = Math.max(0, Math.min(100, value))
  const tone = v >= 80 ? "bg-success" : v >= 60 ? "bg-primary" : v >= 40 ? "bg-warning" : "bg-muted-foreground/50"
  return (
    <span className="inline-flex items-center gap-2" title={label ? `${label}: ${Math.round(v)}${provisional ? " (provisional)" : ""}` : undefined}>
      <span className="tabular w-7 text-right text-[13px] font-medium">{Math.round(v)}{provisional ? <span className="text-muted-foreground">*</span> : null}</span>
      <span className="h-1 w-10 overflow-hidden rounded-full bg-muted">
        <span className={cn("block h-full rounded-full", tone)} style={{ width: `${v}%` }} />
      </span>
    </span>
  )
}

const DOT: Record<string, string> = {
  DISCOVERED: "bg-muted-foreground/40", SHORTLISTED: "bg-info", TO_APPLY: "bg-warning", PREPARING: "bg-warning",
  APPLIED: "bg-primary", ONLINE_ASSESSMENT: "bg-primary", INTERVIEW: "bg-chart-2", FINAL_INTERVIEW: "bg-chart-2",
  OFFER_RECEIVED: "bg-success", OFFER_ACCEPTED: "bg-success", REJECTED: "bg-destructive", WITHDRAWN: "bg-muted-foreground/40",
  CLOSED: "bg-muted-foreground/40",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-xs font-medium">
      <span className={cn("size-1.5 rounded-full", DOT[status] ?? "bg-muted-foreground/40")} />
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function EligibilityBadge({ value }: { value: string | null }) {
  const map: Record<string, [string, string]> = {
    ELIGIBLE: ["Eligible", "text-success border-success/30 bg-success/8"],
    NEEDS_VERIFICATION: ["Verify", "text-warning border-warning/30 bg-warning/8"],
    REJECTED: ["Ineligible", "text-destructive border-destructive/30 bg-destructive/8"],
  }
  const [label, cls] = map[value ?? ""] ?? ["Unknown", "text-muted-foreground"]
  return <span className={cn("inline-flex rounded-md border px-1.5 py-0.5 text-xs font-medium", cls)}>{label}</span>
}

export function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-muted-foreground">—</span>
  const strong = action === "APPLY_NOW" || action === "CONTACT_RECRUITER"
  return (
    <span className={cn("inline-flex rounded-md px-1.5 py-0.5 text-xs font-medium",
      strong ? "bg-primary/10 text-primary" : action === "DISCARD" ? "text-muted-foreground" : "bg-muted text-foreground/80")}>
      {ACTION_LABEL[action] ?? action}
    </span>
  )
}

export function TimingBadge({ action }: { action: string | null | undefined }) {
  if (!action) return <span className="text-muted-foreground">—</span>
  const tone = action === "DO_NOT_CONTACT" ? "text-muted-foreground" : action === "WAIT" ? "text-foreground/70"
    : action === "FOLLOW_UP" ? "text-warning" : "text-primary"
  return <span className={cn("text-xs font-medium", tone)}>{TIMING_LABEL[action] ?? action}</span>
}

export function Pay({ c, compact }: { c: Compensation | { status: string | null; monthly_min_eur: number | null; basis?: string | null }; compact?: boolean }) {
  const known = c.monthly_min_eur !== null && c.monthly_min_eur !== undefined
  const basis = "basis" in c && c.basis && c.basis !== "unknown" ? c.basis : null
  return (
    <span className={cn("tabular text-[13px]", known ? "font-medium" : "text-muted-foreground")}
          title={known ? `As advertised${basis ? ` (${basis})` : ", gross or net not stated"}; minimum of the range` : "Unknown pay is not unpaid"}>
      {pay(c as Compensation)}
      {!compact && basis && <span className="ml-1 text-xs text-muted-foreground">{basis}</span>}
    </span>
  )
}

export function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <Inbox className="size-5 text-muted-foreground" />
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center" role="alert">
      <AlertTriangle className="size-5 text-destructive" />
      <p className="text-sm font-medium">Could not load this view</p>
      <p className="max-w-md text-sm text-muted-foreground">{error instanceof Error ? error.message : "Unknown error"}</p>
      {retry && <Button variant="outline" size="sm" onClick={retry}><RotateCw /> Retry</Button>}
    </div>
  )
}

export function RowsSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded border bg-muted px-1 font-mono text-[10px] text-muted-foreground">{children}</kbd>
}
