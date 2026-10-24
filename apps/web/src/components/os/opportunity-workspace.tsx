"use client"

import { Check, CircleAlert, CircleHelp, ExternalLink, Minus, X } from "lucide-react"
import { useState } from "react"

import { ActionBadge, EligibilityBadge, Pay, Score, Section } from "@/components/os/bits"
import { ContactsPanel, DocumentsPanel } from "@/components/os/panels"
import { ACTION_LABEL, date, money } from "@/lib/format"
import type { OpportunityDetail } from "@/lib/types"
import { cn } from "@/lib/utils"

const CRIT_LABEL: Record<string, string> = {
  internship_status: "Type de contrat", dates: "Dates", duration: "Durée", country: "Pays", safety: "Sécurité", language: "Langue",
  academic: "Diplôme", graduation: "Date de diplôme", citizenship: "Nationalité", work_authorization: "Autorisation de travail",
  qualifications: "Licences", technical_requirements: "Exigences techniques", compensation: "Rémunération",
}
const STRATEGY: Record<string, string> = { AMBITIOUS: "Ambitieuse", TARGETED: "Ciblée", SAFETY: "Sûre" }
const WEIGHT: Record<string, string> = {
  recruitability: "recrutabilité", career_fit: "adéquation", compensation: "rémunération", networking: "réseau", quality: "qualité",
  urgency: "urgence", ease: "facilité",
}
const PAY_STATUS: Record<string, string> = {
  PAID_CONFIRMED: "payé, montant confirmé", PAID_AMOUNT_UNKNOWN: "payé, montant inconnu", UNPAID_CONFIRMED: "non payé",
  MIXED_OR_CONDITIONAL: "conditionnel", COMPENSATION_UNKNOWN: "non précisé",
}
const CONFIDENCE: Record<string, string> = { HIGH: "élevée", MEDIUM: "moyenne", LOW: "faible", UNKNOWN: "inconnue" }
const PERIOD: Record<string, string> = { hour: "heure", day: "jour", week: "semaine", month: "mois", year: "an", total: "total du stage" }
const ICON = { PASS: Check, FAIL: X, UNKNOWN: CircleHelp, NOT_APPLICABLE: Minus } as const
const TONE: Record<string, string> = { PASS: "text-success", FAIL: "text-destructive", UNKNOWN: "text-warning", NOT_APPLICABLE: "text-muted-foreground" }

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex items-center justify-between gap-3 py-1.5 text-sm"><span className="text-muted-foreground">{label}</span><span>{value}</span></div>
}

export function Recommendation({ o }: { o: OpportunityDetail }) {
  const s = o.strategy
  if (!s) return null
  return (
    <Section title="Recommandation">
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <ActionBadge action={s.action} />
          {s.category && <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium">{STRATEGY[s.category] ?? s.category}</span>}
          {s.effort && <span className="text-xs text-muted-foreground">Préparation : {s.effort}</span>}
        </div>
        <p className="text-sm">{ACTION_LABEL[s.action] ?? s.action} : {s.why}{s.caveat ? `, ${s.caveat}` : ""}.</p>
        {s.arguments?.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Tes meilleurs arguments</p>
            <ul className="space-y-1 text-sm">{s.arguments.map((a) => <li key={a} className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-success" />{a}</li>)}</ul>
          </div>
        )}
        {s.gaps?.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Manquant ou incertain</p>
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
        <Row label="Priorité globale" value={<Score value={o.priority} provisional={o.provisional} />} />
        <Row label="Adéquation au parcours" value={<Score value={o.fit} />} />
        <Row label="Recrutabilité" value={<Score value={o.recruitability} />} />
        <Row label="Réseau" value={<Score value={o.networking} />} />
        <Row label="Attractivité financière" value={<Score value={o.compensation.score} />} />
        <Row label="Éligibilité" value={<EligibilityBadge value={o.eligibility} />} />
      </div>
      <p className="border-t px-4 py-2 text-[11px] leading-4 text-muted-foreground">
        Pondération : {Object.entries(weights).map(([k, v]) => `${WEIGHT[k] ?? k.replace("_", " ")} ${Math.round(v * 100)} %`).join(" · ")}. La recrutabilité mesure l’écart entre ton expérience vérifiée et les exigences de l’offre ; ce n’est pas une probabilité d’embauche.
      </p>
    </Section>
  )
}

export function CompensationCard({ o }: { o: OpportunityDetail }) {
  const c = o.compensation_report as Record<string, any> | null // eslint-disable-line @typescript-eslint/no-explicit-any
  const p = c?.pay as Record<string, any> | undefined // eslint-disable-line @typescript-eslint/no-explicit-any
  return (
    <Section title="Rémunération">
      <div className="divide-y px-4">
        <Row label="Par mois (EUR)" value={<Pay c={o.compensation} />} />
        <Row label="Statut" value={<span className="text-xs">{PAY_STATUS[o.compensation.status ?? "COMPENSATION_UNKNOWN"] ?? o.compensation.status}</span>} />
        <Row label="Confiance" value={<span className="text-xs font-medium">{CONFIDENCE[o.compensation.confidence ?? "UNKNOWN"] ?? o.compensation.confidence}</span>} />
        {p && <Row label="Montant annoncé" value={<span className="tabular text-xs">{p.amount_min.toLocaleString("fr-FR")}{p.amount_max !== p.amount_min ? `–${p.amount_max.toLocaleString("fr-FR")}` : ""} {p.currency} / {PERIOD[p.period] ?? p.period ?? "?"}</span>} />}
        <Row label="Coût de la vie (sourcé)" value={money(o.compensation.living_cost_eur)} />
        <Row label="Solde mensuel" value={money(o.compensation.balance_eur)} />
        {c?.benefits && Object.keys(c.benefits).length > 0 && <Row label="Avantages" value={<span className="text-xs">{Object.keys(c.benefits).join(", ").replaceAll("_", " ")}</span>} />}
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
    <Section title="Critères d’éligibilité" action={<EligibilityBadge value={o.eligibility} />}>
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
    <Section title="Description du poste" action={o.official_url && <a href={o.official_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-brand hover:underline">Offre officielle <ExternalLink className="size-3" /></a>}>
      <div className={cn("relative whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed text-foreground/85", !open && "max-h-72 overflow-hidden")}>
        {o.description || "Aucune description enregistrée."}
        {!open && o.description.length > 900 && <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-card" />}
      </div>
      {o.description.length > 900 && <button className="w-full border-t py-2 text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(!open)}>{open ? "Réduire" : "Afficher toute la description"}</button>}
      <div className="border-t px-4 py-2 text-[11px] text-muted-foreground">
        Sources : {o.sources.map((s) => s.source.split(":")[0]).join(", ")} · découverte le {date(o.discovered)} · vérifiée le {date(o.verified)}
      </div>
    </Section>
  )
}

export function WorkspaceGrid({ o }: { o: OpportunityDetail }) {
  return (
    <div className="grid gap-5 px-6 pb-8 pt-4 lg:px-8 xl:grid-cols-3">
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
