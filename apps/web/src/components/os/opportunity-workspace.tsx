"use client"

import { Check, CircleAlert, CircleHelp, ExternalLink, Minus, X } from "lucide-react"
import { useState } from "react"

import { ActionBadge, EligibilityBadge, Pay, Score, Section } from "@/components/os/bits"
import { ContactsPanel, DocumentsPanel } from "@/components/os/panels"
import { ACTION_LABEL, date, money } from "@/lib/format"
import type { OpportunityDetail } from "@/lib/types"
import { cn } from "@/lib/utils"

const CRIT_LABEL: Record<string, string> = {
  internship_status: "Contract type", dates: "Dates", duration: "Duration", country: "Country", safety: "Safety", language: "Language",
  academic: "Degree", graduation: "Graduation date", citizenship: "Citizenship", work_authorization: "Work authorization",
  qualifications: "Licences", technical_requirements: "Technical requirements", compensation: "Compensation",
}
const ICON = { PASS: Check, FAIL: X, UNKNOWN: CircleHelp, NOT_APPLICABLE: Minus } as const
const TONE: Record<string, string> = { PASS: "text-success", FAIL: "text-destructive", UNKNOWN: "text-warning", NOT_APPLICABLE: "text-muted-foreground" }

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex items-center justify-between gap-3 py-1.5 text-sm"><span className="text-muted-foreground">{label}</span><span>{value}</span></div>
}

export function Recommendation({ o }: { o: OpportunityDetail }) {
  const s = o.strategy
  if (!s) return null
  return (
    <Section title="Recommendation">
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <ActionBadge action={s.action} />
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium capitalize">{s.category?.toLowerCase()}</span>
          {s.effort && <span className="text-xs text-muted-foreground">Preparation {s.effort.replace("environ ", "about ")}</span>}
        </div>
        <p className="text-sm">{ACTION_LABEL[s.action] ?? s.action}: {s.why}{s.caveat ? `, ${s.caveat}` : ""}.</p>
        {s.arguments?.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Your strongest arguments</p>
            <ul className="space-y-1 text-sm">{s.arguments.map((a) => <li key={a} className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-success" />{a}</li>)}</ul>
          </div>
        )}
        {s.gaps?.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Missing or uncertain</p>
            <ul className="space-y-1 text-sm">{s.gaps.map((g) => <li key={g} className="flex gap-2"><CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" />{g}</li>)}</ul>
          </div>
        )}
      </div>
    </Section>
  )
}

export function Scores({ o }: { o: OpportunityDetail }) {
  const weights = o.strategy?.global?.weights ?? {}
  return (
    <Section title="Scores">
      <div className="divide-y px-4">
        <Row label="Global priority" value={<Score value={o.priority} provisional={o.provisional} />} />
        <Row label="Career fit" value={<Score value={o.fit} />} />
        <Row label="Recruitability" value={<Score value={o.recruitability} />} />
        <Row label="Networking" value={<Score value={o.networking} />} />
        <Row label="Financial attractiveness" value={<Score value={o.compensation.score} />} />
        <Row label="Eligibility" value={<EligibilityBadge value={o.eligibility} />} />
      </div>
      <p className="border-t px-4 py-2 text-[11px] leading-4 text-muted-foreground">
        Weights {Object.entries(weights).map(([k, v]) => `${k.replace("_", " ")} ${Math.round(v * 100)}%`).join(" · ")}. Recruitability measures the match between your verified experience and the stated requirements; it is not a hiring probability.
      </p>
    </Section>
  )
}

export function CompensationCard({ o }: { o: OpportunityDetail }) {
  const c = o.compensation_report as Record<string, any> | null // eslint-disable-line @typescript-eslint/no-explicit-any
  const p = c?.pay as Record<string, any> | undefined // eslint-disable-line @typescript-eslint/no-explicit-any
  return (
    <Section title="Compensation">
      <div className="divide-y px-4">
        <Row label="Monthly (EUR)" value={<Pay c={o.compensation} />} />
        <Row label="Status" value={<span className="text-xs">{(o.compensation.status ?? "COMPENSATION_UNKNOWN").replaceAll("_", " ").toLowerCase()}</span>} />
        <Row label="Confidence" value={<span className="text-xs font-medium">{o.compensation.confidence ?? "UNKNOWN"}</span>} />
        {p && <Row label="As advertised" value={<span className="tabular text-xs">{p.amount_min.toLocaleString("en-GB")}{p.amount_max !== p.amount_min ? `–${p.amount_max.toLocaleString("en-GB")}` : ""} {p.currency} / {p.period ?? "?"}</span>} />}
        <Row label="Living cost (sourced)" value={money(o.compensation.living_cost_eur)} />
        <Row label="Monthly balance" value={money(o.compensation.balance_eur)} />
        {c?.benefits && Object.keys(c.benefits).length > 0 && <Row label="Benefits" value={<span className="text-xs">{Object.keys(c.benefits).join(", ").replaceAll("_", " ")}</span>} />}
      </div>
      {p?.wording && <p className="border-t px-4 py-2 text-xs italic text-muted-foreground">“{p.wording}”</p>}
      {Array.isArray(c?.notes) && c.notes.length > 0 && (
        <ul className="space-y-0.5 border-t px-4 py-2 text-[11px] text-muted-foreground">{c.notes.slice(0, 4).map((n: string) => <li key={n}>{n}</li>)}</ul>
      )}
    </Section>
  )
}

export function Eligibility({ o }: { o: OpportunityDetail }) {
  return (
    <Section title="Eligibility checks" action={<EligibilityBadge value={o.eligibility} />}>
      <ul className="divide-y">
        {o.criteria.map((c) => {
          const Icon = ICON[c.status as keyof typeof ICON] ?? CircleHelp
          return (
            <li key={c.criterion} className="flex gap-3 px-4 py-2 text-sm">
              <Icon className={cn("mt-0.5 size-4 shrink-0", TONE[c.status])} />
              <div className="min-w-0"><span className="font-medium">{CRIT_LABEL[c.criterion] ?? c.criterion}</span>
                <p className="text-xs text-muted-foreground">{c.explanation}</p></div>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

export function Description({ o }: { o: OpportunityDetail }) {
  const [open, setOpen] = useState(false)
  return (
    <Section title="Job description" action={o.official_url && <a href={o.official_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">Official posting <ExternalLink className="size-3" /></a>}>
      <div className={cn("relative whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed text-foreground/85", !open && "max-h-72 overflow-hidden")}>
        {o.description || "No description stored."}
        {!open && o.description.length > 900 && <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-card" />}
      </div>
      {o.description.length > 900 && <button className="w-full border-t py-2 text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show full description"}</button>}
      <div className="border-t px-4 py-2 text-[11px] text-muted-foreground">
        Sources: {o.sources.map((s) => s.source.split(":")[0]).join(", ")} · discovered {date(o.discovered)} · last verified {date(o.verified)}
      </div>
    </Section>
  )
}

export function WorkspaceGrid({ o }: { o: OpportunityDetail }) {
  return (
    <div className="grid gap-5 p-6 xl:grid-cols-3">
      <div className="space-y-5 xl:col-span-2">
        <Recommendation o={o} />
        <ContactsPanel opportunityId={o.id} company={o.company} contacts={o.contacts} />
        <Eligibility o={o} />
        <Description o={o} />
      </div>
      <div className="space-y-5">
        <Scores o={o} />
        <CompensationCard o={o} />
        <DocumentsPanel opportunityId={o.id} />
      </div>
    </div>
  )
}
