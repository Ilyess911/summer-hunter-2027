"use client"

import { useQuery } from "@tanstack/react-query"
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton, Section } from "@/components/os/bits"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { api } from "@/lib/api"
import { money } from "@/lib/format"

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

const cfg: ChartConfig = { value: { label: "Count", color: "var(--chart-1)" } }
const pct = (v: number | null) => (v === null ? "n/a" : `${Math.round(v * 100)}%`)

function Metric({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="tabular mt-1 text-xl font-semibold tracking-tight">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  )
}

function Bars({ data, x, horizontal }: { data: Record<string, unknown>[]; x: string; horizontal?: boolean }) {
  if (!data.length || data.every((d) => !d.value)) return <EmptyState title="No data yet" />
  return (
    <ChartContainer config={cfg} className="h-56 w-full p-3">
      <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ left: horizontal ? 8 : 0, right: 8 }}>
        <CartesianGrid vertical={!horizontal} horizontal={!!horizontal ? false : true} strokeOpacity={0.4} />
        {horizontal ? <><XAxis type="number" hide allowDecimals={false} /><YAxis type="category" dataKey={x} width={130} tickLine={false} axisLine={false} fontSize={11} /></>
          : <><XAxis dataKey={x} tickLine={false} axisLine={false} fontSize={11} /><YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} fontSize={11} /></>}
        <ChartTooltip content={<ChartTooltipContent />} />
        <Bar dataKey="value" fill="var(--color-value)" radius={4} />
      </BarChart>
    </ChartContainer>
  )
}

export default function AnalyticsPage() {
  const q = useQuery({ queryKey: ["analytics"], queryFn: () => api.get<Analytics>("/analytics") })
  const a = q.data
  return (
    <div>
      <PageHeader title="Analytics" description="Real CRM records only. Rates show n/a until there is something to divide by." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !a ? <RowsSkeleton rows={10} /> : (
        <div className="space-y-5 p-6">
          {a.small_sample && <p className="rounded-md border border-warning/30 bg-warning/8 px-3 py-2 text-xs text-warning">Fewer than 20 submitted applications: conversion rates are indicative only.</p>}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <Metric label="Submitted" value={a.rates.submitted} />
            <Metric label="Response rate" value={pct(a.rates.response)} />
            <Metric label="Interview rate" value={pct(a.rates.interview)} />
            <Metric label="Interview → offer" value={pct(a.rates.interview_to_offer)} />
            <Metric label="Avg confirmed pay" value={money(a.compensation.average_min_eur)} hint={`${a.compensation.converted_count} with a EUR figure`} />
            <Metric label="Outreach reply rate" value={pct(a.outreach.response_rate)} hint={`${a.outreach.replied}/${a.outreach.contacts_messaged} contacts`} />
          </div>
          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Application funnel"><Bars data={a.funnel.map((f) => ({ name: f.stage, value: f.value }))} x="name" horizontal /></Section>
            <Section title="Applications over time (week of)">
              {a.applications_over_time.length ? (
                <ChartContainer config={cfg} className="h-56 w-full p-3">
                  <LineChart data={a.applications_over_time}>
                    <CartesianGrid vertical={false} strokeOpacity={0.4} />
                    <XAxis dataKey="week" tickLine={false} axisLine={false} fontSize={11} />
                    <YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} fontSize={11} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Line dataKey="value" stroke="var(--color-value)" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                </ChartContainer>
              ) : <EmptyState title="No submitted application yet" />}
            </Section>
            <Section title="Applications by country"><Bars data={a.by_country} x="name" horizontal /></Section>
            <Section title="Applications by career family"><Bars data={a.by_family} x="name" horizontal /></Section>
            <Section title="Confirmed pay distribution (monthly EUR, minimum of range)"><Bars data={a.compensation.histogram.map((h) => ({ name: h.bin, value: h.value }))} x="name" /></Section>
            <Section title="Confirmed paid opportunities by country"><Bars data={a.compensation.by_country} x="name" horizontal /></Section>
            <Section title="Outreach per week"><Bars data={a.outreach.per_week.map((w) => ({ name: w.week, value: w.value }))} x="name" /></Section>
            <Section title="Best-performing sources">
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground"><tr className="border-b">{["Source", "Listings", "Relevant", "Tracked"].map((h) => <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y">{a.sources.slice(0, 10).map((s) => (
                  <tr key={s.source}><td className="px-4 py-1.5">{s.source}</td><td className="tabular px-4">{s.listings}</td><td className="tabular px-4">{s.relevant}</td><td className="tabular px-4">{s.tracked}</td></tr>
                ))}</tbody>
              </table>
            </Section>
          </div>
          <p className="text-xs text-muted-foreground">Financial attractiveness needs sourced living costs; {a.compensation.balance_available} opportunities have a balance estimate.</p>
        </div>
      )}
    </div>
  )
}
