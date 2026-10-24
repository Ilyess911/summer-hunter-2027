"use client"

import { useQuery } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { useState } from "react"

import { ErrorState, PageHeader, RowsSkeleton, Section } from "@/components/os/bits"
import { api } from "@/lib/api"
import { date, relative } from "@/lib/format"
import { cn } from "@/lib/utils"

type Rate = { value: number | null; label: string }
type Perf = {
  last_success: string | null; last_sync: string | null; discovered: number; relevant: number; duplicate_rate: number | null
  compensation_coverage: Rate; precision_at_10: Rate; actionability: Rate; paid_rate: Rate
  llm: { calls: number; cost_usd: number; per_day: { day: string; calls: number; cost: number }[] }
  sources: { source: string; status: string | null; runs: number; failures: number; last_items: number; error: string | null }[]
  runs: { id: number; trigger: string; status: string; started: string; finished: string | null; errors: string[]; stats: Record<string, unknown> }[]
}

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="bg-card px-4 py-3.5"><div className="truncate text-xs text-muted-foreground">{label}</div>
    <div className="tabular mt-1 text-lg font-semibold leading-tight">{value}</div>{hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}</div>
}

export default function PerformancePage() {
  const q = useQuery({ queryKey: ["performance"], queryFn: () => api.get<Perf>("/performance") })
  const [open, setOpen] = useState<number | null>(null)
  const p = q.data
  const failing = p?.sources.filter((s) => s.failures > 0) ?? []
  return (
    <div>
      <PageHeader title="Performance" description="Santé de la recherche, qualité et coût. Les sources en échec sont listées, jamais masquées." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : !p ? <RowsSkeleton rows={10} /> : (
        <div className="space-y-5 px-6 pb-8 pt-4 lg:px-8">
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border bg-border md:grid-cols-4 xl:grid-cols-8">
            <Tile label="Dernière recherche réussie" value={relative(p.last_success)} hint={date(p.last_success, true)} />
            <Tile label="Dernière synchro" value={p.last_sync ? relative(p.last_sync) : "local"} />
            <Tile label="Offres découvertes" value={p.discovered} />
            <Tile label="Pertinentes" value={p.relevant} />
            <Tile label="Doublons" value={p.duplicate_rate === null ? "n.d." : `${Math.round(p.duplicate_rate * 100)} %`} />
            <Tile label="Paie connue" value={p.compensation_coverage.label} />
            <Tile label="Précision@10" value={p.precision_at_10.label} hint="d’après tes notes ⭐" />
            <Tile label="Actionnabilité" value={p.actionability.label} />
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <Section title={`Santé des sources · ${failing.length} en échec`}>
              <div className="max-h-[28rem] overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-card text-xs text-muted-foreground"><tr className="border-b">{["Source", "État", "Passages", "Échecs", "Offres"].map((h) => <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>)}</tr></thead>
                  <tbody className="divide-y">{[...p.sources].sort((a, b) => b.failures - a.failures).map((s) => (
                    <tr key={s.source} className={cn(s.failures > 0 && "bg-destructive/5")} title={s.error ?? undefined}>
                      <td className="max-w-60 truncate px-4 py-1.5">{s.source}</td>
                      <td className={cn("px-4", s.failures > 0 ? "text-destructive" : "text-muted-foreground")}>{s.status ?? "—"}</td>
                      <td className="tabular px-4">{s.runs}</td><td className="tabular px-4">{s.failures}</td><td className="tabular px-4">{s.last_items}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {failing.length > 0 && <div className="space-y-1 border-t px-4 py-2 text-xs">{failing.slice(0, 6).map((s) => <p key={s.source}><b>{s.source}</b>: <span className="text-muted-foreground">{s.error}</span></p>)}</div>}
            </Section>
            <Section title="Recherches">
              <ul className="divide-y">{p.runs.map((r) => (
                <li key={r.id}>
                  <button className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm transition-colors hover:bg-subtle" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <span className={cn("size-1.5 rounded-full", r.status === "ok" ? "bg-success" : r.status === "partial" ? "bg-warning" : "bg-destructive")} />
                    <span className="tabular w-32 text-xs text-muted-foreground">{date(r.started, true)}</span>
                    <span>#{r.id} · {r.trigger} · {r.status}</span>
                    <span className="ml-auto text-xs text-muted-foreground">{r.errors.length} erreur{r.errors.length > 1 ? "s" : ""}</span>
                    <ChevronDown className={cn("size-3.5 transition-transform", open === r.id && "rotate-180")} />
                  </button>
                  {open === r.id && (
                    <div className="space-y-1 bg-muted/30 px-4 py-2 font-mono text-[11px]">
                      {r.errors.length ? r.errors.map((e, i) => <p key={i} className="text-destructive">{e}</p>) : <p className="text-muted-foreground">Aucune erreur.</p>}
                      <p className="text-muted-foreground">{Object.entries(r.stats).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k}=${v}`).join(" · ")}</p>
                    </div>
                  )}
                </li>
              ))}</ul>
            </Section>
            <Section title="Usage et coût de l’IA">
              <div className="p-4 text-sm">
                <p>{p.llm.calls} appels · {p.llm.cost_usd.toFixed(2)} $ au total (les lettres tournent sur le modèle local, sans coût).</p>
                <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">{p.llm.per_day.slice(0, 7).map((d) => <li key={d.day}>{d.day} : {d.calls} appels, {d.cost.toFixed(3)} $</li>)}</ul>
              </div>
            </Section>
          </div>
        </div>
      )}
    </div>
  )
}
