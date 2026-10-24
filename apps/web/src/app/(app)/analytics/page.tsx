"use client"

import { useQuery } from "@tanstack/react-query"
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton, Section } from "@/components/os/bits"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { api } from "@/lib/api"
import { FUNNEL_LABEL, STATUS_LABEL, family, money } from "@/lib/format"

type NV = { name: string; value: number }
type Analytics = {
  funnel: { stage: string; value: number }[]
  rates: { response: number | null; interview: number | null; interview_to_offer: number | null; success: number | null; submitted: number }
  applications_over_time: { week: string; value: number }[]
  by_country: NV[]
  by_family: NV[]
  compensation: { histogram: { bin: string; value: number }[]; paid_count: number; converted_count: number; average_min_eur: number | null;
                  balance_available: number; by_country: NV[] }
  outreach: { sent: number; contacts_messaged: number; replied: number; response_rate: number | null; per_week: { week: string; value: number }[]; drafts: number }
  sources: { source: string; listings: number; relevant: number; tracked: number }[]
  small_sample: boolean
}

const cfg: ChartConfig = { value: { label: "Nombre", color: "var(--chart-2)" } }
const line: ChartConfig = { value: { label: "Nombre", color: "var(--chart-1)" } }
const pct = (v: number | null) => (v === null ? "n.d." : `${Math.round(v * 100)} %`)

function Metric({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="bg-card px-4 py-3.5">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="tabular mt-1 text-[22px] font-semibold leading-none tracking-tight">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  )
}

function Bars({ data, x, horizontal }: { data: Record<string, unknown>[]; x: string; horizontal?: boolean }) {
  if (!data.length || data.every((d) => !d.value)) return <EmptyState title="Pas encore de données" />
  return (
    <ChartContainer config={cfg} className="w-full p-3" style={{ height: horizontal ? Math.max(224, data.length * 26 + 24) : 224 }}>
      <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ left: horizontal ? 8 : 0, right: 8 }}>
        <CartesianGrid vertical={!horizontal} horizontal={!!horizontal ? false : true} strokeOpacity={0.4} />
        {horizontal ? <><XAxis type="number" hide allowDecimals={false} /><YAxis type="category" dataKey={x} width={150} interval={0} tickLine={false} axisLine={false} fontSize={11} /></>
          : <><XAxis dataKey={x} tickLine={false} axisLine={false} fontSize={11} /><YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} fontSize={11} /></>}
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="value" fill="var(--color-value)" fillOpacity={0.85} radius={3} maxBarSize={22} />
      </BarChart>
    </ChartContainer>
  )
}

export default function AnalyticsPage() {
  const q = useQuery({ queryKey: ["analytics"], queryFn: () => api.get<Analytics>("/analytics") })
  const a = q.data
  return (
    <div>
      <PageHeader title="Statistiques" description="Uniquement tes vraies données CRM. Un taux reste « n.d. » tant qu’il n’y a rien à diviser." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !a ? <RowsSkeleton rows={10} /> : (
        <div className="space-y-5 px-6 pb-8 pt-4 lg:px-8">
          {a.small_sample && <p className="rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning">Moins de 20 candidatures envoyées : les taux de conversion sont indicatifs.</p>}
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-border md:grid-cols-3 xl:grid-cols-6">
            <Metric label="Envoyées" value={a.rates.submitted} />
            <Metric label="Taux de réponse" value={pct(a.rates.response)} />
            <Metric label="Taux d’entretien" value={pct(a.rates.interview)} />
            <Metric label="Entretien → offre" value={pct(a.rates.interview_to_offer)} />
            <Metric label="Paie moyenne confirmée" value={money(a.compensation.average_min_eur)} hint={`${a.compensation.converted_count} avec un montant en euros`} />
            <Metric label="Réponses aux messages" value={pct(a.outreach.response_rate)} hint={`${a.outreach.replied}/${a.outreach.contacts_messaged} contacts`} />
          </div>
          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Entonnoir des candidatures"><Bars data={a.funnel.map((f) => ({ name: FUNNEL_LABEL[f.stage] ?? STATUS_LABEL[f.stage] ?? f.stage, value: f.value }))} x="name" horizontal /></Section>
            <Section title="Candidatures par semaine">
              {a.applications_over_time.length ? (
                <ChartContainer config={line} className="h-56 w-full p-3">
                  <LineChart data={a.applications_over_time}>
                    <CartesianGrid vertical={false} strokeOpacity={0.4} />
                    <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={11} />
                    <YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} fontSize={11} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Line dataKey="value" stroke="var(--color-value)" strokeWidth={2} dot={{ r: 2.5 }} type="monotone" />
                  </LineChart>
                </ChartContainer>
              ) : <EmptyState title="Aucune candidature envoyée pour l’instant" />}
            </Section>
            <Section title="Candidatures par pays"><Bars data={a.by_country} x="name" horizontal /></Section>
            <Section title="Candidatures par famille de métier"><Bars data={a.by_family.map((f) => ({ ...f, name: family(f.name) }))} x="name" horizontal /></Section>
            <Section title="Paie confirmée (€/mois, bas de fourchette)"><Bars data={a.compensation.histogram.map((h) => ({ name: h.bin, value: h.value }))} x="name" /></Section>
            <Section title="Offres payées confirmées par pays"><Bars data={a.compensation.by_country} x="name" horizontal /></Section>
            <Section title="Messages envoyés par semaine"><Bars data={a.outreach.per_week.map((w) => ({ name: w.week, value: w.value }))} x="name" /></Section>
            <Section title="Sources les plus utiles">
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground"><tr className="border-b">{["Source", "Annonces", "Pertinentes", "Suivies"].map((h) => <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y">{a.sources.slice(0, 10).map((s) => (
                  <tr key={s.source}><td className="px-4 py-1.5">{s.source}</td><td className="tabular px-4">{s.listings}</td><td className="tabular px-4">{s.relevant}</td><td className="tabular px-4">{s.tracked}</td></tr>
                ))}</tbody>
              </table>
            </Section>
          </div>
          <p className="text-xs text-muted-foreground">L’attractivité financière demande des coûts de la vie sourcés ; {a.compensation.balance_available} offre(s) ont un solde estimé.</p>
        </div>
      )}
    </div>
  )
}
