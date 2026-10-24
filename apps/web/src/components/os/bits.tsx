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
    <div className="flex flex-wrap items-end justify-between gap-3 px-6 pb-2 pt-8 lg:px-8">
      <div className="min-w-0 space-y-1">
        <h1 className="text-[28px] font-bold leading-tight tracking-[-0.03em]">{title}</h1>
        {description && <p className="text-[13.5px] text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Section({ title, action, children, className, alert }: {
  title: string; action?: ReactNode; children: ReactNode; className?: string; alert?: boolean
}) {
  return (
    <section className={cn("glass-panel overflow-hidden", alert && "glass-alert", className)}>
      <div className="flex h-12 items-center justify-between gap-2 border-b px-5">
        <h2 className="section-title">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  )
}

/** A 0-100 score with a thin bar; unknown stays an em dash, never a zero. Same thresholds as the Mini App ring. */
export function Score({ value, label, provisional }: { value: number | null | undefined; label?: string; provisional?: boolean }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground tabular">—</span>
  const v = Math.max(0, Math.min(100, value))
  const tone = v >= 85 ? "bg-success" : v >= 70 ? "bg-warning" : "bg-foreground/30"
  return (
    <span className="inline-flex items-center gap-2" title={label ? `${label}: ${Math.round(v)}${provisional ? " (provisoire)" : ""}` : undefined}>
      <span className="tabular w-7 text-right text-[13px] font-semibold">{Math.round(v)}{provisional ? <span className="text-muted-foreground">*</span> : null}</span>
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-foreground/10">
        <span className={cn("block h-full rounded-full", tone)} style={{ width: `${v}%` }} />
      </span>
    </span>
  )
}

const DOT: Record<string, string> = {
  DISCOVERED: "bg-muted-foreground/40", SHORTLISTED: "bg-info", TO_APPLY: "bg-warning", PREPARING: "bg-warning",
  APPLIED: "bg-brand", ONLINE_ASSESSMENT: "bg-brand", INTERVIEW: "bg-info", FINAL_INTERVIEW: "bg-info",
  OFFER_RECEIVED: "bg-success", OFFER_ACCEPTED: "bg-success", REJECTED: "bg-destructive", WITHDRAWN: "bg-muted-foreground/40",
  CLOSED: "bg-muted-foreground/40",
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="status-label inline-flex items-center gap-1.5 whitespace-nowrap text-foreground/85">
      <span className={cn("size-1.5 rounded-full", DOT[status] ?? "bg-muted-foreground/40")} />
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

/** Tinted pill, like the Mini App tags. */
function Pill({ tone, children }: { tone: "good" | "mid" | "bad" | "accent" | "neutral"; children: ReactNode }) {
  const cls = {
    good: "bg-success/15 text-success", mid: "bg-warning/15 text-warning", bad: "bg-destructive/15 text-destructive",
    accent: "bg-brand-soft text-brand", neutral: "bg-foreground/8 text-muted-foreground",
  }[tone]
  return <span className={cn("status-label inline-flex whitespace-nowrap rounded-full px-2.5 py-0.5", cls)}>{children}</span>
}

export function EligibilityBadge({ value }: { value: string | null }) {
  const map: Record<string, [string, "good" | "mid" | "bad"]> = {
    ELIGIBLE: ["Éligible", "good"], NEEDS_VERIFICATION: ["À vérifier", "mid"], REJECTED: ["Inéligible", "bad"],
  }
  const [label, tone] = map[value ?? ""] ?? ["Inconnu", "neutral" as const]
  return <Pill tone={tone}>{label}</Pill>
}

export function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-muted-foreground">—</span>
  const tone = action === "APPLY_NOW" || action === "CONTACT_RECRUITER" ? "accent"
    : action === "VERIFY_ELIGIBILITY" || action === "VERIFY_COMPENSATION" ? "mid" : "neutral"
  return <Pill tone={tone}>{ACTION_LABEL[action] ?? action}</Pill>
}

export function TimingBadge({ action }: { action: string | null | undefined }) {
  if (!action) return <span className="text-muted-foreground">—</span>
  const tone = action === "DO_NOT_CONTACT" ? "text-muted-foreground" : action === "WAIT" ? "text-foreground/70"
    : action === "FOLLOW_UP" ? "text-warning" : "text-brand"
  return <span className={cn("status-label whitespace-nowrap", tone)}>{TIMING_LABEL[action] ?? action}</span>
}

export function Pay({ c, compact }: { c: Compensation | { status: string | null; monthly_min_eur: number | null; basis?: string | null }; compact?: boolean }) {
  const known = c.monthly_min_eur !== null && c.monthly_min_eur !== undefined
  const basis = "basis" in c && c.basis && c.basis !== "unknown" ? c.basis : null
  return (
    <span className={cn("tabular text-[13px]", known ? "font-medium" : "text-muted-foreground")}
          title={known ? `Montant annoncé${basis ? ` (${basis})` : ", brut ou net non précisé"} ; minimum de la fourchette` : "Une paie inconnue n’est pas un stage non payé"}>
      {pay(c as Compensation)}
      {!compact && basis && <span className="ml-1 text-xs text-muted-foreground">{basis}</span>}
    </span>
  )
}

export function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <span className="mb-1 flex size-10 items-center justify-center rounded-full bg-foreground/6"><Inbox className="size-4 text-muted-foreground" /></span>
      <p className="text-[15px] font-semibold">{title}</p>
      {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center" role="alert">
      <AlertTriangle className="size-5 text-destructive" />
      <p className="text-sm font-medium">Impossible de charger cette vue</p>
      <p className="max-w-md text-sm text-muted-foreground">{error instanceof Error ? error.message : "Erreur inconnue"}</p>
      {retry && <Button variant="outline" size="sm" onClick={retry}><RotateCw /> Réessayer</Button>}
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

/** Brand mark: a sun rising over the horizon, on a frosted tile. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-primary text-primary-foreground shadow-[0_6px_16px_-8px_var(--primary)]", className)}>
      <svg viewBox="0 0 20 20" className="size-4" aria-hidden>
        <path d="M5 13a5 5 0 0 1 10 0" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        <path d="M3 16h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" opacity="0.7" />
        <path d="M10 4.5v1.6M4.4 7.2l1.1 1.1M15.6 7.2l-1.1 1.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </span>
  )
}
