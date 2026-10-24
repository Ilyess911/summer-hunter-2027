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
    <div className="flex flex-wrap items-end justify-between gap-3 px-6 pb-2 pt-7 lg:px-8">
      <div className="min-w-0 space-y-1.5">
        <h1 className="font-display text-[19px] font-semibold uppercase leading-tight tracking-[0.08em]">{title}</h1>
        {description && <p className="text-[13px] text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** Targeting marks: [ + ] brackets on the four corners of a HUD panel. */
export function Reticles({ className }: { className?: string }) {
  const c = cn("pointer-events-none absolute z-10 size-2 border-foreground/35 transition-colors group-hover/hud:border-tac-cyan", className)
  return (
    <span aria-hidden className="contents">
      <span className={cn(c, "-left-px -top-px border-l border-t")} />
      <span className={cn(c, "-right-px -top-px border-r border-t")} />
      <span className={cn(c, "-bottom-px -left-px border-b border-l")} />
      <span className={cn(c, "-bottom-px -right-px border-b border-r")} />
    </span>
  )
}

export function Section({ title, action, children, className, alert }: {
  title: string; action?: ReactNode; children: ReactNode; className?: string; alert?: boolean
}) {
  return (
    <section className={cn("hud-panel hud-hover group/hud border bg-card", alert && "hud-alert", className)}>
      <Reticles />
      <div className="flex h-11 items-center justify-between gap-2 border-b px-4">
        <h2 className="hud-title flex items-center gap-2 text-foreground/90">
          <span className="size-1 bg-tac-cyan" aria-hidden />{title}
        </h2>
        {action}
      </div>
      <div className="overflow-hidden">{children}</div>
    </section>
  )
}

/** A 0-100 score with a thin bar; unknown stays an em dash, never a zero. */
export function Score({ value, label, provisional }: { value: number | null | undefined; label?: string; provisional?: boolean }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground tabular">—</span>
  const v = Math.max(0, Math.min(100, value))
  const tone = v >= 80 ? "bg-tac-cyan text-tac-cyan" : v >= 60 ? "bg-foreground/60 text-transparent" : v >= 40 ? "bg-tac-amber text-tac-amber"
    : "bg-tac-red text-tac-red"
  return (
    <span className="inline-flex items-center gap-2" title={label ? `${label}: ${Math.round(v)}${provisional ? " (provisoire)" : ""}` : undefined}>
      <span className="tabular w-7 text-right text-[12.5px]">{Math.round(v)}{provisional ? <span className="text-muted-foreground">*</span> : null}</span>
      <span className="relative h-[2px] w-12 bg-foreground/10">
        <span className={cn("gauge-fill absolute inset-y-0 left-0", tone)} style={{ width: `${v}%` }} />
        <span className="absolute inset-y-[-2px] left-1/2 w-px bg-foreground/15" aria-hidden />
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

/** Status light: a solid dot, with a radar pulse when the state calls for attention. */
export function Pulse({ className, live = true }: { className: string; live?: boolean }) {
  return (
    <span className="relative inline-flex size-1.5 shrink-0">
      {live && <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-60", className)} />}
      <span className={cn("relative inline-flex size-1.5 rounded-full", className)} />
    </span>
  )
}

const LIVE = new Set(["TO_APPLY", "PREPARING", "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED"])

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className="hud-label inline-flex items-center gap-1.5 whitespace-nowrap text-foreground/85">
      <Pulse className={DOT[status] ?? "bg-muted-foreground/40"} live={LIVE.has(status)} />
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function EligibilityBadge({ value }: { value: string | null }) {
  const map: Record<string, [string, string, string, boolean]> = {
    ELIGIBLE: ["Éligible", "text-tac-cyan", "bg-tac-cyan", false],
    NEEDS_VERIFICATION: ["À vérifier", "text-tac-amber", "bg-tac-amber", true],
    REJECTED: ["Inéligible", "text-tac-red", "bg-tac-red", true],
  }
  const [label, text, dot, live] = map[value ?? ""] ?? ["Inconnu", "text-muted-foreground", "bg-muted-foreground/50", false]
  return <span className={cn("hud-label inline-flex items-center gap-1.5 whitespace-nowrap", text)}><Pulse className={dot} live={live} />{label}</span>
}

export function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-muted-foreground">—</span>
  const strong = action === "APPLY_NOW" || action === "CONTACT_RECRUITER"
  const verify = action === "VERIFY_ELIGIBILITY" || action === "VERIFY_COMPENSATION"
  return (
    <span className={cn("hud-label inline-flex items-center gap-1.5 whitespace-nowrap",
      strong ? "text-tac-cyan" : verify ? "text-tac-amber" : "text-muted-foreground")}>
      <Pulse className={strong ? "bg-tac-cyan" : verify ? "bg-tac-amber" : "bg-muted-foreground/50"} live={strong || verify} />
      {ACTION_LABEL[action] ?? action}
    </span>
  )
}

export function TimingBadge({ action }: { action: string | null | undefined }) {
  if (!action) return <span className="text-muted-foreground">—</span>
  const tone = action === "DO_NOT_CONTACT" ? "text-muted-foreground" : action === "WAIT" ? "text-foreground/70"
    : action === "FOLLOW_UP" ? "text-tac-amber" : "text-tac-cyan"
  return <span className={cn("hud-label whitespace-nowrap", tone)}>{TIMING_LABEL[action] ?? action}</span>
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
      <span className="hud-cut mb-1 flex size-9 items-center justify-center border border-dashed"><Inbox className="size-4 text-muted-foreground" /></span>
      <p className="hud-label text-foreground/80">{title}</p>
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

/** Brand mark: a W chevron in a bevelled tactical frame. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={cn("hud-cut flex size-7 shrink-0 items-center justify-center border border-tac-cyan/50 bg-tac-cyan/5 text-tac-cyan", className)}>
      <svg viewBox="0 0 20 20" className="size-4" aria-hidden>
        <path d="M2.5 5l3.6 10L10 7.5l3.9 7.5L17.5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      </svg>
    </span>
  )
}
